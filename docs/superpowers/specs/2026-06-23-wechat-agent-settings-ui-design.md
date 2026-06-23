# 微信 Agent 设置 UI 设计文档

## 概述

将微信自动回复 Agent 的配置从 `.env` 文件迁移到 SightFlow 设置页面的图形界面。用户通过 UI 配置身份、监控群组、AI 服务，配置以加密 JSON 文件形式持久化，glue-layer 热重载生效。

**平台：** 仅支持 Windows

## 设计决策

### 1. UI 位置：扩展 SightFlow 设置页

**选择：** 在 SightFlow 现有设置页面新增"微信 Agent"标签页

**理由：**
- SightFlow 已有完整的设置 UI 基础设施（electron-store、IPC handlers、React 组件）
- 用户只需管理一个应用，降低认知负担
- 复用现有的 UI 模式（卡片布局、表单组件、i18n 支持）

### 2. 配置传递：JSON 配置文件

**选择：** SightFlow 写入 `%APPDATA%\WeChatAgent\config.json`，glue-layer 监听文件变化

**路径选择：**
- Windows 上使用 `%APPDATA%\WeChatAgent\`（而非 `~/.wechat-agent/`）
- Node 端：`path.join(process.env.APPDATA, 'WeChatAgent')`
- Python 端：`Path(os.environ['APPDATA']) / 'WeChatAgent'`
- 理由：符合 Windows 习惯，避免 OneDrive 漫游、企业网络驱动器等问题

**理由：**
- 解耦：SightFlow 和 glue-layer 通过文件系统通信，互不依赖
- 可靠：配置文件可被其他工具读取（调试、手动编辑）
- 简单：不需要额外的 HTTP API 或服务发现机制

### 3. 暴露的设置项

**选择：** 暴露用户必须配置的 3 类设置 + 高级设置

- 身份配置（wxid 自动检测 + 群昵称）
- 监控群组（从 wx-cli 自动加载）
- AI 服务（API URL + Key + Model）
- 高级设置（折叠区域）：wx-cli 路径

**不暴露的：**
- 系统提示词（默认够用）
- 轮询间隔、回复频率（技术细节，保持默认）

### 4. 配置来源：一刀切

**选择：** 只读 JSON 配置文件，不保留 `.env` 回退

**理由：** 两套配置源 + 优先级会导致难以调试的 bug。开发调试时直接修改 JSON 文件。

## 架构

```
SightFlow (Electron)                glue-layer (Python)
┌─────────────────────┐            ┌─────────────────────┐
│ 设置页面 UI         │            │                     │
│ ┌─────────────────┐ │  写入 JSON │  ┌───────────────┐  │
│ │ 身份/群组/AI    │─┼────────────┼─▶│ 配置文件监听  │  │
│ └─────────────────┘ │            │  └───────────────┘  │
│                     │            │         │           │
│ electron-store      │            │         ▼           │
│ (持久化 UI 状态)    │            │  运行时配置 (内存)  │
│                     │            │                     │
│ safeStorage (DPAPI) │            │                     │
│ (加密 API Key)      │            │                     │
└─────────────────────┘            └─────────────────────┘
         │                                    │
         ▼                                    ▼
