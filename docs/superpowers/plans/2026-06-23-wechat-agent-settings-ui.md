# 微信 Agent 设置 UI 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将微信 Agent 配置从 `.env` 文件迁移到 SightFlow 设置页面 UI，配置文件以加密 JSON 持久化，glue-layer 热重载。

**Architecture:** SightFlow (Electron) 提供设置 UI，将配置写入 `%APPDATA%\WeChatAgent\config.json`（API Key 用 DPAPI 加密）。glue-layer (Python) 监听文件变化，热重载配置。两端通过文件解耦。

**Tech Stack:** TypeScript/React (SightFlow), Python (glue-layer), Electron safeStorage (DPAPI), pywin32

**关键发现（实施前确认）：**
- `wx contacts --json` 只返回私聊联系人，**不包含群**。改用 `wx sessions -n 1000 --json` 过滤 `chat_type == 'group'`
- `wx whoami` 命令不存在，作为前置依赖在 wx-cli 仓库独立实现；UI 先做 fallback（按钮置灰）
- `wx sessions` 输出包含 `chat_type` 字段（已确认）

**已确认的代码基础设施：**
- `broadcastToAllWindows()` 函数存在于 `skill-server.ts`（第 278 行），可直接在 alert handler 中使用
- `window.electron.invoke/on/send` API 存在于 `src/preload/index.ts`，renderer 通过 `window.electron` 访问

---

### Task 1: 验证前置条件

**验证内容：**
- [ ] **Step 1: 验证 wx sessions 输出格式**

```bash
cd D:/code/wexin_driverless
wx sessions -n 5 --json
```

确认输出中每条 session 包含 `chat_type` 字段（值为 `"private"` 或 `"group"`）和 `username` 字段。如果格式不符，调整后续实现。

- [ ] **Step 2: 验证 safeStorage DPAPI 互通**

在 SightFlow main 进程临时运行：
```typescript
import { safeStorage } from 'electron'
import * as fs from 'fs'
const encrypted = safeStorage.encryptString('test-key-123')
fs.writeFileSync('C:/tmp/encrypted.bin', encrypted)
```

在 Python 端验证：
```python
import win32crypt
data = open('C:/tmp/encrypted.bin', 'rb').read()
_, decrypted = win32crypt.CryptUnprotectData(data, None, None, None, 0)
print(decrypted.decode())  # 应输出 'test-key-123'
```

如果报错，说明 Electron 使用了非 NULL entropy，需要用 ctypes 传入相同参数。

- [ ] **Step 3: 确认 pywin32 可用**

```bash
D:/miniconda/envs/py3.12/python.exe -c "import win32crypt; print('OK')"
```

如果不存在：`pip install pywin32`

---

### Task 2: glue-layer 配置模型和校验器

**Files:**
- Create: `glue-layer/config_model.py`
- Create: `glue-layer/test_config_model.py`（放在 glue-layer 根目录，避免 import 路径问题）

- [ ] **Step 1: 创建测试文件**

```python
# glue-layer/test_config_model.py
import json
import pytest
from config_model import AgentConfig, ConfigValidator

class TestConfigValidator:
    def test_valid_config(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": [{"room_id": "test@chatroom", "name": "测试群"}]},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"},
            "advanced": {"wx_cli_path": "wx"}
        }
        config = ConfigValidator.validate(data)
        assert config.identity.wxid == "wxid_test"
        assert config.identity.names == ["测试"]
        assert len(config.groups.monitor) == 1
        assert config.ai.api_key_format == "plaintext"

    def test_missing_wxid_fails(self):
        data = {
            "version": 1,
            "identity": {"names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="wxid"):
            ConfigValidator.validate(data)

    def test_empty_names_fails(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": []},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="names"):
            ConfigValidator.validate(data)

    def test_invalid_api_key_format_fails(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "invalid", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="api_key_format"):
            ConfigValidator.validate(data)

    def test_unsupported_version_fails(self):
        data = {
            "version": 99,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        with pytest.raises(ValueError, match="version"):
            ConfigValidator.validate(data)

    def test_defaults_filled(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
        }
        config = ConfigValidator.validate(data)
        assert config.advanced.wx_cli_path == "wx"

    def test_dpapi_format_accepted(self):
        data = {
            "version": 1,
            "identity": {"wxid": "wxid_test", "names": ["测试"]},
            "groups": {"monitor": []},
            "ai": {"api_url": "https://api.test.com", "api_key": "encrypted-data", "api_key_format": "dpapi", "model": "gpt-4o-mini"}
        }
        config = ConfigValidator.validate(data)
        assert config.ai.api_key_format == "dpapi"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -m pytest tests/test_config_model.py -v
```

Expected: FAIL — `config_model` 模块不存在

- [ ] **Step 3: 实现 config_model.py**

