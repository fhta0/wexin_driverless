# glue-layer/main.py
import subprocess
import json
import time
import requests
import sys
from typing import List, Optional, Set
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
MIN_MESSAGE_TIMESTAMP_AGE = 5  # 只处理 5 秒前的消息（避免处理刚发送的自己消息）

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
        # 启动时间（用于过滤历史消息）
        self._start_time = time.time()

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
        print("[WeChatAgent] 开始轮询消息...", flush=True)
        self._poll_loop()

    def _poll_loop(self):
        """轮询循环"""
        while True:
            try:
                # 获取新消息
                new_messages = self._get_new_messages()

                if new_messages:
                    print(f"[WeChatAgent] 收到 {len(new_messages)} 条新消息")

                    for msg in new_messages:
                        self._process_message(msg)

                # 等待下一次轮询
                time.sleep(WX_POLL_INTERVAL)

            except KeyboardInterrupt:
                print("[WeChatAgent] 收到中断信号，停止")
                self.message_queue.stop()
                break
            except Exception as e:
                print(f"[WeChatAgent] 轮询异常: {e}")
                time.sleep(WX_POLL_INTERVAL)

    def _get_new_messages(self) -> List[dict]:
        """调用 wx new-messages 获取新消息"""
        try:
            result = subprocess.run(
                [WX_CLI_PATH, 'new-messages', '--json'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=10
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])

            # 多重过滤机制
            filtered = []
            now = time.time()

            for msg in messages:
                # 1. 过滤已处理的消息（防重复）
                local_id = msg.get('local_id')
                if local_id and local_id in self._processed_ids:
                    continue

                # 2. 过滤自己发送的消息
                # sender 字段：非 "192" 表示对方发送
                sender = msg.get('sender', '')
                if sender == '192':
                    # 这是自己发送的消息，跳过
                    if local_id:
                        self._processed_ids.add(local_id)
                    continue

                # 3. 过滤历史消息（只处理启动后收到的新消息）
                timestamp = msg.get('timestamp', 0)
                if timestamp < self._start_time - 5:
                    # 这是在 Agent 启动之前就存在的消息，跳过
                    print(f"[WeChatAgent] 跳过历史消息: local_id={local_id}, ts={timestamp}", flush=True)
                    if local_id:
                        self._processed_ids.add(local_id)
                    continue

                # 4. 过滤太新的消息（可能是自己刚发送的还没被标记）
                if timestamp > now - MIN_MESSAGE_TIMESTAMP_AGE:
                    # 消息太新，可能是自己刚发送的，延迟处理
                    continue

                # 5. 只处理私聊和群聊
                chat_type = msg.get('chat_type', '')
                if chat_type not in ('private', 'group'):
                    if local_id:
                        self._processed_ids.add(local_id)
                    continue

                # 标记为已处理
                if local_id:
                    self._processed_ids.add(local_id)

                filtered.append(msg)

            return filtered

        except Exception as e:
            print(f"[WeChatAgent] 获取消息失败: {e}", flush=True)
            return []

    def _process_message(self, msg: dict):
        """处理单条消息"""
        # 防刷屏检查
        now = time.time()
        while self._send_times and self._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
            self._send_times.popleft()

        if len(self._send_times) >= MAX_MESSAGES_PER_MINUTE:
            print(f"[WeChatAgent] 已达发送上限 ({MAX_MESSAGES_PER_MINUTE}/{MESSAGE_WINDOW_SECONDS}s)，跳过", flush=True)
            return

        # 翻译 wxid
        target, sender_name, context = self.mapper.translate_message(msg)

        print(f"[WeChatAgent] 处理消息: target={target}, sender={sender_name}", flush=True)

        # AI 生成回复
        reply = self.ai_client.generate_reply(context, target)

        if reply:
            # 加入发送队列
            self.message_queue.enqueue(target, reply, context)
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