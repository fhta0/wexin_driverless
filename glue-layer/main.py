# glue-layer/main.py
import subprocess
import json
import time
import requests
import sys
from typing import List, Optional

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

class WeChatAgent:
    """微信自动回复 Agent 主类"""

    def __init__(self):
        self.mapper = WxidMapper(WX_CLI_PATH)
        self.ai_client = AIClient()
        self.message_queue: Optional[MessageQueue] = None

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
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}")
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])

            # 过滤自己发送的消息（防循环）
            # wx-cli: sender == "192" 表示自己发送
            filtered = []
            for msg in messages:
                if msg.get('sender') == '192':
                    continue
                if SELF_WXID and msg.get('username') == SELF_WXID:
                    continue
                # 只处理私聊和群聊（过滤公众号等）
                chat_type = msg.get('chat_type', '')
                if chat_type not in ('private', 'group'):
                    continue
                filtered.append(msg)

            return filtered

        except Exception as e:
            print(f"[WeChatAgent] 获取消息失败: {e}")
            return []

    def _process_message(self, msg: dict):
        """处理单条消息"""
        # 翻译 wxid
        target, sender_name, context = self.mapper.translate_message(msg)

        print(f"[WeChatAgent] 处理消息: target={target}, sender={sender_name}")

        # AI 生成回复
        reply = self.ai_client.generate_reply(context, target)

        if reply:
            # 加入发送队列
            self.message_queue.enqueue(target, reply, context)
        else:
            print(f"[WeChatAgent] AI 决定不回复: {target}")

    def _send_message(self, contact: str, message: str) -> bool:
        """调用 SightFlow API 发送消息"""
        try:
            response = requests.post(
                f"{SIGHTFLOW_API_URL}/skill/send-message",
                json={"contact": contact, "message": message},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    print(f"[WeChatAgent] 发送成功: {contact}, elapsed={data.get('elapsed_ms')}ms")
                    return True
                else:
                    print(f"[WeChatAgent] 发送失败: {data.get('error')}")
                    return False
            else:
                print(f"[WeChatAgent] HTTP 错误: {response.status_code}")
                return False

        except requests.exceptions.Timeout:
            print(f"[WeChatAgent] 发送超时: {contact}")
            return False
        except Exception as e:
            print(f"[WeChatAgent] 发送异常: {e}")
            return False

def main():
    agent = WeChatAgent()
    agent.start()

if __name__ == '__main__':
    main()