```python
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
    api_url: str
    api_key: str  # 可能是明文或加密后的 base64
    api_key_format: str  # "dpapi" | "plaintext"
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
        """返回解密后的 API Key（plaintext 直接返回，dpapi 需要解密）"""
        if self.ai.api_key_format == "plaintext":
            return self.ai.api_key
        elif self.ai.api_key_format == "dpapi":
            return decrypt_dpapi(self.ai.api_key)
        else:
            raise ValueError(f"Unknown api_key_format: {self.ai.api_key_format}")


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
        if not isinstance(names, list) or len(names) == 0:
            raise ValueError("identity.names must be a non-empty array (at least one nickname)")
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

        # AI 校验
        ai_data = data.get('ai')
        if not ai_data:
            raise ValueError("Missing required field: ai")
        api_url = ai_data.get('api_url', '')
        if not api_url:
            raise ValueError("ai.api_url cannot be empty")
        api_key = ai_data.get('api_key', '')
        if not api_key:
            raise ValueError("ai.api_key cannot be empty")
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -m pytest tests/test_config_model.py -v
```

Expected: 7 passed

- [ ] **Step 5: 添加 pywin32 到 requirements.txt**

```
# glue-layer/requirements.txt
python-dotenv
openai
requests
pywin32; sys_platform == 'win32'
```

- [ ] **Step 6: 提交**

```bash
git add glue-layer/config_model.py glue-layer/tests/test_config_model.py glue-layer/requirements.txt
git commit -m "feat: 添加配置模型和校验器 (config_model.py)"
```

---

### Task 3: glue-layer ConfigWatcher 热重载

**Files:**
- Create: `glue-layer/config_watcher.py`
- Create: `glue-layer/test_config_watcher.py`（放在 glue-layer 根目录）

- [ ] **Step 1: 创建测试文件**

```python
# glue-layer/test_config_watcher.py
import json
import os
import time
import pytest
import tempfile
from pathlib import Path
from config_watcher import ConfigWatcher
from config_model import ConfigValidator


def _write_config(path: Path, data: dict):
    """写入配置文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def _valid_config_data(**overrides):
    """生成有效的配置数据"""
    base = {
        "version": 1,
        "identity": {"wxid": "wxid_test", "names": ["测试"]},
        "groups": {"monitor": []},
        "ai": {"api_url": "https://api.test.com", "api_key": "sk-test", "api_key_format": "plaintext", "model": "gpt-4o-mini"}
    }
    base.update(overrides)
    return base


class TestConfigWatcher:
    def test_loads_config_on_first_check(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        changed = watcher.check_for_updates()

        assert changed is True
        assert watcher.get_config().identity.wxid == "wxid_test"

    def test_no_change_returns_false(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()  # 首次加载
        changed = watcher.check_for_updates()  # 第二次无变化

        assert changed is False

    def test_detects_file_modification(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        # 修改文件（确保 mtime 变化）
        time.sleep(0.1)
        _write_config(config_path, _valid_config_data(identity={"wxid": "wxid_new", "names": ["新名字"]}))
        changed = watcher.check_for_updates()

        assert changed is True
        assert watcher.get_config().identity.wxid == "wxid_new"

    def test_file_not_found_no_crash(self, tmp_path):
        config_path = tmp_path / 'nonexistent.json'
        watcher = ConfigWatcher(config_path)
        changed = watcher.check_for_updates()

        assert changed is False
        assert watcher.get_config() is None

    def test_json_error_preserves_old_config(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()
        old_wxid = watcher.get_config().identity.wxid

        # 写入非法 JSON
        time.sleep(0.1)
        config_path.write_text("not valid json{{{", encoding='utf-8')
        changed = watcher.check_for_updates()

        assert changed is False  # 加载失败，不视为变化
        assert watcher.get_config().identity.wxid == old_wxid  # 保留旧配置

    def test_validation_error_preserves_old_config(self, tmp_path):
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        # 写入格式错误（缺少必填字段）
        time.sleep(0.1)
        _write_config(config_path, {"version": 1, "identity": {}})
        changed = watcher.check_for_updates()

        assert changed is False
        assert watcher.get_config().identity.wxid == "wxid_test"

    def test_deepcopy_snapshot(self, tmp_path):
        """验证 get_config_snapshot 返回深拷贝"""
        config_path = tmp_path / 'config.json'
        _write_config(config_path, _valid_config_data())

        watcher = ConfigWatcher(config_path)
        watcher.check_for_updates()

        snapshot = watcher.get_config_snapshot()
        original = watcher.get_config()

        # 修改 snapshot 不应影响源配置
        snapshot.identity.names.append("修改")
        assert "修改" not in original.identity.names
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -m pytest tests/test_config_watcher.py -v
```

Expected: FAIL — `config_watcher` 模块不存在

- [ ] **Step 3: 实现 config_watcher.py**

