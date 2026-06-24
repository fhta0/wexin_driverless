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

- 复制 SightFlow 到 `Program Files`
- 创建桌面快捷方式
- 创建开始菜单快捷方式

---

## 3. 运行时架构

### 3.1 启动流程

用户点击桌面快捷方式后：

```
用户双击 SightFlow 快捷方式
    ↓
SightFlow 主进程启动 (Electron main)
    ↓
启动 wx-cli daemon
    │  └─ spawn: resources/wx-cli/wx.exe daemon
    │  └─ 等待 stdout 就绪信号（READY port=12682）
    ↓
启动 glue-layer
    │  └─ spawn: resources/glue-layer/glue-layer.exe
    │  └─ CLI 参数：--wx-cli-path=xxx --sightflow-port=12680
    ↓
glue-layer 连接 wx-cli daemon (IPC)
    ↓
SightFlow UI 显示"微信自动回复"模式
    ├─ 状态面板：显示各组件运行状态
    ├─ 开关：启动/停止自动回复
    └─ 配置：监控群聊、AI 设置等
```

**CLI 参数设计**（glue-layer 需新增）：
- `--wx-cli-path <path>`: wx-cli 可执行文件路径（覆盖 config.json 中的 advanced.wx_cli_path）
- `--sightflow-port <port>`: SightFlow API 端口（覆盖默认值 12680）
- `--config <path>`: 配置文件路径（可选，默认 %APPDATA%\WeChatAgent\config.json）

### 3.2 进程管理

- SightFlow 主进程负责启动和监控 wx-cli、glue-layer 子进程
- **指数退避重启策略**：崩溃后等待 2s → 5s → 10s → 停止自动重启（最多 3 次）
- **重启时重置游标**：wx-cli daemon 重启时重置 new-messages 游标，避免重启后重复处理消息
- **Windows Job Object**：SightFlow 创建 Job Object 将所有子进程加入，确保 SightFlow 退出时子进程级联退出（防止孤儿进程）
- 进程状态通过 IPC 传递给 UI 显示

### 3.3 组件间通信

```
wx-cli daemon ──stdout JSON──▶ glue-layer ──HTTP POST──▶ SightFlow (:12680)
                                      │                          │
                                 AI reply API              RPA 发送消息
                                    ▲                          │
                                    │                          ▼
                               LLM Provider              WeChat 窗口
```

- **wx-cli → glue-layer**: stdout JSON（新消息通知）
- **glue-layer → SightFlow**: HTTP API（发送消息、查询状态）
- **SightFlow → wx-cli**: HTTP API（可选，用于直接查询）

### 3.4 端口分配

- SightFlow 默认监听 `12680`，如果被占用 → 自动尝试 `12681`
- wx-cli daemon 默认端口 `12682`，冲突时自动递增
- **端口传递方式**：SightFlow 启动时通过 CLI 参数 `--sightflow-port` 将实际端口传递给 glue-layer，避免修改用户可编辑的配置文件

---

## 4. 用户界面设计

### 4.1 SightFlow UI 扩展

在现有 SightFlow 应用中增加"微信自动回复"模式：

#### 4.1.1 状态面板（新增 tab 或侧边栏）

- wx-cli 状态：运行中 / 已停止 / 错误
- glue-layer 状态：运行中 / 已停止 / 错误
- 微信连接状态：已连接 / 未连接
- 自动回复开关
- 统计信息：已处理消息数、已回复消息数

#### 4.1.2 配置界面（扩展现有设置页）

- 监控的群聊列表（从群列表勾选）
- AI 模型选择（已有，复用）
- 回复策略：延迟、频率限制等
- 高级选项：打开配置文件、导出诊断包

### 4.2 首次启动引导

