# 微信自动回复产品发布设计

**日期**: 2026-06-24  
**目标**: 将微信自动回复工具打包为普通用户可使用的产品

---

## 1. 产品定位

### 1.1 目标用户

普通用户（非技术人员），需要开箱即用的体验，不接触命令行或技术细节。

### 1.2 分发方式

官网下载安装包（传统软件分发模式）。

### 1.3 更新策略

**初期**: 用户手动到官网下载新版安装包覆盖安装。  
**后续**: 增加应用内自动检查更新功能。

---

## 2. 安装包设计

### 2.1 安装包结构

采用 **嵌入式架构**：一个安装包包含所有内容，用户只需点击"下一步"。

**预估安装包大小**：~400MB（含 Electron ~180MB、微信安装包 ~235MB、wx-cli、glue-layer）

```
WeChatAgent-Setup.exe (NSIS 安装包, ~400MB)
├── SightFlow 主程序 (现有 Electron 应用)
├── resources/
│   ├── wx-cli/
│   │   └── wx.exe                    # Rust 二进制，daemon 模式
│   ├── glue-layer/
│   │   └── glue-layer.exe            # PyInstaller 打包的 Python 程序
│   └── wechat/
│       └── WeChatWin_4.1.9.exe       # 微信安装包
└── 启动器逻辑 (嵌入 SightFlow main process)
```

### 2.2 打包流程

1. **wx-cli**: `cargo build --release --target x86_64-pc-windows-gnu` → 产出 `wx.exe`
2. **glue-layer**: 
   ```bash
   pyinstaller --onefile \
     --hidden-import=cryptography \
     --hidden-import=cffi \
     --hidden-import=_cffi_backend \
     main.py --name glue-layer
   ```
   → 产出 `glue-layer.exe`（包含所有依赖，含 DPAPI 加密相关模块）
3. **SightFlow**: 将上述两个文件 + 微信安装包复制到 `resources/` 目录，然后 `npm run build:win`
4. **NSIS 安装包**: electron-builder 自动生成

**electron-builder 配置**（`electron-builder.yml` 追加）：
```yaml
extraResources:
  - from: "resources/wx-cli/"
    to: "wx-cli/"
    filter: ["**/*.exe"]
  - from: "resources/glue-layer/"
    to: "glue-layer/"
    filter: ["**/*.exe"]
  - from: "resources/wechat/"
    to: "wechat/"
    filter: ["**/*.exe"]
```

### 2.3 微信版本检查

**风险提示**：此设计强制绑定微信 4.1.9 版本，原因：
- wx-cli 依赖该版本的 DB 加密方案（legacy/V1/V2 XOR/AES）
- 微信更新版本可能改变 DB 格式，导致功能失效
- 微信 EULA 对版本锁定有限制，存在合规风险

**缓解策略**：
- 在官网和产品界面明确标注"需要微信 4.1.9 版本"
- 后续版本跟进微信 DB 格式变化，扩展兼容性
- **产品定位标注为"仅供学习/个人使用"**，避免商业分发措辞

安装时检测微信版本：

- **未安装微信** → 静默安装 `WeChatWin_4.1.9.exe`
- **已安装微信但版本不是 4.1.9** → 弹出对话框询问是否覆盖安装到 4.1.9
- **已是 4.1.9** → 跳过安装

覆盖安装逻辑：
```
检测到微信版本 ≠ 4.1.9
    ↓
弹出对话框："检测到微信版本 X.X.X，本工具需要 4.1.9 版本，是否覆盖安装？
⚠️ 覆盖安装会将微信降级到 4.1.9，可能影响现有数据。"
    ├─ 用户同意 → 静默安装 WeChatWin_4.1.9.exe
    └─ 用户拒绝 → 安装终止，清理已复制文件
```

**未来扩展**：如果微信发布新版本且 DB 格式兼容，更新 wx-cli 以支持新版本，而非强制降级。

### 2.4 安装后

- 复制 SightFlow 到 `%LOCALAPPDATA%\Programs\WeChatAgent\`（无需管理员权限）
- 创建桌面快捷方式
- 创建开始菜单快捷方式

**安装路径选择理由**：
- `%LOCALAPPDATA%\Programs\` 不需要管理员权限，降低安装门槛
- 避免 UAC 弹窗，提升用户体验
- 如果用户需要"为所有用户安装"，后续版本可提供选项（此时需要管理员权限）

---

## 3. 运行时架构

### 3.1 启动流程

用户点击桌面快捷方式后：

```
用户双击 SightFlow 快捷方式
    ↓
SightFlow 主进程启动 (Electron main)
    ├─ 启动 Skill Server (:12680)
    └─ 启动 glue-layer（由 process-manager.ts 管理，§8.4）
         └─ spawn: resources/glue-layer/glue-layer.exe
         └─ CLI 参数：--wx-cli-path=xxx --sightflow-port=12680
    ↓
glue-layer 首次调用 wx 命令时，wx-cli 自动启动 daemon
    │  └─ transport.rs: ensure_daemon() → 检测 daemon 存活 → 不存在则自动 spawn
    │  └─ 无需 SightFlow 显式管理 wx-cli 生命周期
    ↓
SightFlow UI 显示"微信自动回复"模式
    ├─ 状态面板：显示各组件运行状态
    ├─ 开关：启动/停止自动回复
    └─ 配置：监控群聊、AI 设置等
