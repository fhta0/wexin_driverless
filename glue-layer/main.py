# glue-layer/main.py
import subprocess
import json
import time
import requests
import sys
from typing import List, Optional, Set, Dict
from collections import deque

# Windows 控制台 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from config import (
    WX_CLI_PATH, WX_POLL_INTERVAL,
    SIGHTFLOW_API_URL,
    SELF_WXID
)
from wxid_mapper import WxidMapper
from ai_client import AIClient
from message_queue import MessageQueue

# 防刷屏配置
MAX_MESSAGES_PER_MINUTE = 3  # 每分钟最多回复 3 条
MESSAGE_WINDOW_SECONDS = 60  # 时间窗口 60 秒

class WeChatAgent:
    """微信自动回复 Agent 主类"""

    def __init__(self):
        self.mapper = WxidMapper(WX_CLI_PATH)
        self.ai_client = AIClient()
        self.message_queue: Optional[MessageQueue] = None
        # 已处理消息 ID 缓存（防重复）
        self._processed_ids: Set[int] = set()
        # 发送时间记录（防刷屏）
        self._send_times: deque = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
        # 已回复会话时间戳（避免重复回复同一会话的旧消息）
        self._last_reply_ts: Dict[str, int] = {}

    def start(self):
        """启动 Agent"""
        print("[WeChatAgent] 启动中...", flush=True)

        # 初始化映射表
        print("[WeChatAgent] 初始化联系人映射表...", flush=True)
        if not self.mapper.refresh():
            print("[WeChatAgent] 联系人映射表加载失败，退出", flush=True)
            return

        # 启动消息队列
        self.message_queue = MessageQueue(self._send_message)
        self.message_queue.start()

        # 开始轮询
        print("[WeChatAgent] 开始轮询未读消息...", flush=True)
        self._poll_loop()

    def _poll_loop(self):
        """轮询循环"""
        heartbeat_counter = 0
        while True:
            try:
                # 获取未读会话
                unread_sessions = self._get_unread_sessions()

                if unread_sessions:
                    print(f"[WeChatAgent] 发现 {len(unread_sessions)} 个未读会话", flush=True)

                    for session in unread_sessions:
                        self._process_unread_session(session)

                # 心跳日志（每 12 次轮询约 60 秒）
                heartbeat_counter += 1
                if heartbeat_counter >= 12:
                    print(f"[WeChatAgent] 轮询中... (已处理 {len(self._processed_ids)} 条消息)", flush=True)
                    heartbeat_counter = 0

                # 等待下一次轮询
                time.sleep(WX_POLL_INTERVAL)

            except KeyboardInterrupt:
                print("[WeChatAgent] 收到中断信号，停止", flush=True)
                self.message_queue.stop()
                break
            except Exception as e:
                print(f"[WeChatAgent] 轮询异常: {e}", flush=True)
                time.sleep(WX_POLL_INTERVAL)

    def _get_unread_sessions(self) -> List[dict]:
        """使用 wx unread 获取有未读消息的会话"""
        try:
            result = subprocess.run(
                [WX_CLI_PATH, 'unread', '--json', '--filter', 'private,group'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=10
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx unread 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            sessions = data.get('sessions', [])
            return sessions

        except Exception as e:
            print(f"[WeChatAgent] 获取未读会话失败: {e}", flush=True)
            return []

    def _process_unread_session(self, session: dict):
        """处理单个未读会话"""
        username = session.get('username', '')
        unread_count = session.get('unread', 0)
        last_timestamp = session.get('timestamp', 0)

        # 防止重复处理同一会话（如果时间戳没变化）
        last_reply_ts = self._last_reply_ts.get(username, 0)
        if last_timestamp <= last_reply_ts:
            return

        print(f"[WeChatAgent] 处理未读会话: {session.get('chat')}, 未读={unread_count}", flush=True)

        # 获取最近的未读消息
        messages = self._get_recent_messages(username, unread_count)

        # 找到对方发送的最新消息（未回复的）
        for msg in reversed(messages):  # 从最新到最旧
            sender = msg.get('sender', '')
            local_id = msg.get('local_id')

            # 跳过已处理的消息
            if local_id and local_id in self._processed_ids:
                continue

            # 跳过自己发送的消息（sender == "192"）
            if sender == '192':
                if local_id:
                    self._processed_ids.add(local_id)
                continue

            # 找到对方发送的最新未处理消息
            if sender == '' or sender != '192':
                self._processed_ids.add(local_id)
                self._process_message(msg, session)
                break  # 只处理一条最新消息

    def _get_recent_messages(self, username: str, limit: int) -> List[dict]:
        """获取会话的最近消息"""
        try:
            result = subprocess.run(
                [WX_CLI_PATH, 'history', username, '--json', '-n', str(limit)],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=15
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx history 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])
            return messages

        except Exception as e:
            print(f"[WeChatAgent] 获取历史消息失败: {e}", flush=True)
            return []

    def _process_message(self, msg: dict, session: dict):
        """处理单条消息"""
        # 防刷屏检查
        now = time.time()
        while self._send_times and self._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
            self._send_times.popleft()

        if len(self._send_times) >= MAX_MESSAGES_PER_MINUTE:
            print(f"[WeChatAgent] 已达发送上限 ({MAX_MESSAGES_PER_MINUTE}/{MESSAGE_WINDOW_SECONDS}s)，跳过", flush=True)
            return

        # 获取联系人名（从 session 获取，因为 history 消息没有 sender_wxid）
        chat_type = session.get('chat_type', 'private')
        username = session.get('username', '')
        chat_name = session.get('chat', '')

        # target: SightFlow 搜索目标
        if chat_type == 'private':
            target = chat_name  # 私聊直接用会话名（联系人备注名）
        elif chat_type == 'group':
            target = chat_name  # 群聊用群名
        else:
            target = chat_name

        content = msg.get('content', '')
        context = f"来自 {target} 的消息：{content}"

        print(f"[WeChatAgent] 处理消息: target={target}, content={content[:20]}...", flush=True)

        # AI 生成回复
        reply = self.ai_client.generate_reply(context, target)

        if reply:
            # 加入发送队列
            self.message_queue.enqueue(target, reply, context)
            # 记录回复时间戳
            self._last_reply_ts[username] = msg.get('timestamp', 0)
        else:
            print(f"[WeChatAgent] AI 决定不回复: {target}", flush=True)

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
    agent = WeChatAgent()
    agent.start()

if __name__ == '__main__':
    main()