```python
# glue-layer/config_watcher.py
"""配置文件监听器 — 检测文件变化并热重载"""
import json
import copy
import os
from typing import Optional
from pathlib import Path

from config_model import AgentConfig, ConfigValidator


class ConfigWatcher:
    """监听配置文件变化，提供热重载"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._last_mtime: float = 0
        self._config: Optional[AgentConfig] = None

    def check_for_updates(self) -> bool:
        """检查文件是否变化，变化则重新加载。返回是否发生了更新。"""
        try:
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_mtime:
                new_config = self._load_and_validate()
                if new_config is not None:
                    self._config = new_config
                    self._last_mtime = mtime
                    print(f"[ConfigWatcher] 配置已更新，重新加载", flush=True)
                    return True
                else:
                    # 加载失败，更新 mtime 避免反复尝试损坏的文件
                    self._last_mtime = mtime
                    return False
        except FileNotFoundError:
            pass
        except PermissionError:
            print(f"[ConfigWatcher] 配置文件被占用，等待下一轮重试", flush=True)
        return False

    def get_config(self) -> Optional[AgentConfig]:
        """获取当前配置（引用）"""
        return self._config

    def get_config_snapshot(self) -> Optional[AgentConfig]:
        """获取当前配置的深拷贝（用于消息处理快照）"""
        if self._config is None:
            return None
        return copy.deepcopy(self._config)

    def _load_and_validate(self) -> Optional[AgentConfig]:
        """加载并校验配置，失败返回 None"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return ConfigValidator.validate(data)
        except json.JSONDecodeError as e:
            print(f"[ConfigWatcher] JSON 解析失败: {e}，保留旧配置", flush=True)
            return None
        except (KeyError, TypeError, ValueError) as e:
            print(f"[ConfigWatcher] 配置校验失败: {e}，保留旧配置", flush=True)
            return None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -m pytest tests/test_config_watcher.py -v
```

Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add glue-layer/config_watcher.py glue-layer/tests/test_config_watcher.py
git commit -m "feat: 添加 ConfigWatcher 热重载机制"
```

---

### Task 4: glue-layer 集成配置到 WeChatAgent

**Files:**
- Modify: `glue-layer/main.py`

- [ ] **Step 1: 修改 main.py 导入和初始化**

在 `glue-layer/main.py` 顶部，替换原有的 config 导入：

```python
# 替换:
# from config import (
#     WX_CLI_PATH, WX_POLL_INTERVAL,
#     SIGHTFLOW_API_URL,
#     SELF_WXID
# )

# 改为:
from config import SIGHTFLOW_API_URL, WX_POLL_INTERVAL
from config_watcher import ConfigWatcher
from config_model import AgentConfig, get_config_path
```

- [ ] **Step 2: 修改 WeChatAgent.__init__ 添加 ConfigWatcher**

```python
class WeChatAgent:
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
```

- [ ] **Step 3: 修改 _poll_loop 集成配置热重载**

在 `_poll_loop` 方法的 `while True:` 循环内，在获取新消息之前添加：

```python
    def _poll_loop(self):
        heartbeat_counter = 0
        mapper_refresh_counter = 0
        MAPPER_REFRESH_INTERVAL = 60
        while True:
            try:
                # === 新增：检查配置更新 ===
                if self._config_watcher.check_for_updates():
                    config = self._config_watcher.get_config()
                    if config:
                        self.ai_client = self._create_ai_client()
                        print(f"[WeChatAgent] AI 客户端已重建", flush=True)
                # === 新增结束 ===

                # 检查自动驾驶状态
                if not check_autopilot_enabled():
                    ...
```

- [ ] **Step 4: 修改 _process_new_message 使用配置快照，传递给 _process_message**

```python
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

        # 使用配置快照中的 wxid 判断自己发送的消息
        if sender == config.identity.wxid:
            return

        # 跳过系统消息
        if msg_type == '系统':
            return

        # ... echo 静默期检查（不变）...

        # 构造 session 字典
        session = {
            'username': username,
            'chat_type': chat_type,
            'chat': chat_name,
            'timestamp': timestamp
        }

        # 传递 config 快照给 _process_message
        self._process_message(msg, session, config)
```

- [ ] **Step 5: 修改 _process_message 签名，接受 config 参数**

```python
    def _process_message(self, msg: dict, session: dict, config: AgentConfig):
        """处理单条消息（config 为快照，保证生命周期内一致）"""
        # AI 客户端检查
        if self.ai_client is None:
            print("[WeChatAgent] AI 客户端未就绪，跳过", flush=True)
            return

        # 防刷屏检查（不变）
        now = time.time()
        while self._send_times and self._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
            self._send_times.popleft()
        if len(self._send_times) >= MAX_MESSAGES_PER_MINUTE:
            return

        # ... 其余代码中，使用 config 替换原来的全局配置 ...

        # 群聊 @ 检测：使用 config 快照
        # 替换: should_reply = self._is_mentioned(content)
        should_reply = self._is_mentioned(content, config)
```

- [ ] **Step 6: 修改 _is_mentioned 接受 config 参数**

```python
    def _is_mentioned(self, content: str, config: AgentConfig) -> bool:
        """检测群消息中是否 @ 了自己（使用配置快照）"""
        if not config.identity.names:
            return True

        for name in config.identity.names:
            if f"@{name}" in content:
                return True
        return False
