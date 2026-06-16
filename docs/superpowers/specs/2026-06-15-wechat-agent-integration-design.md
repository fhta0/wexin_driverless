# 微信自动回复 Agent 集成设计

## 概述

构建一个完整的微信自动回复系统，结合三个组件：

- **wx-cli**：消息监控（读取微信数据库）
- **SightFlow Agent**：消息发送（视觉定位+模拟操作）
- **Python 粘合层**：协调两者，处理 AI 回复生成

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          微信客户端                              │
│   (SQLite 数据库)          (GUI 窗口)                           │
└─────────────────────────────────────────────────────────────────┘
        │ 写入                           │ 显示
        ▼                                ▼
┌──────────────────┐              ┌──────────────────┐
│   wx-cli daemon  │              │  SightFlow Agent │
│  (解密 + 缓存)    │              │  (视觉定位+发送)  │
└──────────────────┘              └──────────────────┘
        │                                │
        │ JSON: sender + content         │ HTTP API
        │                                │ POST /skill/send-message
        ▼                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      粘合层 (Python)                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ 防循环过滤   │───▶│ wxid 翻译   │───▶│ 消息队列 + AI 生成  │  │
│  │ (is_from_self)│   │ (私聊/群聊) │    │ (锁外调用 LLM)      │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│                                              │                  │
│                                              ▼                  │
│                              ┌───────────────────────┐          │
│                              │ HTTP 调用 SightFlow   │          │
│                              │ (锁内，串行发送)       │          │
│                              └───────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

## 关键实现细节

### 进程互斥（防止多实例）

粘合层使用 PID 文件确保只有一个实例运行：

```python
PID_FILE = '.glue-layer.pid'

def check_single_instance():
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read())
        # 检查进程是否存活
        if is_process_running(old_pid):
            print(f"已有实例运行 (PID: {old_pid})")
            return False
        os.remove(PID_FILE)
    
    # 写入当前 PID
    open(PID_FILE, 'w').write(str(os.getpid()))
    return True
```

### 消息去重策略

使用复合 key 防止重复处理同一条消息：

```python
def _make_msg_key(msg):
    ts = msg.get('timestamp', 0)
    sender = msg.get('sender', '')
    content_hash = hash(msg.get('content', '')[:50])
    return f"{ts}:{sender}:{content_hash}"

_processed_keys: Set[str] = set()
```

### 会话时间戳跟踪

即使会话全是自己发的消息，也要更新 `_last_reply_ts`，防止重复检查：

```python
def _process_unread_session(session):
    session_timestamp = session.get('timestamp', 0)
    
    # 防止重复处理已检查过的会话
    if session_timestamp <= self._last_reply_ts.get(username, 0):
        return
    
    found_other_message = False
    for msg in reversed(messages):
        if msg['sender'] == '192':  # 自己发的消息
            continue
        # 处理对方消息...
        found_other_message = True
        break
    
    # 无论是否找到对方消息，都更新时间戳
    if not found_other_message:
        self._last_reply_ts[username] = session_timestamp
```

## 数据流

### 步骤 1：防循环过滤

wx-cli 轮询返回的消息包含 `is_from_self` 字段。必须过滤掉自己发出的消息，否则 AI 会和自己陷入无限循环。

```python
for msg in new_messages:
    if msg.get('is_from_self'):
        continue  # 忽略自己发出的消息
```

### 步骤 2：wxid 翻译 + 私聊/群聊分流

wx-cli 返回的 `sender_wxid` 是底层 wxid，需要翻译为可见的备注名/昵称。

**搜索策略**：
- 微信搜索框只能搜索：备注名、微信号、昵称
- wxid（如 `wxid_xxx`）**不能被搜索**
- wx-cli 返回的数据中没有微信号字段
- 因此只能用备注名/昵称作为搜索目标

**私聊场景**：
- `chat_name`（备注名）→ SightFlow 搜索目标
- 所有私聊消息都回复

**群聊场景**：
- `chat_name`（群名）→ SightFlow 搜索目标
- 只有被 @ 的消息才回复
- 其他群消息只监控（打印日志）

