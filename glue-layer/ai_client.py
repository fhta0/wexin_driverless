# glue-layer/ai_client.py
import requests
from typing import Optional

class AIClient:
    """通过 SightFlow HTTP API 调用 AI 生成回复（统一 Provider 系统）"""

    def __init__(self, sightflow_url: str = "http://127.0.0.1:12680"):
        """
        Args:
            sightflow_url: SightFlow Skill Server 的 URL
        """
        self.sightflow_url = sightflow_url

    def generate_reply(self, context: str, target: str) -> Optional[str]:
        """
        通过 SightFlow API 生成回复

        Args:
            context: 消息上下文（如 "来自张三的私聊消息：你好"）
            target: 目标联系人/群名

        Returns:
            生成的回复文本，或 None（表示跳过回复）
        """
        try:
            response = requests.post(
                f"{self.sightflow_url}/skill/generate-reply",
                json={
                    "context": context,
                    "target": target
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data.get('reply')
                else:
                    error = data.get('error', 'unknown')
                    if error == 'skip':
                        print(f"[AIClient] AI 决定不回复")
                        return None
                    print(f"[AIClient] 生成回复失败: {error}")
                    return None
            else:
                print(f"[AIClient] HTTP 错误: {response.status_code}")
                return None

        except requests.exceptions.Timeout:
            print(f"[AIClient] 生成回复超时")
            return None
        except Exception as e:
            print(f"[AIClient] 生成回复失败: {e}")
            return None
