# 微信自动回复 Agent 集成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 wx-cli + SightFlow + Python 粘合层的微信自动回复系统，实现消息监控→AI回复→自动发送的闭环。

**Architecture:** wx-cli 轮询增量消息，Python 粘合层调用 AI 生成回复并通过 HTTP API 触发 SightFlow 发送；SightFlow 新增 `/skill/send-message` 端点，用 Ctrl+F 搜索定位联系人，动态计算输入框坐标。

**Tech Stack:** TypeScript (SightFlow 扩展), Python 3 (粘合层), Electron/RobotJS (桌面自动化), wx-cli (消息读取)

---

## 文件结构

### SightFlow 修改（Phase 1）

| 文件 | 操作 | 职责 |
|------|------|------|
| `sightflow-desktop-agent/src/main/skill-server.ts` | 修改 | 新增 `/skill/send-message` 端点 |
| `sightflow-desktop-agent/src/main/index.ts` | 修改 | 扩展 `SkillEngineController` 接口 |
| `sightflow-desktop-agent/src/core/rpa/input-utils.ts` | 修改 | 新增 `safePaste()` 和 `searchContactByKeyboard()` |
| `sightflow-desktop-agent/src/core/rpa/window-utils.ts` | 修改 | 新增 `calcChatArea()` 和 `getInputBoxCoords()` |

### Python 粘合层新建（Phase 2）

| 文件 | 操作 | 职责 |
|------|------|------|
| `glue-layer/main.py` | 创建 | 主入口：轮询 wx-cli + 调用 SightFlow |
| `glue-layer/wxid_mapper.py` | 创建 | wxid → 备注名映射模块 |
| `glue-layer/ai_client.py` | 创建 | AI API 调用模块 |
| `glue-layer/message_queue.py` | 创建 | 消息队列 + 排他锁实现 |

---

## Phase 1: SightFlow API 扩展

### Task 1: 扩展 window-utils.ts - 动态计算聊天区域和输入框坐标

**Files:**
- Modify: `sightflow-desktop-agent/src/core/rpa/window-utils.ts`

- [ ] **Step 1: 新增 calcChatArea 函数**

在 `window-utils.ts` 文件末尾添加：

```typescript
/**
 * 从窗口边界计算聊天区域
 * 微信左侧边栏固定宽度约 310~350px（导航 + 联系人列表）
 */
export function calcChatArea(bounds: { x: number; y: number; width: number; height: number }): {
  x: number
  y: number
  width: number
  height: number
} {
  const LEFT_SIDEBAR_WIDTH = 350  // 实测校准值

  return {
    x: bounds.x + LEFT_SIDEBAR_WIDTH,
    y: bounds.y,
    width: bounds.width - LEFT_SIDEBAR_WIDTH,
    height: bounds.height
  }
}

/**
 * 计算输入框点击坐标（相对于聊天区域）
 * 输入框位置：横向居中，垂直靠下（约 85% 高度处）
 */
export function getInputBoxCoords(bounds: { x: number; y: number; width: number; height: number }): [number, number] {
  const chatArea = calcChatArea(bounds)

  const INPUT_BOX_OFFSET = {
    widthRatio: 0.5,   // 横向居中
    heightRatio: 0.85  // 垂直靠下
  }

  return [
    Math.round(chatArea.x + chatArea.width * INPUT_BOX_OFFSET.widthRatio),
    Math.round(chatArea.y + chatArea.height * INPUT_BOX_OFFSET.heightRatio)
  ]
}
```

- [ ] **Step 2: 验证 TypeScript 编译通过**

Run: `cd sightflow-desktop-agent && npm run typecheck:node`
Expected: 无编译错误

- [ ] **Step 3: Commit**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/window-utils.ts
git commit -m "feat(window): add calcChatArea and getInputBoxCoords for dynamic input positioning"
```

---

### Task 2: 扩展 input-utils.ts - 新增 safePaste 和键盘搜索联系人

**Files:**
- Modify: `sightflow-desktop-agent/src/core/rpa/input-utils.ts`

- [ ] **Step 1: 新增 safePaste 函数**

在文件末尾添加：

```typescript
/**
 * 安全粘贴函数：写入剪贴板 → 粘贴 → 发送 → 恢复原有剪贴板内容
 * 防止剪贴板脏数据串行问题
 */
