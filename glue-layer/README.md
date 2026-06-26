# 微信自动回复粘合层 (glue-layer)

连接 wx-cli (消息监控) 和 SightFlow (消息发送) 的 Python 粘合层。

## 依赖

- Python 3.8+
- wx-cli 已安装并初始化
- SightFlow 已启动并监听 127.0.0.1:12680

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的 API Key 和微信 ID
```

## 运行

```bash
python main.py
```

## 打包 (PyInstaller)

```bash
python -m PyInstaller --onefile --name glue-layer --distpath . main.py
```

## 流程

```
wx-cli (wx new-messages)
    │
    ▼
glue-layer 轮询 (每 10 秒)
    │
    ├── 过滤自发消息 (sender == wxid 或 sender == '192')
    ├── 跳过系统消息
    ├── 防 echo 回环 (发送后 15s 静默期)
    ├── 检查 autopilot 状态 (GET /skill/autopilot，默认启用)
    ├── 群聊 @提及检测 (使用 identity.names)
    ├── 限速 (3 条/分钟)
    │
    ▼
POST /skill/generate-reply → AI 生成回复
    │
    ├── 有回复 → POST /skill/send-message → SightFlow RPA 发送
    ── 无回复/跳过 → 记录 skip 日志
```

1. 每 10 秒调用 `wx new-messages --json` 获取增量消息
2. 过滤自发消息、系统消息、静默期消息
3. wxid → 备注名/群名翻译（WxidMapper）
4. 检查自动驾驶状态（默认启用）
5. 群聊检测 @提及，私聊直接回复
6. 限速检查（3 条/分钟）
7. 调用 SightFlow `/skill/generate-reply` API 生成回复（AI 配置在 SightFlow 统一管理）
8. 通过 SightFlow `/skill/send-message` API 发送消息（MessageQueue 串行化）

## 配置

配置通过 SightFlow UI 管理，存储在 `%APPDATA%\WeChatAgent\config.json`：

- `identity.wxid`: 当前用户微信 ID（用于过滤自发消息）
- `identity.names`: 昵称列表（用于群聊 @提及检测）
- `groups.monitor`: 监控的群聊列表
- `advanced.wx_cli_path`: wx-cli 可执行文件路径（CLI 参数 `--wx-cli-path` 优先于此配置）

## wx_cli_path 一致性

`WeChatAgent.__init__()` 接收 `wx_cli_path_override` 参数（来自 ProcessManager 的 `--wx-cli-path` CLI 参数），存储在 `self._wx_cli_path`。`_get_new_messages()` 使用此实例变量而非配置文件中的路径，确保始终使用正确的 wx-cli 路径。

## 日志发送

`send_log_to_sightflow()` 通过 `POST /skill/log` 发送结构化日志到 SightFlow UI：

- 成功：skill-server 缓冲并广播到所有窗口
- **失败**：输出到 stdout（`[WeChatAgent][log:<type>] <content>`），由 ProcessManager 通过 IPC 转发到 UI，**日志不会丢失**

日志类型：`receive` / `reply` / `skip` / `error` / `info` / `monitor` / `heartbeat`

## 关键常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `WX_POLL_INTERVAL` | 10s | 轮询间隔 |
| `MAX_MESSAGES_PER_MINUTE` | 3 | 每分钟最多回复数 |
| `ECHO_QUIET_SECONDS` | 15 | 发送后静默期（防 echo 回环） |
| `MAPPER_REFRESH_INTERVAL` | 60 次轮询 (~5 分钟) | 联系人映射表刷新间隔 |
| `HEARTBEAT_INTERVAL` | 12 次轮询 (~60 秒) | 心跳日志间隔 |