```

- [ ] **Step 7: 修改 _get_new_messages 使用配置中的 wx_cli_path**

```python
    def _get_new_messages(self) -> List[dict]:
        config = self._config_watcher.get_config()
        wx_cli_path = config.advanced.wx_cli_path if config else 'wx'
        try:
            result = subprocess.run(
                [wx_cli_path, 'new-messages', '--json', '-n', '50'],
                ...
            )
```

- [ ] **Step 7: 运行验证**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -c "from main import WeChatAgent; print('Import OK')"
```

- [ ] **Step 8: 提交**

```bash
git add glue-layer/main.py
git commit -m "feat: WeChatAgent 集成配置热重载，替换 .env 硬编码"
```

---

### Task 5: SightFlow main 进程 — IPC handlers 和配置读写

**Files:**
- Create: `sightflow-desktop-agent/src/main/wechat-agent-config.ts`
- Modify: `sightflow-desktop-agent/src/main/index.ts`

- [ ] **Step 1: 创建 wechat-agent-config.ts**

```typescript
// sightflow-desktop-agent/src/main/wechat-agent-config.ts
import { safeStorage } from 'electron'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'
import { setTimeout as sleep } from 'timers/promises'

// Windows: %APPDATA%\WeChatAgent\
// Fallback (非 Windows): ~/.wechat-agent/
const CONFIG_DIR = process.env.APPDATA
  ? path.join(process.env.APPDATA, 'WeChatAgent')
  : path.join(os.homedir(), '.wechat-agent')
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json')
const SUPPORTED_VERSION = 1

export interface WechatAgentConfig {
  version: number
  identity: {
    wxid: string
    names: string[]
  }
  groups: {
    monitor: Array<{ room_id: string; name: string }>
  }
  ai: {
    api_url: string
    api_key: string  // 存储时加密
    api_key_format: 'dpapi' | 'plaintext'
    model: string
  }
  advanced: {
    wx_cli_path: string
  }
}

export function getConfigPath(): string {
  return CONFIG_FILE
}

export function getConfigDir(): string {
  return CONFIG_DIR
}

/** 读取配置（解密 API Key） */
export function loadConfig(): WechatAgentConfig | null {
  try {
    if (!fs.existsSync(CONFIG_FILE)) return null
    const raw = fs.readFileSync(CONFIG_FILE, 'utf-8')
    const data = JSON.parse(raw) as WechatAgentConfig

    // 解密 API Key
    if (data.ai?.api_key_format === 'dpapi' && data.ai?.api_key) {
      try {
        const encrypted = Buffer.from(data.ai.api_key, 'base64')
        data.ai.api_key = safeStorage.decryptString(encrypted)
      } catch (e) {
        console.error('[WechatAgentConfig] API Key 解密失败:', e)
        // 返回配置但 API Key 为空
        data.ai.api_key = ''
      }
    }
    return data
  } catch (e) {
    console.error('[WechatAgentConfig] 读取配置失败:', e)
    return null
  }
}

/** 写入配置（加密 API Key，原子写入） */
export async function saveConfig(config: WechatAgentConfig): Promise<{ ok: boolean; error?: string }> {
  try {
    // 确保目录存在
    fs.mkdirSync(CONFIG_DIR, { recursive: true })

    // 加密 API Key
    const toWrite = { ...config }
    if (toWrite.ai?.api_key && safeStorage.isEncryptionAvailable()) {
      const encrypted = safeStorage.encryptString(toWrite.ai.api_key)
      toWrite.ai.api_key = encrypted.toString('base64')
      toWrite.ai.api_key_format = 'dpapi'
    }

    toWrite.version = SUPPORTED_VERSION

    // 原子写入：写临时文件 → rename
    const tmpFile = `${CONFIG_FILE}.tmp.${process.pid}.${Date.now()}`
    fs.writeFileSync(tmpFile, JSON.stringify(toWrite, null, 2), 'utf-8')

    // Windows rename 可能因杀毒软件短暂锁定而失败，retry 3 次（指数退避）
    let lastError: Error | null = null
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        fs.renameSync(tmpFile, CONFIG_FILE)
        return { ok: true }
      } catch (e) {
        lastError = e as Error
        await sleep(100 * Math.pow(2, attempt))  // 100ms, 200ms, 400ms
      }
    }

    // 3 次都失败，保留临时文件
    return { ok: false, error: `rename failed after 3 retries: ${lastError?.message}` }
  } catch (e) {
    return { ok: false, error: (e as Error).message }
  }
}
```

- [ ] **Step 2: 在 index.ts 注册 IPC handlers**

在 `src/main/index.ts` 中，找到其他 `ipcMain.handle` 注册的位置，添加：