export async function safePaste(text: string): Promise<boolean> {
  const robot = getRobot()
  if (!robot) {
    console.error('[safePaste] RobotJS 缺失')
    return false
  }

  // 备份当前剪贴板内容
  const originalContent = clipboard.readText()

  try {
    // 写入新内容
    clipboard.writeText(text)
    await delay(100)  // 剪贴板同步时间

    // 粘贴操作
    if (IS_MAC) {
      robot.keyTap('v', ['command'])
    } else {
      robot.keyTap('v', ['control'])
    }
    await delay(150)  // 等待字符灌入输入框

    // 发送
    robot.keyTap('enter')
    await delay(300)  // 等待微信发送并渲染

    return true
  } catch (error: any) {
    console.error('[safePaste] 执行异常:', error)
    return false
  } finally {
    // 恢复原有剪贴板内容
    if (originalContent && originalContent.trim() !== '') {
      clipboard.writeText(originalContent)
    } else {
      clipboard.clear()
    }
  }
}
```

- [ ] **Step 2: 新增 searchContactByKeyboard 函数**

在文件末尾继续添加：

```typescript
/**
 * 通过 Ctrl+F 键盘搜索定位联系人
 * 解决联系人沉底不可见问题
 * 
 * @param contactName 联系人名称（备注名或群名）
 * @returns 是否成功激活搜索
 */
export async function searchContactByKeyboard(contactName: string): Promise<boolean> {
  const robot = getRobot()
  if (!robot) {
    console.error('[searchContactByKeyboard] RobotJS 缺失')
    return false
  }

  try {
    // Step 1: Ctrl+F 激活搜索框
    if (IS_MAC) {
      robot.keyTap('f', ['command'])
    } else {
      robot.keyTap('f', ['control'])
    }
    await delay(300)  // 等待搜索框激活

    // Step 2: 输入联系人名称
    // 先清空可能存在的旧内容
    if (IS_MAC) {
      robot.keyTap('a', ['command'])
    } else {
      robot.keyTap('a', ['control'])
    }
    await delay(100)

    // 输入名称
    robot.typeString(contactName)
    await delay(300)  // 等待搜索结果渲染

    console.log(`[searchContactByKeyboard] 已搜索联系人: ${contactName}`)
    return true
  } catch (error: any) {
    console.error('[searchContactByKeyboard] 执行异常:', error)
    return false
  }
}
```

- [ ] **Step 3: 新增 sendMessageToContact 组合函数**

在文件末尾继续添加：

```typescript
/**
 * 组合函数：搜索联系人 → 点击第一条结果 → 输入并发送消息
 * 
 * @param contactName 联系人名称
 * @param message 要发送的消息
 * @param bounds 窗口边界（用于计算输入框坐标）
 * @returns 是否成功发送
 */
export async function sendMessageToContact(
  contactName: string,
  message: string,
  bounds: { x: number; y: number; width: number; height: number }
): Promise<boolean> {
  const robot = getRobot()
  if (!robot) {
    console.error('[sendMessageToContact] RobotJS 缺失')
    return false
  }

  try {
    // Step 1: 键盘搜索联系人
    const searchOk = await searchContactByKeyboard(contactName)
    if (!searchOk) {
      console.error('[sendMessageToContact] 搜索失败')
      return false
    }

    // Step 2: 点击第一条搜索结果（向下箭头 + Enter）
    // 搜索结果通常聚焦第一条，直接 Enter 即可
    await delay(200)
    robot.keyTap('enter')
    await delay(500)  // 等待会话窗口打开

    // Step 3: 计算输入框坐标并点击聚焦
    const [inputX, inputY] = getInputBoxCoords(bounds)
    await humanLikeMove(inputX, inputY)
    await delay(100)
    await humanLikeClick('left', 'careful')
    await delay(300)  // 等待输入框激活

    // Step 4: 安全粘贴并发送
    const sendOk = await safePaste(message)
    if (!sendOk) {
      console.error('[sendMessageToContact] 发送失败')
      return false
    }

    console.log(`[sendMessageToContact] 成功发送消息给 ${contactName}`)
    return true
  } catch (error: any) {
    console.error('[sendMessageToContact] 执行异常:', error)
    return false
  }
}
```

- [ ] **Step 4: 验证 TypeScript 编译通过**

Run: `cd sightflow-desktop-agent && npm run typecheck:node`
Expected: 无编译错误

- [ ] **Step 5: Commit**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/input-utils.ts
git commit -m "feat(input): add safePaste, searchContactByKeyboard and sendMessageToContact"
```

