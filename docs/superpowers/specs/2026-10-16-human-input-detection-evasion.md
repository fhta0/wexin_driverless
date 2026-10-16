# Spec — 拟人化输入降低微信风控等级

**Date:** 2026-10-16
**Owner:** Claude (主 agent), 实施: qwen cli
**Scope:** 仅 sightflow-desktop-agent (`src/core/rpa/`)

## 背景

微信 4.x 风控对 RPA 输入行为有多维度检测。竞品识流AI助手 (`shiflow-agent v2.0.3`) 在
`resources/helper/mouse-trajectory.js` 与 `resources/helper/typewriter.js` 里实现了
两类我们当前缺失的拟人化行为。

## 当前差距（已用 REA 拆解验证）

| 维度 | 我们 (`src/core/rpa/input-utils.ts`) | 竞品 |
|---|---|---|
| AI 回复打字 | `clipboard.writeText` + `Ctrl+V` 一次性粘贴整段 | 逐字符 35-55ms |
| 鼠标轨迹缓动 | `t * (2 - t)` (ease-out 二次) | **ease-in-out 二次**（`t<0.5 ? 2t² : 1 - (-2t+2)²/2`） |
| 轨迹曲线 | 三次贝塞尔（cubic Bezier） | 三次贝塞尔（同） |
| 延迟分布 | 均匀 `random()` | Box-Muller 正态分布 |
| 长停顿模拟 | 无 | 10% 概率 2-5x（最小 10ms） |
| 加速段模拟 | 无 | 5% 概率 0.3x（最小 5ms） |

粘贴整段消息是**最强机器人特征**：真人 200 字消息要 10-20 秒，我们 0ms。微信侧
风控直接命中。

## 目标

把 `sendReplyByCoordsAction` 的「整段粘贴」改成「逐字打字」，并升级
`humanLikeMove` 的轨迹生成器到竞品同等水平。

## 范围（In Scope）

1. 新增 `src/core/rpa/human-input/` 模块：
   - `delay.ts` — Box-Muller 正态延迟 + 10%/5% 异常段
   - `bezier.ts` — 三次贝塞尔轨迹生成（纯函数）
   - `typewriter.ts` — 依赖注入式逐字打字
2. 引入 `vitest` 作为单元测试框架（项目当前无单测）
3. 重构 `humanLikeMove` 使用新的 `bezier.ts`
4. 重构 `sendReplyByCoordsAction`：第 233-235 行的 `Ctrl+V` 粘贴改为调用 typewriter

## 范围（Out of Scope，留给后续 P1/P2）

- `safePaste` (line 473) — 通用粘贴工具，由其它调用方使用，本次不动
- `searchContactByKeyboard` (line 526) — 搜索框输入是短文本（联系人名称），风险低
- 外部 HID 硬件支持（CH9329 / NanoKVM / ATOMS3U）
- 滚动行为（`generateScrollSequence`）
- 热更新架构
- 多 IM 平台支持
- 账号体系 / 付费 / OAuth

## 验收标准

**自动化（vitest 单测）：**

> 注：`generateIrregularDelay` 是 Box-Muller + 10% 长停顿（2-5x）+ 4.5% 加速（0.3x）的**混合分布**。
> 期望均值 ≈ baseDelay × 1.20，期望 σ ≈ baseDelay × 0.98（不是 baseDelay 本身）。
> 这是与竞品 `shiflow-agent/mouse-trajectory.js` 一致的算法特性，**不**保留 baseDelay 均值。

1. `generateIrregularDelay(100)` 在 10000 次采样下，均值落在 [110, 140]
2. `generateIrregularDelay(100)` 在 10000 次采样下，标准差落在 [85, 120]
3. `generateIrregularDelay(100)` 在 10000 次采样下，至少 7% 的样本 ≥ 200（10% 长停顿的概率下限）
4. `generateIrregularDelay` 接受 `random` 注入参数（确定性测试用）
5. `generateIrregularDelay(baseDelay=1)` 仍返回 ≥ 5（最小值保护）
6. `generateBezierPath` 步数在 [minSteps, maxSteps] 之间
7. `generateBezierPath` 末点等于 targetX/Y
8. `generateBezierPath` 起点 == 终点（距离 < 1）时返回 `{points: [], finalX, finalY}`
9. `typeStringWithDelay` 用 mock 的 typeChar/keyTap，逐字符触发，且 `\n` 触发 `keyTap('enter', ['shift'])`
10. `typeStringWithDelay` 取消标志位为 true 时立即返回 `{completed: false, charactersTyped}`
11. `typeStringWithDelay` 用 `Array.from(text)` 切分（emoji / CJK 不会被拆成代理项）
12. `typeStringWithDelay` 在 `typeChar` 抛错时，错误向上传播（不被静默吞掉）
13. `typeStringWithDelay` 在 sleep 期间 `isCancelled` 变 true 时立即退出

**手动（真机微信）：**
1. 启动系统 → 在测试群 @ AI → 收到 200 字以上的中文回复
2. 观察微信输入框：文字应**逐字出现**，不应是瞬间整段
3. 总输入耗时应 ≥ 字符数 × 35ms（200 字 ≥ 7s）
4. 鼠标移动到输入框过程肉眼可见曲线轨迹，非直线瞬移
5. 消息发送成功，无 500 错误

## 风险

- **性能**：逐字打字比粘贴慢 ~200 倍。200 字消息从 0.5s 变成 10s。这是**特性**，不是 bug —— 真人就是这个速度。
- **剪贴板污染**：`humanLikeTypeIntoFocusedField` 必须备份用户剪贴板内容，函数退出时（finally）恢复，避免污染。
- **中文输入**：`robot.typeString('中')` 在 Windows 下不支持中文（见原注释）。typewriter 必须使用「单字符剪贴板粘贴」策略，而非直接 typeString。
- **行号偏移**：plan 引用的 `input-utils.ts` 行号基于当前 commit；实施时如行号漂移，必须用 `grep -n clipboard.writeText` 重新定位粘贴段（应位于 `sendReplyByCoordsAction` 内）。