```typescript
// 在 index.ts 顶部添加 import:
import { loadConfig, saveConfig, getConfigDir, type WechatAgentConfig } from './wechat-agent-config'
import { execFile } from 'child_process'
import { promisify } from 'util'
const execFileAsync = promisify(execFile)

// 在 ipcMain.handle 注册区域添加:

// 微信 Agent: 加载配置
ipcMain.handle('wechat-agent:loadConfig', async () => {
  return loadConfig()
})

// 微信 Agent: 保存配置
ipcMain.handle('wechat-agent:saveConfig', async (_event, config: WechatAgentConfig) => {
  return await saveConfig(config)
})

// 微信 Agent: 获取群组列表（从 wx sessions）
ipcMain.handle('wechat-agent:getGroups', async (_event, wxCliPath?: string) => {
  const wxPath = wxCliPath || 'wx'
  try {
    const { stdout } = await execFileAsync(wxPath, ['sessions', '-n', '1000', '--json'], {
      timeout: 15000,
      encoding: 'utf-8'
    })
    const data = JSON.parse(stdout)
    const sessions = data.sessions || []
    // 过滤群组
    const groups = sessions
      .filter((s: any) => s.chat_type === 'group')
      .map((s: any) => ({ room_id: s.username, name: s.chat }))
    return { ok: true, groups }
  } catch (e: any) {
    console.error('[wechat-agent:getGroups] error:', e)
    return { ok: false, error: e.message, groups: [] }
  }
})

// 微信 Agent: 自动检测 wxid
ipcMain.handle('wechat-agent:detectWxid', async (_event, wxCliPath?: string) => {
  const wxPath = wxCliPath || 'wx'
  try {
    const { stdout } = await execFileAsync(wxPath, ['whoami'], {
      timeout: 10000,
      encoding: 'utf-8'
    })
    const data = JSON.parse(stdout)
    return { ok: true, wxid: data.wxid || '' }
  } catch (e: any) {
    // wx whoami 可能不存在
    return { ok: false, error: e.message, wxid: '' }
  }
})

// 微信 Agent: 打开配置文件夹
ipcMain.handle('wechat-agent:openConfigDir', async () => {
  const dir = getConfigDir()
  fs.mkdirSync(dir, { recursive: true })
  shell.openPath(dir)
  return { ok: true }
})
```

- [ ] **Step 3: 在 skill-server.ts 添加 /skill/alert 端点**

```typescript
// 在 skill-server.ts 中添加:

// Alert 去重 Map
const alertDedupMap = new Map<string, number>()  // code → lastShownTimestamp
const ALERT_DEDUP_MS = 5 * 60 * 1000  // 5 分钟

export interface AlertData {
  severity: 'critical' | 'warning' | 'info'
  code: string
  message: string
  timestamp: number
}

async function handleAlert(res: http.ServerResponse, body: string): Promise<void> {
  let alert: AlertData
  try {
    alert = JSON.parse(body) as AlertData
  } catch {
    jsonResponse(res, 400, { ok: false, error: 'invalid_json' })
    return
  }

  // 去重检查
  const lastShown = alertDedupMap.get(alert.code)
  if (lastShown && Date.now() - lastShown < ALERT_DEDUP_MS) {
    jsonResponse(res, 200, { ok: true, deduplicated: true })
    return
  }
  alertDedupMap.set(alert.code, Date.now())

  // 推送到所有窗口
  broadcastToAllWindows('wechat-agent:alert-pushed', alert)

  jsonResponse(res, 200, { ok: true })
}

// 在 requestHandler 的路由中添加:
// } else if (url === '/skill/alert' && method === 'POST') {
//   const body = await readBody(req)
//   await handleAlert(res, body)
```

- [ ] **Step 4: TypeScript 类型检查**

```bash
cd D:/code/wexin_driverless/sightflow-desktop-agent
npm run typecheck:node
```

- [ ] **Step 5: 提交**

```bash
git add sightflow-desktop-agent/src/main/wechat-agent-config.ts
git add sightflow-desktop-agent/src/main/index.ts
git add sightflow-desktop-agent/src/main/skill-server.ts
git commit -m "feat: 添加微信 Agent IPC handlers 和 alert 端点"
```

---

### Task 6: SightFlow renderer — 设置标签页骨架

**Files:**
- Modify: `sightflow-desktop-agent/src/renderer/src/App.tsx`
- Create: `sightflow-desktop-agent/src/renderer/src/WechatAgentSettings.tsx`

- [ ] **Step 1: 扩展 SettingsSection 类型**

在 `App.tsx` 中修改：

```typescript
// 替换:
// type SettingsSection = 'base' | 'agent'
// 改为:
type SettingsSection = 'base' | 'agent' | 'wechat-agent'
```

- [ ] **Step 2: 在 SettingsWindow 添加导航按钮**

```tsx
// 在 SettingsWindow 函数的 <aside> 中，"智能体" 按钮后面添加:
<button
  className={`settings-nav-item ${section === 'wechat-agent' ? 'active' : ''}`}
  onClick={() => setSection('wechat-agent')}
>
  微信 Agent
</button>

// 修改 main 区域的渲染:
<main className="settings-main">
  {section === 'base' ? <SettingsPanel /> : section === 'agent' ? <AgentPanel /> : <WechatAgentSettings />}
</main>
```

