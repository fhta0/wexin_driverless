# 微信 Agent 设置 UI 设计文档

## 概述

将微信自动回复 Agent 的配置从 `.env` 文件迁移到 SightFlow 设置页面的图形界面。用户通过 UI 配置身份、监控群组、AI 服务，配置以 JSON 文件形式持久化，glue-layer 热重载生效。

## 设计决策

### 1. UI 位置：扩展 SightFlow 设置页

**选择：** 在 SightFlow 现有设置页面新增"微信 Agent"标签页

**理由：**
- SightFlow 已有完整的设置 UI 基础设施（electron-store、IPC handlers、React 组件）
- 用户只需管理一个应用，降低认知负担
- 复用现有的 UI 模式（卡片布局、表单组件、i18n 支持）

### 2. 配置传递：JSON 配置文件

**选择：** SightFlow 写入 `~/.wechat-agent/config.json`，glue-layer 监听文件变化

**理由：**
- 解耦：SightFlow 和 glue-layer 通过文件系统通信，互不依赖
- 可靠：配置文件可被其他工具读取（调试、手动编辑）
- 简单：不需要额外的 HTTP API 或服务发现机制

### 3. 暴露的设置项：最小化

**选择：** 只暴露用户必须配置的 3 类设置

- 身份配置（wxid + 群昵称）
- 监控群组（从 wx-cli 自动加载）
- AI 服务（API URL + Key）

**不暴露的：**
- AI 模型（用默认值）
- 系统提示词（默认够用）
- 轮询间隔、回复频率（技术细节，保持默认）
- wx-cli 路径（自动检测 PATH）

**理由：** YAGNI 原则，只暴露用户真正需要修改的，减少认知负担。

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
└─────────────────────┘            └─────────────────────┘
```

**核心流程：**
1. 用户在 SightFlow 设置页填写配置
2. 点击保存，SightFlow 写入 `~/.wechat-agent/config.json`
3. glue-layer 定时检查文件修改时间（在现有轮询循环中）
4. 文件变化时，glue-layer 重新加载配置，无需重启

## UI 布局

在 SightFlow 设置页新增 **"微信 Agent"** 标签页（和现有的"基础设置"、"Agent 设置"并列）。

```
┌─────────────────────────────────────────────────────┐
│  基础设置  |  Agent 设置  |  微信 Agent  ← 新增      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─ 身份配置 ────────────────────────────────────┐ │
│  │ 微信 ID:    [192                    ]         │ │
│  │ 群昵称:     [付海涛, 乐悠客技术      ]         │ │
│  │ (用于识别自己的消息和检测@)                    │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌─ 监控群组 ────────────────────────────────────┐ │
│  │ [搜索群名...]                                 │ │
│  │ ☑ TEST                                        │ │
│  │ ☑ abv                                         │ │
│  │ ☐ 其他群...                                   │ │
│  │ (从 wx-cli 自动加载，勾选要监控的群)           │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌─ AI 服务 ────────────────────────────────────┐  │
│  │ API URL:  [https://api.openai.com/v1 ]        │ │
│  │ API Key:  [sk-••••••••••••         ] 🔒       │ │
│  │ (支持 OpenAI 兼容接口)                         │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│              [ 保存配置 ]                           │
└─────────────────────────────────────────────────────┘
```

**交互细节：**
- **身份配置**：两个普通文本输入框（wxid、群昵称逗号分隔）
- **群组选择**：多选列表，数据源是 `wx sessions --json` 返回的群列表，带搜索过滤
- **AI 服务**：URL 文本框 + Key 密码框（带显示/隐藏切换）
- **保存按钮**：写入 JSON 文件，显示"已保存 ✓"提示

## 配置文件格式

路径：`~/.wechat-agent/config.json`

```json
{
  "version": 1,
  "updated_at": "2026-06-23T14:30:00Z",
  "identity": {
    "wxid": "192",
    "names": ["192", "付海涛", "乐悠客技术"]
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
    "api_key": "sk-xxx",
    "model": "gpt-4o-mini"
  }
}
```

**设计要点：**
- `version` 字段用于未来配置迁移
- `identity.names` 是数组，UI 里用逗号分隔输入，JSON 里存结构化数据
- `groups.monitor` 存 room_id + name，方便 glue-layer 直接使用（不需要再查映射）
- `ai.model` 保留字段，目前用默认值，未来 UI 可扩展
- 文件写入用原子操作（先写临时文件再 rename），避免读到半写状态

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
                self._load_config()
                self._last_mtime = mtime
                return True
        except FileNotFoundError:
            pass
        return False
```

**集成到现有轮询循环：**
- 每次轮询（10秒间隔）时调用 `config_watcher.check_for_updates()`
- 如果配置变化，重新加载并打印日志：`[WeChatAgent] 配置已更新，重新加载`
- 更新内存中的配置（identity、groups、ai settings）

**需要注意的：**
- AI 客户端需要重建（API Key/URL 变了）
- 群监控列表实时更新（新增/移除群立即生效）
- 身份配置更新后，下一次消息处理就用新配置

## 实现范围

### SightFlow 端（TypeScript/React）

1. **新增设置标签页组件**
   - `WechatAgentSettings.tsx` — 主组件
   - 三个卡片：IdentityCard、GroupsCard、AIServiceCard

2. **IPC handlers**
   - `wechat-agent:getGroups` — 调用 `wx sessions --json` 获取群列表
   - `wechat-agent:saveConfig` — 写入 `~/.wechat-agent/config.json`
   - `wechat-agent:loadConfig` — 读取当前配置（用于初始化 UI）

3. **配置写入逻辑**
   - 原子写入（写临时文件 → rename）
   - 路径处理（跨平台 `~/.wechat-agent/`）

### glue-layer 端（Python）

1. **配置加载模块**
   - `config_loader.py` — 从 JSON 文件加载配置
   - 回退到 `.env`（开发/调试用途）

2. **热重载机制**
   - `ConfigWatcher` 类，集成到 `WeChatAgent._poll_loop()`
   - 配置变化时重建 AI 客户端、更新群列表

3. **配置使用**
   - `identity` — 替换 `SELF_WXID`、`SELF_NAMES`
   - `groups.monitor` — 替换硬编码的群过滤逻辑
   - `ai.*` — 替换 `AI_API_URL`、`AI_API_KEY`、`AI_MODEL`

## 不在范围内

- AI 模型选择 UI（保留字段，未来扩展）
- 系统提示词编辑
- 运行状态仪表盘
- 消息历史记录
- 多账号支持

## 测试策略

1. **单元测试**
   - `ConfigWatcher` 文件变化检测
   - 配置解析和验证

2. **集成测试**
   - SightFlow 写入配置 → glue-layer 热重载
   - 群组列表从 wx-cli 加载

3. **手动测试**
   - 修改配置后验证 glue-layer 行为变化
   - 边界情况：配置文件不存在、格式错误、权限问题
