# glue-layer/config_model.py
"""微信 Agent 配置模型和校验器"""
import sys
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


SUPPORTED_VERSIONS = {1}


@dataclass
class IdentityConfig:
    wxid: str
    names: List[str]


@dataclass
class GroupEntry:
    room_id: str
    name: str


@dataclass
class GroupsConfig:
    monitor: List[GroupEntry] = field(default_factory=list)


@dataclass
class AIConfig:
    """AI 配置（已废弃，AI 调用统一走 SightFlow Provider）"""
    api_url: str = ""
    api_key: str = ""
    api_key_format: str = "plaintext"  # "dpapi" | "plaintext"
    model: str = "gpt-4o-mini"


@dataclass
class AdvancedConfig:
    wx_cli_path: str = "wx"


@dataclass
class AgentConfig:
    version: int
    identity: IdentityConfig
    groups: GroupsConfig
    ai: AIConfig
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)

    @property
    def resolved_api_key(self) -> str:
        """已废弃：AI 调用统一走 SightFlow Provider，此属性不再使用"""
        return ""


def decrypt_dpapi(encrypted_base64: str) -> str:
    """使用 DPAPI 解密 API Key"""
    import base64
    if sys.platform != 'win32':
        raise RuntimeError("DPAPI decryption only supported on Windows")
    try:
        import win32crypt
    except ImportError:
        raise RuntimeError("pywin32 not installed. Run: pip install pywin32")

    encrypted_data = base64.b64decode(encrypted_base64)
    _, decrypted_bytes = win32crypt.CryptUnprotectData(encrypted_data, None, None, None, 0)
    return decrypted_bytes.decode('utf-8')


def get_config_dir() -> Path:
    """获取配置文件目录"""
    if sys.platform == 'win32':
        import os
        appdata = os.environ.get('APPDATA', '')
        if appdata:
            return Path(appdata) / 'WeChatAgent'
    # Fallback
    return Path.home() / '.wechat-agent'


def get_config_path() -> Path:
    """获取配置文件完整路径"""
    return get_config_dir() / 'config.json'


class ConfigValidator:
    """配置校验器"""

    @staticmethod
    def validate(data: dict) -> AgentConfig:
        """校验并解析配置字典，失败抛出 ValueError"""
        # Version 检查
        version = data.get('version')
        if version is None:
            raise ValueError("Missing required field: version")
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported config version: {version}. Supported: {SUPPORTED_VERSIONS}")

        # Identity 校验
        identity_data = data.get('identity')
        if not identity_data:
            raise ValueError("Missing required field: identity")
        wxid = identity_data.get('wxid', '')
        if not wxid:
            raise ValueError("identity.wxid cannot be empty")
        names = identity_data.get('names', [])
        if not isinstance(names, list):
            raise ValueError("identity.names must be an array")
        # names 可以为空（空数组表示不检测 @提及，群聊中始终回复）
        identity = IdentityConfig(wxid=wxid, names=names)

        # Groups 校验
        groups_data = data.get('groups', {})
        monitor_data = groups_data.get('monitor', [])
        monitor = []
        for g in monitor_data:
            if 'room_id' not in g or 'name' not in g:
                raise ValueError(f"Invalid group entry: {g}. Must have room_id and name")
            monitor.append(GroupEntry(room_id=g['room_id'], name=g['name']))
        groups = GroupsConfig(monitor=monitor)

        # AI 校验（已废弃，AI 调用统一走 SightFlow Provider，这里只做兼容性处理）
        ai_data = data.get('ai', {})
        api_url = ai_data.get('api_url', '')
        api_key = ai_data.get('api_key', '')
        api_key_format = ai_data.get('api_key_format', 'plaintext')
        if api_key_format not in ('dpapi', 'plaintext'):
            raise ValueError(f"ai.api_key_format must be 'dpapi' or 'plaintext', got '{api_key_format}'")
        model = ai_data.get('model', 'gpt-4o-mini')
        ai = AIConfig(api_url=api_url, api_key=api_key, api_key_format=api_key_format, model=model)

        # Advanced 校验（带默认值）
        advanced_data = data.get('advanced', {})
        advanced = AdvancedConfig(
            wx_cli_path=advanced_data.get('wx_cli_path', 'wx')
        )

        return AgentConfig(
            version=version,
            identity=identity,
            groups=groups,
            ai=ai,
            advanced=advanced
        )
