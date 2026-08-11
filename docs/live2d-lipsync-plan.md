# Moonlight Live2D 语音对口型（Lip-Sync + 说话动作）实现方案

> 版本 v1.0 ｜ 2026-08-09 ｜ 依据：`frontend/src` 代码走查 + GitHub 开源参考（`reference/pymouth`、`reference/daidai-live2d-pet`、`reference/live2d-llm-chat`）
> 目标：TTS 播放时 Live2D 同步口型 + 说话动作，支持打断，质量从「能张嘴」升级到「像在说话」。

## ✅ 实施记录（2026-08-10 Phase B 增强：随机动作 + 头部微动 + 模型资源补齐）

| 阶段 | 内容 | 落地文件 | 关键决策 |
|------|------|---------|---------|
| **B1** | 说话/idle 随机动作 | 新增 `live2d/motionUtils.ts`（getMotionGroups/pickRandomMotion/HeadMotion）；`Live2DAdapter.ts` 接口加 `playRandomMotion(mode)`；两个 adapter 实现（多组名兼容 + priority 2 不打断关键动作）；`LipSyncDriver.ts` 改造：enterSpeaking 优先随机说话动作（失败回退固定 Talk）、说话中每 3.2-5.8s 随机换动作、resetToIdle 后 7-14s idle 随机动作调度 | 仿 daidai `scheduleRandomBodyMotion`/`scheduleSpeakingBodyMotion`；候选组列表 Talk/talk/tap_body/Tap/tap/flick_head 与 Idle/idle/rest/sleepy 多命名兼容 |
| **B2** | 说话头部微动 | `motionUtils.ts` 的 `HeadMotion` 类（三轴正弦错相 + 音量低通 + 快速衰减）；`Live2DModelAdapter.applyFrameParams` 说话时写 ParamAngleX/Y/Z；`SoullinkAdapter` frame() 更新微动 + applyParametersNow 跳过引擎头部 0 输出（**修 bug：引擎 idle 输出 headX/Y/Z=0 会覆盖模型动作头部动画**）+ 说话时叠加微动 | 幅度 ≤3.5° 小量叠加，不冲突动作 |
| **A/B3** | 模型资源补齐 + blinkRate 修复 | `hiyori`：model3.json 注册 8 个 Expressions（exp_01~08，新建） + 新增 Talk 组（复用 m01/m03/m06/m10）；`mao_pro`：model3.json 新增 Talk 组（mtn_02/03/04）；`model_dict.json`：hiyori emotionMap 34 token、mao_pro 补齐 30 token；`SoullinkAdapter` blinkRate 0.35 → **2.2** | 引擎 blink 公式 `base=(3+rand*4)/rate`，0.35 反而放大间隔到 8.6-20s/次（原注释算错） |
| **C1** | 情绪兜底 | `messageHandlers.ts` handleAudio：expression 优先级改为 actions.expressions → **emotion_meta.emotion** → msg.emotion；`types/ws.ts` emotion_meta 类型加 emotion 字段 | emotion_meta 是 LLM/规则分类结果，比规则 msg.emotion 更准 |

**测试（2026-08-10）**：`npx tsc --noEmit` 0 错误；后端 pytest 429 passed + smoke 23 passed（仅已知基线 `test_url_and_decimal_not_mangled` 失败，非本范围）；两个 model3.json + 8 个 exp3.json 引用完整性校验通过。
**验证**：重启后端加载新 model_dict.json（其实 `_load_model_dict()` 每次请求读文件，无需重启）；前端 Vite HMR 自动生效；切换模型时前端拿到新 emotionMap。