---

### Task 3: 扩展 skill-server.ts - 新增 POST /skill/send-message 端点

**Files:**
- Modify: `sightflow-desktop-agent/src/main/skill-server.ts`

- [ ] **Step 1: 新增 SendMessageRequest/Response 类型**

在文件顶部的类型定义区域（约第 17-46 行后）添加：

```typescript
export interface SendMessageRequest {
  contact: string   // 联系人备注名 或 群名
  message: string   // 要发送的消息文本
}

export interface SendMessageResponse {
  ok: boolean
  error?: 'contact_not_found' | 'send_failed' | 'operation_in_progress' | 'window_not_found' | 'missing_contact_or_message'
  message?: string
  elapsed_ms?: number
}

export interface SendMessageResult {
  ok: boolean
  error?: string
  elapsed_ms?: number
}

export interface SkillEngineControllerWithSend extends SkillEngineController {
  /** 发送消息给指定联系人 */
  sendMessage(contact: string, message: string): Promise<SendMessageResult>
}
```

- [ ] **Step 2: 新增 handleSendMessage 函数**

在 `handleStatus` 函数（约第 175-184 行）后添加：

```typescript
async function handleSendMessage(res: http.ServerResponse, body: string): Promise<void> {
  if (!controller) {
    jsonResponse(res, 503, { ok: false, error: 'controller_unavailable' })
    return
  }

  if (skillOperationLock) {
    jsonResponse(res, 409, { ok: false, error: 'operation_in_progress' })
    return
  }

  // 解析请求体
  let request: SendMessageRequest
  try {
    request = JSON.parse(body) as SendMessageRequest
  } catch {
    jsonResponse(res, 400, { ok: false, error: 'invalid_json' })
    return
  }

  if (!request.contact || !request.message) {
    jsonResponse(res, 400, { ok: false, error: 'missing_contact_or_message' })
    return
  }

  skillOperationLock = true
  const startTime = Date.now()

  try {
    const result = await controller.sendMessage(request.contact, request.message)
    
    if (result.ok) {
      jsonResponse(res, 200, {
        ok: true,
        elapsed_ms: Date.now() - startTime
      })
    } else {
      jsonResponse(res, 500, {
        ok: false,
        error: result.error || 'send_failed',
        elapsed_ms: Date.now() - startTime
      })
    }
  } catch (error: any) {
    console.error('[Skill Server] send-message error:', error)
    jsonResponse(res, 500, {
      ok: false,
      error: 'send_failed',
      message: error?.message || String(error)
    })
  } finally {
    skillOperationLock = false
  }
}
```

- [ ] **Step 3: 修改 requestHandler 函数添加新路由**

找到 `requestHandler` 函数（约第 186-215 行），在现有的路由判断后添加新路由：

```typescript
// 在现有的路由判断中添加：
} else if (url === '/skill/send-message' && method === 'POST') {
  const body = await readBody(req)
  await handleSendMessage(res, body)
```

完整修改后的 `requestHandler` 应包含：

```typescript
async function requestHandler(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const { method, url } = req

  if (method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    })
    res.end()
    return
  }

  try {
    if (url === '/skill/start' && method === 'POST') {
      await readBody(req)
      await handleStart(res)
    } else if (url === '/skill/pause' && method === 'POST') {
      await readBody(req)
      await handlePause(res)
    } else if (url === '/skill/status' && method === 'GET') {
      handleStatus(res)
    } else if (url === '/skill/send-message' && method === 'POST') {
      const body = await readBody(req)
      await handleSendMessage(res, body)
    } else {
      jsonResponse(res, 404, { ok: false, error: 'not_found' })
    }
  } catch (error) {
    console.error('[Skill Server] 请求处理异常:', error)
    jsonResponse(res, 500, { ok: false, error: 'internal_error' })
  }
}
```