- [ ] **Step 3: 创建 WechatAgentSettings.tsx 骨架**

```tsx
// sightflow-desktop-agent/src/renderer/src/WechatAgentSettings.tsx
import { useState, useEffect, useCallback } from 'react'

interface WechatAgentConfig {
  version: number
  identity: { wxid: string; names: string[] }
  groups: { monitor: Array<{ room_id: string; name: string }> }
  ai: { api_url: string; api_key: string; api_key_format: string; model: string }
  advanced: { wx_cli_path: string }
}

interface GroupOption {
  room_id: string
  name: string
}

export function WechatAgentSettings(): React.JSX.Element {
  const [config, setConfig] = useState<WechatAgentConfig>({
    version: 1,
    identity: { wxid: '', names: [] },
    groups: { monitor: [] },
    ai: { api_url: '', api_key: '', api_key_format: 'dpapi', model: 'gpt-4o-mini' },
    advanced: { wx_cli_path: 'wx' }
  })
  const [availableGroups, setAvailableGroups] = useState<GroupOption[]>([])
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    loadConfig()
    loadGroups()
  }, [])

  const loadConfig = async () => {
    const loaded = await window.electron?.invoke('wechat-agent:loadConfig')
    if (loaded) {
      setConfig(loaded)
    }
  }

  const loadGroups = async () => {
    const result = await window.electron?.invoke('wechat-agent:getGroups', config.advanced.wx_cli_path)
    if (result?.ok) {
      setAvailableGroups(result.groups || [])
    }
  }

  const handleSave = async () => {
    // 客户端校验
    if (!config.identity.wxid.trim()) {
      alert('请填写微信 ID')
      return
    }
    if (config.identity.names.length === 0) {
      alert('请至少添加一个群昵称')
      return
    }
    if (!config.ai.api_url.trim()) {
      alert('请填写 AI API URL')
      return
    }
    if (!config.ai.api_key.trim()) {
      alert('请填写 AI API Key')
      return
    }

    setSaving(true)
    const result = await window.electron?.invoke('wechat-agent:saveConfig', config)
    setSaving(false)
    if (result?.ok) {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } else {
      alert(`保存失败: ${result?.error || '未知错误'}`)
    }
  }

  const handleDetectWxid = async () => {
    const result = await window.electron?.invoke('wechat-agent:detectWxid', config.advanced.wx_cli_path)
    if (result?.ok && result.wxid) {
      setConfig(prev => ({ ...prev, identity: { ...prev.identity, wxid: result.wxid } }))
    }
  }

  const handleOpenConfigDir = async () => {
    await window.electron?.invoke('wechat-agent:openConfigDir')
  }

  // 群监控切换
  const toggleGroup = (group: GroupOption) => {
    setConfig(prev => {
      const monitored = prev.groups.monitor
      const exists = monitored.some(g => g.room_id === group.room_id)
      if (exists) {
        return { ...prev, groups: { monitor: monitored.filter(g => g.room_id !== group.room_id) } }
      } else {
        return { ...prev, groups: { monitor: [...monitored, group] } }
      }
    })
  }

  const isGroupMonitored = (roomId: string) => {
    return config.groups.monitor.some(g => g.room_id === roomId)
  }

  return (
    <div className="settings-page slide-up">
      <div className="settings-page-header">
        <div>
          <h1>微信 Agent</h1>
          <p>配置微信自动回复 Agent 的身份、监控群组和 AI 服务。</p>
        </div>
      </div>

      {/* 身份配置 */}
      <div className="card">
        <div className="card-title">身份配置</div>
        <div className="form-group">
          <label className="form-label">微信 ID</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              className="form-input"
              value={config.identity.wxid}
              onChange={(e) => setConfig(prev => ({ ...prev, identity: { ...prev.identity, wxid: e.target.value } }))}
              placeholder="wxid_xxxxxxxx"
            />
            <button className="btn btn-secondary" onClick={handleDetectWxid}>
              自动检测
            </button>
          </div>
        </div>
        <div className="form-group">
          <label className="form-label">群昵称（输入后按回车添加）</label>
          <TagInput
            tags={config.identity.names}
            onChange={(names) => setConfig(prev => ({ ...prev, identity: { ...prev.identity, names } }))}
          />
          <p className="form-hint">用于识别自己的消息和检测群聊中的 @</p>
        </div>
      </div>

      {/* 监控群组 */}
      <div className="card">
        <div className="card-title">监控群组</div>
        <div className="form-group">
          <p className="form-hint">勾选要开启自动回复的群</p>
          {availableGroups.length === 0 ? (
            <p className="form-hint">未找到群组，请确认 wx-cli daemon 已启动</p>
          ) : (
            <div className="group-list">
              {availableGroups.map(group => (
                <label key={group.room_id} className="group-list-item">
                  <input
                    type="checkbox"
                    checked={isGroupMonitored(group.room_id)}
                    onChange={() => toggleGroup(group)}
                  />
                  <span>{group.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* AI 服务 */}
      <div className="card">
        <div className="card-title">AI 服务</div>
        <div className="form-group">
          <label className="form-label">API URL</label>
          <input
            className="form-input"
            value={config.ai.api_url}
            onChange={(e) => setConfig(prev => ({ ...prev, ai: { ...prev.ai, api_url: e.target.value } }))}
            placeholder="https://api.openai.com/v1"
          />
        </div>
        <div className="form-group">
          <label className="form-label">API Key</label>
          <input
            className="form-input"
            type="password"
            value={config.ai.api_key}
            onChange={(e) => setConfig(prev => ({ ...prev, ai: { ...prev.ai, api_key: e.target.value } }))}
            placeholder="sk-..."
          />
        </div>
        <div className="form-group">
          <label className="form-label">Model</label>
          <input
            className="form-input"
            value={config.ai.model}
            onChange={(e) => setConfig(prev => ({ ...prev, ai: { ...prev.ai, model: e.target.value } }))}
            placeholder="gpt-4o-mini"
          />
        </div>
      </div>

      {/* 高级设置 */}
      <details className="card">
        <summary className="card-title" style={{ cursor: 'pointer' }}>高级设置</summary>
        <div className="form-group">
          <label className="form-label">wx-cli 路径</label>
          <input
            className="form-input"
            value={config.advanced.wx_cli_path}
            onChange={(e) => setConfig(prev => ({ ...prev, advanced: { ...prev.advanced, wx_cli_path: e.target.value } }))}
            placeholder="wx"
          />
          <p className="form-hint">默认自动检测 PATH，如未生效可手动指定完整路径</p>
        </div>
        <button className="btn btn-secondary" onClick={handleOpenConfigDir}>
          打开配置文件夹
        </button>
      </details>

      {/* 保存按钮 */}
      <div style={{ textAlign: 'center', marginTop: '24px' }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '已保存 ✓' : '保存配置'}
        </button>
      </div>
    </div>
  )
}

// TagInput 组件
function TagInput({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [input, setInput] = useState('')

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && input.trim()) {
      e.preventDefault()
      if (!tags.includes(input.trim())) {
        onChange([...tags, input.trim()])
      }
      setInput('')
    }
  }

  const removeTag = (index: number) => {
    onChange(tags.filter((_, i) => i !== index))
  }

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '8px' }}>
        {tags.map((tag, i) => (
          <span key={i} className="tag">
            {tag}
            <button onClick={() => removeTag(i)} style={{ marginLeft: '4px', cursor: 'pointer', background: 'none', border: 'none', color: 'inherit' }}>×</button>
          </span>
        ))}
      </div>
      <input
        className="form-input"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入昵称，按回车添加"
      />
    </div>
  )
}
```