| 阶段 | 内容 | 落地文件 | 与计划的差异（关键决策） |
|------|------|---------|------------------------|
| **P0** | 口型平滑 + 说话动作 | 新增 `frontend/src/live2d/LipSyncDriver.ts`；改 `api/audioPlayer.ts`（onStop）、`hooks/useAppShell.ts`（四钩子接线） | **平滑从 adapter 内移到 driver 的 rAF 循环**（60fps 一阶滤波）——两个 adapter 共享、接口零破坏；driver 用滞回阈值（0.05/0.02）+ 200ms 防抖 + 静音停帧省电 |
| **P1** | 多参数口型 + 切片插值 | `live2d/Live2DModelAdapter.ts`（resolveLipParams 探测 6 参数）、`api/audioPlayer.ts`（浮点索引线性插值） | 副参数固定比例 A=0.35/I=0.25/U=0.15/E=0.2/O=0.3，静音时只归零主参数、副参数留给 idle 动画 |
| **P2** | 后端音素级 viseme | 新增 `backend/src/open_llm_vtuber/utils/viseme.py`（F1/F2 共振峰 + 温度 softmax）；`utils/stream_audio.py`（payload 加 visemes）、`contracts.py`（AudioMessage 加字段）；前端 `types/ws.ts` / `messageHandlers.ts` / `audioPlayer.ts`（透传）、`Live2DAdapter.ts`（setLipSync 加可选 viseme 参）、`Live2DModelAdapter.ts`（按元音概率驱动）、`SoullinkAdapter.ts`（签名兼容忽略）、`LipSyncDriver.ts`（透传） | **未引 librosa**：pymouth 的 MFCC+DTW 改为 F1/F2 共振峰 + 标准语音学模板（纯 numpy）；**温度 10 → 0.8**（log 域距离尺度小，T=10 会稀释成均匀分布）；**F1 用「最低频显著峰」而非全局 argmax**（防等幅/谐波抢峰，合成测试 5/5 元音分类正确）；测试 `tests/test_stream_audio.py`（6 用例 + 55 子断言） |

**测试**：前端 `npx tsc --noEmit` 0 错误；后端 `pytest tests/` 全量通过（新 viseme 测试 6 passed）。
**验证**：合成五元音 a/i/u/e/o 各 1s → viseme argmax 全部命中（a:0.40 / i:0.43 / u:0.38 / e:0.35 / o:0.33）。
**兜底**：viseme 计算异常静默降级为 `[]`，前端无 viseme 时自动回退 RMS 口型（Phase 1 固定比例），主链路不受影响。

---

## 一、现状盘点（已代码走查，结论先行）

**口型主链路已通**，不是从零开始：

```
后端 TTS(ws) ──► base64 wav + volumes[] + sliceLengthMs
                    │
        AudioPlayer.ts 逐 slice 回调 onVolume(0..1)  ──(20ms 轮询查表)
                    │
        useAppShell.ts:99  onVolume → adapterRef.setLipSync(v)
                    │
        Live2DModelAdapter.applyFrameParams（ticker 每帧）
                    │
        setParameterValueById('ParamMouthOpenY', v * 0.9)
```

但存在 5 个缺口，按影响排序：

| # | 缺口 | 现状 | 影响 | 参考来源 |
|---|------|------|------|---------|
| 1 | **无平滑** | 每帧直接写参数，音量跳变 → 嘴巴抽搐 | 观感差，最直观 | daidai `app.js:1123-1138` |
| 2 | **无说话动作** | 说话时只有嘴动、身体静止；说完不恢复 | 不生动 | 模型 motions `Talk` 组惯例 |
| 3 | **单参数口型** | 只驱动 `ParamMouthOpenY` | 所有音都是一个嘴型 | Cubism4 标准 `ParamA/I/U/E/O` |
| 4 | **切片量化** | `startVolumeTimer` 按 slice 取整，slice 通常 50-100ms，口型跟不上语速 | 与音频错位 | AudioPlayer 内插值 |
| 5 | **振幅级上限** | volumes 是 RMS，只能表达「嘴张多大」，分不清 a/i/u/e/o | 音素级才能真对口型 | pymouth 共振峰 DTW |

> 另外：`SoullinkAdapter`（`VITE_USE_SOULLINK=true` 时启用）已有 `setVoicePlaybackActive` 概念，同样受益于平滑与动作联动，Phase 0 设计成**对两种 adapter 同时生效**。

---

## 二、架构设计（三层，接口零破坏）

