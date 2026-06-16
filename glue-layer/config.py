# glue-layer/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# wx-cli 配置
WX_CLI_PATH = os.getenv('WX_CLI_PATH', 'wx')  # wx 命令路径
WX_POLL_INTERVAL = int(os.getenv('WX_POLL_INTERVAL', '5'))  # 轮询间隔（秒）

# SightFlow 配置
SIGHTFLOW_API_URL = os.getenv('SIGHTFLOW_API_URL', 'http://127.0.0.1:12680')

# AI 配置（示例：使用 OpenAI SDK）
AI_API_KEY = os.getenv('AI_API_KEY', '')
AI_API_URL = os.getenv('AI_API_URL', 'https://api.openai.com/v1')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')

# 自己的微信 ID（用于防循环过滤）
SELF_WXID = os.getenv('SELF_WXID', '')

# 自己的群昵称/备注名（用于检测群聊中是否被 @）
# 格式可以是多个名字，逗号分隔，如 "付海涛,老付,海涛"
SELF_NAMES = os.getenv('SELF_NAMES', '')