```

**wx-cli daemon 自动启动机制**（`wx-cli/src/cli/transport.rs:38-44`）：
- 每个 `wx` CLI 命令调用 `send()` 时，内部先调 `ensure_daemon()`
- `ensure_daemon()` 检查 daemon 是否存活，不存在则自动 `start_daemon()`
- daemon 使用 `DETACHED_PROCESS`（Windows）/ `setsid()`（Unix）脱离控制台
- **SightFlow 不需要显式 spawn/管理 wx-cli daemon**

**CLI 参数设计**（glue-layer 需新增）：
- `--wx-cli-path <path>`: wx-cli 可执行文件路径（覆盖 config.json 中的 advanced.wx_cli_path）
- `--sightflow-port <port>`: SightFlow API 端口（覆盖默认值 12680）
- `--config <path>`: 配置文件路径（可选，默认 %APPDATA%\WeChatAgent\config.json）

### 3.2 进程管理

**wx-cli daemon（无需外部管理）**：
- `ensure_daemon()` 自动启动、自动检测存活（§3.1）
- daemon 崩溃后，下次 `wx` 命令会重新触发 auto-start
- SightFlow 不需要 spawn、监控或重启 wx-cli

**glue-layer（由 SightFlow 管理）**：
- SightFlow 的 `process-manager.ts`（§8.4）负责启动和监控 glue-layer 子进程
- **指数退避重启策略**：崩溃后等待 2s → 5s → 10s → 停止自动重启（最多 3 次）
- **Windows Job Object**：SightFlow 创建 Job Object 将 glue-layer 加入，确保 SightFlow 退出时子进程级联退出（防止孤儿进程）
- 进程状态通过 IPC 传递给 UI 显示

### 3.3 组件间通信

```
wx-cli daemon（auto-start，无需外部管理）
    ↑  glue-layer 通过 subprocess 调用 wx 命令
    │  （任何 wx 命令自动触发 ensure_daemon()）
    │
glue-layer ──HTTP POST──▶ SightFlow Skill Server (:12680)
                                │
                           AI reply + RPA 发送
                                │
                                ▼
                           WeChat 窗口
```

**组件间通信方式**：
- **glue-layer → wx-cli**：`subprocess.run(['wx', 'new-messages', ...])` → wx 客户端自动连 Named Pipe/UDS
- **glue-layer → SightFlow**：HTTP API（`POST /skill/generate-reply`、`POST /skill/send-message`）
- **SightFlow → wx-cli**：同样通过 IPC handlers spawn `wx` 子命令（如 `wechat-agent:getGroups`）

**wx-cli daemon 就绪**：
- **无需就绪信号协议**。daemon 由 `ensure_daemon()` 自动管理，首次 `wx` 命令调用时自动启动
- glue-layer 调用 `wx new-messages` 时，如果 daemon 还没启动，`ensure_daemon()` 会自动启动它
- **注意**：wx-cli 使用 Named Pipe（Windows）/ UDS（Linux）通信，**不使用 TCP 端口**

### 3.5 进程间数据流

**消息处理流程**：
```
glue-layer 定时轮询
    │
    ├─ subprocess.run(['wx', 'new-messages', '--json'])
    │       └─ wx-cli 内部连 Named Pipe → daemon → 返回新消息
    │
    ├─ 过滤（chat_type、self-send、@检测）+ 限速（per-contact 3条/分钟）
    │
    ├─ POST /skill/generate-reply → SightFlow 调用 Provider 生成回复
    │
    ├─ POST /skill/send-message → SightFlow RPA 发送到微信
    │
    └─ 更新 _processed_keys 去重集合
```

**发送路径互斥**（当前代码缺陷，需修复）：
- SightFlow 有两套发送消息的路径：
  - **路径 A**（glue-layer 使用）：`POST /skill/send-message` → `sendMessageToContact()` → 键盘搜索 + VLM 验证 + 鼠标仿人移动 + 粘贴
  - **路径 B**（主 RPA 使用）：`RuntimeHost` → `device.sendMessage()` → 当前聊天窗口直接输入 + pasteAndSend
- 两条路径同时操作 WeChat 窗口会导致冲突（一个在搜索联系人，另一个在输入）
- **需要互斥锁**：当主 RPA 运行时（`_engineBusy` 标志），`sendMessage` 应等待或拒绝
- 当前代码（`skill-server.ts:313`）有 `_engineBusy` 锁，但 `sendMessage` 端点注释说"不需要并发锁"——这是错误的

**关键数据持久化**：
- **wx-cli 游标**：存储在 `%LOCALAPPDATA%\wx-cli\cache\`，记录每个 shard 最后读取的消息位置
- **glue-layer _processed_keys**：**MVP 阶段不持久化**（内存 Set），崩溃时丢失。重启后可能重复处理少量消息
- **MessageQueue**：**MVP 阶段不持久化**（内存 Queue），崩溃时丢失排队中的消息
- **autopilot 状态**：需要持久化到 electron-store（当前代码缺陷，需修复）

**重复处理缓解**：
- wx-cli 游标在 `new-messages` 被调用时更新（拉到即标记）
- glue-layer 的 `_processed_keys` 用于去重，重启后清空
- 最坏情况：崩溃前已处理但未标记的消息，重启后会被再次拉到并回复
- 后续版本可持久化 `_processed_keys` 到 SQLite 解决

**消息送达确认**（当前限制）：
- `/skill/send-message` 返回 `{ok: true}` 只代表 RPA 动作完成（搜索联系人 + 粘贴 + Enter）
- **不验证消息是否真的出现在聊天窗口**
- **不验证接收方是否收到**
- 统计面板应标注为"已尝试发送"而非"已送达"
- 后续版本可通过视觉验证（VLM 截图分析）确认送达

### 3.4 wx-cli 通信机制

**wx-cli 不使用 TCP 端口**，而是使用：
- **Windows**: Named Pipe `\\.\pipe\wx-cli`
- **Linux/macOS**: Unix Domain Socket `~/.wx-cli/wx-cli.sock`

glue-layer 通过 `subprocess.run(['wx', 'sessions', ...])` 调用 wx-cli，wx 客户端内部自动连接 named pipe/UDS。**不存在端口冲突/递增问题**。

**wx-cli 首次初始化**：
- wx-cli 有自己的配置文件 `~/.wx-cli/config.json`（包含 `db_path`、`key`）
- 首次启动时自动探测 WeChat DB 路径和解密密钥
- 如果自动探测失败，用户需要手动运行 `wx init --wx-dir <path>` 指定 WeChat 数据目录
- SightFlow 在首次启动引导中检测 wx-cli 是否已初始化，未初始化则提供引导

---

## 4. 用户界面设计

### 4.1 SightFlow UI 扩展

在现有 SightFlow 应用中增加"微信自动回复"模式：

#### 4.1.1 状态面板（新增 tab 或侧边栏）

- wx-cli 状态：运行中 / 已停止 / 错误
- glue-layer 状态：运行中 / 已停止 / 错误
- 微信连接状态：已连接 / 未连接
- **自动回复开关（toggle 按钮）**：
  - `POST /skill/autopilot` 端点**已实现**（`skill-server.ts:461-463`）
  - UI toggle **已实现**（`App.tsx:176-315` ControlPanel 组件）
  - **当前缺陷**：状态存于内存变量（`let autopilotEnabled = true`），重启后丢失
  - **需修复**：持久化到 electron-store，启动时从 store 读取初始值
  - 当前默认值：`true`（启用）。产品化后应改为从 electron-store 读取，首次运行默认关闭
- 统计信息：已尝试发送消息数、已回复消息数（**注意：不保证送达确认，仅代表 RPA 动作已执行**）

#### 4.1.2 配置界面（扩展现有设置页）

- 监控的群聊列表（从群列表勾选）
- AI 模型选择（已有，复用）
- 回复策略：延迟、频率限制等
- 高级选项：打开配置文件、导出诊断包

### 4.2 首次启动引导

**设计原则**：引导 wizard 允许跳过，不阻塞用户使用。

**首次运行初始化流程**（SightFlow 在 spawn glue-layer 之前执行）：

```
1. SightFlow 启动
    ↓