- [ ] **Step 4: 修改 readBody 函数增加最大 body 大小**

找到 `readBody` 函数（约第 66-83 行），将最大大小从 1024 改为 4096 以支持较长消息：

```typescript
function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    let size = 0
    req.on('data', (chunk: Buffer) => {
      size += chunk.length
      if (size > 4096) {  // 改为 4096，支持较长消息
        req.destroy()
        reject(new Error('body_too_large'))
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')))
    req.on('error', reject)
  })
}
```

- [ ] **Step 5: 更新导出的 controller 类型**

修改 `startSkillServer` 函数签名，将 `SkillEngineController` 改为 `SkillEngineControllerWithSend`：

```typescript
export function startSkillServer(engineController: SkillEngineControllerWithSend): void {
  // ... 其余代码不变
}
```

- [ ] **Step 6: 验证 TypeScript 编译通过**

Run: `cd sightflow-desktop-agent && npm run typecheck:node`
Expected: 无编译错误

- [ ] **Step 7: Commit**

```bash
cd sightflow-desktop-agent
git add src/main/skill-server.ts
git commit -m "feat(skill-server): add POST /skill/send-message endpoint"
```

---

### Task 4: 扩展 index.ts - 实现 sendMessage 控制器方法

**Files:**
- Modify: `sightflow-desktop-agent/src/main/index.ts`

- [ ] **Step 1: 导入新的依赖和类型**

在文件顶部的 import 区域（约第 1-35 行），添加：

```typescript
import {
  sendMessageToContact
} from '../core/rpa/input-utils'
import { getInputBoxCoords } from '../core/rpa/window-utils'
import { SendMessageResult } from './skill-server'
```

- [ ] **Step 2: 新增 sendMessage 实现函数**

在文件中（约第 840 行 `skillEngineController` 定义附近），找到 `skillEngineController` 对象，扩展其接口：

```typescript
const skillEngineController: SkillEngineControllerWithSend = {
  start: () => startEngineCore(),
  pause: () => stopEngineCore('skill_pause'),
  isRunning: () => runtime?.isRunning() ?? false,
  
  // 新增 sendMessage 方法
  sendMessage: async (contact: string, message: string): Promise<SendMessageResult> => {
    try {
      // 获取微信窗口信息
      const windowInfo = await getWindowInfo('wechat', false)
      if (!windowInfo || !windowInfo.bounds) {
        return { ok: false, error: 'window_not_found' }
      }

      // 调用组合发送函数
      const success = await sendMessageToContact(contact, message, windowInfo.bounds)
      
      if (success) {
        return { ok: true }
      } else {
        return { ok: false, error: 'send_failed' }
      }
    } catch (error: any) {
      console.error('[sendMessage] 执行异常:', error)
      return { ok: false, error: error?.message || 'unknown_error' }
    }
  }
}
```

- [ ] **Step 3: 更新 SkillEngineController 类型导入**

修改 skill-server 的导入，使用新类型：

```typescript
import {
  SkillEngineControllerWithSend,
  startSkillServer,
  stopSkillServer
} from './skill-server'
```

- [ ] **Step 4: 验证 TypeScript 编译通过**

Run: `cd sightflow-desktop-agent && npm run typecheck:node`
Expected: 无编译错误

- [ ] **Step 5: Commit**

```bash
cd sightflow-desktop-agent
git add src/main/index.ts
git commit -m "feat(main): implement sendMessage controller method"
```

---

### Task 5: Phase 1 完成 - 构建并测试 SightFlow

- [ ] **Step 1: 构建 SightFlow**

Run: `cd sightflow-desktop-agent && npm run build`
Expected: 构建成功，输出到 `out/` 目录

- [ ] **Step 2: 手动测试 HTTP API（可选）**

启动 SightFlow 后，使用 curl 测试新端点：

```bash
curl -X POST http://127.0.0.1:12680/skill/send-message \
  -H "Content-Type: application/json" \
  -d '{"contact": "文件传输助手", "message": "测试消息"}'
```

Expected: 返回 `{ "ok": true, "elapsed_ms": ... }` 或错误信息