**设计原则**：引导 wizard 允许跳过，不阻塞用户使用。

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
    ├─ 步骤 2：登录微信（如未连接，提供"打开微信"按钮）
    └─ 步骤 3：选择要监控的群聊（微信连接后才可用）
              ← 可跳过，稍后在主界面配置
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
    "rate_limit": 3,
    "echo_quiet_seconds": 15
  }
}
```

**字段说明**：
- `identity.wxid`: 用户自己的微信 ID，用于过滤自己发送的消息
- `identity.names`: 用户昵称列表，用于检测群聊中 @自己的情况
- `groups.monitor`: 需要监控的群聊名称列表
- `advanced.wx_cli_path`: wx-cli 可执行文件路径（留空则使用默认路径，CLI 参数 `--wx-cli-path` 优先）
- `advanced.rate_limit`: 每个群聊每分钟最大回复数
- `advanced.echo_quiet_seconds`: 发送消息后的静默期（防止处理自己发送的消息）

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

### 8.2 glue-layer 打包

```bash
cd glue-layer
pip install pyinstaller cryptography
pyinstaller --onefile \
  --hidden-import=cryptography \
  --hidden-import=cffi \
  --hidden-import=_cffi_backend \
  --add-data "config_model.py;." \
  main.py --name glue-layer
# 产出: dist/glue-layer.exe
```

**注意事项**：
- `--hidden-import` 确保 cryptography 的 native 扩展被打包
- 如果使用 DPAPI 加密，需要包含 `config_model.py` 和相关依赖

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

### 8.4 进程管理模块

在 SightFlow main process 中新增模块：
- `src/main/process-manager.ts`：负责启动、监控、重启子进程
- `src/main/wechat-installer.ts`：负责微信版本检测和安装

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
  - ✅ 是。CLAUDE.md 数据流图明确写 `wx-cli (daemon)`，命令是 `wx daemon`
  - `wx new-messages` 依赖 daemon 维护的游标

- [x] **glue-layer 是否需要修改以支持 PyInstaller 打包？**
  - 需要修改：添加 CLI 参数解析（`--wx-cli-path`、`--sightflow-port`）
  - 需要修改：添加 `logging.FileHandler` 输出到 `%APPDATA%\WeChatAgent\logs\`
  - 需要修改：确保 `config_model.py` 的 DPAPI 加密在 PyInstaller 下正常工作

- [x] **SightFlow 是否需要新增 UI 组件，还是复用现有界面？**
  - 部分复用。设置 UI（`WechatAgentSettings.tsx`）已完成，包含 identity/groups/AI/advanced 几个 section
  - 缺少的部分：状态面板、首次启动引导 wizard、实时日志流

- [ ] **微信版本检测的具体实现方式（注册表、文件版本、还是 API）？**
  - 待实现时确认。建议优先尝试：
    1. 注册表 `HKCU\Software\Tencent\WeChat` 下的 `InstallPath`
    2. 读取安装目录下的 `WeChatWin.exe` 文件版本

- [x] **是否需要支持 macOS/Linux？**
  - 短期只支持 Windows。wx-cli 依赖 Windows WeChat DB 文件路径 + msvcrt 锁
  - 后续考虑跨平台需要：替换 msvcrt 文件锁为跨平台方案、适配 macOS WeChat 路径

---

## 11. 已确认的设计决策

1. **启动参数方式**：采用 CLI 参数（`--wx-cli-path`、`--sightflow-port`）覆盖配置文件
2. **安装包体积**：接受 ~400MB，保持"开箱即用"体验
3. **微信版本策略**：检测到非 4.1.9 时询问是否覆盖安装，用户接受法律风险
4. **进程重启策略**：指数退避（2s → 5s → 10s）+ 游标重置 + Windows Job Object 级联退出
5. **卸载数据保留**：保留 wx-cli 游标，避免重装后重复处理历史消息

---

## 11. 附录

### 11.1 参考文档

- [wx-cli CLAUDE.md](../../wx-cli/CLAUDE.md)
- [SightFlow CLAUDE.md](../../sightflow-desktop-agent/CLAUDE.md)
- [glue-layer README](../../glue-layer/README.md)

### 11.2 相关文档

- [微信自动回复设置 UI 设计](./2026-06-23-wechat-agent-settings-ui-design.md)
- [微信自动回复设置 UI 实施计划](../plans/2026-06-23-wechat-agent-settings-ui.md)
