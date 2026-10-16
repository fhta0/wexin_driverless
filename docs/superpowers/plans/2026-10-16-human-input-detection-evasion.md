# 拟人化输入（防风控） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把微信自动回复的"粘贴整段"改成"逐字打字 + Box-Muller 正态延迟 + 三次贝塞尔曲线（二次 ease-in-out 缓动）"，对齐竞品 `shiflow-agent` 的拟人化水平，显著降低风控等级。

**Architecture:** 抽出一个纯函数模块 `src/core/rpa/human-input/`（delay / bezier / typewriter 三个独立单元），由 `vitest` 跑算法单测；`input-utils.ts` 现有 `humanLikeMove` 和 `sendReplyByCoordsAction` 改为调用新模块。所有 robotjs 调用通过依赖注入或 `getRobot()` mock，单测不依赖真实鼠标键盘。

**Tech Stack:** TypeScript + vitest + electron-vite 项目（已有）。无新运行时依赖。

**Spec:** `docs/superpowers/specs/2026-10-16-human-input-detection-evasion.md`

## Global Constraints

- 不引入新的运行时 npm 依赖（`vitest` 仅作为 devDependency）
- 不修改 `safePaste`（line 473）和 `searchContactByKeyboard`（line 526），本次仅重构 `humanLikeMove` 和 `sendReplyByCoordsAction`
- 中文 `typeString` 在 Windows 下不可用（CLAUDE.md 已记录），typewriter 必须用「单字符剪贴板粘贴」策略而非 `robot.typeString`
- `humanLikeTypeIntoFocusedField` 必须备份并在 finally 中恢复用户剪贴板，避免污染
- `\n` 必须映射为 `Shift+Enter`（微信输入框内换行），而非 `Enter`（直接发送）
- 所有概率/随机函数接受可选 `random?: () => number` 注入，便于测试用确定性 fake
- 提交遵循 conventional commits（`feat(...)`, `chore(...)`, `test(...)`），scope 用 `rpa`
- 实施时如 `input-utils.ts` 行号漂移，必须用 `grep -n clipboard.writeText` 重新定位粘贴段（在 `sendReplyByCoordsAction` 函数内），不要硬抄行号

## Review Focus

下列输入是 spec 暗示但默认 happy-path 测试不覆盖的，每个任务里会加显式测试：

1. **零宽文本** 给 `typeStringWithDelay` —— 应立刻返回 `{completed: true, charactersTyped: 0}`，不应调用任何 `typeChar` / `keyTap`。Task 3。
2. **CJK + emoji** 必须按 code point 切分（`Array.from("你👍好")` 得 3 个），不能用 `String.split('')`（会把 `👍` 拆成 2 个代理项）。Task 3。
3. **`\n` 在文本中间** —— 必须触发 `keyTap('enter', ['shift'])` 一次，且**不**计入 typeChar 调用。Task 3。
4. **取消位 `isCancelled()` 为 true** —— 必须立即停止当前字符循环，返回 `{completed: false, charactersTyped: <已打字符数>}`。Task 3。
5. **`generateBezierPath` 起点 = 终点** —— 距离 < 1 时返回 `{points: [], finalX, finalY}`（直接到位），不产生 NaN / 0 步除零。Task 2。
6. **`typeChar` 抛错** —— `typeStringWithDelay` 必须传播错误（不被静默吞）。Task 3。
7. **`generateIrregularDelay` 在 `baseDelay=1` 极端值** —— 仍返回 ≥ 5（最小值保护）。Task 1。
8. **`generateIrregularDelay` 接受 `random: () => 0`** —— Box-Muller 对 u1=0 敏感，必须有保护，不抛 `Math.log(0) = -Infinity`。Task 1。

---

## File Structure

```
sightflow-desktop-agent/
  package.json                                   ← Modify: + vitest devDep + test script
  vitest.config.ts                               ← Create: 最小配置
  src/core/rpa/human-input/
    index.ts                                     ← Create: barrel
    delay.ts                                     ← Create: generateIrregularDelay
    bezier.ts                                    ← Create: easeInOut, generateBezierPath
    typewriter.ts                                ← Create: typeStringWithDelay
    __tests__/
      delay.test.ts                              ← Create
      bezier.test.ts                             ← Create
      typewriter.test.ts                         ← Create
  src/core/rpa/input-utils.ts                    ← Modify (Task 4a): 重构 humanLikeMove (line 13-77)
                                                  ← Modify (Task 4b): 重构 sendReplyByCoordsAction 内的粘贴段 (line 222-267)
```

---

### Task 1: vitest 基础设施 + Box-Muller 正态延迟

**Files:**
- Modify: `sightflow-desktop-agent/package.json`
- Create: `sightflow-desktop-agent/vitest.config.ts`
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/delay.ts`
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/__tests__/delay.test.ts`
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/index.ts`

**Interfaces:**
- Consumes: 无
- Produces:
  - `generateIrregularDelay(baseDelay: number, random?: () => number): number` —— 返回整数毫秒，最小值 5（长停顿分支最小值 10）。**注意：因长停顿分支与加速段是混合分布，期望均值 ≈ 1.20 × baseDelay、期望 σ ≈ 0.98 × baseDelay**（不是 baseDelay 与 0.4 × baseDelay）。这是竞品算法特性，单测期望值要按 1.20× / 0.98× 写
  - npm script `test` 已注册：`npm test` 跑 vitest

- [ ] **Step 1: 安装 vitest 并加 test script**

在 `sightflow-desktop-agent/package.json` 的 `devDependencies` 加：
```json
"vitest": "^2.1.0"
```

在 `scripts` 加：
```json
"test": "vitest run",
"test:watch": "vitest"
```

`cd sightflow-desktop-agent && npm install`

如果 npm install 因 vitest 版本不兼容当前 Node 而失败，降级到 `"vitest": "^1.6.0"` 并重试。

- [ ] **Step 2: 创建 `vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/__tests__/**/*.test.ts'],
    globals: false
  }
})
```

- [ ] **Step 3: 写失败测试（统计特性 + 边界）**

`sightflow-desktop-agent/src/core/rpa/human-input/__tests__/delay.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { generateIrregularDelay } from '../delay'

