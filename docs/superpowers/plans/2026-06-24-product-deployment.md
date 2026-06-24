# 微信自动回复产品发布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the WeChat auto-reply tool (wx-cli + glue-layer + SightFlow) as a single NSIS installer that ordinary users can install and use.

**Architecture:** Three independent subsystems modified in parallel: (1) wx-cli gains a `whoami` command and is built to `wx.exe`, (2) glue-layer gets bug fixes + CLI args + tests, then PyInstaller-packaged to `glue-layer.exe`, (3) SightFlow gets bug fixes (CORS, generateReply key), a new process-manager to spawn glue-layer, a wechat-installer for version detection, and electron-builder config for bundling everything into an NSIS installer.

**Tech Stack:** Rust (wx-cli), Python/PyInstaller (glue-layer), TypeScript/Electron/electron-builder (SightFlow), NSIS (installer)

**Spec:** `docs/superpowers/specs/2026-06-24-product-deployment-design.md`

---

## File Structure

### wx-cli (Rust, independent git repo at `wx-cli/`)
- **Create:** `wx-cli/src/cli/whoami.rs` — CLI handler for `wx whoami`
- **Modify:** `wx-cli/src/cli/mod.rs` — register Whoami subcommand
- **Modify:** `wx-cli/src/ipc.rs` — add Whoami request variant
- **Modify:** `wx-cli/src/daemon/server.rs` — dispatch Whoami
- **Modify:** `wx-cli/src/daemon/query.rs` — implement `q_whoami()`

### SightFlow (TypeScript/Electron, independent git repo at `sightflow-desktop-agent/`)
- **Create:** `sightflow-desktop-agent/src/main/process-manager.ts` — spawn/monitor/restart glue-layer
- **Create:** `sightflow-desktop-agent/src/main/wechat-installer.ts` — detect/install WeChat
- **Modify:** `sightflow-desktop-agent/src/main/skill-server.ts` — CORS fix, autopilot persistence, generateReply key fix
- **Modify:** `sightflow-desktop-agent/src/main/index.ts` — IPC handlers for process status, start process-manager
- **Modify:** `sightflow-desktop-agent/electron-builder.yml` — extraResources config
- **Create:** `sightflow-desktop-agent/resources/README.md` — documents what binaries go here

### glue-layer (Python, at `glue-layer/`)
- **Modify:** `glue-layer/main.py` — CLI args (argparse), PID/LOCK path fix, logging setup, _is_mentioned fix, autopilot fail-closed
- **Modify:** `glue-layer/config.py` — support CLI args for SIGHTFLOW_API_URL
- **Create:** `glue-layer/test_main.py` — unit tests for _make_msg_key, _is_mentioned, rate limiting
- **Create:** `scripts/build-glue-layer.sh` — PyInstaller build script

---

## Task 1: CORS Security Fix (P0 Blocker)

**Files:**
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts:128-132, 440-447`

**Context:** `skill-server.ts` sets `Access-Control-Allow-Origin: *` on every response (line 130) and on OPTIONS preflight (line 442). This allows any malicious website to call the Skill Server API and send WeChat messages on behalf of the user. This is a P0 blocker for product launch.

**Fix approach:** Replace `*` with Origin validation. Only allow requests from `null` origin (file:// protocol in Electron) or `http://127.0.0.1:*` / `http://localhost:*` (dev mode). Reject all other origins.

- [ ] **Step 1: Add origin validation helper function**

In `sightflow-desktop-agent/src/main/skill-server.ts`, add a helper function before the `jsonResponse` function (around line 120):

```typescript
/** Allowed origins for CORS validation */
function isAllowedOrigin(origin: string | undefined): boolean {
  if (!origin) return true  // non-browser clients (curl, glue-layer) have no Origin
  // Allow localhost dev servers
  if (origin.startsWith('http://127.0.0.1:') || origin.startsWith('http://localhost:')) return true
  // Allow Electron file:// and app:// protocols
  if (origin.startsWith('file://') || origin.startsWith('app://')) return true
  return false
}
```

- [ ] **Step 2: Update jsonResponse to use origin validation**

Replace the `jsonResponse` function (line 123-133):

```typescript
function jsonResponse(
  res: http.ServerResponse,
  statusCode: number,
  body: Record<string, unknown>,
  requestOrigin?: string
): void {
  const origin = requestOrigin ?? ''
  const allowed = isAllowedOrigin(origin || undefined)
  res.writeHead(statusCode, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': allowed ? (origin || '*') : 'null'
  })
  res.end(JSON.stringify(body))
}
```

- [ ] **Step 3: Update OPTIONS preflight handler**

Replace the OPTIONS handler (line 440-448):

```typescript
  if (method === 'OPTIONS') {
    const origin = req.headers.origin
    const allowed = isAllowedOrigin(origin)
    res.writeHead(204, {
      'Access-Control-Allow-Origin': allowed ? (origin || '*') : 'null',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    })
    res.end()
    return
  }
```

- [ ] **Step 4: Pass origin to jsonResponse in all handlers**

Update `requestHandler` to extract origin once and pass to all `jsonResponse` calls. Add at the top of `requestHandler`:

```typescript
  const requestOrigin = req.headers.origin
```

Then update every `jsonResponse(res, ...)` call in the handler to pass `requestOrigin` as the 4th argument. For example:

```typescript
jsonResponse(res, 404, { ok: false, error: 'not_found' }, requestOrigin)
```

Apply this to ALL `jsonResponse` calls within `requestHandler`. The helper functions like `handleStart`, `handleStatus` etc. that call `jsonResponse` internally need to also receive and forward `requestOrigin`. Update their signatures to accept `requestOrigin?: string`.

- [ ] **Step 5: Run typecheck and lint**

```bash
cd sightflow-desktop-agent
npm run typecheck:node
npm run lint
```

Expected: No new errors.

- [ ] **Step 6: Commit**

```bash
cd sightflow-desktop-agent
git add src/main/skill-server.ts
git commit -m "fix: validate CORS origin to prevent cross-site API abuse (P0)"
git push
```

