# glue-layer/main.py
import subprocess
import json
import time
import requests
import sys
import os
from typing import List, Optional, Set, Dict
from collections import deque

# Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from config import SIGHTFLOW_API_URL, WX_POLL_INTERVAL
from config_watcher import ConfigWatcher
from config_model import AgentConfig, get_config_path
from wxid_mapper import WxidMapper
from ai_client import AIClient
from message_queue import MessageQueue

# 进程互斥：PID 文件路径
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.glue-layer.pid')
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.glue-layer.lock')

def check_single_instance() -> bool:
    """检查是否已有实例运行，使用文件锁确保原子性"""
    import msvcrt  # Windows 文件锁

    lock_fd = None
    try:
        # 尝试获取文件锁（非阻塞）
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        try:
            # 尝试获取排他锁（非阻塞）
            msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        except (IOError, OSError):
            # 无法获取锁，说明已有实例在运行
            print(f"[WeChatAgent] 已有实例运行（文件锁被占用），退出", flush=True)
            os.close(lock_fd)
            return False

        # 获取锁成功，检查 PID 文件
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())

                # 检查旧进程是否还在运行
                try:
                    result = subprocess.run(
                        ['tasklist', '/FI', f'PID eq {old_pid}', '/NH'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if str(old_pid) in result.stdout:
                        print(f"[WeChatAgent] 已有实例运行 (PID: {old_pid})，退出", flush=True)
                        # 释放锁并关闭文件
                        try:
                            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                        except:
                            pass
                        os.close(lock_fd)
                        return False
                except:
                    pass

                # 旧进程已不在，删除旧 PID 文件
                print(f"[WeChatAgent] 旧实例已不存在，清理 PID 文件", flush=True)
                os.remove(PID_FILE)
            except (ValueError, IOError):
                # PID 文件格式错误，删除它
                try:
                    os.remove(PID_FILE)
                except:
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
        # 出错时允许继续运行（但不写 PID 文件）
        if lock_fd:
            try:
                os.close(lock_fd)
            except:
                pass
        return True

def cleanup_pid_file():
    """清理 PID 文件和锁文件"""
    import msvcrt
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except:
        pass

# 防刷屏配置
MAX_MESSAGES_PER_MINUTE = 3  # 每分钟最多回复 3 条
MESSAGE_WINDOW_SECONDS = 60  # 时间窗口 60 秒
ECHO_QUIET_SECONDS = 15      # 发送后 15 秒内跳过该联系人消息（防 echo 回环）


def check_autopilot_enabled() -> bool:
    """检查 SightFlow 自动驾驶状态"""
    try:
        response = requests.get(
            f"{SIGHTFLOW_API_URL}/skill/autopilot",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('enabled', True)
        return True  # 默认启用
    except Exception as e:
        print(f"[WeChatAgent] 检查自动驾驶状态失败: {e}", flush=True)
        return True  # 出错时默认启用


def send_log_to_sightflow(log_type: str, content: str, contact: str = None):
    """发送日志到 SightFlow UI"""
    try:
        log_entry = {
            "type": log_type,
            "content": content,
            "timestamp": int(time.time() * 1000)
        }
        if contact:
            log_entry["contact"] = contact

        response = requests.post(
            f"{SIGHTFLOW_API_URL}/skill/log",
            json=log_entry,
            timeout=5
        )
        if response.status_code != 200:
            print(f"[WeChatAgent] 发送日志失败: {response.status_code}", flush=True)
    except Exception as e:
        # 日志发送失败不影响主流程
        pass


class WeChatAgent:
    """微信自动回复 Agent 主类"""

    def __init__(self):
        # 配置监听器
        self._config_watcher = ConfigWatcher(get_config_path())
        self._config_watcher.check_for_updates()  # 首次加载

        # 从配置获取 wx_cli_path
        config = self._config_watcher.get_config()
        wx_cli_path = config.advanced.wx_cli_path if config else 'wx'

        self.mapper = WxidMapper(wx_cli_path)
        self.ai_client = self._create_ai_client()
        self.message_queue: Optional[MessageQueue] = None
        self._processed_keys: Set[str] = set()
        self._send_times: deque = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
        self._last_reply_ts: Dict[str, int] = {}
        self._start_timestamp = int(time.time())
        self._last_send_time: Dict[str, float] = {}

    def _create_ai_client(self) -> Optional[AIClient]:
        """从当前配置创建 AI 客户端，配置未加载返回 None"""
        config = self._config_watcher.get_config()
        if config is None:
            print("[WeChatAgent] 配置未加载，AI 客户端不可用", flush=True)
            return None
        try:
            api_key = config.resolved_api_key
            return AIClient(
                api_key=api_key,
                api_url=config.ai.api_url,
                model=config.ai.model
            )
        except Exception as e:
            print(f"[WeChatAgent] AI 客户端创建失败: {e}，发送 alert", flush=True)
            self._send_alert('api_key_decrypt_failed', f'API Key 解密失败: {e}')
            return None

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
                f"{SIGHTFLOW_API_URL}/skill/alert",
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

    def start(self):
        """启动 Agent"""
        print("[WeChatAgent] 启动中...", flush=True)
        send_log_to_sightflow('info', 'WeChat Agent 启动中...')

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
                    config = self._config_watcher.get_config()
                    if config:
                        self.ai_client = self._create_ai_client()
                        print(f"[WeChatAgent] AI 客户端已重建", flush=True)

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
        config = self._config_watcher.get_config()
        wx_cli_path = config.advanced.wx_cli_path if config else 'wx'
        try:
            result = subprocess.run(
                [wx_cli_path, 'new-messages', '--json', '-n', '50'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=15
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])
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

        # 从配置快照构建 AI 客户端（保证整个消息处理生命周期用同一份配置）
        if config.resolved_api_key is None:
            print("[WeChatAgent] API Key 未配置，跳过", flush=True)
            return
        try:
            ai_client = AIClient(
                api_key=config.resolved_api_key,
                api_url=config.ai.api_url,
                model=config.ai.model
            )
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
            return True

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
                f"{SIGHTFLOW_API_URL}/skill/send-message",
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

def main():
    # 进程互锁检查
    if not check_single_instance():
        sys.exit(1)

    try:
        agent = WeChatAgent()
        agent.start()
    finally:
        cleanup_pid_file()

if __name__ == '__main__':
    main()