2. 检查 %APPDATA%\WeChatAgent\config.json 是否存在
    ├─ 不存在 → 创建最小可用配置：
    │   {
    │     "identity": { "wxid": "<自动获取>", "names": [] },
    │     "groups": { "monitor": [] },
    │     "advanced": { "wx_cli_path": "<resources/wx-cli/wx.exe 绝对路径>" }
    │   }
    └─ 已存在 → 加载配置
    ↓
3. 自动获取 identity.wxid：
    ├─ 调用 wx whoami --json（**需先在 wx-cli 实现此命令，当前未实现**）
    ├─ 如果失败/不可用 → 留空，引导用户手动填写
    └─ 成功 → 写入 config.json
    ↓
4. 启动 glue-layer（传入 CLI 参数）
    └─ wx-cli daemon 会在首次 wx 命令调用时自动启动（无需显式管理）
```

**wx-cli 首次初始化检测**（wx-cli daemon auto-start 时自动执行）：
```
1. 任何 wx 命令触发 ensure_daemon() → daemon 启动
    ↓
2. daemon 启动时检查 ~/.wx-cli/config.json 是否存在
    ├─ 已存在（含 db_path + key）→ 直接使用
    └─ 不存在 → 自动探测 WeChat DB 路径和密钥
                ├─ 成功 → 写入 ~/.wx-cli/config.json
                └─ 失败 → 需要用户手动指定 WeChat 数据目录：
                          "请找到 WeChat 的数据文件夹（通常在 %APPDATA%\Tencent\WeChat\xwechat_files）"
                          → 调用 wx init --wx-dir <path>
```
- SightFlow UI 应检测 wx-cli 初始化状态（通过 `wx ping` 测试）
- 如果初始化失败，在设置页显示引导信息

**identity.wxid 自动发现**（**前置依赖：需先实现 `wx whoami` 命令**）：
- wx-cli **当前未实现** `whoami` 子命令（源码中仅有 shell 占位符 `transport.rs:102`）
- 实现方案：从 contact 数据库中提取 `flag=0` 的记录（即自己的 wxid）
- UI 的"自动检测 wxid"按钮已接线（`WechatAgentSettings.tsx:87-96`），但命令不存在会导致永久失败
- **MVP 前置任务**：在 wx-cli 实现 `wx whoami [--json]` 子命令
- 在 whoami 实现前：UI "自动检测"按钮应禁用 + tooltip 提示"请先升级 wx-cli"

**AI 配置流程**（通过 SightFlow Provider 系统）：
1. 用户在 SightFlow 设置页选择 Provider（豆包/OpenAI/...）
2. 填入 API Key → 存储到 electron-store（DPAPI 加密）
3. glue-layer 通过 SightFlow HTTP API 调用 AI，不需要直接管理 API Key

**VLM Key 配置**（用于 SightFlow RPA 定位联系人）：
- `/skill/send-message` 内部调用 `sendMessageToContact()`，该函数需要 VLM API Key
- VLM Key 用于视觉模型识别搜索结果中的联系人（防止点错）
- **首次引导时必须提示用户配置 VLM Key**（在 SightFlow 基础设置页）
- 如果未配置 VLM Key，消息能生成但发不出去（RPA 无法确认联系人）

```
1. 用户双击桌面快捷方式
    ↓
2. SightFlow 启动，显示欢迎界面
    ↓
3. 自动检测各组件状态
    ├─ ✅ wx-cli 已启动
    ├─ ✅ glue-layer 已启动
    └─ ⚠️ 微信未连接（显示提示，但不阻塞）
    ↓
4. 引导用户配置（向导模式，可跳过）
    ├─ 步骤 1：配置 AI 模型（选择模型 + 填入 API Key）
    │          ← 即使微信未连接也可配置
    ├─ 步骤 2：配置 VLM Key（用于 RPA 定位联系人）
    │          ← 在 SightFlow 基础设置页配置
    ├─ 步骤 3：登录微信（如未连接，提供"打开微信"按钮）
    └─ 步骤 4：选择要监控的群聊（微信连接后才可用）
              ← 可跳过，稍后在主界面配置（允许 0 个群）
    ↓
5. 点击"完成"或"跳过"，进入主界面
    ├─ 左侧：状态面板（组件状态、统计信息）
    └─ 右侧：配置面板（群聊列表、AI 设置、高级选项）
```

**循环依赖处理**：
- 步骤 3（选择群聊）依赖微信登录 + wx-cli daemon 正常
- 如果步骤 2 失败，步骤 3 自动禁用并显示提示
- 用户可随时跳过，稍后在主界面重新配置

### 4.3 日常使用流程

```
用户打开电脑 → 微信自动登录 → SightFlow 自动启动（可选）
    ↓
SightFlow 自动启动各组件 → 开始监控消息
    ↓
收到新消息 → glue-layer 调用 AI 生成回复 → SightFlow 发送
    ↓
