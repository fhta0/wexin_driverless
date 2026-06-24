# glue-layer/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# SightFlow 配置（固定端口 12680，支持环境变量覆盖）
SIGHTFLOW_API_URL = os.getenv('SIGHTFLOW_API_URL_OVERRIDE',
                    os.getenv('SIGHTFLOW_API_URL', 'http://127.0.0.1:12680'))

# 轮询间隔（开发/调试用，生产环境从 JSON 配置读取）
WX_POLL_INTERVAL = int(os.getenv('WX_POLL_INTERVAL', '10'))

# 以下配置已迁移到 JSON 配置文件（%APPDATA%\WeChatAgent\config.json）：
# - AI_API_KEY / AI_API_URL / AI_MODEL → ai 区块
# - SELF_WXID / SELF_NAMES → identity 区块
# - WX_CLI_PATH → advanced 区块
# - GROUP_NAMES → groups 区块