**群消息 @ 检测**：
```python
def _is_mentioned(self, content: str) -> bool:
    """检测群消息中是否 @ 了自己"""
    names = [name.strip() for name in SELF_NAMES.split(',') if name.strip()]
    for name in names:
        if f"@{name}" in content:
            return True
    return False
```

配置自己的名字（`SELF_NAMES`）：
- 支持多个名字，逗号分隔
- 如 `SELF_NAMES=付海涛,老付,海涛`
- 未配置时默认回复所有群消息

### 步骤 3：消息队列 + 排他锁

**并发冲突问题**：如果用户 A 和用户 B 同时发消息，粘合层可能连续发送两个请求，导致 SightFlow 在界面上操作混乱（把给 A 的消息发给 B）。

**解决方案**：消息队列 + 排他锁，同一时间只允许一个发送任务。

```python
import queue, threading

send_queue = queue.Queue()
send_lock = threading.Lock()

def send_worker():
    while True:
        target, raw_msg = send_queue.get()
        
        # AI 生成回复（锁外执行，不阻塞其他发送）
        ai_reply = call_llm_api(target, raw_msg)
        
        # 调用 SightFlow（锁内执行，串行化）
        with send_lock:
            response = requests.post(
                'http://127.0.0.1:12680/skill/send-message',
                json={'contact': target, 'message': ai_reply}
            )
        
        send_queue.task_done()
```

**关键**：AI 生成在锁外（耗时 1~3 秒），只有 HTTP 调用在锁内。

### 步骤 4：轮询间隔

粘合层每 **5 秒** 调用 `wx new-messages --json --filter private,group`。

## SightFlow API 扩展

### 新增端点：`POST /skill/send-message`

**请求体**：
```json
{
  "contact": "付海涛",  // 联系人备注名 或 群名
  "message": "好的，收到"
}
```

**响应体**：
```json
{
  "ok": true,
  "elapsed_ms": 1800
}
```

**错误码**：
- `contact_not_found`：搜索不到联系人
- `send_failed`：发送操作失败
- `operation_in_progress`：当前有其他任务正在执行

### 内部执行流程

```
POST /skill/send-message
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 1. 检查操作锁                                        │
│    if operation_lock: return 409                    │
│    set operation_lock = true                        │
│    备份剪贴板内容                                    │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 2. 激活微信窗口                                      │
│    activateWindow("WeChat")                         │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 3. Ctrl+F 搜索定位联系人                             │
│    3.1 Ctrl + F 激活搜索框                          │
│    3.2 输入 contact 名称 + 等待 300ms               │
│    3.3 截图左侧列表顶部                             │
│    3.4 VLM 定位第一个匹配结果并点击                 │
│    ✓ 解决：联系人沉底不可见问题                     │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 4. 等待会话窗口打开                                  │
│    sleep(500ms)                                     │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 5. 动态计算输入框坐标                                │
│    winRect = getWeChatWindowRect()                  │
│    chatArea = calcChatArea(winRect)                 │
│    inputCoords = getInputBoxCoords(chatArea)        │
│    clickAt(inputCoords)                             │
│    ✓ 支持窗口拉伸/最大化                            │
│    ✓ 省去第二次 VLM 调用                            │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 6. safePaste 发送消息                                │
│    clipboard.write(message)                         │
│    sleep(100ms)                                     │
│    Ctrl/Cmd + V                                     │
│    sleep(150ms)                                     │
│    Enter                                            │
│    sleep(300ms)                                     │
│    恢复原有剪贴板内容                                │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 7. 清理并返回                                        │
│    operation_lock = false                           │
│    return { ok: true, elapsed_ms }                  │
└─────────────────────────────────────────────────────┘
```

## 关键实现模块

### 窗口定位 (window-utils.ts)

```typescript
export async function getWeChatWindowRect(): Promise<BBox> {
  const windows = windowManager.getWindows()
  const wechatWin = windows.find(w => 
    w.getTitle().includes('微信') || 
    w.getProcessName().includes('WeChat')
  )
  
  if (!wechatWin) {
    throw new Error('WeChat window not found')
  }
  
  return wechatWin.getBounds()
}

export function calcChatArea(winRect: BBox): BBox {
  const LEFT_SIDEBAR_WIDTH = 350  // 微信左侧边栏固定宽度
  
  return {
    x: winRect.x + LEFT_SIDEBAR_WIDTH,
    y: winRect.y,
    width: winRect.width - LEFT_SIDEBAR_WIDTH,
    height: winRect.height
  }
}

export function getInputBoxCoords(chatArea: BBox): [number, number] {
  return [
    chatArea.x + chatArea.width * 0.5,   // 横向居中
    chatArea.y + chatArea.height * 0.85  // 垂直靠下
  ]
}
```