```
┌─────────────────────────────────────────────────────────────┐
│ Level 0 数据源层                                             │
│   A. volumes[]（现有，后端 RMS 分片）                          │
│   B. AnalyserNode 实时 RMS（Phase 1 增强，可选）               │
│   C. visemes[] 音素流（Phase 2 升级，参考 pymouth）            │
├─────────────────────────────────────────────────────────────┤
│ Level 1 驱动层（新增 LipSyncDriver.ts）                       │
│   平滑：attack/release 一阶滤波（开口快 0.65 / 闭口慢 0.32）   │
│   状态机：Idle ──► Speaking ──► Idle（滞回阈值防抖）           │
│   动作联动：Speaking 时 playMotion('Talk')，Idle 回 Idle      │
├─────────────────────────────────────────────────────────────┤
│ Level 2 渲染层（Live2DAdapter，接口不变）                     │
│   setLipSync(v) / playMotion(group,idx) / stopMotion()      │
│   内部：多口型参数驱动（ParamMouthOpenY + A/I/U/E/O）          │
└─────────────────────────────────────────────────────────────┘
```

**设计决策（防技术债）：**

1. **不改 `Live2DAdapter` 接口**。上层（`App.tsx` / `Live2DCanvas` / `useAppShell`）零改动，两个 adapter 都实现同一接口，平滑可下沉到 adapter 内部（帧同步天然准确），动作联动上提到驱动层。
2. **平滑放渲染层**：ticker 逐帧做一阶滤波，与渲染帧率同步，比 20ms 定时器平滑得多。
3. **动作组名按 Cubism 惯例 `Talk`**：存在则播，缺失静默降级（现有 `playMotion` 已 try/catch）。说话动作优先于 Idle，但不打断 Tap 动作——Tap 优先级更高（点击互动期间不强行播 Talk）。
4. **阈值滞回**：`>0.05` 进 Speaking、`<0.02` 且持续 200ms 才回 Idle，避免音量抖动反复切动作。

---

## 三、分阶段实现路径

### Phase 0 —— 最小可用（前端改动，半天量）
**目标：嘴不抽搐 + 说话有动作。** 不动后端、不动协议。

1. **`Live2DModelAdapter.applyFrameParams` 加平滑**（参考 daidai `app.js:1132-1136`）：
   - `target = clamp((v - 0.018) * 9.5, 0, 1)` 再 `pow(0.72)`（弱音增益、强音压缩）
   - `smoothing = target > last ? 0.65 : 0.32`（开口快、闭口慢，最自然）
   - `value = last + (target - last) * smoothing`
2. **新增 `LipSyncDriver.ts`**（驱动层）：持有说话状态机 + 动作联动，挂在 `useAppShell.ts:99` 的 `onVolume` 与 `onItemStart/onItemEnd` 上。

### Phase 1 —— 质量（多参数口型 + 插值）
1. `resolveLipParam` 扩展为 **参数集**：按 `getParameterIndex` 探测 `ParamMouthOpenY / ParamA / ParamI / ParamU / ParamE / ParamO`，存在哪些驱动哪些。音量值分配到 `ParamMouthOpenY`，其余按固定权重微动（如 `ParamA = v*0.35`），整体观感明显提升且不破坏模型。
2. **AudioPlayer 切片内线性插值**：`idx` 处取 `volumes[idx]` 与 `volumes[idx+1]` 之间按帧内进度线性插值，消除 50-100ms 量化跳变。
3. `SoullinkAdapter` 同步接入 LipSyncDriver（其 `runtime.setVoicePlaybackActive` 已是状态机的雏形）。

### Phase 2 —— 音素级升级（可选，后端改动）
**参考 `reference/pymouth/src/pymouth/analyser.py`（303 行，核心无 AI 依赖）：**
- 后端 TTS 出音频后，用 librosa 提取每帧 F1/F2 共振峰 → 与预置日/中元音模板做 DTW → softmax 温度 10 → 输出 `visemes[]`（每 slice 一个 `{a,i,u,e,o,o_}` 概率向量）。
- WS 协议 `AudioItem` 增加可选 `visemes` 字段；前端有则按元音概率驱动多参数口型，无则回退 RMS 方案。
- **收益**：日文 TTS（VOICEVOX 链路）a/i/u/e/o 五元音嘴型可分辨，是「像在说话」的关键一跳。

### Phase 3 —— 打磨
- 尾部拖音：`onItemEnd` 后口型指数衰减回零（≈300ms），避免「说完立刻闭嘴」的生硬。
- 打断恢复：`AudioPlayer.stop()` 时驱动层复位 Speaking→Idle、停止 Talk 动作。
- 性能：口型参数写入集中在 ticker 内一次批量写，避免每帧多次 `setParameterValueById`。