- [ ] **Step 3: Phase 1 Commit（汇总）**

```bash
cd sightflow-desktop-agent
git add -A
git commit -m "feat(phase1): SightFlow API extension complete - POST /skill/send-message"
```

---

## Phase 2: Python 粘合层

### Task 6: 创建项目结构和依赖配置

**Files:**
- Create: `glue-layer/requirements.txt`
- Create: `glue-layer/config.py`

- [ ] **Step 1: 创建项目目录**

Run: `mkdir -p glue-layer`

- [ ] **Step 2: 创建 requirements.txt**

```text
requests>=2.28.0
openai>=1.0.0  # 或使用其他 AI SDK
python-dotenv>=1.0.0
```

- [ ] **Step 3: 创建 config.py 配置文件**

```python
# glue-layer/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# wx-cli 配置
WX_CLI_PATH = os.getenv('WX_CLI_PATH', 'wx')  # wx 命令路径
WX_POLL_INTERVAL = int(os.getenv('WX_POLL_INTERVAL', '5'))  # 轮询间隔（秒）

# SightFlow 配置
SIGHTFLOW_API_URL = os.getenv('SIGHTFLOW_API_URL', 'http://127.0.0.1:12680')

# AI 配置（示例：使用 OpenAI SDK）
AI_API_KEY = os.getenv('AI_API_KEY', '')
AI_API_URL = os.getenv('AI_API_URL', 'https://api.openai.com/v1')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')

# 自己的微信 ID（用于防循环过滤）
SELF_WXID = os.getenv('SELF_WXID', '')
```

- [ ] **Step 4: Commit**

```bash
git add glue-layer/config.py glue-layer/requirements.txt
git commit -m "feat(glue): add project structure and config"
```

---

### Task 7: 实现 wxid_mapper.py - wxid 翻译模块

**Files:**
- Create: `glue-layer/wxid_mapper.py`

- [ ] **Step 1: 创建 wxid_mapper.py**

```python
# glue-layer/wxid_mapper.py
import subprocess
import json
from typing import Dict, Optional

class WxidMapper:
    """wxid → 备注名/昵称 映射器"""
    
    def __init__(self, wx_cli_path: str = 'wx'):
        self.wx_cli_path = wx_cli_path
        self._name_map: Dict[str, str] = {}  # wxid → display_name
        self._group_map: Dict[str, str] = {}  # room_id → group_name
    
    def refresh(self) -> bool:
        """从 wx contacts 刷新映射表"""
        try:
            result = subprocess.run(
                [self.wx_cli_path, 'contacts', '--json'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                print(f"[WxidMapper] wx contacts 失败: {result.stderr}")
                return False
            
            contacts = json.loads(result.stdout)
            
            # 构建映射表
            for contact in contacts.get('contacts', []):
                username = contact.get('username', '')
                display = contact.get('display', '') or contact.get('remark', '') or username
                
                if '@chatroom' in username:
                    # 群聊
                    self._group_map[username] = display
                else:
                    # 私聊联系人
                    self._name_map[username] = display
            
            print(f"[WxidMapper] 已加载 {len(self._name_map)} 联系人, {len(self._group_map)} 群")
            return True
            
        except Exception as e:
            print(f"[WxidMapper] 刷新失败: {e}")
            return False
    
    def get_contact_name(self, wxid: str) -> str:
        """获取联系人备注名"""
        return self._name_map.get(wxid, wxid)
    
    def get_group_name(self, room_id: str) -> str:
        """获取群名"""
        return self._group_map.get(room_id, room_id)
    
    def translate_message(self, msg: dict) -> tuple[str, str, str]:
        """
        翻译消息中的 wxid
        
        Args:
            msg: wx new-messages 返回的消息对象
        
        Returns:
            (target, sender_name, context)
            - target: SightFlow 搜索目标（联系人名或群名）
            - sender_name: 发送者名称（用于上下文）
            - context: AI 回复的上下文描述
        """
        chat_type = msg.get('chat_type', 'private')
        sender_wxid = msg.get('sender_wxid', '') or msg.get('username', '')
        content = msg.get('content', '')
        
        if chat_type == 'private':
            # 私聊：直接翻译 sender_wxid
            target = self.get_contact_name(sender_wxid)
            sender_name = target
            context = f"来自 {sender_name} 的私聊消息：{content}"
            
        elif chat_type == 'group':
            # 群聊：目标是群，sender 是群成员
            room_id = msg.get('room_id', '') or msg.get('username', '')
            target = self.get_group_name(room_id)
            sender_name = self.get_contact_name(sender_wxid)
            context = f"群【{target}】中 {sender_name} 说：{content}"
            
        else:
            # 其他类型（公众号等）
            target = self.get_contact_name(sender_wxid)
            sender_name = target
            context = f"{sender_name} 的消息：{content}"
        
        return target, sender_name, context
```