### 剪贴板保护 (input-utils.ts)

```typescript
import { clipboard } from 'electron'
import * as robotjs from 'robotjs'

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

export async function safePaste(text: string): Promise<void> {
  const originalContent = clipboard.readText()
  
  try {
    clipboard.writeText(text)
    await sleep(100)  // 剪贴板同步时间
    
    const modifier = process.platform === 'darwin' ? ['command'] : ['control']
    robotjs.keyTap('v', modifier)
    await sleep(150)  // 等待字符灌入输入框
    
    robotjs.keyTap('enter')
    await sleep(300)  // 等待微信发送并渲染
  } catch (error) {
    console.error('safePaste 执行异常:', error)
    throw error
  } finally {
    if (originalContent && originalContent.trim() !== "") {
      clipboard.writeText(originalContent)
    } else {
      clipboard.clear()
    }
  }
}
```

### HTTP 端点 (skill-server.ts 扩展)

在现有 `/skill/start`, `/skill/pause`, `/skill/status` 基础上新增：

```typescript
async function handleSendMessage(
  req: http.IncomingMessage,
  res: http.ServerResponse
): Promise<void> {
  if (!controller) {
    jsonResponse(res, 503, { ok: false, error: 'controller_unavailable' })
    return
  }
  
  if (skillOperationLock) {
    jsonResponse(res, 409, { ok: false, error: 'operation_in_progress' })
    return
  }
  
  const body = await readBody(req)
  const { contact, message } = JSON.parse(body)
  
  if (!contact || !message) {
    jsonResponse(res, 400, { ok: false, error: 'missing_contact_or_message' })
    return
  }
  
  skillOperationLock = true
  const startTime = Date.now()
  
  try {
    await controller.sendMessage(contact, message)
    jsonResponse(res, 200, {
      ok: true,
      elapsed_ms: Date.now() - startTime
    })
  } catch (error: any) {
    jsonResponse(res, 500, {
      ok: false,
      error: error.message || 'send_failed'
    })
  } finally {
    skillOperationLock = false
  }
}
```

## 性能预估

| 步骤 | 耗时 |
|------|------|
| wx-cli 轮询 | ~100ms |
| AI 生成回复 | 1~3s |
| Ctrl+F 搜索 | ~500ms |
| VLM 定位联系人 | 1~2s |
| 点击 + 等待 | ~500ms |
| 输入框定位（硬编码） | 0ms |
| safePaste 发送 | ~550ms |
| **总耗时** | **3~5s** |

**优化效果**：
- 输入框硬编码偏移省去第二次 VLM 调用（~1.5s）
- 剪贴板操作已优化延时参数

## 待实现清单

### SightFlow 修改

1. `src/main/skill-server.ts`：新增 `/skill/send-message` 端点
2. `src/core/rpa/window-utils.ts`：新增窗口定位函数
3. `src/core/rpa/input-utils.ts`：新增 `safePaste()` 实现
4. `src/main/index.ts`：扩展 `SkillEngineController` 接口，新增 `sendMessage()` 方法

### Python 粘合层（新建）

1. 主入口：轮询 wx-cli + 调用 SightFlow API
2. wxid 翻译模块：从 wx-cli contacts 构建映射表
3. 消息队列 + 排他锁实现
4. AI 调用模块（豆包/Claude API）

## 风险与注意事项

1. **剪贴板冲突**：真人和 Bot 共用剪贴板可能串数据。建议在专用机器运行。
2. **联系人同名冲突**：用备注名匹配时，多个同名联系人可能定位错误。
3. **群聊成员名**：群聊中 AI 回复时需区分是回复群还是回复某个成员。
4. **微信窗口最小化**：窗口最小化时坐标计算可能失效，需确保窗口可见。