function stats(samples: number[]) {
  const mean = samples.reduce((a, b) => a + b, 0) / samples.length
  const variance = samples.reduce((a, b) => a + (b - mean) ** 2, 0) / samples.length
  const longPct = samples.filter((v) => v >= 200).length / samples.length
  return { mean, stddev: Math.sqrt(variance), longPct }
}

describe('generateIrregularDelay', () => {
  it('returns integer >= 5 for any base', () => {
    for (const base of [10, 100, 1000]) {
      for (let i = 0; i < 200; i++) {
        const v = generateIrregularDelay(base)
        expect(Number.isInteger(v)).toBe(true)
        expect(v).toBeGreaterThanOrEqual(5)
      }
    }
  })

  it('clamps to minimum 5 even when baseDelay is tiny', () => {
    for (let i = 0; i < 500; i++) {
      const v = generateIrregularDelay(1)
      expect(v).toBeGreaterThanOrEqual(5)
    }
  })

  it('mean is approximately 1.20 * baseDelay (mixture distribution due to long-pause branch)', () => {
    const samples = Array.from({ length: 10000 }, () => generateIrregularDelay(100))
    const { mean } = stats(samples)
    expect(mean).toBeGreaterThanOrEqual(110)
    expect(mean).toBeLessThanOrEqual(140)
  })

  it('stddev is approximately 0.98 * baseDelay (mixture distribution)', () => {
    const samples = Array.from({ length: 10000 }, () => generateIrregularDelay(100))
    const { stddev } = stats(samples)
    expect(stddev).toBeGreaterThanOrEqual(85)
    expect(stddev).toBeLessThanOrEqual(120)
  })

  it('produces >= 7% long-pause samples (>= 2x baseDelay) at base=100', () => {
    const samples = Array.from({ length: 10000 }, () => generateIrregularDelay(100))
    const { longPct } = stats(samples)
    expect(longPct).toBeGreaterThanOrEqual(0.07)
  })

  it('handles random() returning 0 without -Infinity (Box-Muller edge case)', () => {
    const v = generateIrregularDelay(100, () => 0)
    expect(Number.isInteger(v)).toBe(true)
    expect(Number.isFinite(v)).toBe(true)
    expect(v).toBeGreaterThanOrEqual(5)
  })

  it('handles random() returning values close to 1', () => {
    const v = generateIrregularDelay(100, () => 0.9999999)
    expect(Number.isFinite(v)).toBe(true)
    expect(v).toBeGreaterThanOrEqual(5)
  })

  it('accepts injected random for deterministic tests', () => {
    let idx = 0
    const seq = [0.5, 0.5, 0.99, 0.99, 0.99, 0.99]
    const fake = () => seq[idx++ % seq.length]
    const v = generateIrregularDelay(100, fake)
    expect(Number.isInteger(v)).toBe(true)
  })
})
```

- [ ] **Step 4: 跑测试确认失败**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/delay.test.ts`

Expected: `Cannot find module '../delay'` 或 import 解析失败。

- [ ] **Step 5: 实现 `delay.ts`**

`sightflow-desktop-agent/src/core/rpa/human-input/delay.ts`：

```ts
/**
 * Box-Muller 正态分布 + 10% 长停顿 + 5% 加速段。
 * 对齐竞品 shiflow-agent/mouse-trajectory.js 的 generateIrregularDelay。
 *
 * 注：长停顿分支最小值 10ms（Math.max(10, ...)），加速段和默认分支最小值 5ms。
 * 长停顿和加速段是互斥分支（命中第一个 if 即 return）。
 */
export function generateIrregularDelay(
  baseDelay: number,
  random: () => number = Math.random
): number {
  const u1 = random() || 1e-9
  const u2 = random()
  const z0 = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2)
  const normalDelay = baseDelay + z0 * baseDelay * 0.4

  if (random() < 0.1) {
    const pauseMultiplier = 2 + random() * 3
    return Math.max(10, Math.round(normalDelay * pauseMultiplier))
  }
  if (random() < 0.05) {
    return Math.max(5, Math.round(normalDelay * 0.3))
  }
  return Math.max(5, Math.round(normalDelay))
}
```

注意 `u1 || 1e-9`：`Math.log(0)` 会得 `-Infinity`，必须保护。

- [ ] **Step 6: 加 barrel `index.ts`**

`sightflow-desktop-agent/src/core/rpa/human-input/index.ts`：

```ts
export { generateIrregularDelay } from './delay'
```

- [ ] **Step 7: 跑测试确认通过**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/delay.test.ts`

Expected: 8 passed, 0 failed。

- [ ] **Step 8: 提交**

```bash
cd sightflow-desktop-agent
git add package.json vitest.config.ts src/core/rpa/human-input/
git commit -m "feat(rpa): 加 vitest 基础设施 + Box-Muller 正态延迟