用户可在 UI 查看：
- 已处理消息数
- 已回复消息数
- 错误日志
- 实时消息流（可选）
```

---

## 5. 配置管理

### 5.1 配置文件位置

`%APPDATA%\WeChatAgent\config.json`（已有）

### 5.2 配置结构

**注意**：AI 相关配置（API Key、模型等）由 SightFlow Provider 系统管理（electron-store + DPAPI 加密），不存储在此配置文件中。glue-layer 通过 SightFlow HTTP API 调用 AI。

```json
{
  "identity": {
    "wxid": "your_wxid",
    "names": ["昵称1", "昵称2"]
  },
  "groups": {
    "monitor": ["群聊1", "群聊2"]
  },
  "advanced": {
    "wx_cli_path": "",
    "rate_limit_per_contact": 3,
    "echo_quiet_seconds": 15
  }
}
```

**字段说明**：
- `identity.wxid`: 用户自己的微信 ID，用于过滤自己发送的消息（**需实现 `wx whoami` 后自动获取，当前需手动填写**）
- `identity.names`: 用户昵称列表，用于检测群聊中 @自己的情况。**空数组表示不检测 @提及，群聊中不回复**（防止首次配置前全量回复）
- `groups.monitor`: 需要监控的群聊名称列表（存储群名，运行时通过 `WxidMapper.get_room_id_by_name()` 反查 room_id）
- `advanced.wx_cli_path`: wx-cli 可执行文件路径（留空则使用默认路径，CLI 参数 `--wx-cli-path` 优先）
- `advanced.rate_limit_per_contact`: **每个联系人/群**每分钟最大回复数（per-contact 限速，避免一个群占用全部配额）
- `advanced.echo_quiet_seconds`: 发送消息后的静默期（防止处理自己发送的消息）

**限速策略**：
- 采用 per-contact 限速：每个联系人/群独立计算 3条/分钟
- 10 个群各自 3条/分钟，而非全局共享 3条/分钟
- 实现：`MessageQueue` 中维护 `Dict[contact_id, Deque[timestamp]]` 而非全局队列

### 5.3 配置编辑方式

- **UI 图形界面**（主要方式，面向普通用户）
- **直接编辑 JSON 文件**（高级用户，提供"打开配置文件"按钮）

---

## 6. 错误处理

### 6.1 组件启动失败

- **wx-cli daemon 启动失败**
  - UI 显示错误提示："消息监控服务启动失败"
  - 提供"重试"按钮
  - 显示详细日志（可复制给技术支持）

- **glue-layer 启动失败**
  - UI 显示错误提示："自动回复服务启动失败"
  - 提供"重试"按钮
  - 显示详细日志

- **微信未登录**
  - glue-layer 检测到 wx-cli 无法连接微信
  - UI 提示："请先登录微信"
  - 自动打开微信窗口（如果最小化）

### 6.2 进程崩溃恢复

```
glue-layer 进程崩溃
    ↓
SightFlow 检测到进程退出
    ↓
等待 2 秒 → 自动重启
    ↓
重启失败 → 等待 5 秒 → 再次重启
    ↓
连续 3 次失败 → UI 显示错误，停止自动重启
    └─ 用户可手动点击"重新启动"
```

### 6.3 日志与诊断

- **SightFlow 日志**: `electron-log`（已有）
- **glue-layer 日志**: `%APPDATA%\WeChatAgent\logs\glue-layer.log`
  - **实现要求**：glue-layer 需添加 `logging` 配置（`RotatingFileHandler`），输出到指定路径
  - **PyInstaller 注意**：`--onefile` 模式下 `sys._MEIPASS` 是临时目录，日志必须写入 `%APPDATA%`，不能写入程序目录
- **wx-cli 日志**: `%APPDATA%\WeChatAgent\logs\wx-cli.log`
  - wx-cli daemon 需支持 `--log-file` 参数指定日志路径
- **诊断包导出**: UI 提供"导出诊断包"按钮（打包所有日志 + 配置 + 系统信息）
  - **敏感信息 redact 要求**：
    - API Key 永不写入日志（`ai_client.py` 的 `requests` 调用不应记录 headers）
    - 配置文件中的 DPAPI 加密字段保持加密状态导出
    - 提供"redact PII"选项（隐藏联系人 wxid、聊天内容片段）
    - 诊断包应包含版本信息、错误日志、配置快照（不含明文密钥）

---

## 7. 用户数据管理

### 7.1 数据存储位置

- **配置文件**: `%APPDATA%\WeChatAgent\config.json`
- **日志文件**: `%APPDATA%\WeChatAgent\logs\`
- **wx-cli 缓存**: `%LOCALAPPDATA%\wx-cli\cache\`
  - **消息游标**: wx-cli daemon 维护 `new-messages` 的读取位置游标，存储在缓存目录
  - 游标包含：每个 shard 最后读取的消息 ID / 时间戳

### 7.2 卸载行为

卸载时询问是否删除用户数据：
- 删除程序文件
- **保留**用户配置和日志（除非用户明确选择删除）
- **wx-cli 游标处理**：
  - 卸载时**保留** `%LOCALAPPDATA%\wx-cli\cache\` 中的游标数据
  - 重新安装后，wx-cli 继续从上次位置读取，不会重复处理历史消息
  - 如果用户选择"完全删除"，则清理所有缓存（包括游标）

---

## 8. 技术实现要点

### 8.1 wx-cli 打包

```bash
cd wx-cli
cargo build --release --target x86_64-pc-windows-gnu
# 产出: target/x86_64-pc-windows-gnu/release/wx.exe
```

**wx-cli 需新增的命令**（MVP 前置依赖）：

`wx whoami [--json]`：查询当前登录用户 wxid（**当前未实现，需新增**）
```json
{
  "wxid": "your_wxid"
}
```
- 实现方案：从 contact 数据库中提取 `flag=0` 的记录（即自己的 wxid）
- UI 的"自动检测 wxid"按钮（`WechatAgentSettings.tsx:87-96`）已接线，等待命令实现
- 用于首次启动时自动获取 identity.wxid

**wx-cli 首次初始化**（首次启动引导时执行）：
```
1. 检测 wx-cli 是否已初始化（检查 ~/.wx-cli/config.json 是否存在）
    ├─ 已存在 → 跳过
    └─ 不存在 → 自动探测 WeChat DB 路径和密钥
                ├─ 成功 → 写入 ~/.wx-cli/config.json
                └─ 失败 → 引导用户手动指定 WeChat 数据目录：
                          "请找到 WeChat 的数据文件夹（通常在 %APPDATA%\Tencent\WeChat\xwechat_files）"
