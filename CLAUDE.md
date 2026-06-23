# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

This is a coordination repo holding three sub-projects. Two of them (`wx-cli/`, `sightflow-desktop-agent/`) have their **own git histories** and are listed in the root `.gitignore` — do not try to `git add` their contents at the root level.

```
wx-cli/                      Rust CLI — decrypts local WeChat DB, exposes wx subcommands
sightflow-desktop-agent/     Electron app — VLM-driven RPA, HTTP API on :12680
glue-layer/                  Python glue — polls wx-cli, calls AI, sends via SightFlow
WeChatWin_4.1.9.exe          Windows WeChat installer (tracked in root, ~235MB)
docs/                        Superseded / top-level docs
```

### Data flow (the big picture)

```
wx-cli (daemon)  ──stdout JSON──▶  glue-layer  ──HTTP POST──▶  SightFlow (:12680)
                                        │                              │
                                   AI reply API                  RPA types into
                                      ▲                          WeChat window
                                      │                              │
                                   LLM provider                    WeChat UI
```

1. `wx new-messages --json` yields incremental private/group messages since last check.
2. `glue-layer/main.py` filters echo (self-sends, quiet window), rate-limits (3/min), detects `@` in groups, calls the configured LLM for a reply, then enqueues it.
3. `MessageQueue` dispatches serially to `POST http://127.0.0.1:12680/skill/send-message`, which SightFlow executes via keyboard/mouse RPA against the WeChat window.
4. SightFlow also exposes `/skill/autopilot` (enable/disable), `/skill/log` (UI log stream), `/skill/start`, `/skill/pause`, `/skill/status`.

## Per-Subproject Commands

### `wx-cli/` (Rust)

```bash
cargo build --release              # build wx binary → target/release/wx
cargo check                        # MUST pass before commit (per wx-cli/CLAUDE.md)
cargo check --target x86_64-pc-windows-gnu   # when touching #[cfg] / Cargo.toml deps
cargo update --workspace           # required after any version bump in Cargo.toml
```

Remote is `origin` (HTTPS: `https://github.com/fhta0/wx-cli`); push is mandatory after each commit (per wx-cli/CLAUDE.md). Cross-platform checklist (libc calls, cfg blocks, Cargo section ordering, Windows named-pipe imports) is in wx-cli/CLAUDE.md — read it before any IPC/platform change.

### `sightflow-desktop-agent/` (TypeScript / Electron)

```bash
npm install                        # also runs patch-package + electron-builder install-app-deps
npm run dev                        # HMR dev (via scripts/dev-launch.mjs)
npm run build                      # typecheck + electron-vite build
npm run build:win / build:mac / build:linux
npm run typecheck                  # both node and web
npm run lint                       # eslint --cache
npm run format                     # prettier --write
```

Three Electron processes: main (`src/main/index.ts`), preload (`src/preload/`), renderer (`src/renderer/src/`). Built by `electron-vite` into `out/main/`, `out/preload/`, `out/renderer/`. Full architecture notes (Device / GenericChannelSession / RuntimeHost / Provider plugin system / Box-Select wizard / IPC channel naming) are in `sightflow-desktop-agent/CLAUDE.md` — read it before touching `src/core/` or IPC.

### `glue-layer/` (Python)

```bash
cd glue-layer
pip install -r requirements.txt
cp .env.example .env               # then fill in AI_API_KEY, SELF_WXID, etc.
python main.py                     # starts polling loop
```

Config is in JSON file (`%APPDATA%\WeChatAgent\config.json`), managed via SightFlow UI. Legacy env vars (`.env`) are deprecated. Key settings: `identity.wxid` (self ID), `identity.names` (nicknames for @-mention detection), `groups.monitor` (monitored chat rooms), `advanced.wx_cli_path`. Uses Windows file locks (`msvcrt.locking`) for single-instance; the lock path is hard-coded to Windows semantics — porting to Linux/macOS needs a different guard.

## Conventions Worth Knowing

- **wx-cli output is AI-shaped.** `history`/`search`/`sessions`/`new-messages`/`stats`/`attachments` return `{..., meta}` wrappers by default — `meta.status` (`ok` / `possibly_stale` / `windowed`) and `meta.unknown_shards` tell you whether results can be trusted. `--with-meta` adds per-shard cache detail; `--debug-source` adds shard paths.
- **chat_type values:** `private` / `group` / `official_account` / `folded`. `official_account` includes 公众号/订阅号/服务号 + system notices (`mphelper`, `qqsafe`). `folded` is the 订阅号折叠 / 折叠群聊 aggregation entry.
- **Stable identity triplet** in group outputs: `sender_username` (wxid), `sender_contact_display` (备注 > 昵称 > wxid), `sender_group_nickname` (群名片). Absent when wxid can't be resolved — do not synthesize empty values.
- **Echo prevention in glue-layer:** after sending, the contact is muted for `ECHO_QUIET_SECONDS=15` because WeChat echoes our own sends back as if they were incoming. `SELF_WXID` and `sender == '192'` are the self-send filters.
- **Image attachments** live as `.dat` files under `xwechat_files/<wxid>/msg/attach/`. Three decoder tiers: legacy XOR, V1 fixed-AES (`cfcd208495d565ef`), V2 AES+XOR (platform-derived key). V2 key extraction is unsupported on Linux.
- **SNS (朋友圈)** commands (`sns-notifications` / `sns-feed` / `sns-search`) only cover posts the local WeChat has already fetched — not the full timeline.
- **Biz (公众号文章)** live in separate `biz_message_*.db` shards, queried via `wx biz-articles`, not `history`/`search`.
- **SightFlow skill server** binds `127.0.0.1:12680` (falls back to `12681`). Defined in `src/main/skill-server.ts`. Glue-layer depends on this being up.
- **Settings persistence** in SightFlow uses `electron-store`; legacy shapes are migrated by `normalizeSettings()`. Provider Hub URL is `https://sightflow.dev/provider-hub.json` (override via `SIGHTFLOW_PROVIDER_HUB_URL`).
- **WeChatWin_4.1.9.exe** is a tracked binary at the repo root — treat it as opaque payload, don't try to diff or grep it.

## Reading Order

For questions about… → read:
- WeChat DB decryption / CLI commands / daemon cache: `wx-cli/README.md`, then `wx-cli/CLAUDE.md`
- RPA / VLM / provider plugin / IPC: `sightflow-desktop-agent/CLAUDE.md`, then `sightflow-desktop-agent/README.md`
- Auto-reply loop / rate limit / echo handling / config knobs: `glue-layer/README.md` + `glue-layer/main.py`
- Provider integration contract: `sightflow-desktop-agent/docs/provider.md`