- 新增 vitest devDep 与 npm test
- generateIrregularDelay 用 Box-Muller + 10% 长停顿 + 5% 加速
- 单测覆盖均值/标准差/长停顿比例/边界值/random=0

Spec: docs/superpowers/specs/2026-10-16-human-input-detection-evasion.md"
```

---

### Task 2: 三次贝塞尔轨迹 + 二次 ease-in-out 缓动

**Files:**
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/bezier.ts`
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/__tests__/bezier.test.ts`
- Modify: `sightflow-desktop-agent/src/core/rpa/human-input/index.ts`

**Interfaces:**
- Consumes: `generateIrregularDelay` (Task 1)
- Produces:
  - `interface BezierPoint { x: number; y: number; delay: number }`
  - `interface BezierPath { points: BezierPoint[]; finalX: number; finalY: number }`
  - `interface BezierOptions { minSteps?: number; maxSteps?: number; baseDelay?: number; random?: () => number }`
  - `easeInOut(t: number): number` —— 二次 ease-in-out 缓动（与竞品 mouse-trajectory.js 一致）
  - `generateBezierPath(sx: number, sy: number, tx: number, ty: number, opts?: BezierOptions): BezierPath`
  - 默认 `minSteps=5, maxSteps=15, baseDelay=10`

- [ ] **Step 1: 写失败测试**

`sightflow-desktop-agent/src/core/rpa/human-input/__tests__/bezier.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { easeInOut, generateBezierPath } from '../bezier'

function makeSeqFake(seq: number[]) {
  let i = 0
  return () => seq[i++ % seq.length]
}

describe('easeInOut', () => {
  it('maps 0 → 0, 1 → 1, 0.5 → 0.5', () => {
    expect(easeInOut(0)).toBeCloseTo(0)
    expect(easeInOut(1)).toBeCloseTo(1)
    expect(easeInOut(0.5)).toBeCloseTo(0.5)
  })
  it('is monotonically increasing', () => {
    let prev = -1
    for (let t = 0; t <= 1.0001; t += 0.05) {
      const v = easeInOut(t)
      expect(v).toBeGreaterThanOrEqual(prev)
      prev = v
    }
  })
  it('matches quadratic ease-in-out formula', () => {
    expect(easeInOut(0.25)).toBeCloseTo(2 * 0.25 * 0.25) // 0.125
    expect(easeInOut(0.75)).toBeCloseTo(1 - Math.pow(-2 * 0.75 + 2, 2) / 2) // 0.875
  })
})