```

### 8.2 glue-layer 打包

```bash
cd glue-layer
pip install pyinstaller cryptography pywin32
pyinstaller --onefile \
  --hidden-import=cryptography \
  --hidden-import=cffi \
  --hidden-import=_cffi_backend \
  --hidden-import=win32crypt \
  --hidden-import=win32security \
  --hidden-import=win32gui \
  --hidden-import=win32process \
  --hidden-import=win32api \
  --hidden-import=win32con \
  --add-data "config_model.py;." \
  --add-data "wxid_mapper.py;." \
  main.py --name glue-layer
# 产出: dist/glue-layer.exe
```

**注意事项**：
- `--hidden-import` 确保 native 扩展被打包（cryptography、pywin32 相关模块）
- `--add-data` 确保所有 Python 模块被包含
- DPAPI 加密需要 `win32crypt`、`win32security`
- `wxid_mapper.py` 需要 `win32gui`、`win32process`

**glue-layer 代码修改要求**（打包前必须完成）：
1. **PID/LOCK 文件路径**：改为 `%APPDATA%\WeChatAgent\`，不再使用 `__file__` 目录
   ```python
   def get_data_dir():
       return os.path.join(os.environ['APPDATA'], 'WeChatAgent')
   PID_FILE = os.path.join(get_data_dir(), '.glue-layer.pid')
   LOCK_FILE = os.path.join(get_data_dir(), '.glue-layer.lock')
   ```

2. **identity.names 空数组行为**：改为不回复（而非全量回复）
   ```python
   def _is_mentioned(self, content: str, config: AgentConfig) -> bool:
       if not config.identity.names:
           return False  # 空数组表示不检测 @提及，群聊中不回复
       ...
   ```

3. **autopilot 检查失败行为**：改为默认关闭
   ```python
   def check_autopilot_enabled() -> bool:
       ...
       except Exception as e:
           print(f"[WeChatAgent] 检查自动驾驶状态失败: {e}", flush=True)
           return False  # 出错时默认关闭，而非启用
   ```

4. **日志路径统一**：所有日志写入 `%APPDATA%\WeChatAgent\logs\`
   ```python
   import logging
   from logging.handlers import RotatingFileHandler
   
   log_dir = os.path.join(os.environ['APPDATA'], 'WeChatAgent', 'logs')
   os.makedirs(log_dir, exist_ok=True)
   logging.basicConfig(
       handlers=[RotatingFileHandler(
           os.path.join(log_dir, 'glue-layer.log'),
           maxBytes=10*1024*1024, backupCount=5
       )],
       level=logging.INFO,
       format='%(asctime)s [%(levelname)s] %(message)s'
   )
   ```

5. **wxid_mapper 进程名匹配**：精确匹配 `WeChat.exe`
   ```python
   if process_name and process_name.lower().endswith('wechat.exe'):
       ...
   ```

### 8.3 SightFlow 集成

在 `sightflow-desktop-agent/resources/` 目录创建子目录：
- `wx-cli/` → 放入 `wx.exe`
- `glue-layer/` → 放入 `glue-layer.exe`
- `wechat/` → 放入 `WeChatWin_4.1.9.exe`

**electron-builder.yml 追加配置**：
```yaml
extraResources:
  - from: "resources/wx-cli/"
    to: "wx-cli/"
    filter: ["**/*.exe"]
  - from: "resources/glue-layer/"
    to: "glue-layer/"
    filter: ["**/*.exe"]
  - from: "resources/wechat/"
    to: "wechat/"
    filter: ["**/*.exe"]