---

## 四、核心代码（可直接落地）

### 4.1 新增 `frontend/src/live2d/LipSyncDriver.ts`

```ts
/**
 * LipSyncDriver — 口型驱动层：平滑 + 说话状态机 + 动作联动。
 * 挂在 AudioPlayer.onVolume / onItemStart / onItemEnd 上，
 * 对 Live2DAdapter 两个实现（切换式 / Soullink）同时生效。
 */
import type { Live2DAdapter } from './Live2DAdapter';

const SPEAK_THRESHOLD = 0.05;   // 进入 Speaking
const RELEASE_THRESHOLD = 0.02; // 回到 Idle（滞回）
const IDLE_HOLD_MS = 200;       // 低于阈值持续多久才回 Idle

export class LipSyncDriver {
  private speaking = false;
  private idleHoldTimer: number | null = null;
  private lastVoice = 0;

  constructor(private readonly adapter: () => Live2DAdapter | null) {}

  /** 由 AudioPlayer.onVolume 驱动（0..1，20ms 粒度）。 */
  onVolume(value: number): void {
    this.lastVoice = value;
    this.adapter()?.setLipSync(value);

    if (!this.speaking && value >= SPEAK_THRESHOLD) {
      this.enterSpeaking();
    } else if (this.speaking && value < RELEASE_THRESHOLD) {
      // 滞回：持续低音量才回 Idle，防抖动
      if (this.idleHoldTimer === null) {
        this.idleHoldTimer = window.setTimeout(() => this.enterIdle(), IDLE_HOLD_MS);
      }
    } else if (this.speaking && value >= RELEASE_THRESHOLD) {
      this.clearIdleTimer();
    }
  }

  /** 由 AudioPlayer.onItemStart 驱动（说话动作 + 表情已在别处设置）。 */
  onItemStart(): void {
    this.clearIdleTimer();
    if (this.lastVoice >= SPEAK_THRESHOLD || true) this.enterSpeaking();
  }

  /** 由 AudioPlayer.onItemEnd / stop() 驱动。 */
  onItemEnd(immediate = false): void {
    if (immediate) {
      this.clearIdleTimer();
      this.enterIdle();
    } else {
      this.clearIdleTimer();
      this.idleHoldTimer = window.setTimeout(() => this.enterIdle(), IDLE_HOLD_MS);
    }
  }

  private enterSpeaking(): void {
    this.clearIdleTimer();
    this.speaking = true;
    // Cubism 惯例动作组；模型缺失时 playMotion 内部 try/catch 静默降级
    this.adapter()?.playMotion('Talk', 0);
  }

  private enterIdle(): void {
    this.speaking = false;
    this.adapter()?.stopMotion();
    this.adapter()?.setLipSync(0);
  }

  private clearIdleTimer(): void {
    if (this.idleHoldTimer !== null) {
      window.clearTimeout(this.idleHoldTimer);
      this.idleHoldTimer = null;
    }
  }
}
```

### 4.2 改造 `Live2DModelAdapter.ts` 的平滑（Phase 0）

`applyFrameParams`（现 242-254 行）替换为带 attack/release 的版本，核心参考 daidai `app.js:1132-1136`：

```ts
private lipSyncValue = 0;
private mouthSmooth = 0; // 新增：平滑后的嘴部值

private applyFrameParams(): void {
  if (!this.model || !this.app) return;

  if (this.lipSyncValue > 0.005 && this.lipParamId) {
    // RMS → 非线性映射（弱音增益 / 强音压缩），daidai app.js:1132
    const target = Math.pow(
      Math.min(1, Math.max(0, (this.lipSyncValue - 0.018) * 9.5)),
      0.72,
    );
    // attack/release：开口快(0.65) / 闭口慢(0.32)，daidai app.js:1134-1135
    const smoothing = target > this.mouthSmooth ? 0.65 : 0.32;
    this.mouthSmooth += (target - this.mouthSmooth) * smoothing;

    try {
      const core = this.model.internalModel.coreModel as {
        setParameterValueById(id: string, value: number, weight?: number): void;
      };
      core.setParameterValueById(this.lipParamId, this.mouthSmooth);
    } catch {
      // ignore per-frame param errors
    }
  } else {
    this.mouthSmooth = 0; // 静音时回零（可改为 Phase 3 的指数衰减）
  }
}
```