- [ ] **Step 4: 在 App.tsx 导入新组件**

```tsx
// 在 App.tsx 顶部添加:
import { WechatAgentSettings } from './WechatAgentSettings'
```

- [ ] **Step 5: 添加 CSS 样式**

在 `sightflow-desktop-agent/src/renderer/src/index.css` 中添加：

```css
/* 微信 Agent 设置 */
.group-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}

.group-list-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.group-list-item:hover {
  background: var(--bg-secondary, #f5f5f5);
}

.tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 12px;
  background: var(--accent, #4f46e5);
  color: white;
  font-size: 13px;
}

/* Alert banner */
.alert-banner {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.alert-banner.critical {
  background: #fef2f2;
  border: 1px solid #fca5a5;
  color: #991b1b;
}

.alert-banner.warning {
  background: #fffbeb;
  border: 1px solid #fcd34d;
  color: #92400e;
}

.alert-banner .close-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 18px;
  color: inherit;
}
```

- [ ] **Step 6: TypeScript 类型检查**

```bash
cd D:/code/wexin_driverless/sightflow-desktop-agent
npm run typecheck:web
```

- [ ] **Step 7: 提交**

```bash
git add sightflow-desktop-agent/src/renderer/src/App.tsx
git add sightflow-desktop-agent/src/renderer/src/WechatAgentSettings.tsx
git add sightflow-desktop-agent/src/renderer/src/index.css
git commit -m "feat: 添加微信 Agent 设置标签页和 TagInput 组件"
```

---

### Task 7: SightFlow renderer — Alert banner 和通知

**Files:**
- Modify: `sightflow-desktop-agent/src/renderer/src/App.tsx`

- [ ] **Step 1: 在 ControlPanel 中添加 alert 监听**

在 `ControlPanel` 组件中添加：