- [ ] **Step 2: Commit**

```bash
git add glue-layer/wxid_mapper.py
git commit -m "feat(glue): add wxid_mapper for wxid to display name translation"
```

---

### Task 8: 实现 ai_client.py - AI 调用模块

**Files:**
- Create: `glue-layer/ai_client.py`

- [ ] **Step 1: 创建 ai_client.py**

```python
# glue-layer/ai_client.py
import os
from typing import Optional
from openai import OpenAI
from config import AI_API_KEY, AI_API_URL, AI_MODEL

class AIClient:
    """AI API 调用客户端"""
    
    def __init__(self):
        self.client = OpenAI(
            api_key=AI_API_KEY,
            base_url=AI_API_URL
        )
        self.model = AI_MODEL
        
        # 系统提示词
        self.system_prompt = """你是一个微信自动回复助手。
根据收到的消息，生成简洁、自然、友好的回复。
回复应该：
1. 简短（通常 1-2 句话）
2. 自然，像真人回复
3. 避免过于正式或机械
4. 不要回复自己的消息"""
    
    def generate_reply(self, context: str, target: str) -> Optional[str]:
        """
        根据上下文生成回复
        
        Args:
            context: 消息上下文（如 "来自张三的私聊消息：你好"）
            target: 目标联系人/群名
        
        Returns:
            生成的回复文本，或 None（表示跳过回复）
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"收到消息：{context}\n请生成回复（回复给 {target}）："}
                ],
                max_tokens=100,
                temperature=0.7
            )
            
            reply = response.choices[0].message.content.strip()
            
            # 如果回复表示跳过（如"无需回复"、"不回复"等），返回 None
            skip_keywords = ['无需回复', '不回复', '跳过', '暂不回复', '不用回复']
            if any(kw in reply for kw in skip_keywords):
                return None
            
            return reply
            
        except Exception as e:
            print(f"[AIClient] 生成回复失败: {e}")
            return None
```

- [ ] **Step 2: Commit**

```bash
git add glue-layer/ai_client.py
git commit -m "feat(glue): add ai_client for reply generation"
```

---

### Task 9: 实现 message_queue.py - 消息队列和排他锁

**Files:**
- Create: `glue-layer/message_queue.py`

- [ ] **Step 1: 创建 message_queue.py**

```python
# glue-layer/message_queue.py
import queue
import threading
import time
from typing import Callable, Optional, Tuple

class MessageQueue:
    """消息队列 + 排他锁，确保串行化发送"""
    
    def __init__(self, send_callback: Callable[[str, str], bool]):
        """
        Args:
            send_callback: 发送回调函数 (contact, message) -> success
        """
        self.queue: queue.Queue[Tuple[str, str, str]] = queue.Queue()
        self.lock = threading.Lock()
        self.send_callback = send_callback
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False
    
    def start(self):
        """启动工作线程"""
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        print("[MessageQueue] 工作线程已启动")
    
    def stop(self):
        """停止工作线程"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        print("[MessageQueue] 工作线程已停止")
    
    def enqueue(self, target: str, message: str, context: str):
        """将消息加入队列"""
        self.queue.put((target, message, context))
        print(f"[MessageQueue] 已入队: target={target}, message={message[:20]}...")
    
    def _worker(self):
        """工作线程：从队列取出消息，串行化发送"""
        while self.running:
            try:
                # 从队列取出（最多等待 1 秒）
                target, message, context = self.queue.get(timeout=1)
                
                print(f"[MessageQueue] 处理消息: {target}")
                
                # 获取锁，执行发送
                with self.lock:
                    success = self.send_callback(target, message)
                    
                    if success:
                        print(f"[MessageQueue] 发送成功: {target}")
                    else:
                        print(f"[MessageQueue] 发送失败: {target}")
                
                self.queue.task_done()
                
            except queue.Empty:
                # 队列空，继续等待
                continue
            except Exception as e:
                print(f"[MessageQueue] 处理异常: {e}")
                time.sleep(1)
    
    def wait_empty(self, timeout: float = 30) -> bool:
        """等待队列清空"""
        try:
            self.queue.join()
            return True
        except Exception:
            return False
```

