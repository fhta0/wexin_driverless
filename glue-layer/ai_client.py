# glue-layer/ai_client.py
from typing import Optional
from openai import OpenAI
from config import AI_API_KEY, AI_API_URL, AI_MODEL

class AIClient:
    """AI API 调用客户端"""

    def __init__(self):
        self.client = OpenAI(
            api_key=AI_API_KEY,
            base_url=AI_API_URL
        )
        self.model = AI_MODEL

        # 系统提示词
        self.system_prompt = """你是一个微信自动回复助手。
根据收到的消息，生成简洁、自然、友好的回复。
回复应该：
1. 简短（通常 1-2 句话）
2. 自然，像真人回复
3. 避免过于正式或机械
4. 不要回复自己的消息"""

    def generate_reply(self, context: str, target: str) -> Optional[str]:
        """
        根据上下文生成回复

        Args:
            context: 消息上下文（如 "来自张三的私聊消息：你好"）
            target: 目标联系人/群名

        Returns:
            生成的回复文本，或 None（表示跳过回复）
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"收到消息：{context}\n请生成回复（回复给 {target}）："}
                ],
                max_tokens=100,
                temperature=0.7
            )

            reply = response.choices[0].message.content.strip()

            # 如果回复表示跳过（如"无需回复"、"不回复"等），返回 None
            skip_keywords = ['无需回复', '不回复', '跳过', '暂不回复', '不用回复']
            if any(kw in reply for kw in skip_keywords):
                return None

            return reply

        except Exception as e:
            print(f"[AIClient] 生成回复失败: {e}")
            return None