```tsx
function ControlPanel() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [autopilotEnabled, setAutopilotEnabled] = useState(true)
  const [serverPort, setServerPort] = useState(12680)
  const [currentAlert, setCurrentAlert] = useState<AlertData | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const listenerAttached = useRef(false)

  interface AlertData {
    severity: 'critical' | 'warning' | 'info'
    code: string
    message: string
    timestamp: number
  }

  useEffect(() => {
    if (listenerAttached.current) return
    listenerAttached.current = true

    // 监听 alert 事件
    const unsubAlert = window.electron?.on('wechat-agent:alert-pushed', (alert: AlertData) => {
      setCurrentAlert(alert)
      // 系统通知
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('微信 Agent 告警', { body: alert.message })
      }
    })

    // 请求通知权限
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }

    return () => {
      unsubAlert?.()
    }
  }, [])

  // ... 在 ControlPanel 的 JSX 中，logs 列表上方插入 alert banner（取消注释）:
  return (
    <div className="control-panel">
      {/* Alert Banner — 放在最顶部，所有页面都能看到 */}
      {currentAlert && (
        <div className={`alert-banner ${currentAlert.severity}`}>
          <span>{currentAlert.message}</span>
          <button className="close-btn" onClick={() => setCurrentAlert(null)}>×</button>
        </div>
      )}
      {/* ... 其余 JSX 不变 ... */}
    </div>
  )
```

- [ ] **Step 2: 提交**

```bash
git add sightflow-desktop-agent/src/renderer/src/App.tsx
git commit -m "feat: 添加 alert banner 和系统通知"
```

---

### Task 8: 集成测试和手动验证

- [ ] **Step 1: 端到端测试 — SightFlow 写入 → glue-layer 读取**

1. 启动 SightFlow（`npm run dev`）
2. 打开设置 → 微信 Agent 标签
3. 填写配置并保存
4. 检查 `%APPDATA%\WeChatAgent\config.json` 文件是否生成
5. 检查 API Key 是否被加密（不应该是明文）
6. 启动 glue-layer：
   ```bash
   cd D:/code/wexin_driverless/glue-layer
   D:/miniconda/envs/py3.12/python.exe main.py
   ```
7. 确认日志显示 `[ConfigWatcher] 配置已更新，重新加载`

- [ ] **Step 2: 热重载测试**

1. 在 SightFlow 中修改配置并保存
2. 观察 glue-layer 日志是否显示配置重新加载
3. 确认 AI 客户端重建

- [ ] **Step 3: 错误恢复测试**

1. 手动编辑 `%APPDATA%\WeChatAgent\config.json`，写入非法 JSON
2. 观察 glue-layer 日志是否显示错误但继续运行
3. 恢复正确 JSON，确认配置重新加载

- [ ] **Step 4: Alert 测试**

1. 把 config.json 从当前机器拷贝到另一台 Windows 机器
2. 启动 glue-layer
3. 确认 SightFlow UI 显示红色 alert banner

- [ ] **Step 5: 提交所有修改**

```bash
git push
```

---

### Task 9: 清理旧的 .env 配置代码

**Files:**
- Modify: `glue-layer/config.py`
- Delete: `glue-layer/.env.example`
- Modify: `glue-layer/.gitignore`

- [ ] **Step 1: 精简 config.py — 只保留 SIGHTFLOW_API_URL 和 WX_POLL_INTERVAL**

```python
# glue-layer/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# SightFlow 配置（固定端口 12680）
SIGHTFLOW_API_URL = os.getenv('SIGHTFLOW_API_URL', 'http://127.0.0.1:12680')

# 轮询间隔（开发/调试用，生产环境从 JSON 配置读取）
WX_POLL_INTERVAL = int(os.getenv('WX_POLL_INTERVAL', '10'))

# 以下配置已迁移到 JSON 配置文件（%APPDATA%\WeChatAgent\config.json）：
# - AI_API_KEY / AI_API_URL / AI_MODEL → ai 区块
# - SELF_WXID / SELF_NAMES → identity 区块
# - WX_CLI_PATH → advanced 区块
# - GROUP_NAMES → groups 区块
```

- [ ] **Step 2: 更新 .env.example 说明配置已迁移**

```bash
# 删除旧的 .env.example
rm glue-layer/.env.example

# 创建新的说明文件
cat > glue-layer/.env.example << 'EOF'
# 配置已迁移到 SightFlow UI（设置 → 微信 Agent）
# 配置文件位置：%APPDATA%\WeChatAgent\config.json
#
# 以下环境变量仅用于开发/调试：
# SIGHTFLOW_API_URL=http://127.0.0.1:12680
# WX_POLL_INTERVAL=10
EOF
```

- [ ] **Step 3: 确认 main.py 中没有残留的旧导入**

```bash
cd D:/code/wexin_driverless/glue-layer
grep -n "from config import" main.py
```

应只有 `from config import SIGHTFLOW_API_URL, WX_POLL_INTERVAL`。如果有 SELF_WXID、SELF_NAMES 等残留，删除。

- [ ] **Step 4: 运行测试确认无回归**

```bash
cd D:/code/wexin_driverless/glue-layer
D:/miniconda/envs/py3.12/python.exe -m pytest test_config_model.py test_config_watcher.py -v
```

- [ ] **Step 5: 提交**

```bash
git add glue-layer/config.py glue-layer/.env.example glue-layer/main.py
git commit -m "refactor: 清理旧的 .env 配置代码，完成 JSON 配置迁移"
```