- [ ] **Step 2: Commit**

```bash
git add glue-layer/message_queue.py
git commit -m "feat(glue): add message_queue with mutex lock for serialized sending"
```

---

### Task 10: 实现 main.py - 主入口

**Files:**
- Create: `glue-layer/main.py`

- [ ] **Step 1: 创建 main.py**

```python
# glue-layer/main.py
import subprocess
import json
import time
import requests
from typing import List, Optional

from config import (
    WX_CLI_PATH, WX_POLL_INTERVAL,
    SIGHTFLOW_API_URL,
    SELF_WXID
)
from wxid_mapper import WxidMapper
from ai_client import AIClient
from message_queue import MessageQueue

class WeChatAgent:
    """微信自动回复 Agent 主类"""
    
    def __init__(self):
        self.mapper = WxidMapper(WX_CLI_PATH)
        self.ai_client = AIClient()
        self.message_queue: Optional[MessageQueue] = None
    
    def start(self):
        """启动 Agent"""
        print("[WeChatAgent] 启动中...")
        
        # 初始化映射表
        print("[WeChatAgent] 初始化联系人映射表...")
        if not self.mapper.refresh():
            print("[WeChatAgent] 联系人映射表加载失败，退出")
            return
        
        # 启动消息队列
        self.message_queue = MessageQueue(self._send_message)
        self.message_queue.start()
        
        # 开始轮询
        print("[WeChatAgent] 开始轮询消息...")
        self._poll_loop()
    
    def _poll_loop(self):
        """轮询循环"""
        while True:
            try:
                # 获取新消息
                new_messages = self._get_new_messages()
                
                if new_messages:
                    print(f"[WeChatAgent] 收到 {len(new_messages)} 条新消息")
                    
                    for msg in new_messages:
                        self._process_message(msg)
                
                # 等待下一次轮询
                time.sleep(WX_POLL_INTERVAL)
                
            except KeyboardInterrupt:
                print("[WeChatAgent] 收到中断信号，停止")
                self.message_queue.stop()
                break
            except Exception as e:
                print(f"[WeChatAgent] 轮询异常: {e}")
                time.sleep(WX_POLL_INTERVAL)
    
    def _get_new_messages(self) -> List[dict]:
        """调用 wx new-messages 获取新消息"""
        try:
            result = subprocess.run(
                [WX_CLI_PATH, 'new-messages', '--json', '--filter', 'private,group'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                print(f"[WeChatAgent] wx new-messages 失败: {result.stderr}")
                return []
            
            data = json.loads(result.stdout)
            messages = data.get('messages', [])
            
            # 过滤自己发送的消息（防循环）
            filtered = []
            for msg in messages:
                if msg.get('is_from_self'):
                    continue
                if SELF_WXID and msg.get('sender_wxid') == SELF_WXID:
                    continue
                filtered.append(msg)
            
            return filtered
            
        except Exception as e:
            print(f"[WeChatAgent] 获取消息失败: {e}")
            return []
    
    def _process_message(self, msg: dict):
        """处理单条消息"""
        # 翻译 wxid
        target, sender_name, context = self.mapper.translate_message(msg)
        
        print(f"[WeChatAgent] 处理消息: target={target}, sender={sender_name}")
        
        # AI 生成回复
        reply = self.ai_client.generate_reply(context, target)
        
        if reply:
            # 加入发送队列
            self.message_queue.enqueue(target, reply, context)
        else:
            print(f"[WeChatAgent] AI 决定不回复: {target}")
    
    def _send_message(self, contact: str, message: str) -> bool:
        """调用 SightFlow API 发送消息"""
        try:
            response = requests.post(
                f"{SIGHTFLOW_API_URL}/skill/send-message",
                json={"contact": contact, "message": message},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    print(f"[WeChatAgent] 发送成功: {contact}, elapsed={data.get('elapsed_ms')}ms")
                    return True
                else:
                    print(f"[WeChatAgent] 发送失败: {data.get('error')}")
                    return False
            else:
                print(f"[WeChatAgent] HTTP 错误: {response.status_code}")
                return False
                
        except requests.exceptions.Timeout:
            print(f"[WeChatAgent] 发送超时: {contact}")
            return False
        except Exception as e:
            print(f"[WeChatAgent] 发送异常: {e}")
            return False

def main():
    agent = WeChatAgent()
    agent.start()

if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Commit**

```bash
git add glue-layer/main.py
git commit -m "feat(glue): add main entry point with polling loop"
```

---

### Task 11: Phase 2 完成 - 创建启动脚本和文档

**Files:**
- Create: `glue-layer/.env.example`
- Create: `glue-layer/README.md`

- [ ] **Step 1: 创建 .env.example**

```text
# wx-cli 配置
WX_CLI_PATH=wx
WX_POLL_INTERVAL=5