%APPDATA%\WeChatAgent\config.json  (加密的 API Key)
```

**核心流程：**
1. 用户在 SightFlow 设置页填写配置
2. 点击保存，SightFlow 用 `safeStorage.encryptString()` 加密 API Key，写入 `%APPDATA%\WeChatAgent\config.json`
3. glue-layer 定时检查文件修改时间（在现有轮询循环中）
4. 文件变化时，glue-layer 重新加载配置，解密 API Key，无需重启

**安全说明：**
- Windows 上 `safeStorage` 使用 DPAPI（Data Protection API），加密绑定当前 Windows 用户 + 机器
- 其他用户或其他机器无法解密
- 如果用户把 config.json 拷贝到其他机器，API Key 无法使用（需重新配置）

## UI 布局

在 SightFlow 设置页新增 **"微信 Agent"** 标签页（和现有的"基础设置"、"Agent 设置"并列）。

```
┌─────────────────────────────────────────────────────┐
│  基础设置  |  Agent 设置  |  微信 Agent  ← 新增      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─ 身份配置 ────────────────────────────────────┐ │
│  │ 微信 ID:    [wxid_xxxxxxxx        ] [自动检测] │ │
│  │ 群昵称:     [付海涛] [乐悠客技术] [+ 添加]     │ │
│  │ (用于识别自己的消息和检测@)                    │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌─ 监控群组 ────────────────────────────────────┐ │
│  │ [搜索群名...]                                 │ │
│  │ ☑ TEST                                        │ │
│  │ ☑ abv                                         │ │
│  │ ☐ 其他群...                                   │ │
│  │ (从 wx contacts 自动加载，勾选要监控的群)      │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌─ AI 服务 ────────────────────────────────────┐  │
│  │ API URL:  [https://api.openai.com/v1 ]        │ │
│  │ API Key:  [sk-••••••••••••         ] 🔒       │ │
│  │ Model:    [gpt-4o-mini              ]         │ │
│  │ (支持 OpenAI 兼容接口)                         │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌─ 高级设置（折叠） ──────────────────────────┐  │
│  │ wx-cli 路径: [wx                     ]       │ │
│  │ (默认自动检测 PATH，如未生效可手动指定)       │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│              [ 保存配置 ]                           │
└─────────────────────────────────────────────────────┘
```

**交互细节：**
- **身份配置**：
  - 微信 ID：文本框 + "自动检测"按钮（调用 `wx whoami` 或读取 wx-cli 缓存）
  - 群昵称：**Tag input** 组件（输入一个名字，按回车变成 chip，支持删除）
- **群组选择**：多选列表，数据源是 `wx contacts --json` 过滤 `chat_type == 'group'`，带搜索过滤
- **AI 服务**：URL 文本框 + Key 密码框（带显示/隐藏切换）+ Model 文本框
- **高级设置**：折叠区域，默认收起，wx-cli 路径输入框
- **保存按钮**：写入加密 JSON 文件，显示"已保存 ✓"提示

## 配置文件格式

路径：`%APPDATA%\WeChatAgent\config.json`

```json
{
  "version": 1,
  "identity": {
    "wxid": "wxid_xxxxxxxx",
    "names": ["付海涛", "乐悠客技术"]
  },
  "groups": {
    "monitor": [
      {
        "room_id": "50522397150@chatroom",
        "name": "TEST"
      },
      {
        "room_id": "50102516648@chatroom",
        "name": "abv"
      }
    ]
  },
  "ai": {
    "api_url": "https://api.openai.com/v1",
    "api_key_encrypted": "base64-encoded-encrypted-data",
    "api_key_encrypted_flag": true,
    "model": "gpt-4o-mini"
  },
  "advanced": {
    "wx_cli_path": "wx"
  }
}
```

**设计要点：**
- `version` 字段用于未来配置迁移（写入方负责升级 schema）
- `identity.names` 是数组，UI 用 tag input，JSON 存结构化数据
- `groups.monitor` 存 room_id + name，glue-layer 直接使用（无需再查映射）
- `ai.api_key_encrypted`：DPAPI 加密后的 base64 字符串
- `ai.api_key_encrypted_flag`：标记是否已加密（未来兼容明文迁移）
- `ai.model`：用户填写的模型名（文本框，非下拉）
- `advanced.wx_cli_path`：wx-cli 路径（默认 `wx`，自动检测 PATH）
- 文件写入用原子操作（写临时文件 → rename），Windows 上 rename 失败时 retry 3 次（间隔 100ms），防杀毒软件短暂锁文件

## 热重载机制

glue-layer 通过定时检查文件修改时间实现热重载：

```python
class ConfigWatcher:
    def __init__(self, config_path):
        self.config_path = config_path
        self._last_mtime = 0
        self._config = {}
    
    def check_for_updates(self):
        """在轮询循环中调用，检查文件是否变化"""
        try:
            mtime = os.path.getmtime(self.config_path)
            if mtime > self._last_mtime:
                new_config = self._load_and_validate()
                if new_config:
                    self._config = new_config
                    self._last_mtime = mtime
                    return True
        except FileNotFoundError:
            pass
        return False
    
    def _load_and_validate(self):
        """加载并校验配置，失败返回 None（保留旧配置）"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 用 pydantic 或手动校验 schema
            return ConfigValidator.validate(data)
        except json.JSONDecodeError as e:
            print(f"[ConfigWatcher] JSON 解析失败: {e}，保留旧配置", flush=True)
            return None
        except (KeyError, TypeError, ValueError) as e:
            print(f"[ConfigWatcher] 配置校验失败: {e}，保留旧配置", flush=True)
            return None
```

**集成到现有轮询循环：**
- 每次轮询（10秒间隔）时调用 `config_watcher.check_for_updates()`
- 如果配置变化，重新加载并打印日志：`[WeChatAgent] 配置已更新，重新加载`
- 更新内存中的配置（identity、groups、ai settings）

**需要注意的：**
- AI 客户端需要重建（API Key/URL/Model 变了）
- 群监控列表实时更新（新增/移除群立即生效）
- 身份配置更新后，下一次消息处理就用新配置

## 错误处理策略

**配置加载失败：**
- JSON 语法错误 → 保留旧配置 + 打印错误日志（不 crash）
- 字段缺失 → 用默认值补全 + 打印 warn 日志
- 类型错误 → 校验失败，保留旧配置
- 权限问题 → 等下一轮重试

**API Key 解密失败：**
- 文件从其他机器拷贝过来 → 打印错误提示"API Key 需要重新配置"
- 降级处理：AI 功能不可用，但消息监控继续运行

**wx-cli 不可用：**
- daemon 没跑 → UI 里群组列表为空，显示"请先启动 wx-cli daemon"
- 命令执行超时 → 显示错误提示，不 crash

**配置写入冲突：**
- SightFlow 写入时 glue-layer 正在读 → 原子写入 + mtime 检查，读到半写状态的概率极低
- 万一读到空文件 → 校验失败，保留旧配置

## 失败模式表

| 场景 | 预期行为 |
|------|----------|
| SightFlow 没启动 | glue-layer 继续用上次配置运行，日志里显示"SightFlow 未连接" |
| wx-cli daemon 没跑 | UI 群组列表为空，glue-layer 日志显示"wx-cli 不可用" |
| 配置文件不存在 | glue-layer 用默认配置运行（空配置），UI 显示"未配置" |
| 配置文件格式错误 | glue-layer 保留旧配置，日志显示错误详情 |
| API Key 解密失败 | glue-layer 日志显示"API Key 需要重新配置"，AI 功能不可用 |
| 用户改配置时正在发消息 | 当前消息用旧配置完成，下一条消息用新配置 |
| wx-cli 路径错误 | UI 群组列表加载失败，显示"wx-cli 未找到" |
| 文件被杀毒软件锁定 | 写入失败，retry 3 次，仍失败则显示错误提示 |

## 实现范围

### SightFlow 端（TypeScript/React）

1. **新增设置标签页组件**
   - `WechatAgentSettings.tsx` — 主组件
   - 三个卡片：IdentityCard、GroupsCard、AIServiceCard
   - 一个折叠区域：AdvancedSettingsCard

2. **UI 组件**
   - TagInput 组件（群昵称输入）
   - "自动检测"按钮（wxid）
   - 密码框显示/隐藏切换

3. **IPC handlers**
   - `wechat-agent:getGroups` — 调用 `wx contacts --json` 过滤群组
   - `wechat-agent:detectWxid` — 调用 `wx whoami` 或读取 wx-cli 缓存
   - `wechat-agent:saveConfig` — 加密 API Key，写入 `%APPDATA%\WeChatAgent\config.json`
   - `wechat-agent:loadConfig` — 读取并解密配置（用于初始化 UI）

4. **配置写入逻辑**
   - 原子写入（写临时文件 → rename，失败 retry 3 次）
   - 路径处理：`path.join(process.env.APPDATA, 'WeChatAgent')`
   - 加密：`safeStorage.encryptString(apiKey)` → base64 编码

### glue-layer 端（Python）

1. **配置加载模块**
   - `config_loader.py` — 从 JSON 文件加载配置
   - `ConfigValidator` — 用 pydantic 做 schema 校验
   - API Key 解密：调用系统命令或直接用 ctypes + CryptUnprotectData（DPAPI）

2. **热重载机制**
   - `ConfigWatcher` 类，集成到 `WeChatAgent._poll_loop()`
   - 配置变化时重建 AI 客户端、更新群列表
   - 错误处理：加载失败保留旧配置，不 crash

3. **配置使用**
   - `identity` — 替换 `SELF_WXID`、`SELF_NAMES`
   - `groups.monitor` — 替换硬编码的群过滤逻辑
   - `ai.*` — 替换 `AI_API_URL`、`AI_API_KEY`、`AI_MODEL`
   - `advanced.wx_cli_path` — 替换 `WX_CLI_PATH`

## 不在范围内

- 系统提示词编辑
- 运行状态仪表盘
- 消息历史记录
- 多账号支持
- 跨平台支持（仅 Windows）

## 测试策略

### 单元测试（Python）

1. **ConfigWatcher**
   - 文件变化检测（mtime）
   - JSON 解析错误恢复
   - 文件不存在处理
   - 字段缺失时默认值补全

2. **ConfigValidator**
   - 参数化测试各种非法输入（类型错误、字段缺失、格式错误）
   - 有效配置通过校验

3. **API Key 解密**
   - Mock DPAPI 解密过程
   - 解密失败处理

### 集成测试（TypeScript）

1. **IPC handlers**
   - Mock `child_process.spawn` 测试 wx-cli 调用
   - 测试 `wx contacts --json` 解析和过滤

2. **配置读写**
   - 写入加密配置 → 读取解密 → 验证一致性
   - 原子写入 + retry 逻辑

### 手动测试

- 修改配置后验证 glue-layer 热重载
- 边界情况：配置文件不存在、格式错误、权限问题、API Key 解密失败