---

## Task 2: SightFlow Bug Fixes — generateReply API Key + Autopilot Persistence

**Files:**
- Modify: `sightflow-desktop-agent/src/main/index.ts:948-980` (generateReply)
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts:118` (autopilot)
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts` (handleAutopilotSet, handleAutopilotGet)
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts` (startSkillServer)

- [ ] **Step 1: Fix generateReply API Key bug**

In `sightflow-desktop-agent/src/main/index.ts`, around line 956, change:

```typescript
const apiKey = settings.vision?.apiKey || ''
```

to:

```typescript
const providerConfig = settings.chatProvider.config || {}
const apiKey = providerConfig.apiKey || ''
```

And remove the duplicate `providerConfig` declaration that was on line 964 (now redundant since we moved it up).

The full corrected block:

```typescript
  generateReply: async (context: string, _target?: string): Promise<GenerateReplyResponse> => {
    const startTime = Date.now()
    try {
      console.log('[generateReply] 开始生成回复:', context.substring(0, 50))

      // 获取当前配置
      const settings = normalizeSettings(settingsStore.store)
      const providerConfig = settings.chatProvider.config || {}
      const apiKey = providerConfig.apiKey || ''

      if (!apiKey) {
        console.error('[generateReply] 未配置 API Key')
        return { ok: false, error: 'no_api_key' }
      }

      // 构建 Provider 配置
      const model = providerConfig.model || 'doubao-seed-2.0-lite'
      const baseURL = providerConfig.baseURL || 'https://ark.cn-beijing.volces.com/api/plan/v3'
```

- [ ] **Step 2: Add autopilot persistence to electron-store**

In `skill-server.ts`, import settingsStore at the top:

```typescript
import { settingsStore } from './index'
```

Note: if there's a circular dependency issue, instead import the store module directly. Check how other files access settingsStore and follow the same pattern.

Replace line 118:

```typescript
/** 自动驾驶状态：从 store 读取，首次默认 false */
let autopilotEnabled = settingsStore?.get('autopilot.enabled', false) ?? false
```

- [ ] **Step 3: Update handleAutopilotSet to persist**

Find `handleAutopilotSet` function and update it to save to store:

```typescript
async function handleAutopilotSet(res: http.ServerResponse, body: string): Promise<void> {
  // ... existing parsing ...
  autopilotEnabled = enabled  // keep memory var for fast access
  
  // Persist to electron-store
  try {
    settingsStore?.set('autopilot.enabled', enabled)
  } catch (e) {
    console.error('[Skill Server] 持久化 autopilot 失败:', e)
  }
  
  // ... existing IPC broadcast ...
}
```

- [ ] **Step 4: Run typecheck and commit**

```bash
cd sightflow-desktop-agent
npm run typecheck:node
git add src/main/index.ts src/main/skill-server.ts
git commit -m "fix: generateReply uses chatProvider apiKey, autopilot persists to store"
git push
```

---

## Task 3: SightFlow Send Message Mutual Exclusion Lock

**Files:**
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts` (handleSendMessage)

- [ ] **Step 1: Add mutex check in handleSendMessage**

Find `handleSendMessage` function (around line 338). Add at the beginning, before the RPA logic:

```typescript
async function handleSendMessage(res: http.ServerResponse, body: string): Promise<void> {
  // Check if main RPA engine is busy — prevent concurrent WeChat window manipulation
  if (controller?.isEngineBusy?.()) {
    jsonResponse(res, 409, { ok: false, error: 'engine_busy', message: '主 RPA 引擎正在运行，请稍后重试' })
    return
  }
  
  // ... existing sendMessageToContact logic ...
}
```

Note: Check if `controller` (SkillEngineController) has an `isEngineBusy` method or equivalent. The `_engineBusy` flag mentioned in the spec is at `skill-server.ts:313`. If it's a module-level variable, check it directly:

```typescript
// If _engineBusy is a module-level variable:
if (_engineBusy) {
  jsonResponse(res, 409, { ok: false, error: 'engine_busy' })
  return
}
```

- [ ] **Step 2: Typecheck and commit**

```bash
cd sightflow-desktop-agent
npm run typecheck:node
git add src/main/skill-server.ts
git commit -m "fix: add mutex between send-message API and main RPA engine"
git push
```

---

## Task 4: wx-cli `whoami` Command

**Files:**
- Create: `wx-cli/src/cli/whoami.rs`
- Modify: `wx-cli/src/cli/mod.rs`
- Modify: `wx-cli/src/ipc.rs`
- Modify: `wx-cli/src/daemon/server.rs`
- Modify: `wx-cli/src/daemon/query.rs`

**Implementation approach:** Query the `contact` table in `contact.db` for the self user record. In WeChat's contact table, the self user is stored with specific attributes. We identify it by querying for contacts that are NOT groups (`@chatroom`), NOT official accounts (`gh_*`, `verify_flag != 0`), and NOT system accounts. The self user's `username` is the wxid.

A more robust approach: the self user is typically the contact with the smallest `rowid` that has `type = 0` (or equivalent) and a `wxid_*` pattern username. Alternatively, we can check which contact's username appears as `sender` in sent messages. For MVP, we'll use the simpler approach of finding the contact with `type = 0` (personal account, not group, not official).

- [ ] **Step 1: Add Whoami variant to IPC Request enum**

In `wx-cli/src/ipc.rs`, add to the `Request` enum (after the `Extract` variant, before the closing `}`):

```rust
    /// 查询当前登录用户的 wxid
    Whoami,
```

- [ ] **Step 2: Implement q_whoami in query.rs**

In `wx-cli/src/daemon/query.rs`, add a new function at the end of the file:

```rust
/// 查询当前登录用户的 wxid
///
/// 从 contact.db 中查找自己的记录。实现方案：
/// 查找 type=0 且 username 不以特殊前缀开头的联系人（即非群、非公众号、非系统号）。
/// 在微信 DB 中，自己的记录通常 type=0。
pub async fn q_whoami(db: &DbCache) -> Result<Value> {
    let path = db.get("contact/contact.db").await?;
    if path.is_none() {
        return Ok(serde_json::json!({ "wxid": null, "error": "contact_db_not_found" }));
    }
    let p = path.unwrap().clone();
    
    let result = tokio::task::spawn_blocking(move || {
        let conn = Connection::open(&p).context("打开 contact.db 失败")?;
        // 查询自己的 wxid：type=0 表示个人账号（非群、非公众号）
        // 排除系统账号前缀
        let mut stmt = conn.prepare(
            "SELECT username, nick_name FROM contact 
             WHERE type = 0 
             AND username NOT LIKE '@%'
             AND username NOT LIKE 'gh_%'
             AND username NOT LIKE 'biz_%'
             AND username NOT LIKE '%@chatroom'
             ORDER BY rowid ASC
             LIMIT 1"
        )?;
        
        let row: Option<(String, String)> = stmt
            .query_row([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1).unwrap_or_default()))
            })
            .ok();
        
        Ok::<_, anyhow::Error>(row)
    })
    .await??;
    
    match result {
        Some((wxid, nick)) => {
            Ok(serde_json::json!({ "wxid": wxid, "nick_name": nick }))
        }
        None => {
            Ok(serde_json::json!({ "wxid": null, "error": "self_not_found" }))
        }
    }
}
```

**Important note:** The exact query may need adjustment during testing. If `type = 0` doesn't uniquely identify self, alternatives include:
- Looking at the `reserved1` or `reserved2` columns for a self-indicator
- Using the message DB to find which contact appears as sender of "sent" messages
- Checking the `extra_buf` column

If the initial implementation doesn't work on the test machine, adjust the SQL query based on actual DB inspection.

- [ ] **Step 3: Add Whoami dispatch in daemon server.rs**

In `wx-cli/src/daemon/server.rs`, find the `dispatch` function's match block. Add a new arm (after `Extract` or at the end):

```rust
        Whoami => {
            match query::q_whoami(db).await {
                Ok(v) => Response::ok(v),
                Err(e) => Response::err(e.to_string()),
            }
        }
```

- [ ] **Step 4: Add Whoami CLI handler**

Create `wx-cli/src/cli/whoami.rs`:

```rust
use anyhow::Result;
use crate::ipc::Request;
use super::transport;
use super::output::{resolve, print_value};

pub fn cmd_whoami(json: bool) -> Result<()> {
    let resp = transport::send(Request::Whoami)?;
    let data = &resp.data;
    print_value(data, &resolve(json))
}
```

- [ ] **Step 5: Register Whoami subcommand in mod.rs**

In `wx-cli/src/cli/mod.rs`:

1. Add `pub mod whoami;` at the top with other module declarations.

2. Add to the `Commands` enum (after `Extract` variant):

```rust
    /// 查询当前登录用户的 wxid
    Whoami {
        /// 输出 JSON（默认 YAML）
        #[arg(long)]
        json: bool,
    },
```

3. Add to the `dispatch` function's match block:

```rust
        Commands::Whoami { json } => whoami::cmd_whoami(json),
```

- [ ] **Step 6: Build and test**

```bash
cd wx-cli
cargo check
cargo build --release
```

Expected: Builds successfully.

Manual test (requires WeChat running with loaded DB):

```bash
./target/release/wx whoami --json
```

Expected output: `{"wxid": "wxid_xxxxx", "nick_name": "your_name"}`

If it returns `{"wxid": null}`, adjust the SQL query in Step 2.

- [ ] **Step 7: Commit and push (wx-cli has its own git repo)**

```bash
cd wx-cli
git add src/cli/whoami.rs src/cli/mod.rs src/ipc.rs src/daemon/server.rs src/daemon/query.rs
git commit -m "feat: add wx whoami command to query current user wxid"
git push wx-cli main
```

---

## Task 5: glue-layer Bug Fixes

**Files:**
- Modify: `glue-layer/main.py`

This task addresses all glue-layer code defects listed in spec §12.6.

- [ ] **Step 1: Fix PID/LOCK file paths (PyInstaller compatibility)**

In `glue-layer/main.py`, replace lines 22-24:

```python
# 进程互斥：PID 文件路径
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.glue-layer.pid')
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.glue-layer.lock')
```

with:

```python
# 进程互斥：PID 文件路径（使用 %APPDATA% 以兼容 PyInstaller --onefile 模式）
def get_data_dir():
    """获取数据目录（PyInstaller --onefile 下 sys._MEIPASS 是临时目录，不可写）"""
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', '')
        if appdata:
            return os.path.join(appdata, 'WeChatAgent')
    return os.path.join(os.path.expanduser('~'), '.wechat-agent')

PID_FILE = os.path.join(get_data_dir(), '.glue-layer.pid')
LOCK_FILE = os.path.join(get_data_dir(), '.glue-layer.lock')
```

- [ ] **Step 2: Fix _is_mentioned empty names behavior**

In `glue-layer/main.py`, find `_is_mentioned` method (around line 468-477). Replace:

```python
    def _is_mentioned(self, content: str, config: AgentConfig) -> bool:
        """检测群消息中是否 @ 了自己（使用配置快照）"""
        if not config.identity.names:
            return True
```

with:

```python
    def _is_mentioned(self, content: str, config: AgentConfig) -> bool:
        """检测群消息中是否 @ 了自己（使用配置快照）"""
        if not config.identity.names:
            return False  # 空数组表示不检测 @提及，群聊中不回复
```

- [ ] **Step 3: Fix check_autopilot_enabled fail-open behavior**

In `glue-layer/main.py`, find `check_autopilot_enabled` (around line 145-158). Change both `return True` fallbacks to `return False`:

```python
def check_autopilot_enabled() -> bool:
    """检查 SightFlow 自动驾驶状态"""
    try:
        response = requests.get(
            f"{SIGHTFLOW_API_URL}/skill/autopilot",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('enabled', False)  # 默认关闭
        return False  # 请求失败时默认关闭
    except Exception as e:
        print(f"[WeChatAgent] 检查自动驾驶状态失败: {e}", flush=True)
        return False  # 出错时默认关闭
```

- [ ] **Step 4: Add logging configuration**

Add after the imports section (around line 20), before the PID_FILE definitions:

```python
import logging
from logging.handlers import RotatingFileHandler

# 日志配置：写入 %APPDATA%\WeChatAgent\logs\
_log_dir = os.path.join(get_data_dir(), 'logs')
os.makedirs(_log_dir, exist_ok=True)

logging.basicConfig(
    handlers=[
        logging.StreamHandler(),  # 保留 stdout 输出
        RotatingFileHandler(
            os.path.join(_log_dir, 'glue-layer.log'),
            maxBytes=10*1024*1024, backupCount=5,
            encoding='utf-8'
        )
    ],
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('WeChatAgent')
```

Then throughout the file, replace `print(f"[WeChatAgent] ...")` calls with `logger.info(...)` for important events. Keep `print()` for startup messages that need to appear immediately.

- [ ] **Step 5: Add CLI argument parsing (argparse)**

Add at the top of `main()`, or at module level before `WeChatAgent` is instantiated:

```python
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description='WeChat Agent glue-layer')
    parser.add_argument('--wx-cli-path', dest='wx_cli_path',
                        help='wx-cli 可执行文件路径（覆盖 config）')
    parser.add_argument('--sightflow-port', dest='sightflow_port', type=int, default=None,
                        help='SightFlow API 端口（覆盖默认 12680）')
    parser.add_argument('--config', dest='config_path',
                        help='配置文件路径（默认 %APPDATA%\\WeChatAgent\\config.json）')
    return parser.parse_args()
```

In the `main()` function (or at module startup), apply args:

```python
args = parse_args()

# CLI 参数覆盖
if args.sightflow_port:
    SIGHTFLOW_API_URL = f'http://127.0.0.1:{args.sightflow_port}'
if args.wx_cli_path:
    os.environ['WX_CLI_PATH_OVERRIDE'] = args.wx_cli_path
if args.config_path:
    os.environ['WECHAT_AGENT_CONFIG_PATH'] = args.config_path
```

Update `config.py` to check the env var:

```python
# In config.py, modify SIGHTFLOW_API_URL:
SIGHTFLOW_API_URL = os.getenv('SIGHTFLOW_API_URL_OVERRIDE', 
                    os.getenv('SIGHTFLOW_API_URL', 'http://127.0.0.1:12680'))
```

- [ ] **Step 6: Fix new-messages cursor (save state between calls)**

In `glue-layer/main.py`, find `_get_new_messages` (around line 308-332). Currently it calls `wx new-messages --json` without passing state, so every call fetches 24h of messages. Fix by maintaining state:

Add to `WeChatAgent.__init__`:

```python
self._new_messages_state: Optional[dict] = None  # wx new-messages cursor state
```

Update `_get_new_messages`:

```python
    def _get_new_messages(self) -> List[dict]:
        """使用 wx new-messages 获取自上次检查以来的新消息"""
        config = self._config_watcher.get_config()
        wx_cli_path = config.advanced.wx_cli_path if config else 'wx'
        try:
            cmd = [wx_cli_path, 'new-messages', '--json', '-n', '50']
            # Pass state if we have it (for incremental fetching)
            if self._new_messages_state:
                cmd.extend(['--state', json.dumps(self._new_messages_state)])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=15
            )

            if result.returncode != 0:
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}", flush=True)
                return []

            data = json.loads(result.stdout)
            messages = data.get('messages', [])
            
            # Save state for next call
            new_state = data.get('new_state')
            if new_state:
                self._new_messages_state = new_state
            
            return [m for m in messages if m.get('chat_type') in ('private', 'group')]

        except Exception as e:
            print(f"[WeChatAgent] 获取新消息失败: {e}", flush=True)
            return []
```

Note: Check if `wx new-messages` actually accepts `--state` as a CLI argument. Looking at `mod.rs` line 159-166, the `NewMessages` command has a `state` field. The CLI args might need to be `--state '<json>'`. Verify the exact CLI syntax during implementation.

- [ ] **Step 7: Verify all fixes**

```bash
cd glue-layer
python -c "import main; print('imports ok')"
```

- [ ] **Step 8: Commit**

```bash
cd glue-layer  # or from root: git add glue-layer/
git add main.py config.py
git commit -m "fix: glue-layer bug fixes for MVP (PID paths, _is_mentioned, autopilot, logging, CLI args, cursor)"
git push origin dev
```

---

## Task 6: glue-layer Unit Tests

**Files:**
- Create: `glue-layer/test_main.py`

**Context:** glue-layer core logic has zero test coverage (spec §12.8). We need tests for: `_make_msg_key`, `_is_mentioned`, rate limiting, and echo quiet period.

- [ ] **Step 1: Create test file with testable extractions**

Some functions are methods on `WeChatAgent` which has heavy dependencies (subprocess, HTTP). For testability, we'll create a minimal test instance or extract pure functions.

```python
# glue-layer/test_main.py
"""glue-layer 核心逻辑单元测试"""
import pytest
import time
from unittest.mock import MagicMock, patch
from collections import deque


# ─── _make_msg_key tests ─────────────────────────────────────────────────────

def test_make_msg_key_basic():
    """消息 key 由 timestamp + sender + content hash 组成"""
    # Import after mocking to avoid side effects
    from main import WeChatAgent
    
    # Create a minimal mock instance (bypass __init__)
    agent = WeChatAgent.__new__(WeChatAgent)
    
    msg = {
        'timestamp': 1700000000,
        'sender': 'wxid_abc',
        'content': '你好世界'
    }
    key = agent._make_msg_key(msg)
    assert '1700000000' in key
    assert 'wxid_abc' in key
    # Same message should produce same key
    assert agent._make_msg_key(msg) == key


def test_make_msg_key_truncates_long_content():
    """消息 key 只取前 50 字符"""
    from main import WeChatAgent
    agent = WeChatAgent.__new__(WeChatAgent)
    
    msg1 = {'timestamp': 100, 'sender': 's', 'content': 'a' * 50}
    msg2 = {'timestamp': 100, 'sender': 's', 'content': 'a' * 100}
    # Both should produce same key (content truncated to 50 chars)
    assert agent._make_msg_key(msg1) == agent._make_msg_key(msg2)


def test_make_msg_key_different_senders():
    """不同 sender 产生不同 key"""
    from main import WeChatAgent
    agent = WeChatAgent.__new__(WeChatAgent)
    
    msg1 = {'timestamp': 100, 'sender': 'alice', 'content': 'hi'}
    msg2 = {'timestamp': 100, 'sender': 'bob', 'content': 'hi'}
    assert agent._make_msg_key(msg1) != agent._make_msg_key(msg2)


# ─── _is_mentioned tests ─────────────────────────────────────────────────────

def test_is_mentioned_with_matching_name():
    """@昵称 检测正常"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig
    
    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=['小明']),
        groups=GroupsConfig(),
        ai=AIConfig()
    )
    
    assert agent._is_mentioned('你好 @小明', config) is True
    assert agent._is_mentioned('你好 @小红', config) is False


def test_is_mentioned_empty_names_returns_false():
    """空 names 列表应返回 False（不回复，防止刷屏）"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig
    
    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=[]),
        groups=GroupsConfig(),
        ai=AIConfig()
    )
    
    assert agent._is_mentioned('你好 @任何人', config) is False


def test_is_mentioned_multiple_names():
    """多个昵称，任一匹配即可"""
    from main import WeChatAgent
    from config_model import AgentConfig, IdentityConfig, GroupsConfig, AIConfig
    
    agent = WeChatAgent.__new__(WeChatAgent)
    config = AgentConfig(
        version=1,
        identity=IdentityConfig(wxid='wxid_self', names=['小明', 'Ming', '明明']),
        groups=GroupsConfig(),
        ai=AIConfig()
    )
    
    assert agent._is_mentioned('@Ming 你好', config) is True
    assert agent._is_mentioned('@明明 在吗', config) is True
    assert agent._is_mentioned('@小红 你好', config) is False


# ─── Rate limiting tests ─────────────────────────────────────────────────────

def test_rate_limit_global():
    """全局限速：3 条/分钟"""
    from main import MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS
    
    assert MAX_MESSAGES_PER_MINUTE == 3
    assert MESSAGE_WINDOW_SECONDS == 60


def test_rate_limit_allows_under_limit():
    """未达限速时应允许发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS
    
    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
    
    # 只有 2 条记录，且都在窗口内
    now = time.time()
    agent._send_times.append(now - 30)
    agent._send_times.append(now - 10)
    
    # 检查限速逻辑（模拟 _process_message 中的限速代码）
    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()
    
    assert len(agent._send_times) < MAX_MESSAGES_PER_MINUTE  # 应该还能发


def test_rate_limit_blocks_at_limit():
    """达到限速时应阻止发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS
    
    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
    
    # 填满 3 条，都在窗口内
    now = time.time()
    agent._send_times.append(now - 30)
    agent._send_times.append(now - 20)
    agent._send_times.append(now - 10)
    
    # 检查限速
    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()
    
    assert len(agent._send_times) >= MAX_MESSAGES_PER_MINUTE  # 应该被限速


def test_rate_limit_allows_after_window():
    """窗口期过后应允许发送"""
    from main import WeChatAgent, MAX_MESSAGES_PER_MINUTE, MESSAGE_WINDOW_SECONDS
    
    agent = WeChatAgent.__new__(WeChatAgent)
    agent._send_times = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
    
    # 3 条记录，但都在窗口外（> 60 秒前）
    now = time.time()
    agent._send_times.append(now - 120)
    agent._send_times.append(now - 90)
    agent._send_times.append(now - 70)
    
    # 清理过期记录
    while agent._send_times and agent._send_times[0] < now - MESSAGE_WINDOW_SECONDS:
        agent._send_times.popleft()
    
    assert len(agent._send_times) == 0  # 全部过期，可以发送


# ─── Echo quiet period tests ─────────────────────────────────────────────────

def test_echo_quiet_seconds_constant():
    """Echo 静默期常量检查"""
    from main import ECHO_QUIET_SECONDS
    assert ECHO_QUIET_SECONDS == 15


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
```

- [ ] **Step 2: Run tests**

```bash
cd glue-layer
pip install pytest  # if not already installed
python -m pytest test_main.py -v
```

Expected: All tests pass. If any tests fail due to import side effects (e.g., `main.py` tries to connect to something on import), add appropriate mocks or restructure imports.

- [ ] **Step 3: Commit**

```bash
git add test_main.py
git commit -m "test: add unit tests for glue-layer core logic (msg key, mention, rate limit)"
git push origin dev
```

---

## Task 7: SightFlow process-manager.ts

**Files:**
- Create: `sightflow-desktop-agent/src/main/process-manager.ts`

**Context:** This is a new module that spawns and monitors glue-layer as a child process. It only manages glue-layer (wx-cli self-starts via `ensure_daemon()`).

- [ ] **Step 1: Create process-manager.ts**

```typescript
// sightflow-desktop-agent/src/main/process-manager.ts
import { spawn, ChildProcess } from 'child_process'
import { app } from 'electron'
import * as path from 'path'

export interface ProcessManagerCallbacks {
  onStatusChange: (status: GlueLayerStatus) => void
  onLog: (line: string) => void
}

export type GlueLayerStatus = 'stopped' | 'starting' | 'running' | 'crashed' | 'failed'

interface ProcessManagerOptions {
  /** glue-layer.exe 路径（通常为 process.resourcesPath + '/glue-layer/glue-layer.exe'） */
  executablePath: string
  /** wx-cli.exe 路径（传给 glue-layer 的 --wx-cli-path 参数） */
  wxCliPath: string
  /** SightFlow API 端口 */
  sightflowPort: number
  /** 配置文件路径 */
  configPath?: string
  /** 回调函数 */
  callbacks: ProcessManagerCallbacks
}

const MAX_RESTART_ATTEMPTS = 3
const RESTART_DELAYS = [2000, 5000, 10000]  // 指数退避: 2s, 5s, 10s

export class ProcessManager {
  private process: ChildProcess | null = null
  private restartCount = 0
  private options: ProcessManagerOptions
  private _status: GlueLayerStatus = 'stopped'

  constructor(options: ProcessManagerOptions) {
    this.options = options
  }

  get status(): GlueLayerStatus {
    return this._status
  }

  private setStatus(status: GlueLayerStatus): void {
    this._status = status
    this.options.callbacks.onStatusChange(status)
  }

  /** 启动 glue-layer 进程 */
  start(): void {
    if (this.process) {
      console.warn('[ProcessManager] glue-layer already running')
      return
    }

    this.setStatus('starting')
    this.restartCount = 0

    this.spawnProcess()
  }

  /** 停止 glue-layer 进程 */
  stop(): void {
    if (!this.process) return
    
    console.log('[ProcessManager] Stopping glue-layer...')
    this.process.removeAllListeners('exit')
    this.process.kill()
    this.process = null
    this.setStatus('stopped')
  }

  /** 手动重启 */
  restart(): void {
    this.stop()
    this.restartCount = 0
    setTimeout(() => this.spawnProcess(), 500)
  }

  private spawnProcess(): void {
    const args: string[] = [
      '--wx-cli-path', this.options.wxCliPath,
      '--sightflow-port', String(this.options.sightflowPort),
    ]
    if (this.options.configPath) {
      args.push('--config', this.options.configPath)
    }

    console.log('[ProcessManager] Spawning:', this.options.executablePath, args.join(' '))

    try {
      this.process = spawn(this.options.executablePath, args, {
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: false,
      })
    } catch (e) {
      console.error('[ProcessManager] spawn failed:', e)
      this.setStatus('failed')
      return
    }

    this.process.stdout?.on('data', (data: Buffer) => {
      const lines = data.toString('utf-8').trim().split('\n')
      for (const line of lines) {
        if (line.trim()) {
          this.options.callbacks.onLog(line)
          console.log('[glue-layer]', line)
        }
      }
    })

    this.process.stderr?.on('data', (data: Buffer) => {
      const lines = data.toString('utf-8').trim().split('\n')
      for (const line of lines) {
        if (line.trim()) {
          this.options.callbacks.onLog(`[stderr] ${line}`)
          console.error('[glue-layer]', line)
        }
      }
    })

    this.process.on('exit', (code, signal) => {
      console.log(`[ProcessManager] glue-layer exited: code=${code}, signal=${signal}`)
      this.process = null

      if (code === 0) {
        // Normal exit
        this.setStatus('stopped')
        return
      }

      // Abnormal exit — attempt restart with backoff
      this.setStatus('crashed')
      this.scheduleRestart()
    })

    this.process.on('error', (err) => {
      console.error('[ProcessManager] process error:', err)
      this.setStatus('failed')
    })

    // Give it a moment to fail fast, then mark as running
    setTimeout(() => {
      if (this.process && !this.process.killed) {
        this.setStatus('running')
      }
    }, 1000)
  }

  private scheduleRestart(): void {
    if (this.restartCount >= MAX_RESTART_ATTEMPTS) {
      console.error('[ProcessManager] Max restart attempts reached, giving up')
      this.setStatus('failed')
      return
    }

    const delay = RESTART_DELAYS[this.restartCount] || RESTART_DELAYS[RESTART_DELAYS.length - 1]
    this.restartCount++
    console.log(`[ProcessManager] Restarting in ${delay}ms (attempt ${this.restartCount}/${MAX_RESTART_ATTEMPTS})`)

    setTimeout(() => {
      if (this._status === 'stopped') return  // User manually stopped
      this.spawnProcess()
    }, delay)
  }
}

/** 获取 glue-layer.exe 的完整路径 */
export function getGlueLayerPath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'glue-layer', 'glue-layer.exe')
  }
  // Dev mode: look in project resources
  return path.join(app.getAppPath(), 'resources', 'glue-layer', 'glue-layer.exe')
}

/** 获取 wx-cli.exe 的完整路径 */
export function getWxCliPath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'wx-cli', 'wx.exe')
  }
  return path.join(app.getAppPath(), 'resources', 'wx-cli', 'wx.exe')
}
```

- [ ] **Step 2: Typecheck**

```bash
cd sightflow-desktop-agent
npm run typecheck:node
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add src/main/process-manager.ts
git commit -m "feat: add process-manager for glue-layer lifecycle management"
git push
```

---

## Task 8: SightFlow wechat-installer.ts

**Files:**
- Create: `sightflow-desktop-agent/src/main/wechat-installer.ts`

- [ ] **Step 1: Create wechat-installer.ts**

```typescript
// sightflow-desktop-agent/src/main/wechat-installer.ts
import { execSync } from 'child_process'
import * as path from 'path'
import * as fs from 'fs'

const REQUIRED_WECHAT_VERSION = '4.1.9'

export interface WeChatInstallStatus {
  installed: boolean
  version: string | null
  installPath: string | null
  needsInstall: boolean
}

/** 检测微信安装状态和版本 */
export function detectWeChat(): WeChatInstallStatus {
  // 1. 尝试从注册表获取安装路径
  let installPath: string | null = null
  try {
    installPath = execSync(
      'reg query "HKCU\\Software\\Tencent\\WeChat" /v InstallPath',
      { encoding: 'utf-8', windowsHide: true }
    )
      .split('\n')
      .find(line => line.includes('InstallPath'))
      ?.split('REG_SZ')
      .pop()
      ?.trim() || null
  } catch {
    // Registry key not found — WeChat not installed
  }

  // 2. 如果注册表没找到，尝试默认路径
  if (!installPath) {
    const defaultPaths = [
      path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Tencent', 'WeChat'),
      path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Tencent', 'WeChat'),
      path.join(process.env['APPDATA'] || '', 'Tencent', 'WeChat'),
    ]
    for (const p of defaultPaths) {
      if (fs.existsSync(path.join(p, 'WeChat.exe'))) {
        installPath = p
        break
      }
    }
  }

  if (!installPath) {
    return { installed: false, version: null, installPath: null, needsInstall: true }
  }

  // 3. 获取版本信息
  const exePath = path.join(installPath, 'WeChat.exe')
  let version: string | null = null
  try {
    if (fs.existsSync(exePath)) {
      const versionInfo = execSync(
        `powershell -Command "(Get-Item '${exePath}').VersionInfo.ProductVersion"`,
        { encoding: 'utf-8', windowsHide: true }
      ).trim()
      version = versionInfo || null
    }
  } catch {
    // Failed to get version
  }

  const needsInstall = !version || !version.startsWith(REQUIRED_WECHAT_VERSION)

  return { installed: true, version, installPath, needsInstall }
}

/** 静默安装微信（使用 /S 参数） */
export function installWeChat(installerPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      // NSIS 静默安装：/S
      execSync(`"${installerPath}" /S`, {
        timeout: 120_000,  // 2 分钟超时
        windowsHide: false,
      })
      resolve(true)
    } catch (e) {
      console.error('[WeChatInstaller] Installation failed:', e)
      resolve(false)
    }
  })
}
```

- [ ] **Step 2: Typecheck and commit**

```bash
cd sightflow-desktop-agent
npm run typecheck:node
git add src/main/wechat-installer.ts
git commit -m "feat: add WeChat version detection and installer module"
git push
```

---

## Task 9: SightFlow Integration — Wire process-manager + IPC

**Files:**
- Modify: `sightflow-desktop-agent/src/main/index.ts`

- [ ] **Step 1: Import and initialize ProcessManager**

At the top of `index.ts`, add:

```typescript
import { ProcessManager, getGlueLayerPath, getWxCliPath } from './process-manager'
import { detectWeChat, installWeChat } from './wechat-installer'
```

After the skill server starts (find where `startSkillServer` is called), add:

```typescript
// Initialize glue-layer process manager
let glueLayerManager: ProcessManager | null = null

function startGlueLayer(): void {
  const wxCliPath = getWxCliPath()
  const glueLayerPath = getGlueLayerPath()
  
  if (!fs.existsSync(glueLayerPath)) {
    console.error('[Main] glue-layer.exe not found at:', glueLayerPath)
    return
  }
  
  glueLayerManager = new ProcessManager({
    executablePath: glueLayerPath,
    wxCliPath,
    sightflowPort: currentPort || 12680,
    callbacks: {
      onStatusChange: (status) => {
        // Broadcast to all renderer windows
        BrowserWindow.getAllWindows().forEach(win => {
          win.webContents.send('wechat-agent:glue-layer-status', status)
        })
      },
      onLog: (line) => {
        // Forward to skill log system
        BrowserWindow.getAllWindows().forEach(win => {
          win.webContents.send('wechat-agent:glue-layer-log', line)
        })
      }
    }
  })
  
  glueLayerManager.start()
}

// Start glue-layer after a short delay (let skill server initialize first)
setTimeout(startGlueLayer, 2000)
```

- [ ] **Step 2: Add IPC handlers for process management**

```typescript
// IPC: Get glue-layer status
ipcMain.handle('wechat-agent:getGlueLayerStatus', () => {
  return glueLayerManager?.status ?? 'stopped'
})

// IPC: Restart glue-layer
ipcMain.handle('wechat-agent:restartGlueLayer', () => {
  glueLayerManager?.restart()
  return { ok: true }
})

// IPC: Check WeChat installation
ipcMain.handle('wechat-agent:checkWeChat', () => {
  return detectWeChat()
})

// IPC: Install WeChat
ipcMain.handle('wechat-agent:installWeChat', async () => {
  const installerPath = path.join(process.resourcesPath, 'wechat', 'WeChatWin_4.1.9.exe')
  if (!fs.existsSync(installerPath)) {
    return { ok: false, error: 'installer_not_found' }
  }
  const success = await installWeChat(installerPath)
  return { ok: success }
})
```

- [ ] **Step 3: Cleanup on app quit**

Find the app `before-quit` or `will-quit` handler and add:

```typescript
app.on('before-quit', () => {
  glueLayerManager?.stop()
})
```

- [ ] **Step 4: Register IPC channels in preload**

Update `sightflow-desktop-agent/src/preload/index.ts` to expose the new IPC channels:

```typescript
// Add to the existing IPC bridge:
'wechat-agent:getGlueLayerStatus': () => ipcRenderer.invoke('wechat-agent:getGlueLayerStatus'),
'wechat-agent:restartGlueLayer': () => ipcRenderer.invoke('wechat-agent:restartGlueLayer'),
'wechat-agent:checkWeChat': () => ipcRenderer.invoke('wechat-agent:checkWeChat'),
'wechat-agent:installWeChat': () => ipcRenderer.invoke('wechat-agent:installWeChat'),
```

- [ ] **Step 5: Typecheck and commit**

```bash
cd sightflow-desktop-agent
npm run typecheck
git add src/main/index.ts src/preload/index.ts
git commit -m "feat: wire process-manager and wechat-installer into main process"
git push
```

---

## Task 10: electron-builder Configuration + Resources

**Files:**
- Modify: `sightflow-desktop-agent/electron-builder.yml`
- Create: `sightflow-desktop-agent/resources/README.md`

- [ ] **Step 1: Update electron-builder.yml**

Add `extraResources` section to `sightflow-desktop-agent/electron-builder.yml`:

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

Add this after the `asarUnpack` section.

- [ ] **Step 2: Create resources directory structure**

Create `sightflow-desktop-agent/resources/README.md`:

```markdown
# Resources Directory

This directory contains binary resources that are bundled into the SightFlow installer.

## Required Files

Before running `npm run build:win`, place the following files:

- `wx-cli/wx.exe` — wx-cli binary (build from `wx-cli/` with `cargo build --release`)
- `glue-layer/glue-layer.exe` — glue-layer binary (build from `glue-layer/` with PyInstaller)
- `wechat/WeChatWin_4.1.9.exe` — WeChat installer (already at repo root as `WeChatWin_4.1.9.exe`)

## Build Scripts

See `scripts/` directory for automated build scripts.
```

- [ ] **Step 3: Copy WeChat installer to resources**

```bash
cd sightflow-desktop-agent
mkdir -p resources/wx-cli resources/glue-layer resources/wechat
cp ../WeChatWin_4.1.9.exe resources/wechat/
```

- [ ] **Step 4: Commit**

```bash
git add electron-builder.yml resources/README.md resources/wechat/
# Note: WeChatWin_4.1.9.exe is ~235MB, may need git-lfs or .gitignore
git commit -m "build: configure electron-builder extraResources for wx-cli, glue-layer, wechat"
git push
```

---

## Task 11: Build Scripts

**Files:**
- Create: `scripts/build-glue-layer.sh` (or `.bat` for Windows)
- Create: `scripts/build-all.sh`

- [ ] **Step 1: Create glue-layer build script**

Create `scripts/build-glue-layer.sh`:

```bash
#!/bin/bash
# Build glue-layer.exe using PyInstaller
# Usage: bash scripts/build-glue-layer.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GLUE_LAYER_DIR="$SCRIPT_DIR/../glue-layer"
OUTPUT_DIR="$SCRIPT_DIR/../sightflow-desktop-agent/resources/glue-layer"

echo "=== Building glue-layer ==="
cd "$GLUE_LAYER_DIR"

# Install dependencies
pip install pyinstaller cryptography pywin32

# Build with PyInstaller
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
  main.py --name glue-layer

# Copy to resources
mkdir -p "$OUTPUT_DIR"
cp dist/glue-layer.exe "$OUTPUT_DIR/"
echo "✅ glue-layer.exe → $OUTPUT_DIR/"
```

- [ ] **Step 2: Create master build script**

Create `scripts/build-all.sh`:

```bash
#!/bin/bash
# Build all components for the WeChatAgent installer
# Usage: bash scripts/build-all.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$SCRIPT_DIR/.."

echo "=== Step 1: Build wx-cli ==="
cd "$ROOT_DIR/wx-cli"
cargo build --release --target x86_64-pc-windows-gnu
mkdir -p "$ROOT_DIR/sightflow-desktop-agent/resources/wx-cli"
cp target/x86_64-pc-windows-gnu/release/wx.exe "$ROOT_DIR/sightflow-desktop-agent/resources/wx-cli/"
echo "✅ wx.exe copied"

echo "=== Step 2: Build glue-layer ==="
bash "$SCRIPT_DIR/build-glue-layer.sh"

echo "=== Step 3: Copy WeChat installer ==="
mkdir -p "$ROOT_DIR/sightflow-desktop-agent/resources/wechat"
cp "$ROOT_DIR/WeChatWin_4.1.9.exe" "$ROOT_DIR/sightflow-desktop-agent/resources/wechat/"
echo "✅ WeChat installer copied"

echo "=== Step 4: Build SightFlow ==="
cd "$ROOT_DIR/sightflow-desktop-agent"
npm run build:win

echo "✅ All done! Installer at: sightflow-desktop-agent/dist/"
```

- [ ] **Step 3: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add scripts/build-glue-layer.sh scripts/build-all.sh
git commit -m "build: add build scripts for glue-layer and full installer"
git push origin dev
```

---

## Task 12: Final Integration Test + Smoke Test

- [ ] **Step 1: Run all typechecks**

```bash
cd sightflow-desktop-agent && npm run typecheck
cd wx-cli && cargo check
cd glue-layer && python -m pytest test_main.py -v
```

- [ ] **Step 2: Build wx-cli for Windows**

```bash
cd wx-cli
cargo build --release --target x86_64-pc-windows-gnu
```

- [ ] **Step 3: Build glue-layer**

```bash
bash scripts/build-glue-layer.sh
```

- [ ] **Step 4: Attempt SightFlow build**

```bash
cd sightflow-desktop-agent
npm run build:win
```

- [ ] **Step 5: Manual smoke test (if on Windows)**

1. Run the built SightFlow
2. Verify glue-layer spawns (check task manager for `glue-layer.exe`)
3. Verify `wx ping --json` works
4. Check `%APPDATA%\WeChatAgent\logs\glue-layer.log` exists
5. Open browser devtools on a random page, try `fetch('http://127.0.0.1:12680/skill/status')` — should be BLOCKED by CORS

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final integration fixes for MVP"
git push origin dev
```

---

## Summary: Task Dependency Graph

```
Task 1 (CORS fix) ─────────────────────────────────┐
Task 2 (generateReply + autopilot) ────────────────┤
Task 3 (send-message mutex) ───────────────────────┤
                                                     │
Task 4 (wx whoami) ─── independent (wx-cli repo)    │
                                                     │
Task 5 (glue-layer fixes) ──┐                        │
Task 6 (glue-layer tests) ──┘                        │
                                                     │
Task 7 (process-manager) ────┐                       │
Task 8 (wechat-installer) ───┤                       │
                              ├──▶ Task 9 (wire up) ─┤
                              │                       │
Task 10 (electron-builder) ──┤                       │
Task 11 (build scripts) ─────┤                       │
                              │                       │
                              └──▶ Task 12 (smoke test)
```

**Parallelizable:** Tasks 1-4 can all run in parallel (different repos/files). Tasks 5-6 can run in parallel. Tasks 7-8 can run in parallel. Task 9 depends on 7+8. Task 12 depends on everything.

**Critical path:** Task 1 (CORS) → Task 9 → Task 12