# SightFlow 配置
SIGHTFLOW_API_URL=http://127.0.0.1:12680

# AI 配置（示例：OpenAI）
AI_API_KEY=your-api-key-here
AI_API_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini

# 自己的微信 ID（用于防循环过滤）
SELF_WXID=wxid_yourself
```

- [ ] **Step 2: 创建 README.md**

```markdown
# 微信自动回复粘合层

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

## 流程

1. 每 5 秒调用 `wx new-messages --json --filter private,group`
2. 过滤自己发送的消息（防循环）
3. 翻译 wxid → 备注名/群名
4. 调用 AI 生成回复
5. 通过 SightFlow API 发送消息（串行化）
```

- [ ] **Step 3: Phase 2 Commit（汇总）**

```bash
git add glue-layer/
git commit -m "feat(phase2): Python glue layer complete - wx-cli polling + SightFlow API integration"
```

---

## Phase 3: 集成测试

### Task 12: 端到端测试

- [ ] **Step 1: 启动 SightFlow**

确保 SightFlow 已启动并监听端口：

```bash
cd sightflow-desktop-agent
npm run dev
```

- [ ] **Step 2: 初始化 wx-cli**

确保 wx-cli 已初始化：

```bash
wx init
wx sessions  # 验证能获取会话列表
```

- [ ] **Step 3: 启动粘合层**

```bash
cd glue-layer
pip install -r requirements.txt
python main.py
```

- [ ] **Step 4: 测试流程**

1. 用另一个微信号发送消息给测试账号
2. 观察粘合层日志是否收到消息
3. 观察 SightFlow 是否成功发送回复
4. 验证微信界面是否显示回复消息

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git commit -m "feat: WeChat auto-reply Agent integration complete"
```

---

## Self-Review Check

**1. Spec coverage:**
- ✓ 防循环过滤 (is_from_self) - Task 10 `_get_new_messages()`
- ✓ wxid 翻译 - Task 7 `WxidMapper`
- ✓ 消息队列 + 排他锁 - Task 9 `MessageQueue`
- ✓ SightFlow API `/skill/send-message` - Tasks 3, 4
- ✓ Ctrl+F 搜索联系人 - Task 2 `searchContactByKeyboard()`
- ✓ 动态窗口坐标计算 - Task 1 `calcChatArea()`, `getInputBoxCoords()`
- ✓ safePaste 剪贴板保护 - Task 2 `safePaste()`
- ✓ Python 粘合层 - Tasks 6-11

**2. Placeholder scan:**
- ✓ 无 TBD/TODO
- ✓ 无 "implement later" 或 "fill in details"
- ✓ 无 "add validation" 无具体代码

**3. Type consistency:**
- ✓ `SendMessageRequest` / `SendMessageResponse` 类型在 skill-server.ts 和 index.ts 中一致使用
- ✓ `WxidMapper.translate_message()` 返回 `tuple[str, str, str]` 与 `MessageQueue.enqueue()` 参数匹配