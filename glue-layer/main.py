# glue-layer/main.py
#
# 微信自动回复粘合层：连接 wx-cli（消息监控）和 SightFlow（消息发送/AI 回复）。
#
# 数据流：
#   wx-cli (wx new-messages) → 轮询 → 过滤/限速/@检测 → POST /skill/generate-reply
#   → AI 回复 → POST /skill/send-message → SightFlow RPA 发送
#
# 日志可靠性：
#   send_log_to_sightflow() 通过 POST /skill/log 发送结构化日志。
#   失败时输出到 stdout（[WeChatAgent][log:<type>] <content>），
#   由 ProcessManager 通过 IPC 转发到 UI，日志不会丢失。
#
# 配置：
#   %APPDATA%\WeChatAgent\config.json（由 SightFlow UI 管理）
#   CLI 参数 --wx-cli-path 优先于 config 中的 advanced.wx_cli_path
import subprocess
import json
import time
import requests
import sys
import os
import argparse
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Set, Dict
from collections import deque

# Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def get_data_dir():
    """获取数据目录（PyInstaller --onefile 下 sys._MEIPASS 是临时目录，不可写）"""
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', '')
        if appdata:
            return os.path.join(appdata, 'WeChatAgent')
    return os.path.join(os.path.expanduser('~'), '.wechat-agent')


# 日志配置：写入 %APPDATA%\WeChatAgent\logs\
_log_dir = os.path.join(get_data_dir(), 'logs')
os.makedirs(_log_dir, exist_ok=True)

logging.basicConfig(
    handlers=[
        logging.StreamHandler(),  # 保留 stdout 输出
        RotatingFileHandler(
            os.path.join(_log_dir, 'glue-layer.log'),
            maxBytes=10*1024*1024, backupCount=5,
            encoding='utf-8'
        )
    ],
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('WeChatAgent')

import config as _config_module
from config import WX_POLL_INTERVAL
from config_watcher import ConfigWatcher
from config_model import AgentConfig, get_config_path
from wxid_mapper import WxidMapper
from ai_client import AIClient
from message_queue import MessageQueue

# 进程互斥：PID 文件路径（放在 %APPDATA%\WeChatAgent\ 下，兼容 PyInstaller）
PID_FILE = os.path.join(get_data_dir(), '.glue-layer.pid')
LOCK_FILE = os.path.join(get_data_dir(), '.glue-layer.lock')


# 平台抽象：文件锁 + 进程存活检测
if sys.platform == 'win32':
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except (IOError, OSError):
            return False

    def _unlock(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass

    def _process_alive(pid: int) -> bool:
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, text=True, timeout=5
            )
            return str(pid) in result.stdout
        except Exception:
            return False
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (IOError, OSError):
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass

    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def check_single_instance() -> bool:
    """检查是否已有实例运行，使用文件锁确保原子性"""
    lock_fd = None
    try:
        # 确保数据目录存在
        os.makedirs(get_data_dir(), exist_ok=True)
        # 尝试获取文件锁（非阻塞）
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        if not _try_lock(lock_fd):
            print(f"[WeChatAgent] 已有实例运行（文件锁被占用），退出", flush=True)
            os.close(lock_fd)
            return False

        # 获取锁成功，检查 PID 文件
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())

                if _process_alive(old_pid):
                    print(f"[WeChatAgent] 已有实例运行 (PID: {old_pid})，退出", flush=True)
                    _unlock(lock_fd)
                    os.close(lock_fd)
                    return False

                # 旧进程已不在，删除旧 PID 文件
                print(f"[WeChatAgent] 旧实例已不存在，清理 PID 文件", flush=True)
                os.remove(PID_FILE)
            except (ValueError, IOError):
                try:
                    os.remove(PID_FILE)
                except Exception:
                    pass

        # 写入当前 PID
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        print(f"[WeChatAgent] 进程互斥检查通过 (PID: {os.getpid()})", flush=True)

        # 注意：不释放锁，保持锁直到进程退出
        # lock_fd 会在进程退出时自动关闭并释放锁
        return True

    except Exception as e:
        print(f"[WeChatAgent] 进程互锁检查失败: {e}", flush=True)
        if lock_fd:
            try:
                os.close(lock_fd)
            except Exception:
                pass
        return True


def cleanup_pid_file():
    """清理 PID 文件和锁文件"""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass

# 防刷屏配置
MAX_MESSAGES_PER_MINUTE = 3  # 每分钟最多回复 3 条
MESSAGE_WINDOW_SECONDS = 60  # 时间窗口 60 秒
ECHO_QUIET_SECONDS = 15      # 发送后 15 秒内跳过该联系人消息（防 echo 回环）


def check_autopilot_enabled() -> bool:
    """检查 SightFlow 自动驾驶状态"""
    try:
        response = requests.get(
            f"{_config_module.SIGHTFLOW_API_URL}/skill/autopilot",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('enabled', False)  # 默认关闭
        return False  # 请求失败时默认关闭
    except Exception as e:
        print(f"[WeChatAgent] 检查自动驾驶状态失败: {e}", flush=True)
        return False  # 出错时默认关闭


def send_log_to_sightflow(log_type: str, content: str, contact: str = None):
    """发送日志到 SightFlow UI（失败时输出到 stdout，不静默吞掉）"""
    try:
        log_entry = {
            "type": log_type,
            "content": content,
            "timestamp": int(time.time() * 1000)
        }
        if contact:
            log_entry["contact"] = contact

        response = requests.post(
            f"{_config_module.SIGHTFLOW_API_URL}/skill/log",
            json=log_entry,
            timeout=5
        )
        if response.status_code != 200:
            print(f"[WeChatAgent] 发送日志失败: HTTP {response.status_code}", flush=True)
    except Exception as e:
        # 发送失败时输出到 stdout，让 ProcessManager 通过 IPC 转发到 UI
        print(f"[WeChatAgent][log:{log_type}] {content}", flush=True)


class WeChatAgent:
    """微信自动回复 Agent 主类"""

    def __init__(self, config_path_override=None, wx_cli_path_override=None):
        # 配置监听器
        cfg_path = config_path_override or get_config_path()
        self._config_watcher = ConfigWatcher(cfg_path)
        self._config_watcher.check_for_updates()  # 首次加载

        # 从配置获取 wx_cli_path（CLI 参数优先）
        config = self._config_watcher.get_config()
        if wx_cli_path_override:
            wx_cli_path = wx_cli_path_override
        else:
            wx_cli_path = config.advanced.wx_cli_path if config else 'wx'

        self._wx_cli_path = wx_cli_path  # 存储 wx_cli_path（CLI 参数优先于 config）
        self.mapper = WxidMapper(wx_cli_path)
        # 不再需要校验 AI 配置，因为 AI 调用统一走 SightFlow Provider
        self.message_queue: Optional[MessageQueue] = None
        self._processed_keys: Set[str] = set()
        self._send_times: deque = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
        self._last_reply_ts: Dict[str, int] = {}
        self._start_timestamp = int(time.time())
        self._last_send_time: Dict[str, float] = {}
        self._new_messages_state: Optional[dict] = None

    def _validate_ai_config(self) -> None:
        """已废弃：AI 配置统一走 SightFlow Provider，不再需要校验"""
        pass

    def _send_alert(self, code: str, message: str, severity: str = 'critical'):
        """向 SightFlow 发送错误告警"""
        try:
            alert = {
                "severity": severity,
                "code": code,
                "message": message,
                "timestamp": int(time.time())
            }
            requests.post(
                f"{_config_module.SIGHTFLOW_API_URL}/skill/alert",
                json=alert,
                timeout=5
            )
        except Exception as e:
            print(f"[WeChatAgent] 发送 alert 失败: {e}", flush=True)

    def _make_msg_key(self, msg: dict) -> str:
        """生成消息唯一标识：时间戳+发送者+内容hash"""
        ts = msg.get('timestamp', 0)
        sender = msg.get('sender', '')
        content = msg.get('content', '')[:50]
        return f"{ts}:{sender}:{hash(content)}"

    def _ensure_daemon_running(self) -> bool:
        """确保 wx-daemon 正在运行，必要时启动它（使用较长的超时时间）"""
        try:
            # 先检查 daemon 状态（快速路径）
            result = subprocess.run(
                [self._wx_cli_path, 'daemon', 'status'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=5
            )
            if result.returncode == 0 and '运行中' in result.stdout:
                print("[WeChatAgent] wx-daemon 已在运行", flush=True)
                return True
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            print(f"[WeChatAgent] 检查 daemon 状态失败: {e}", flush=True)

        # daemon 未运行，需要启动
        print("[WeChatAgent] 启动 wx-daemon...", flush=True)
        send_log_to_sightflow('info', '正在启动 wx-daemon...')

        try:
            # 触发 daemon 启动：执行一个简单的命令，daemon 会自动启动
            # 使用较长的超时时间（30秒），因为首次启动可能需要初始化数据库等
            result = subprocess.run(
                [self._wx_cli_path, 'daemon', 'status'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=30
            )

            # 等待 daemon 完全就绪
            time.sleep(1)

            # 再次检查状态
            result = subprocess.run(
                [self._wx_cli_path, 'daemon', 'status'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=5
            )
            if result.returncode == 0 and '运行中' in result.stdout:
                print("[WeChatAgent] wx-daemon 启动成功", flush=True)
                send_log_to_sightflow('info', 'wx-daemon 启动成功')
                return True
            else:
                print(f"[WeChatAgent] wx-daemon 启动失败: {result.stdout} {result.stderr}", flush=True)
                send_log_to_sightflow('error', 'wx-daemon 启动失败')
                return False

        except subprocess.TimeoutExpired:
            print("[WeChatAgent] wx-daemon 启动超时（>30秒）", flush=True)
            send_log_to_sightflow('error', 'wx-daemon 启动超时')
            return False
        except Exception as e:
            print(f"[WeChatAgent] wx-daemon 启动异常: {e}", flush=True)
            send_log_to_sightflow('error', f'wx-daemon 启动异常: {e}')
            return False

    def start(self):
        """启动 Agent"""
        print("[WeChatAgent] 启动中...", flush=True)
        send_log_to_sightflow('info', 'WeChat Agent 启动中...')

        # 确保 wx-daemon 正在运行（首次启动可能需要较长时间）
        if not self._ensure_daemon_running():
            print("[WeChatAgent] wx-daemon 启动失败，退出", flush=True)
            send_log_to_sightflow('error', 'wx-daemon 启动失败')
            return

        # 初始化映射表
        print("[WeChatAgent] 初始化联系人映射表...", flush=True)
        if not self.mapper.refresh():
            print("[WeChatAgent] 联系人映射表加载失败，退出", flush=True)
            send_log_to_sightflow('error', '联系人映射表加载失败')
            return

        # 启动消息队列
        self.message_queue = MessageQueue(self._send_message)
        self.message_queue.start()

        # 开始轮询
        print("[WeChatAgent] 开始轮询未读消息...", flush=True)
        send_log_to_sightflow('info', '开始轮询未读消息')
        self._poll_loop()

    def _poll_loop(self):
        """轮询循环：使用 wx new-messages 获取新消息"""
        heartbeat_counter = 0
        mapper_refresh_counter = 0
        MAPPER_REFRESH_INTERVAL = 60  # 每 60 次轮询（约 5 分钟）刷新一次映射表
        while True:
            try:
                # === 检查配置更新 ===
                if self._config_watcher.check_for_updates():
                    # AI 配置统一走 SightFlow Provider，不再需要校验
                    pass

                # 检查自动驾驶状态
                if not check_autopilot_enabled():
                    heartbeat_counter += 1
                    if heartbeat_counter >= 12:
                        print("[WeChatAgent] 自动驾驶已禁用，等待启用...", flush=True)
                        send_log_to_sightflow('info', '自动驾驶已禁用')
                        heartbeat_counter = 0
                    time.sleep(WX_POLL_INTERVAL)
                    continue

                # 获取新消息
                new_messages = self._get_new_messages()

                if new_messages:
                    print(f"[WeChatAgent] 发现 {len(new_messages)} 条新消息", flush=True)
                    for msg in new_messages:
                        self._process_new_message(msg)

                # 心跳日志（每 12 次轮询约 60 秒）
                heartbeat_counter += 1
                if heartbeat_counter >= 12:
                    print(f"[WeChatAgent] 轮询中... (已处理 {len(self._processed_keys)} 条消息)", flush=True)
                    send_log_to_sightflow('heartbeat', f'已处理 {len(self._processed_keys)} 条消息')
                    heartbeat_counter = 0

                # 定期刷新映射表（获取新群名、新联系人等）
                mapper_refresh_counter += 1
                if mapper_refresh_counter >= MAPPER_REFRESH_INTERVAL:
                    self.mapper.refresh()
                    mapper_refresh_counter = 0

                # 等待下一次轮询
                time.sleep(WX_POLL_INTERVAL)

            except KeyboardInterrupt:
                print("[WeChatAgent] 收到中断信号，停止", flush=True)
                self.message_queue.stop()
                break
            except Exception as e:
                print(f"[WeChatAgent] 轮询异常: {e}", flush=True)
                time.sleep(WX_POLL_INTERVAL)

    def _get_new_messages(self) -> List[dict]:
        """使用 wx new-messages 获取自上次检查以来的新消息"""
        try:
            # Build command（使用 __init__ 中存储的 wx_cli_path，而非 config 中的路径）
            cmd = [self._wx_cli_path, 'new-messages', '--json', '-n', '50']

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=20  # 增加超时时间，daemon 可能仍在初始化
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])

            # Save cursor state for future use
            new_state = data.get('new_state')
            if new_state:
                self._new_messages_state = new_state

            # 只保留私聊和群聊，过滤系统/公众号等
            return [m for m in messages if m.get('chat_type') in ('private', 'group')]

        except Exception as e:
            print(f"[WeChatAgent] 获取新消息失败: {e}", flush=True)
            return []

    def _process_new_message(self, msg: dict):
        # 获取配置快照（深拷贝，整个消息处理生命周期用同一份）
        config = self._config_watcher.get_config_snapshot()
        if config is None:
            return  # 配置未加载，跳过

        username = msg.get('username', '')
        sender = msg.get('sender', '')
        content = msg.get('content', '')
        msg_type = msg.get('type', '')
        chat_type = msg.get('chat_type', 'private')
        chat_name = msg.get('chat', '')
        timestamp = msg.get('timestamp', 0)

        # 生成消息唯一标识，防重复
        msg_key = self._make_msg_key(msg)
        if msg_key in self._processed_keys:
            return
        self._processed_keys.add(msg_key)

        # 跳过启动前的历史消息
        if timestamp > 0 and timestamp < self._start_timestamp:
            return

        # 跳过自己发送的消息（wxid 匹配，或 sender == '192' 即微信内部系统账号回显）
        if sender == config.identity.wxid or sender == '192':
            return

        # 跳过系统消息
        if msg_type == '系统':
            return

        # 跳过自己发送的回复（防止回环）
        last_send = self._last_send_time.get(username, 0)
        if last_send and time.time() - last_send < ECHO_QUIET_SECONDS:
            print(f"[WeChatAgent] 静默期跳过 (距发送 {time.time() - last_send:.1f}s): {username}", flush=True)
            return

        # 构造 session 字典
        session = {
            'username': username,
            'chat_type': chat_type,
            'chat': chat_name,
            'timestamp': timestamp
        }

        # 传递 config 快照给 _process_message
        self._process_message(msg, session, config)

    def _process_message(self, msg: dict, session: dict, config: AgentConfig):
        """处理单条消息"""
        # 防刷屏检查
        now = time.time()
        while self._send_times and self._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
            self._send_times.popleft()

        if len(self._send_times) >= MAX_MESSAGES_PER_MINUTE:
            print(f"[WeChatAgent] 已达发送上限 ({MAX_MESSAGES_PER_MINUTE}/{MESSAGE_WINDOW_SECONDS}s)，跳过", flush=True)
            return

        # 从配置快照构建 AI 客户端（使用 SightFlow 统一 Provider 系统）
        try:
            ai_client = AIClient()  # 默认使用 http://127.0.0.1:12680
        except Exception as e:
            print(f"[WeChatAgent] AI 客户端构建失败: {e}", flush=True)
            return

        # 获取联系人名（从 session 获取，因为 history 消息没有 sender_wxid）
        chat_type = session.get('chat_type', 'private')
        username = session.get('username', '')
        chat_name = session.get('chat', '')
        content = msg.get('content', '')

        # target: SightFlow 搜索目标
        # 微信搜索框只能搜索：备注名、微信号、昵称
        # wxid（如 wxid_xxx）不能被搜索，只能用备注名/昵称
        # 群聊搜索可以用群名（群名是唯一的）
        if chat_type == 'private':
            target = chat_name  # 私聊用备注名/昵称
            # 私聊：所有消息都回复
            should_reply = True
            context = f"来自 {target} 的私聊消息：{content}"
        elif chat_type == 'group':
            # 群聊：翻译 room_id 为群名
            if '@chatroom' in username:
                target = self.mapper.get_group_name(username)
                # 如果翻译失败（返回原值），尝试用 chat_name
                if target == username:
                    target = chat_name if chat_name and '@chatroom' not in chat_name else username
            else:
                target = chat_name

            # 群聊：只有被 @ 才回复，否则只监控
            should_reply = self._is_mentioned(content, config)
            sender = msg.get('sender', '') or session.get('chat', '')

            if should_reply:
                context = f"群【{target}】中 {sender} @你：{content}"
            else:
                # 只监控，不回复
                print(f"[WeChatAgent] 群消息监控: 群={target}, 发送者={sender}, 内容={content[:30]}...", flush=True)
                send_log_to_sightflow('monitor', f'{sender}: {content[:50]}', target)
                # 更新时间戳防止重复处理
                self._last_reply_ts[username] = msg.get('timestamp', 0)
                return  # 不回复，直接返回
        else:
            target = chat_name
            should_reply = True
            context = f"来自 {target} 的消息：{content}"

        print(f"[WeChatAgent] 处理消息: target={target}, content={content[:20]}...", flush=True)

        # 立即更新会话时间戳，防止重复处理（即使后续处理失败）
        self._last_reply_ts[username] = msg.get('timestamp', 0)

        # 发送收到消息日志到 SightFlow
        send_log_to_sightflow('receive', content[:50] + '...' if len(content) > 50 else content, target)

        # AI 生成回复（使用从快照构建的客户端）
        reply = ai_client.generate_reply(context, target)

        if reply:
            # 发送回复日志到 SightFlow
            send_log_to_sightflow('reply', reply[:50] + '...' if len(reply) > 50 else reply, target)

            # 记录发送时间（防止 echo 回环：发送后 N 秒内跳过该联系人消息）
            self._last_send_time[username] = time.time()

            # 加入发送队列
            self.message_queue.enqueue(target, reply, context)
        else:
            print(f"[WeChatAgent] AI 决定不回复: {target}", flush=True)
            send_log_to_sightflow('skip', 'AI 决定不回复', target)

    def _is_mentioned(self, content: str, config: AgentConfig) -> bool:
        """检测群消息中是否 @ 了自己（使用配置快照）"""
        if not config.identity.names:
            return False  # 空数组表示不检测 @提及，群聊中不回复

        for name in config.identity.names:
            if f"@{name}" in content:
                return True

        return False

    def _send_message(self, contact: str, message: str) -> bool:
        """调用 SightFlow API 发送消息"""
        try:
            # 记录发送时间（防刷屏）
            self._send_times.append(time.time())

            response = requests.post(
                f"{_config_module.SIGHTFLOW_API_URL}/skill/send-message",
                json={"contact": contact, "message": message},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    print(f"[WeChatAgent] 发送成功: {contact}, elapsed={data.get('elapsed_ms')}ms", flush=True)
                    return True
                else:
                    print(f"[WeChatAgent] 发送失败: {data.get('error')}", flush=True)
                    return False
            else:
                print(f"[WeChatAgent] HTTP 错误: {response.status_code}", flush=True)
                return False

        except requests.exceptions.Timeout:
            print(f"[WeChatAgent] 发送超时: {contact}", flush=True)
            return False
        except Exception as e:
            print(f"[WeChatAgent] 发送异常: {e}", flush=True)
            return False

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='WeChat Agent glue-layer')
    parser.add_argument('--wx-cli-path', dest='wx_cli_path',
                        help='wx-cli 可执行文件路径（覆盖 config）')
    parser.add_argument('--sightflow-port', dest='sightflow_port', type=int, default=None,
                        help='SightFlow API 端口（覆盖默认 12680）')
    parser.add_argument('--config', dest='config_path',
                        help='配置文件路径（默认 %APPDATA%\\WeChatAgent\\config.json）')
    return parser.parse_args()


def main():
    # 解析命令行参数
    args = parse_args()

    # 应用 --sightflow-port 覆盖
    if args.sightflow_port is not None:
        _config_module.SIGHTFLOW_API_URL = f'http://127.0.0.1:{args.sightflow_port}'

    # 进程互锁检查
    if not check_single_instance():
        sys.exit(1)

    try:
        agent = WeChatAgent(
            config_path_override=args.config_path,
            wx_cli_path_override=args.wx_cli_path,
        )
        agent.start()
    finally:
        cleanup_pid_file()

if __name__ == '__main__':
    main()