```

**说明**：使用 `extraResources` 而非 `extraFiles`，因为这些文件不需要解压到安装目录，而是打包在 asar 外部，运行时通过 `process.resourcesPath` 访问。

### 8.4 进程管理模块（**MVP 首要实现任务，当前未实现**）

在 SightFlow main process 中新增模块：
- `src/main/process-manager.ts`：负责启动、监控、重启子进程
- `src/main/wechat-installer.ts`：负责微信版本检测和安装

**process-manager.ts 需要实现的核心功能**（仅管理 glue-layer，wx-cli 自动启动无需管理）：
1. **启动 glue-layer**：`spawn(resources/glue-layer/glue-layer.exe, ['--wx-cli-path=...', '--sightflow-port=...'])`
2. **健康检查**：定期检测 glue-layer 进程存活，崩溃后指数退避重启
3. **级联退出**：使用 Windows Job Object 确保 SightFlow 退出时 glue-layer 同步终止
4. **状态同步**：将 glue-layer 状态通过 IPC 传递给 UI 显示

**注意**：wx-cli daemon 通过 `ensure_daemon()` 自动启动（§3.1），process-manager.ts **不需要**管理 wx-cli。

**Autopilot 状态持久化**（当前代码缺陷，需修复）：
- `POST /skill/autopilot` 端点**已实现**（`skill-server.ts:461-463`）
- UI toggle **已实现**（`App.tsx:176-315` ControlPanel）
- **唯一缺陷**：`autopilotEnabled` 是内存变量（`skill-server.ts:118`），重启后丢失
- 需要改为：
  - 持久化到 electron-store（`settingsStore.set('autopilot.enabled', ...)`）
  - skill-server 启动时从 store 读取初始值
  - 首次运行默认值改为 `false`（当前代码默认 `true`）

**发送消息互斥锁**（当前代码缺陷，需修复）：
- SightFlow 有两套发送路径：
  - 路径 A（glue-layer）：`POST /skill/send-message` → `sendMessageToContact()`
  - 路径 B（主 RPA）：`RuntimeHost` → `device.sendMessage()`
- 两条路径同时操作 WeChat 窗口会导致冲突
- 需要互斥锁：当主 RPA 运行时，`sendMessage` 应等待或拒绝

### 8.5 UI 扩展

- 新增"微信自动回复"tab 或侧边栏
- 实现状态面板、配置界面、首次启动引导

---

## 9. 实施阶段

### 阶段 1：基础打包（MVP）

- 实现 wx-cli 和 glue-layer 的打包脚本
- 修改 SightFlow 以嵌入这些组件
- 实现基本的进程管理（启动、监控）
- 实现微信版本检测和安装
- 生成 NSIS 安装包

### 阶段 2：UI 完善

- 实现状态面板
- 实现配置界面
- 实现首次启动引导
- 实现日志查看和诊断包导出

### 阶段 3：高级功能（可选）

- 自动更新功能
- 消息过滤规则
- 自定义回复模板
- 多账号支持
- 消息统计报表

---

## 10. 待确认问题

- [x] **wx-cli daemon 模式是否已实现？**
  - ✅ 是。使用 Named Pipe（Windows）/ UDS（Linux）协议，完整实现 sessions/history/new-messages/search/stats 等命令
  - daemon 通过 `ensure_daemon()` 自动启动（`transport.rs:38-44`），无需外部管理

- [x] **glue-layer 是否需要修改以支持 PyInstaller 打包？**
  - ✅ 需要修改（已在 §8.2 列出）：
    - PID/LOCK 文件路径改为 `%APPDATA%\WeChatAgent\`
    - 添加 CLI 参数解析（`--wx-cli-path`、`--sightflow-port`）
    - 添加 `logging.FileHandler` 输出到 `%APPDATA%\WeChatAgent\logs\`
    - identity.names 空数组行为改为不回复
    - autopilot 检查失败改为默认关闭
    - wxid_mapper 进程名精确匹配 `WeChat.exe`

- [x] **SightFlow 是否需要新增 UI 组件，还是复用现有界面？**
  - ✅ 大部分已实现：设置 UI（`WechatAgentSettings.tsx`）、autopilot toggle（`App.tsx` ControlPanel）、日志流（`/skill/log`）、alert 系统（`/skill/alert`）
  - 缺少的部分：状态面板（wx-cli/glue-layer 进程状态）、进程管理 UI、首次启动引导 wizard

- [x] **微信版本检测的具体实现方式？**
  - ✅ 待实现时确认。建议：注册表 `HKCU\Software\Tencent\WeChat\InstallPath` + 文件版本

- [x] **是否需要支持 macOS/Linux？**
  - ✅ 短期只支持 Windows（wx-cli 依赖 Windows WeChat 本地 DB + named pipe）

- [x] **identity.wxid 如何获取？**
  - ❌ `wx whoami` 命令**未实现**（需新增，MVP 前置依赖）
  - UI 的"自动检测 wxid"按钮已接线，但命令不存在会导致失败
  - 当前用户需手动填写 wxid

- [x] **groups.monitor 存储格式？**
  - ✅ `Array<{ room_id: string; name: string }>`（非字符串数组）
  - `config_model.py:20-26` 和 `wechat-agent-config.ts:23` 都定义为此结构
  - UI 让用户选群名，运行时通过 `WxidMapper.get_room_id_by_name()` 反查 room_id

- [x] **process-manager.ts 是否已实现？**
  - ❌ 未实现。MVP 首要实现任务，需从零开发
  - **仅需管理 glue-layer**（wx-cli daemon 通过 `ensure_daemon()` 自动启动，无需外部管理）
  - 职责：启动/监控/重启 glue-layer、级联退出（Job Object）、状态同步

- [x] **wx-cli 是否使用 TCP 端口？**
  - ❌ 不使用。wx-cli 使用 Named Pipe（Windows）/ UDS（Linux），没有端口概念
  - glue-layer 通过 `subprocess.run(['wx', ...])` 调用，wx 客户端自动连接 pipe/socket

- [x] **Autopilot 状态是否持久化？**
  - `POST /skill/autopilot` 端点 ✅ 已实现；UI toggle ✅ 已实现
  - ❌ 未持久化（内存变量 `autopilotEnabled = true`）。需改为 electron-store
  - 当前默认 `true`，产品化后首次运行应默认 `false`

- [x] **发送消息是否有送达确认？**
  - ❌ 没有。`/skill/send-message` 返回 `ok` 只代表 RPA 动作完成，不代表消息送达
  - 统计面板应标注为"已尝试发送"而非"已送达"

- [x] **wx-cli 首次初始化流程？**
  - wx-cli 有自己的配置文件 `~/.wx-cli/config.json`（包含 db_path、key）
  - `ensure_daemon()` 自动启动 daemon 时，自动探测 WeChat DB 路径和解密密钥
  - 如果自动探测失败，需要引导用户手动运行 `wx init --wx-dir <path>` 指定 WeChat 数据目录
  - **SightFlow 不需要显式管理 wx-cli daemon 生命周期**

- [x] **wx-cli daemon 是否需要 SightFlow 显式启动？**
  - ❌ 不需要。`transport.rs:38-44` 的 `ensure_daemon()` 在每次 `wx` 命令时自动检测并启动
  - 使用 `DETACHED_PROCESS`（Windows）/ `setsid()`（Unix）脱离控制台
  - SightFlow 只需确保 resources 中包含 `wx.exe`，无需进程管理

- [x] **`generateReply` 是否使用了正确的 API Key？**
  - ❌ **已知 bug**。`index.ts:956` 使用 `settings.vision?.apiKey`（VLM Key），但 model/baseURL 来自 `settings.chatProvider.config`
  - 如果 vision 和 chatProvider 使用不同的 API Key → 生成回复失败
  - **需修复**：改用 `settings.chatProvider.config.apiKey`

- [x] **glue-layer 是否可以合入 SightFlow？**
  - 🤔 **架构讨论**：glue-layer 的核心逻辑（poll + filter + rate limit + @detect + AI call + send）约 500 行 Python
  - 数据从 glue-layer 到 SightFlow 再回来两次，glue-layer 本质是 SightFlow 的"中间层"
  - **保留理由**：模块边界清晰、独立测试/部署、崩溃隔离
  - **合入理由**：少一个进程/打包组件/PyInstaller 麻烦、跨进程协调消失、安装包更小
  - **MVP 建议**：评估将 glue-layer 核心逻辑做成 SightFlow 的 `WechatAgentPoller` 模块的可行性
  - glue-layer 作为"高级/开发者模式"保留给想自部署的用户

- [x] **Skill Server CORS 是否安全？**
  - ❌ **CRITICAL 安全漏洞**。`skill-server.ts:130, 442` 设置 `Access-Control-Allow-Origin: *`
  - 任意恶意网站可通过浏览器跨域调用 Skill Server API（发送消息、控制引擎、消耗 API 额度）
  - **P0 阻塞产品发布**。修复方案：验证 Origin header / secret token / 改用 named pipe

- [x] **glue-layer 测试覆盖情况？**
  - ❌ 核心逻辑（main.py、ai_client.py、message_queue.py、wxid_mapper.py）**无测试**
  - 仅 config_model.py 和 config_watcher.py 有测试
  - MVP 发布前需补充单元测试（消息处理、限速、去重）和集成测试

---

## 11. 已确认的设计决策

1. **启动参数方式**：采用 CLI 参数（`--wx-cli-path`、`--sightflow-port`）覆盖配置文件
2. **安装包体积**：接受 ~400MB，保持"开箱即用"体验
3. **微信版本策略**：检测到非 4.1.9 时询问是否覆盖安装，用户接受法律风险
4. **进程重启策略**：指数退避（2s → 5s → 10s）+ 游标重置 + Windows Job Object 级联退出
5. **卸载数据保留**：保留 wx-cli 游标，避免重装后重复处理历史消息
6. **安装路径**：`%LOCALAPPDATA%\Programs\WeChatAgent\`（无需管理员权限）
7. **限速策略**：per-contact（每个联系人/群独立 3条/分钟）
8. **identity.wxid 获取**：`wx whoami` 命令**需新增实现**（当前未实现），UI 自动检测按钮已接线等待命令就绪
9. **groups.monitor schema**：存储群名，运行时反查 room_id
10. **identity.names 空数组行为**：不回复（而非全量回复）
11. **autopilot 检查失败**：默认关闭（而非启用）
12. **消息持久化**：MVP 不持久化，显式标注"崩溃时丢失排队消息"
13. **法务标注**：产品定位为"仅供学习/个人使用"
14. **wx-cli 通信协议**：Named Pipe（Windows）/ UDS（Linux），**不使用 TCP 端口**
15. **process-manager.ts**：MVP 首要实现任务，当前未实现，需从零开发
16. **autopilot 状态持久化**：`POST /skill/autopilot` + UI toggle 已实现，仅需改为 electron-store 持久化
17. **发送消息送达确认**：无送达确认，统计面板标注为"已尝试发送"
18. **VLM Key 依赖**：首次引导需提示用户配置 VLM Key（用于 RPA 定位联系人）
19. **发送路径互斥**：需要互斥锁防止两套发送路径并发冲突
20. **wx-cli 首次初始化**：自动探测 WeChat DB，失败时引导用户手动指定目录
21. **群聊数量限制**：允许 0 个群（首次使用时可跳过，稍后配置）
22. **wx-cli daemon 生命周期**：`ensure_daemon()` 自动管理，SightFlow 不需要显式启动/监控
23. **wx whoami 命令**：当前未实现，MVP 前置依赖，UI 按钮已接线等待命令就绪
24. **generateReply API Key**：已知 bug（用了 vision key），需改用 chatProvider key
25. **glue-layer 架构讨论**：MVP 阶段评估合入 SightFlow 的可行性，减少打包复杂度
26. **process-manager.ts 范围缩小**：仅需管理 glue-layer（wx-cli 自动启动）
27. **Skill Server CORS**：`Access-Control-Allow-Origin: *` 是 CRITICAL 安全漏洞，P0 阻塞发布
28. **测试覆盖**：glue-layer 核心逻辑无测试，MVP 前需补充单元测试和集成测试

---

## 12. 附录

### 12.1 参考文档

- [wx-cli CLAUDE.md](../../wx-cli/CLAUDE.md)
- [SightFlow CLAUDE.md](../../sightflow-desktop-agent/CLAUDE.md)
- [glue-layer README](../../glue-layer/README.md)

### 12.2 相关文档

- [微信自动回复设置 UI 设计](./2026-06-23-wechat-agent-settings-ui-design.md)
- [微信自动回复设置 UI 实施计划](../plans/2026-06-23-wechat-agent-settings-ui.md)

### 12.3 SightFlow Skill Server 完整端点

| 端点 | 方法 | 说明 | 状态 |
|---|---|---|---|
| `/skill/status` | GET | 查询运行状态 | ✅ 已实现 |
| `/skill/start` | POST | 启动引擎 | ✅ 已实现 |
| `/skill/pause` | POST | 暂停引擎 | ✅ 已实现 |
| `/skill/autopilot` | GET | 查询自动驾驶状态 | ✅ 已实现 |
| `/skill/autopilot` | POST | 设置自动驾驶状态 | ✅ 已实现（**需持久化**） |
| `/skill/send-message` | POST | 发送消息（VLM 搜索 + 仿人输入 + 9 阶段 RPA） | ✅ 已实现 |
| `/skill/generate-reply` | POST | 生成 AI 回复（**已知 bug：用了错误的 API Key**） | ✅ 已实现 |
| `/skill/log` | POST | 实时日志推送 | ✅ 已实现 |
| `/skill/alert` | POST | 告警系统（banner + 通知 + 5 分钟去重） | ✅ 已实现 |

### 12.4 wx-cli 完整命令参考

| 命令 | 用途 | MVP 是否使用 |
|---|---|---|
| `ping` | 健康检查 | 推荐（检测 daemon 状态） |
| `sessions` | 获取会话列表（getGroups 用） | ✅ 核心 |
| `history` | 查询历史消息 | ✗ |
| `search` | 搜索消息 | ✗ |
| `contacts` | 联系人列表 | ✗ |
| `unread` | 未读消息 | ✗ |
| `members` | 群成员 | ✗ |
| `new-messages` | 增量新消息（核心轮询命令） | ✅ 核心 |
| `favorites` | 收藏 | ✗ |
| `stats` | 统计 | ✗ |
| `sns-notifications` | 朋友圈通知 | ✗ |
| `sns-feed` | 朋友圈 feed | ✗ |
| `sns-search` | 朋友圈搜索 | ✗ |
| `biz-articles` | 公众号文章 | ✗ |
| `attachments` | 附件 | ✗ |
| `extract` | 提取附件 | ✗ |
| `whoami` | 当前用户 wxid（**需新增实现**） | ✅ 核心（前置依赖） |
| `daemon status/stop/logs` | daemon 管理 | ✗（auto-start 不需要） |
| `init` | 初始化 wx-cli config | ✅ 首次初始化 |

### 12.5 已实现功能清单（spec 列为"规划"但实际可用）

| 模块 | 文件位置 | 说明 |
|---|---|---|
| 设置 UI | `WechatAgentSettings.tsx` | 身份 / 群组 / AI 跳转 / 高级 4 个卡片 |
| 设置持久化 | `wechat-agent-config.ts` | 原子写入 + 3 次 retry + 指数退避 |
| 设置热重载 | `glue-layer/config_watcher.py` | mtime 检查 + 配置快照 deepcopy |
| 配置校验器 | `glue-layer/config_model.py` | version / identity / groups 校验完整 |
| 群列表加载 | `index.ts` `wechat-agent:getGroups` | `wx sessions -n 1000 --json` 过滤 group |
| 告警系统 | `skill-server.ts:215-253` `/skill/alert` | banner + 系统通知 + 5 分钟 code 去重 |
| 日志流 | `skill-server.ts:283-301` `/skill/log` | 实时推送到 ControlPanel |
| 自动驾驶 toggle | `App.tsx:176-315` + `POST /skill/autopilot` | UI 完整：toggle、IPC 监听、port fallback |
| AI 回复生成 | `skill-server.ts:386-434` `/skill/generate-reply` | 走 chatProvider 配置 |
| 消息发送 | `skill-server.ts:338-367` `/skill/send-message` | VLM 搜索 + 仿人输入 + 9 阶段 RPA |
| wx-cli daemon 自动启动 | `wx-cli/src/cli/transport.rs:424` | 任何 `wx` 命令必调 `ensure_daemon()` |
| 进程互斥（glue-layer） | `glue-layer/main.py:29-75` | 文件锁 + PID 文件，跨平台抽象 |
| Alert IPC 推送 | `index.ts` + `App.tsx:200-206` | `wechat-agent:alert-pushed` → banner + Notification |
| 配置文件夹打开 | `index.ts:664-668` `wechat-agent:openConfigDir` | `shell.openPath()` |

### 12.6 已知代码缺陷（需在 MVP 前修复）

| 问题 | 位置 | 影响 | 修复方案 |
|---|---|---|---|
| `generateReply` 用了错误的 API Key | `index.ts:956` | vision key ≠ chatProvider key 时回复失败 | 改用 `settings.chatProvider.config.apiKey` |
| autopilot 不持久化 | `skill-server.ts:118` | 重启后用户设置丢失 | 读写 electron-store |
| autopilot 默认 true | `skill-server.ts:118` | 首次运行就启用自动回复 | 改为从 store 读取，首次默认 false |
| `wx whoami` 未实现 | wx-cli 源码 | UI 自动检测按钮永久失败 | 新增子命令 |
| 空 `identity.names` 全量回复 | `main.py:468-477` | 首次使用刷屏 | 改为 return False |
| `check_autopilot_enabled` fail-open | `main.py:145-158` | SightFlow 崩溃后继续回复 | 改为 return False |
| PID/LOCK 路径 PyInstaller 失效 | `main.py:24-25` | 单实例锁失效 | 改为 `%APPDATA%\WeChatAgent\` |
| 日志路径 PyInstaller 失效 | `wxid_mapper.py:32` | 日志写到临时目录 | 统一到 `%APPDATA%\WeChatAgent\logs\` |
| glue-layer 不维护 new-messages 游标 | `main.py:308-332` | 每次拉 24h 全量消息 | 保存 `new_state` 下次传入 |

### 12.7 🔴 CRITICAL：Skill Server CORS 安全漏洞

**问题**：`skill-server.ts:130, 442` 设置 `Access-Control-Allow-Origin: *`，允许任意网站跨域调用 Skill Server API。

**攻击场景**：
1. 用户运行 SightFlow（autopilot 启用）
2. 用户在浏览器打开恶意网站
3. 恶意网站执行 `fetch('http://127.0.0.1:12680/skill/send-message', { method: 'POST', body: ... })`
4. CORS `*` 允许此跨域请求
5. SightFlow 通过 WeChat RPA 发送恶意消息

**影响范围**：
- `/skill/send-message` — 任意网站可发送任意 WeChat 消息
- `/skill/autopilot` POST — 可启用/禁用 autopilot
- `/skill/start` / `/skill/pause` — 可控制引擎状态
- `/skill/generate-reply` — 可消耗用户的 API Key 额度

**修复方案**（任选其一）：
1. **验证 Origin header**：只允许 SightFlow 自身的 origin（`file://*` / `app://*`）
2. **使用 secret token**：请求 header 加 `X-SightFlow-Token`，服务端验证
3. **改用 Unix socket / named pipe**：完全不暴露 HTTP 端口（需改 glue-layer 调用方式）