### 4.3 接线 `frontend/src/hooks/useAppShell.ts`（diff 式）

现 `:99-107`：

```ts
const player = new AudioPlayer({
  onVolume: (value) => adapterRef.current?.setLipSync(value),   // ← 改
  onItemStart: (item) => { /* 现：setExpression */ },           // ← 加说话动作
  onItemEnd: (item) => { /* 现：无口型处理 */ },                // ← 加回 Idle
  ...
});
```

改为：

```ts
const lipDriverRef = useRef<LipSyncDriver | null>(null);
// adapter 就绪后：lipDriverRef.current = new LipSyncDriver(() => adapterRef.current);

const player = new AudioPlayer({
  onVolume: (value) => lipDriverRef.current?.onVolume(value),
  onItemStart: (item) => {
    if (item.expression && item.expression.length > 0) {
      adapterRef.current?.setExpression(item.expression[0]);
    }
    lipDriverRef.current?.onItemStart();
  },
  onItemEnd: (item) => lipDriverRef.current?.onItemEnd(false),
  ...
});
```

> 注意：`AudioPlayer.stop()`（打断）目前只清队列不回调 `onItemEnd`，需要在 `stop()` 后手动 `lipDriver.onItemEnd(true)` 立即复位（Phase 3 打磨项）。

---

## 五、参考仓库映射（已拉取到 `reference/`）

| 仓库 | 核心文件 | 抄什么 | 技术路线 |
|------|---------|--------|---------|
| **daidai-live2d-pet** | `src/renderer/app.js:1119-1178` | RMS 计算 + 非线性映射 + attack/release 平滑 + 起停 | AnalyserNode 时域采样 |
| **pymouth** | `src/pymouth/analyser.py`（303 行） | F1/F2 共振峰 + 元音模板 DTW + softmax（temperature=10） | 音素级，无 AI 依赖 |
| **live2d-llm-chat** | 全仓库 | ASR→LLM→TTS→口型 全链路集成顺序 | live2d-py + pixi-live2d-display |
| Open-LLM-VTuber（已有） | `backend/` | 后端 `volumes[]` 的源头实现（RMS 分片） | 与本地后端同源 |

**选型结论**：Phase 0/1 全部前端完成，直接移植 daidai 算法（MIT）；Phase 2 音素级移植 pymouth 的 `VowelAnalyser` 思路（协议用 DTW+共振峰，避免引 librosa 太重可自行实现，见其 analyser.py 的模板与距离计算）。

---

## 六、验证方式

1. **口型**：`cd backend && ./.venv/Scripts/python.exe run_server.py` + `cd frontend && MOONLIGHT_USER_DATA=.electron-user-data npm run dev`，对角色说一句话，观察 `ParamMouthOpenY` 随音量平滑开合（而非跳变）。
2. **动作**：说话时角色播放 `Talk` 组动作，停顿/说完 200ms 后回 `Idle`；点击互动（Tap 动作）期间不被说话动作打断。
3. **回归**：`cd frontend && npx tsc --noEmit` 0 错误；`curl http://127.0.0.1:12393` 200；`backend/tests/` 无新增失败（注意已知基线 `test_url_and_decimal_not_mangled` 不在本次范围）。

---

## 附：为什么是「RMS + 平滑」而不是直接上 AI 对口型

- **延迟**：实时数字人口型必须 <100ms 链路延迟。RMS 分析在浏览器内 <5ms；共振峰 DTW 每 slice 毫秒级；而基于神经网络的 viseme 分类（如 wav2lip 类）需 GPU/服务器往返，且对 Live2D 参数化模型收益低。
- **成本**：Live2D 的嘴型参数（`ParamMouthOpenY` 等）本身就是为「少量参数驱动」设计的，RMS→开口度 是一对一映射；音素级是锦上添花，振幅级是性价比最优的基线。
- **升级路径清晰**：Phase 0 RMS → Phase 2 共振峰元音，接口不变，只换数据源。