describe('generateBezierPath', () => {
  it('returns empty points when start == end (distance < 1)', () => {
    const r = generateBezierPath(100, 200, 100, 200)
    expect(r.points).toEqual([])
    expect(r.finalX).toBe(100)
    expect(r.finalY).toBe(200)
  })

  it('returns last point exactly at target coords', () => {
    const r = generateBezierPath(0, 0, 500, 300)
    expect(r.points.length).toBeGreaterThan(0)
    const last = r.points[r.points.length - 1]
    expect(last.x).toBe(500)
    expect(last.y).toBe(300)
  })

  it('step count respects minSteps/maxSteps', () => {
    const r = generateBezierPath(0, 0, 1000, 800, { minSteps: 8, maxSteps: 12 })
    expect(r.points.length).toBeGreaterThanOrEqual(8)
    expect(r.points.length).toBeLessThanOrEqual(12)
  })

  it('every point has finite coordinates and integer delay >= 5', () => {
    const r = generateBezierPath(50, 50, 600, 400)
    for (const p of r.points) {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
      expect(Number.isInteger(p.delay)).toBe(true)
      expect(p.delay).toBeGreaterThanOrEqual(5)
    }
  })

  it('accepts deterministic fake random and produces same path twice', () => {
    const seq = [0.3, 0.7, 0.1, 0.9, 0.5, 0.5, 0.2, 0.8, 0.4, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5]
    const a = generateBezierPath(0, 0, 200, 200, { random: makeSeqFake([...seq, ...seq]) })
    const b = generateBezierPath(0, 0, 200, 200, { random: makeSeqFake([...seq, ...seq]) })
    expect(a).toEqual(b)
  })

  it('handles negative coordinates', () => {
    const r = generateBezierPath(-100, -100, 100, 100)
    expect(r.points.length).toBeGreaterThan(0)
    const last = r.points[r.points.length - 1]
    expect(last.x).toBe(100)
    expect(last.y).toBe(100)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/bezier.test.ts`

Expected: `Cannot find module '../bezier'`。

- [ ] **Step 3: 实现 `bezier.ts`**

`sightflow-desktop-agent/src/core/rpa/human-input/bezier.ts`：

```ts
import { generateIrregularDelay } from './delay'

export interface BezierPoint {
  x: number
  y: number
  delay: number
}

export interface BezierPath {
  points: BezierPoint[]
  finalX: number
  finalY: number
}

export interface BezierOptions {
  minSteps?: number
  maxSteps?: number
  baseDelay?: number
  random?: () => number
}

/**
 * 二次 ease-in-out 缓动（与竞品 shiflow-agent/mouse-trajectory.js 一致）。
 * 注意：这是缓动函数，不是贝塞尔曲线的次数。贝塞尔曲线本身是三次（cubic）。
 */
export function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

export function generateBezierPath(
  startX: number,
  startY: number,
  targetX: number,
  targetY: number,
  opts: BezierOptions = {}
): BezierPath {
  const {
    minSteps = 5,
    maxSteps = 15,
    baseDelay = 10,
    random = Math.random
  } = opts

  const dx = targetX - startX
  const dy = targetY - startY
  const distance = Math.sqrt(dx * dx + dy * dy)

  if (distance < 1) {
    return { points: [], finalX: targetX, finalY: targetY }
  }

  const steps = Math.min(
    maxSteps,
    Math.max(minSteps, Math.floor(distance / 40) + Math.floor(random() * 3))
  )

  const ctrl1X = startX + dx * random() * 0.5 + (random() - 0.5) * distance * 0.2
  const ctrl1Y = startY + dy * random() * 0.5 + (random() - 0.5) * distance * 0.2
  const ctrl2X = startX + dx * (0.5 + random() * 0.5) + (random() - 0.5) * distance * 0.2
  const ctrl2Y = startY + dy * (0.5 + random() * 0.5) + (random() - 0.5) * distance * 0.2

  const points: BezierPoint[] = []
  for (let i = 1; i <= steps; i++) {
    const t = i / steps
    const easedT = easeInOut(t)
    const invT = 1 - easedT
    const x =
      Math.pow(invT, 3) * startX +
      3 * Math.pow(invT, 2) * easedT * ctrl1X +
      3 * invT * Math.pow(easedT, 2) * ctrl2X +
      Math.pow(easedT, 3) * targetX
    const y =
      Math.pow(invT, 3) * startY +
      3 * Math.pow(invT, 2) * easedT * ctrl1Y +
      3 * invT * Math.pow(easedT, 2) * ctrl2Y +
      Math.pow(easedT, 3) * targetY

    const jitterX = i === steps ? 0 : (random() - 0.5) * 1.5
    const jitterY = i === steps ? 0 : (random() - 0.5) * 1.5
    const delay = generateIrregularDelay(baseDelay, random)

    points.push({
      x: Math.round(x + jitterX),
      y: Math.round(y + jitterY),
      delay: Math.round(delay)
    })
  }
  // 末点修正到精确坐标（覆盖 jitter），保证最后一步落点 = target
  const last = points[points.length - 1]
  last.x = targetX
  last.y = targetY
  return { points, finalX: targetX, finalY: targetY }
}
```

- [ ] **Step 4: 跑测试确认通过**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/bezier.test.ts`

Expected: 7 passed, 0 failed。

- [ ] **Step 5: 加到 barrel**

修改 `src/core/rpa/human-input/index.ts`：

```ts
export { generateIrregularDelay } from './delay'
export { easeInOut, generateBezierPath } from './bezier'
export type { BezierPoint, BezierPath, BezierOptions } from './bezier'
```

- [ ] **Step 6: 提交**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/human-input/
git commit -m "feat(rpa): 三次贝塞尔曲线 + 二次 ease-in-out 缓动

- 路径是三次 cubic Bezier (与竞品一致)
- 缓动函数 easeInOut 是二次 (与竞品 mouse-trajectory.js 一致)
- 起点 == 终点 早退避免除零
- 末点修正到 target 覆盖 jitter
- 7 个 vitest 单测覆盖统计/边界/负坐标"
```

---

### Task 3: 逐字打字 typewriter（依赖注入）

**Files:**
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/typewriter.ts`
- Create: `sightflow-desktop-agent/src/core/rpa/human-input/__tests__/typewriter.test.ts`
- Modify: `sightflow-desktop-agent/src/core/rpa/human-input/index.ts`

**Interfaces:**
- Consumes: 无（独立模块）
- Produces:
  - `interface TypewriterOptions { text: string; minDelayMs?: number; maxDelayMs?: number; isCancelled?: () => boolean; random?: () => number; typeChar: (ch: string) => void | Promise<void>; keyTap: (key: string, modifiers?: string[]) => void | Promise<void>; sleep: (ms: number) => Promise<void> }`
  - `interface TypewriterResult { completed: boolean; charactersTyped: number }`
  - `typeStringWithDelay(options: TypewriterOptions): Promise<TypewriterResult>`
  - 默认 `minDelayMs=35, maxDelayMs=55`
  - `\n` → `keyTap('enter', ['shift'])`，且**计入** charactersTyped（与竞品一致，JSDoc 注明）
  - 其它 → `typeChar(ch)`
  - 使用 `Array.from(text)` 切分（保留 emoji / CJK）
  - `typeChar` 抛错时，错误向上传播（不被吞）

- [ ] **Step 1: 写失败测试**

`sightflow-desktop-agent/src/core/rpa/human-input/__tests__/typewriter.test.ts`：

```ts
import { describe, it, expect } from 'vitest'
import { typeStringWithDelay } from '../typewriter'

function makeDeps() {
  const chars: string[] = []
  const keys: Array<{ key: string; mods?: string[] }> = []
  const sleeps: number[] = []
  return {
    deps: {
      typeChar: (ch: string) => { chars.push(ch) },
      keyTap: (key: string, mods?: string[]) => { keys.push({ key, mods }) },
      sleep: async (ms: number) => { sleeps.push(ms) },
      random: (() => {
        let i = 0
        return () => [0, 0.5, 1, 0.25, 0.75][i++ % 5]
      })()
    },
    chars,
    keys,
    sleeps
  }
}

describe('typeStringWithDelay', () => {
  it('returns {completed:true, charactersTyped:0} for empty text without any calls', async () => {
    const { deps, chars, keys, sleeps } = makeDeps()
    const r = await typeStringWithDelay({ text: '', ...deps })
    expect(r).toEqual({ completed: true, charactersTyped: 0 })
    expect(chars).toEqual([])
    expect(keys).toEqual([])
    expect(sleeps).toEqual([])
  })

  it('splits by code point: emoji stays as one char', async () => {
    const { deps, chars } = makeDeps()
    await typeStringWithDelay({ text: '👍ok', ...deps })
    expect(chars).toEqual(['👍', 'o', 'k'])
  })

  it('handles CJK characters as single chars', async () => {
    const { deps, chars } = makeDeps()
    await typeStringWithDelay({ text: '你好', ...deps })
    expect(chars).toEqual(['你', '好'])
  })

  it('maps \\n to keyTap("enter", ["shift"]) not typeChar', async () => {
    const { deps, chars, keys } = makeDeps()
    await typeStringWithDelay({ text: 'a\nb', ...deps })
    expect(chars).toEqual(['a', 'b'])
    expect(keys).toEqual([{ key: 'enter', mods: ['shift'] }])
  })

  it('counts \\n in charactersTyped (matches competitor behavior)', async () => {
    const { deps } = makeDeps()
    const r = await typeStringWithDelay({ text: 'a\nb', ...deps })
    expect(r.charactersTyped).toBe(3) // a, \n, b
  })

  it('sleeps between every character including the last', async () => {
    const { deps, sleeps } = makeDeps()
    await typeStringWithDelay({ text: 'ab', minDelayMs: 35, maxDelayMs: 55, ...deps })
    expect(sleeps).toHaveLength(2)
    for (const s of sleeps) {
      expect(s).toBeGreaterThanOrEqual(35)
      expect(s).toBeLessThanOrEqual(55)
    }
  })

  it('stops early when isCancelled returns true after first char', async () => {
    let calls = 0
    const { deps, chars } = makeDeps()
    const r = await typeStringWithDelay({
      text: 'abc',
      isCancelled: () => calls++ >= 1,
      ...deps
    })
    expect(r.completed).toBe(false)
    expect(r.charactersTyped).toBe(1)
    expect(chars).toEqual(['a'])
  })

  it('reports charactersTyped on cancellation after newline (newline counts as a typed char)', async () => {
    let calls = 0
    const { deps } = makeDeps()
    const r = await typeStringWithDelay({
      text: '\n\nx',
      isCancelled: () => calls++ >= 2,
      ...deps
    })
    expect(r.completed).toBe(false)
    expect(r.charactersTyped).toBe(2)
  })

  it('handles cancellation during sleep', async () => {
    let cancelled = false
    const r = await typeStringWithDelay({
      text: 'abc',
      isCancelled: () => cancelled,
      typeChar: async () => {},
      keyTap: async () => {},
      sleep: async (ms) => {
        if (ms > 40) cancelled = true // second sleep cancels
      },
      random: () => 0.8 // produces large delay
    })
    expect(r.completed).toBe(false)
    expect(r.charactersTyped).toBeGreaterThanOrEqual(1)
  })

  it('uses default 35-55 range when minDelayMs/maxDelayMs omitted', async () => {
    const { deps, sleeps } = makeDeps()
    await typeStringWithDelay({ text: 'ab', ...deps })
    for (const s of sleeps) {
      expect(s).toBeGreaterThanOrEqual(35)
      expect(s).toBeLessThanOrEqual(55)
    }
  })

  it('swaps min/max when caller passes reversed values', async () => {
    const { deps, sleeps } = makeDeps()
    await typeStringWithDelay({ text: 'a', minDelayMs: 100, maxDelayMs: 30, ...deps })
    expect(sleeps[0]).toBeGreaterThanOrEqual(30)
    expect(sleeps[0]).toBeLessThanOrEqual(100)
  })

  it('supports async typeChar and keyTap', async () => {
    const chars: string[] = []
    const r = await typeStringWithDelay({
      text: 'ab',
      typeChar: async (ch) => { await Promise.resolve(); chars.push(ch) },
      keyTap: async () => {},
      sleep: async () => {},
      random: () => 0.5
    })
    expect(r.charactersTyped).toBe(2)
    expect(chars).toEqual(['a', 'b'])
  })

  it('waits for typeChar promise before next character', async () => {
    const order: string[] = []
    await typeStringWithDelay({
      text: 'ab',
      typeChar: async (ch) => {
        await new Promise((res) => setTimeout(res, 5))
        order.push(`char:${ch}`)
      },
      keyTap: () => {},
      sleep: async () => { order.push('sleep') },
      random: () => 0.5
    })
    expect(order).toEqual(['char:a', 'sleep', 'char:b', 'sleep'])
  })

  it('propagates typeChar errors instead of swallowing them', async () => {
    await expect(
      typeStringWithDelay({
        text: 'ab',
        typeChar: () => { throw new Error('typeChar failed') },
        keyTap: () => {},
        sleep: async () => {}
      })
    ).rejects.toThrow('typeChar failed')
  })

  it('propagates keyTap errors', async () => {
    await expect(
      typeStringWithDelay({
        text: 'a\nb',
        typeChar: () => {},
        keyTap: () => { throw new Error('keyTap failed') },
        sleep: async () => {}
      })
    ).rejects.toThrow('keyTap failed')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/typewriter.test.ts`

Expected: `Cannot find module '../typewriter'`。

- [ ] **Step 3: 实现 `typewriter.ts`**

`sightflow-desktop-agent/src/core/rpa/human-input/typewriter.ts`：

```ts
const DEFAULT_MIN_DELAY_MS = 35
const DEFAULT_MAX_DELAY_MS = 55

function getNonNegativeNumber(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : fallback
}

export interface TypewriterOptions {
  text: string
  minDelayMs?: number
  maxDelayMs?: number
  isCancelled?: () => boolean
  random?: () => number
  typeChar: (ch: string) => void | Promise<void>
  keyTap: (key: string, modifiers?: string[]) => void | Promise<void>
  sleep: (ms: number) => Promise<void>
}

export interface TypewriterResult {
  completed: boolean
  /**
   * 已打字符数（含 \n）。
   * 与竞品 shiflow-agent/helper/typewriter.js 行为一致：\n 也算一个 typed character。
   */
  charactersTyped: number
}

export async function typeStringWithDelay(options: TypewriterOptions): Promise<TypewriterResult> {
  const characters = Array.from(String(options.text ?? ''))
  const rawMin = getNonNegativeNumber(options.minDelayMs, DEFAULT_MIN_DELAY_MS)
  const rawMax = getNonNegativeNumber(options.maxDelayMs, DEFAULT_MAX_DELAY_MS)
  const minDelayMs = Math.min(rawMin, rawMax)
  const maxDelayMs = Math.max(rawMin, rawMax)
  const isCancelled = options.isCancelled ?? (() => false)
  const random = options.random ?? Math.random

  let charactersTyped = 0
  for (const ch of characters) {
    if (isCancelled()) return { completed: false, charactersTyped }
    if (ch === '\n') {
      await options.keyTap('enter', ['shift'])
    } else {
      await options.typeChar(ch)
    }
    charactersTyped += 1
    if (isCancelled()) return { completed: false, charactersTyped }
    const delayMs = Math.round(minDelayMs + random() * (maxDelayMs - minDelayMs))
    await options.sleep(delayMs)
  }
  return { completed: true, charactersTyped }
}
```

注意：循环内的 `await options.typeChar(ch)` 和 `await options.keyTap(...)` 不包 try/catch，错误自然向上传播。

- [ ] **Step 4: 跑测试确认通过**

`cd sightflow-desktop-agent && npm test -- src/core/rpa/human-input/__tests__/typewriter.test.ts`

Expected: 15 passed, 0 failed。

- [ ] **Step 5: 加到 barrel**

修改 `src/core/rpa/human-input/index.ts`：

```ts
export { generateIrregularDelay } from './delay'
export { easeInOut, generateBezierPath } from './bezier'
export type { BezierPoint, BezierPath, BezierOptions } from './bezier'
export { typeStringWithDelay } from './typewriter'
export type { TypewriterOptions, TypewriterResult } from './typewriter'
```

- [ ] **Step 6: 提交**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/human-input/
git commit -m "feat(rpa): 逐字打字 typewriter (依赖注入)

- Array.from 切 code point (emoji / CJK 安全)
- \\n → keyTap('enter', ['shift']) 输入框内换行 (计入 charactersTyped)
- 默认 35-55ms 间隔
- isCancelled 位可在任意字符之间或 sleep 期间提前退出
- typeChar/keyTap 抛错向上传播 (不被吞)
- 15 个 vitest 单测覆盖空文本/CJK/emoji/取消/异常传播"
```

---

### Task 4a: 重构 humanLikeMove 使用新 bezier 模块

**Files:**
- Modify: `sightflow-desktop-agent/src/core/rpa/input-utils.ts:13-77` (`humanLikeMove`)
- Modify: `sightflow-desktop-agent/src/core/rpa/input-utils.ts` 文件头 import

**Interfaces:**
- Consumes: `generateBezierPath` (Task 2), `getRobot()` (已有), `delay()` (已有)
- Produces: 同 `humanLikeMove(targetX, targetY, opts?)` 签名（外部调用方无改动）

- [ ] **Step 1: 加 import**

在 `src/core/rpa/input-utils.ts` 文件顶部（line 10 附近）加：

```ts
import { generateBezierPath } from './human-input'
```

- [ ] **Step 2: 替换 humanLikeMove 函数体**

定位 line 13-77 的 `async function humanLikeMove`。整个函数体替换为：

```ts
async function humanLikeMove(
  targetX: number,
  targetY: number,
  options: { minSteps?: number; maxSteps?: number; baseDelay?: number } = {}
): Promise<void> {
  const robot = getRobot()
  if (!robot) return

  const startPos = robot.getMousePos()
  const path = generateBezierPath(startPos.x, startPos.y, targetX, targetY, {
    minSteps: options.minSteps ?? 5,
    maxSteps: options.maxSteps ?? 15,
    baseDelay: options.baseDelay ?? 10
  })

  for (const pt of path.points) {
    robot.moveMouse(pt.x, pt.y)
    await delay(pt.delay)
  }
  // 确保末态精确
  robot.moveMouse(path.finalX, path.finalY)
}
```

注意保留函数注释（如有）。原函数体的局部变量 `startPos`, `dx`, `dy`, `distance`, `ctrl1X/Y`, `ctrl2X/Y`, `steps`, `easeT`, `jitterX/Y` 等全部删除，由 bezier 模块负责。

- [ ] **Step 3: 类型检查**

`cd sightflow-desktop-agent && npm run typecheck:node`

Expected: 0 错误。

- [ ] **Step 4: 跑全单测**

`cd sightflow-desktop-agent && npm test`

Expected: 全部 30 个 case 通过（Task 1: 8 + Task 2: 7 + Task 3: 15）。

- [ ] **Step 5: 提交**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/input-utils.ts
git commit -m "refactor(rpa): humanLikeMove 使用 generateBezierPath

- 删除内联的 cubic Bezier + 二次缓动实现 (重复代码)
- 改用 human-input/bezier.ts 的 generateBezierPath
- 保留同样的函数签名，外部调用方无改动

Task 4a of: docs/superpowers/plans/2026-10-16-human-input-detection-evasion.md"
```

---

### Task 4b: 重构 sendReplyByCoordsAction —— 粘贴改为逐字打字

**Files:**
- Modify: `sightflow-desktop-agent/src/core/rpa/input-utils.ts:222-267` (`sendReplyByCoordsAction` 内的粘贴段)

**Interfaces:**
- Consumes: `typeStringWithDelay` (Task 3), `getRobot()`, `clipboard`, `IS_MAC`, `delay`, `randomDelayIn`
- Produces: 同 `sendReplyByCoordsAction(x, y, text)` 签名；新增导出 `humanLikeTypeIntoFocusedField(text, isCancelled?)`

- [ ] **Step 1: 加 typewriter import**

在 `src/core/rpa/input-utils.ts` 文件头（与 Task 4a 的 import 同位置）补：

```ts
import { generateBezierPath, typeStringWithDelay } from './human-input'
```

- [ ] **Step 2: 新增 `humanLikeTypeIntoFocusedField`**

在 `input-utils.ts` 任意位置（推荐 line 130 附近，`humanLikeClick` 之后）加新导出函数：

```ts
/**
 * 把整段文本逐字"打"进当前焦点输入框。
 * Windows/macOS 中文场景：每字符写剪贴板 + Ctrl/Cmd+V，避免 typeString 中文失败。
 * 视觉与人类逐字输入一致（每字符 35-55ms）。
 *
 * 调用前必须保证输入框已聚焦（点击或 Tab 已完成）。
 * 函数内部备份当前剪贴板，退出时恢复，避免污染用户剪贴板。
 */
export async function humanLikeTypeIntoFocusedField(
  text: string,
  isCancelled?: () => boolean
): Promise<{ completed: boolean; charactersTyped: number }> {
  const robot = getRobot()
  if (!robot) {
    console.error('[humanLikeTypeIntoFocusedField] RobotJS 缺失')
    return { completed: false, charactersTyped: 0 }
  }
  const originalClipboard = clipboard.readText()
  try {
    return await typeStringWithDelay({
      text,
      isCancelled,
      typeChar: (ch) => {
        clipboard.writeText(ch)
        if (IS_MAC) robot.keyTap('v', ['command'])
        else robot.keyTap('v', ['control'])
      },
      keyTap: (key, mods) => robot.keyTap(key, mods),
      sleep: (ms) => delay(ms)
    })
  } finally {
    if (originalClipboard && originalClipboard.trim() !== '') {
      clipboard.writeText(originalClipboard)
    } else {
      clipboard.clear()
    }
  }
}
```

- [ ] **Step 3: 替换 sendReplyByCoordsAction 内的粘贴段**

定位 line 222-267 区域（粘贴 + Ctrl+V + Enter）。把这段（含 `clipboard.writeText(text)`、`keyTap('v', [...])`、Enter 之前的剪贴板恢复逻辑）整段替换为：

```ts
    // 阶段 4: 逐字输入（替代原 Ctrl+V 整段粘贴）
    await humanLikeTypeIntoFocusedField(text)

    // 阶段 5: 发送前"检查内容"停顿（人类习惯）
    await randomDelayIn(300, 700)
```

**重要：**
- 删除原 `clipboard.writeText(text)`（整段写剪贴板）
- 删除原 `keyTap('v', ['command'])` 与 `keyTap('v', ['control'])` 两行
- 删除原 try/finally 中针对 sendReplyByCoordsAction 的剪贴板备份/恢复（现在由 `humanLikeTypeIntoFocusedField` 内部处理）
- 保留 `humanLikeClick()` 之后等待输入框聚焦的 `randomDelayIn(80, 180)`
- 保留发送 Enter 之后的所有 `randomDelayIn` 段落

- [ ] **Step 4: 类型检查**

`cd sightflow-desktop-agent && npm run typecheck:node`

Expected: 0 错误。

- [ ] **Step 5: 跑全单测**

`cd sightflow-desktop-agent && npm test`

Expected: 全部 30 个 case 通过。

- [ ] **Step 6: 提交**

```bash
cd sightflow-desktop-agent
git add src/core/rpa/input-utils.ts
git commit -m "feat(rpa): sendReplyByCoordsAction 粘贴改为逐字打字

- 新增 humanLikeTypeIntoFocusedField: 单字符 clipboard+Ctrl+V
  - 内部备份/恢复用户剪贴板，避免污染
  - 默认 35-55ms/字符
- sendReplyByCoordsAction 替换整段粘贴为逐字输入
- 删除函数内重复的剪贴板备份/恢复逻辑（已下沉到 humanLikeTypeIntoFocusedField）

风控意义: 200 字 AI 回复输入耗时从 0.5s → 7-10s, 真人行为

Task 4b of: docs/superpowers/plans/2026-10-16-human-input-detection-evasion.md"
```

---

### Task 5: 端到端手动验收

**Files:** 无新增。验证任务。

**Interfaces:** 无产出。

- [ ] **Step 1: 启动开发模式**

```bash
cd sightflow-desktop-agent
npm run dev
```

观察 stdout 看到：
```
[Skill Server] 已启动，监听 http://127.0.0.1:12680
[ProcessManager] Spawning: ...glue-layer.exe ...
[glue-layer] [WeChatAgent] 启动中...
```

- [ ] **Step 2: 把微信主窗口置于前台**（标题为 "微信"，CLAUDE.md 已知约束）

- [ ] **Step 3: 启动引擎**

```bash
curl -X POST http://127.0.0.1:12680/skill/start
```

Expected: `{"ok":true}`，且 `/skill/status` 在 3 秒后仍返回 `running`。

- [ ] **Step 4: 在测试群发 @AI 一条消息（≥ 80 字内容）**

例如：
```
@SightFlow 帮我详细说说 TypeScript 的泛型约束怎么用，要举例子
```

- [ ] **Step 5: 肉眼观察微信输入框**

Expected：
- ✅ 文字**逐字出现**（不是瞬间整段）
- ✅ 总耗时 ≥ 字符数 × 35ms（一条 150 字回复要 ≥ 5.2 秒）
- ✅ 看到鼠标曲线移动到输入框（不是直线瞬移）
- ✅ 最后 `Enter` 键触发发送
- ✅ 用户原剪贴板内容在发送完成后被恢复（手动验证：发送前先复制一段文字，发送后用 Ctrl+V 粘贴看是否还在）

- [ ] **Step 6: 验证消息成功送达**

```bash
curl http://127.0.0.1:12680/skill/logs?limit=20 | grep "发送成功"
```

Expected: 至少一条 `发送成功` 记录。如果失败，记录完整错误并停止本任务，提交 issue 给主 agent。

- [ ] **Step 7: 验证未触发风控**（连续运行 30 分钟观察）

- [ ] **Step 8: 提交验收记录**

把观察结果写到 `docs/superpowers/acceptance/2026-10-16-human-input.md`：

```bash
mkdir -p docs/superpowers/acceptance
# 写"逐字打字耗时: Xs / 字符数: Y / 是否触发风控: ... / 剪贴板恢复: 是/否"
git add docs/superpowers/acceptance/2026-10-16-human-input.md
git commit -m "docs: 拟人化输入验收记录"
```

---

## Self-Review 已完成

**1. Spec coverage 检查：**
- ✅ 逐字打字 → Task 3 + Task 4b
- ✅ Box-Muller → Task 1
- ✅ 三次贝塞尔 + 二次 ease-in-out → Task 2
- ✅ 10%/5% 概率 → Task 1 测试 + 实现
- ✅ CJK 单字符粘贴 → Task 4b (`humanLikeTypeIntoFocusedField`)
- ✅ `\n` → Shift+Enter → Task 3 实现 + 测试
- ✅ 剪贴板备份/恢复 → Task 4b
- ✅ typeChar 抛错传播 → Task 3 测试
- ✅ sleep 期间取消 → Task 3 测试

**2. Step scan：** 无 TBD/TODO/placeholder；每个 test 步骤都有具体断言代码；每个实现步骤都有具体函数体或精确签名。

**3. Type consistency：** Task 2 用 `BezierOptions`，Task 4a 用 `generateBezierPath` 同名同签名；Task 3 的 `TypewriterOptions` 在 Task 4b `humanLikeTypeIntoFocusedField` 调用处字段对齐；`humanLikeTypeIntoFocusedField` 返回类型 `{completed, charactersTyped}` 与 `TypewriterResult` 等价。

**4. Review Focus 覆盖：**
- ✅ 空文本 → Task 3 test `returns {completed:true, charactersTyped:0}`
- ✅ CJK + emoji → Task 3 test `splits by code point` + `handles CJK characters`
- ✅ `\n` 中间 → Task 3 test `maps \n to keyTap('enter', ['shift'])`
- ✅ 取消位 → Task 3 test `stops early` + `handles cancellation during sleep`
- ✅ 起终点相同 → Task 2 test `returns empty points when start == end`
- ✅ typeChar 抛错 → Task 3 test `propagates typeChar errors`
- ✅ baseDelay=1 极小 → Task 1 test `clamps to minimum 5`
- ✅ random()=0 → Task 1 test `handles random() returning 0`

**5. Proportion：** 计划 ~750 行（含代码块）vs spec ~100 行。算法代码必须给出（qwen cli 需要确定边界条件，且 Box-Muller 与 bezier 是已知精确公式），不算 transcript。

## 已采纳的 qwen 评审反馈（v1 → v2）

- ✅ 措辞修正：三次贝塞尔曲线（路径） + 二次 ease-in-out（缓动）
- ✅ Task 4 拆分为 4a（humanLikeMove）+ 4b（sendReply）
- ✅ 加 typeChar 抛错传播测试
- ✅ 加 sleep 期间取消测试
- ✅ humanLikeTypeIntoFocusedField 加剪贴板备份/恢复
- ✅ spec 注明 `Math.max(10, ...)` 在长停顿分支
- ✅ 加 baseDelay=1 / random()=0 边界测试
- ✅ 加 CJK 测试用例
- ✅ 加"行号漂移用 grep 重新定位"提示

**未采纳的 qwen 反馈（附理由）：**
- ❌ "delay.ts 串行 if 是 bug" —— 实际有 `return` 保证互斥，竞品同款写法
- ❌ "input-utils.ts 文件不存在" —— qwen 用 `**/*.json` glob 找 `.ts` 文件，看错了
- ❌ "getNonNegativeNumber 未使用" —— 实际 rawMin/rawMax 都用了
- ❌ "easeInOut 应改三次方" —— 实测竞品就是二次（已 grep `mouse-trajectory.js`）
- ❌ "typeChar 返回类型不一致" —— `void | Promise<void>` + `await` 已正确覆盖
- ❌ "控制点偏移可能极端弯曲" —— 竞品验证过的参数，0.2 系数合理