**优先级**：**P0（阻塞产品发布）**

### 12.8 测试覆盖要求

glue-layer 测试现状：
- ✅ `config_model.py` — 有 `test_config_model.py`
- ✅ `config_watcher.py` — 有 `test_config_watcher.py`
- ❌ `main.py`（核心逻辑）— **无测试**
- ❌ `ai_client.py`、`message_queue.py`、`wxid_mapper.py` — 无测试

**未测试的关键路径**：
- 消息去重（`_make_msg_key`）
- 限速逻辑（per-contact 3 条/分钟）
- Echo 静默期（15 秒）
- @mention 检测
- 消息队列串行化
- 配置快照 deepcopy

**MVP 发布前需补充的测试**：
- **单元测试**：main.py 核心逻辑（消息处理、限速、去重、@检测）
- **集成测试**：glue-layer ↔ wx-cli 交互（mock wx-cli daemon）
- **端到端测试**：完整消息接收 → AI 生成 → 发送流程（可选，后续迭代）

### 12.9 性能特征

glue-layer 性能参数：
- 轮询间隔：10 秒（`WX_POLL_INTERVAL`，可配置）
- AI 调用超时：30 秒
- 发送超时：30 秒
- 消息队列：串行处理，1 秒间隔

**实际吞吐量**：~3-6 条消息/分钟（受 per-contact 3 条/分钟限速约束）

**延迟**：新消息检测延迟最多 10 秒

对于微信自动回复场景，性能可接受，不是瓶颈。
