# 微信自动驾驶 (WeChat Autopilot)

一个**免驱动、无侵入**的微信 AI 自动驾驶系统：AI 像真人一样看消息、思考、打字、发送。全程不注入 DLL、不 hook 微信进程、不装任何驱动——只模拟人的操作。

## 它如何运转

```
wx-cli (daemon) ──增量消息──▶ glue-layer ──HTTP──▶ SightFlow 引擎 ──RPA──▶ 微信窗口
                                       │                                       │
                                 AI Provider 生成回复                    键盘鼠标真实操作
```

| 能力 | 说明 |
|------|------|
| **本地读取** | `wx-cli` 直接解密微信本地数据库，获取私聊/群聊/朋友圈消息，无需服务器 |
| **AI 回复** | 豆包 / 可插拔 Provider 生成自然回复，群聊仅在被 @ 时响应 |
| **拟人化输入** | 逐字打字（35–55ms/字）+ 贝塞尔鼠标轨迹 + Box-Muller 正态延迟，对抗风控 |
| **防自循环** | 回声过滤 + 15 秒静默窗口，AI 不会跟自己对话 |
| **远程管控** | 本机 HTTP API（`:12680`），可被外部工具编排 |

## 仓库结构

这是一个协调仓库，包含三个子项目：

| 目录 | 技术栈 | 职责 |
|------|--------|------|
| [`wx-cli/`](./wx-cli/) | Rust | 解密微信本地库，提供 `wx` 命令行（消息查询、附件解码、daemon 缓存） |
| [`sightflow-desktop-agent/`](./sightflow-desktop-agent/) | Electron + TS | VLM 驱动的桌面 RPA，把 AI 回复用键盘鼠标"打"进微信窗口 |
| [`glue-layer/`](./glue-layer/) | Python | 粘合层：轮询 wx-cli、调用 AI、限流去重、经 SightFlow 发送 |

> ⚠️ `wx-cli/` 与 `sightflow-desktop-agent/` 各自有独立 git 历史（根 `.gitignore` 已排除），提交时请进入对应子目录操作。

## 快速开始

各子项目的详细步骤见其 README，但整体启动顺序是：

1. 构建 `wx-cli`（`cargo build --release`）
2. 配置 `glue-layer`（`%APPDATA%\WeChatAgent\config.json`，填 `identity.wxid`、AI Key 等）
3. 启动 SightFlow（`npm install && npm run dev`，或运行安装包）
4. 把微信主窗口置于前台（标题为"微信"），引擎启动后自动驾驶即生效

## 文档

- 项目约定与已知限制：[CLAUDE.md](./CLAUDE.md)
- 设计文档与实施计划：[docs/superpowers/](./docs/superpowers/)
  - 拟人化输入（防风控）设计与计划：[docs/superpowers/plans/2026-10-16-human-input-detection-evasion.md](./docs/superpowers/plans/2026-10-16-human-input-detection-evasion.md)

## 免责声明

本项目仅供技术研究和个人自动化场景使用。使用者需自行遵守微信及相关法律法规的服务条款，因使用本工具导致的封号、数据风险等后果由使用者自行承担。
