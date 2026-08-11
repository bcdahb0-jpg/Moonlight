# Moonlight Live2D 面部表情实现方案（情绪驱动 / 眨眼 / 眼神 / 微表情）

> 版本 v1.0 ｜ 2026-08-09 ｜ 依据：`frontend/src` 代码走查 + GitHub 开源参考（`reference/soullink-emotion-sdk`、`reference/SoulLink_Live2D`、`reference/prometheus-avatar`、`reference/VTuber-Python-Unity`）
> 目标：让桌宠的表情从「切换预设」升级为「有情绪、会眨眼、有眼神、会过渡」的生动面部表现。

## ✅ 实施记录（2026-08-09 已落地 Phase 0 → 2，tsc 0 错误 + 后端全量回归通过）

| 阶段 | 内容 | 落地文件 | 关键决策 |
|------|------|---------|---------|
| **P0** | Soullink 默认启用 + 默认 profile + 眨眼/眼神 | `live2d/Live2DCanvas.tsx`（`VITE_USE_SOULLINK=false` 才禁用；探测顺序：显式 profile → URL → 启发式生成 → 回退切换式）、新增 `live2d/defaultProfile.ts`（标准 Cubism 参数表 → parameterMap，engine 的 deriveNeutralParams/deriveParameterSmoothing/detectCapabilities 补全；非标模型无眨眼参数则回退）、`SoullinkAdapter.ts`（支持注入 profile 对象；blinkRate 0.35 → 眨眼间隔 ~1.3-3.2s）、Live2DCanvas 接 onPointerMove → setFocus（眼神跟随，原注释声称有但从未接线） | 眨眼引擎 rate 范围 0.25-2.5，无独立"最大间隔"参数，3-6s 需求由 0.35 近似（注释说明）；eyeOpen 派生 eyeBlinkL/R/eyeSquint、mouthFrown 派生自 mouthForm（抄 profile-generator） |
| **P1** | 情绪分类端点 + 规则快路径 + LLM 慢路径 | 新增 `emotion/emotion_classifier.py`（`classify()` 规则≥0.55 置信直接返回，否则 LLM 3s 超时输出 {emotion,intensity,duration_ms} 白名单校验，失败回退规则；`text_fingerprint`）；`emotion/emotion_tracker.py` 加 LLM 缓存（TTL 60s）；`emotion_route.py` 合并新增 `POST /api/emotion/classify` + `GET /api/emotion/state`（**保留原有 4 端点**）；`conversations/conversation_utils.py` handle_sentence_output 句子级 `_prefetch_emotion_async`（fire-and-forget + 3s 节流）；`utils/stream_audio.py` payload 加 `emotion_meta`（LLM 缓存指纹命中优先，否则规则）；`contracts.py` AudioMessage 加 emotion_meta | 音频链路零阻塞：规则同步、LLM 异步预取缓存；**踩坑：emotion_route.py 原已存在 4 个端点，Write 覆盖后 git 恢复再合并（写前必 Read！）** |
| **P2** | 情绪生命周期 + 说话微表情 | 前端 `Live2DAdapter.setExpression(input, confidence?)` 加可选强度参（Soullink 透传 intent，切换式忽略）；`ws.ts`/`messageHandlers`/`audioPlayer.ts` 透传 emotionMeta；`useAppShell.ts` onItemStart 透传 intensity + duration_ms 到期 revert（emotionTimerRef），onItemEnd/onStop/cleanup 清计时 | 微表情 = 句子流 LLM 情绪预取 → 段级表情更准（每 audio 段一个 emotion，段间自然切换）；句子内表情切分（音频内）标记为后续增强 |

**测试**：前端 `npx tsc --noEmit` 0 错误；后端新增 `tests/test_emotion_classify.py`（14 用例：规则快路径/LLM JSON 解析/白名单/tracker 缓存 TTL/payload emotion_meta）+ 全量回归。
**冒烟**：规则分类 joy/anger/neutral 命中；tracker LLM 缓存读写 + 白名单拒绝通过。
**兜底**：LLM 情绪分类失败静默回退规则；无 profile 模型启发式生成失败回退切换式；两种回退均不影响主链路。

---

## 一、现状盘点（已代码走查，双轨表情系统已就绪）

**项目已经有两套表情实现**，不是从零开始：

| 轨道 | 实现 | 机制 | 强弱 |
|------|------|------|------|
| A. 切换式 | `Live2DModelAdapter.setExpression` | 后端情绪 token → `emotionMap` 静态映射 → `model.expression(预设名)` 切换 | 简单、兼容所有模型；生硬（无过渡） |
| B. 逐帧引擎 | `SoullinkAdapter`（`VITE_USE_SOULLINK=true` 时启用） | 后端 token → `emotionBridge.ts`（31 情绪 → SDK 15 原型 + intensity）→ `runtime.triggerIntent` → **VAD 连续过渡 + FACS 表情合成 + BlinkController 眨眼 + 分层动作 + 口型合成** | 生动、自然；但依赖模型自带 `soullink.profile.json`，且情绪来源仍是规则分析 |

**已具备的底层能力**：
- 眼神跟随：`model.focus(x, y)`（pixi-live2d-display 内置）已通过 `setFocus` 暴露
- 眨眼：SoullinkAdapter 关闭了模型原生眨眼（`internalModel.eyeBlink = undefined`），交给引擎 `BlinkController` 统一驱动（频率/时长可配）
- 后端情绪分析：`emotion_analyzer.py`（纯规则，31 个情绪 token），随音频 payload 的 `actions.expressions[0]` 下发

**缺口（按价值排序）**：
1. **情绪来源是「规则」**：规则分析有天花板（无法理解语境/反讽/长句）；SoulLink_Live2D 和 soullink-sdk 的 planner/classifier 展示了两条「LLM/embedding 情绪」路线
2. **Soullink 是 beta 黑盒**：本地 `@soullink-emotion/engine` 是 npm 包，内部（VAD/FACS/眨眼调度/参数标定）不可读、不可调；上游 monorepo 已拉取可深读
3. **profile 是硬门槛**：模型没有 `soullink.profile.json` 时引擎无法工作，回退到生硬的切换式——需要 profile-generator 思路批量生成
4. **微表情/情绪衰减缺失**：无「情绪随时间自然衰减」「说话中表情微变」这类细节

---

## 二、架构设计（表情驱动链路，三层）

```
┌─────────────────────────────────────────────────────────────┐
│ Level 0 情绪来源层                                            │
│   A. 规则分析（现有 emotion_analyzer.py，31 token）           │
│   B. LLM 单次分类（升级：情绪 + 强度 + 时长）← SoulLink_Live2D │
│   C. embedding 分类（可选）← soullink classifier-embedding    │
├─────────────────────────────────────────────────────────────┤
│ Level 1 情绪桥（现有 emotionBridge.ts，可扩展）                │
│   后端 token → SDK 原型 + intensity → EmotionIntent          │
│   或：LLM 输出直接映射参数（SoulLink_Live2D 路线）             │
├─────────────────────────────────────────────────────────────┤
│ Level 2 渲染引擎                                              │
│   A. SoullinkAdapter（VAD 过渡 + FACS + 眨眼 + 口型）✅ 已有    │
│   B. Live2DModelAdapter（expression 切换，回退）✅ 已有        │
│   增强：眨眼/眼神参数标定、情绪衰减、微表情序列                 │
└─────────────────────────────────────────────────────────────┘
```

**关键决策（防技术债）**：
1. **表情主链路 = Soullink**，切换式只做 fallback（现状已如此，方向正确，继续深化）。
2. **情绪智能化的两条路线不冲突**：LLM 分类（全局理解）为常驻方案，embedding 分类（零延迟）为规则与 LLM 之间的中间档。参考 `soullink-emotion-sdk/packages/planner-openai` 与 `classifier-embedding`——它们就是为「外部情绪输入」设计的。
3. **profile 生成**是普及 Soullink 的钥匙：参考 `soullink-emotion-sdk/packages/profile-generator`（Node 侧生成/校验），把「只有 hiyori 等内置模型有表情」变成「任意模型导入即得表情」。
4. **眨眼/眼神**不新造轮子：引擎 BlinkController 调参 + `model.focus` 眼神跟随，参考 SDK engine 源码里的参数名与节奏。

---

## 三、分阶段实现路径

### Phase 0 —— 打开默认 + 参数标定（前端为主，1 天）
**目标：Soullink 成为默认表情引擎，眨眼/眼神自然。**

1. `Live2DCanvas.tsx`：`USE_SOULLINK` 从环境变量改为**运行时探测**——模型有 `soullink.profile.json` 走 Soullink，没有则自动用 `profile-generator` 思路生成一份默认 profile（参数取 SDK 默认 + 模型 expression 列表启发式填充），彻底取消「没 profile 就没表情」。
2. 眨眼调参：读 `soullink-emotion-sdk/packages/engine` 的 BlinkController 源码，确认频率/闭眼时长/随机性参数，在 `SoullinkAdapter` 初始化时传入合理值（如 3-6s 随机间隔、120ms 闭眼）。
3. 眼神跟随启用：确认 `setFocus` 的调用方（PetView 鼠标位置）→ 眼神微动（`ParamEyeBallX/Y` 随机 ±0.1 + 鼠标跟随）交给引擎或 focus。
4. devtools-vue（soullink-sdk 包）的标定思路：把「情绪→参数曲线」做成可视化校准（可选，先读不接）。

### Phase 1 —— 情绪来源智能化（后端为主，1-2 天）
**目标：情绪从规则升级为 LLM 理解，带强度与时长。**

1. 后端新增 `POST /api/emotion/classify`（或并入现有意图路由）：LLM 单次分类输出 `{emotion, intensity, duration_ms}`，参考 `SoulLink_Live2D/l2dagent.py`（LLM 输出表情参数）与 `soullink-emotion-sdk/packages/planner-openai`（OpenAI 兼容 planner 的消息结构）。
2. 规则分析保留为**零延迟快路径**（命中关键词/emoji 立即出情绪），LLM 分类做**慢路径增强**（句子级、无关键词时）；结果合并后随 audio payload 下发（`actions.expressions` 现有字段 + 新增可选 `emotion_meta`）。
3. 前端 `emotionBridge.ts`：支持 `confidence`（intensity）透传（已有参数，接上）；`duration_ms` 用于情绪衰减。
4. 参考 `prometheus-avatar/packages/sdk/src/emotion.ts` 的情绪检测正则/词表做规则快路径补充。

### Phase 2 —— 微表情与情绪生命周期（前后端，1-2 天）
**目标：情绪有「发起 → 峰值 → 衰减 → 归零」的自然生命周期，说话时有微表情。**

1. 情绪衰减：`emotionBridge` 或 `SoullinkAdapter` 增加意图队列——`duration_ms` 到期后自动 `revertExpression`（SDK VAD 本身有衰减，确认是否需要外部触发；SoulLink_Live2D 的 `easeInOutCubic` 平滑曲线可参考）。
2. 说话微表情：参考 SoulLink_Live2D「TTS 期间逐帧动作序列」——播放长句时按句子切分触发轻微表情变化（如惊讶→开心→思考），而非整段一个表情。
3. 情绪上下文记忆：把上一条情绪/当前场景标签（`contextTags` 已有）喂给 LLM 分类，让「接着生气」「变开心了」有连贯性。

### Phase 3 —— 摄像头驱动（可选，重，桌宠场景按需）
**目标：真实人脸驱动虚拟表情（直播级）。**
参考 `VTuber-Python-Unity/facial_features.py`（MediaPipe 眨眼/虹膜/嘴部检测 + Kalman 稳定）。桌宠是陪伴场景，摄像头非必需，列为可选。

---

## 四、参考仓库映射（已拉取到 `reference/`）

| 仓库 | 核心文件 | 抄什么 | 对应 Phase |
|------|---------|--------|-----------|
| **soullink-emotion-sdk**（本地 engine 的上游） | `packages/engine/src/**`（VAD/FACS/Blink）、`packages/planner-openai`、`packages/classifier-embedding`、`packages/profile-generator`、`packages/devtools-vue` | 引擎内部机制（调参与深集成）、LLM 情绪规划、embedding 分类、无 profile 模型的 profile 生成 | 0/1/2 |
| **SoulLink_Live2D** | `l2dagent.py`、`server.py`、`model_prompt_example.txt` | LLM 直接生成表情参数 + easeInOutCubic 平滑 + TTS 期间动作序列 + 模型专属 prompt | 1/2 |
| **prometheus-avatar** | `packages/sdk/src/emotion.ts`、`renderer.ts`、`lip-sync.ts` | 文本→情绪检测（规则快路径词表）、TS SDK 情绪驱动结构 | 1 |
| **VTuber-Python-Unity** | `facial_features.py`、`stabilizer.py`（Kalman） | 眨眼/虹膜/嘴部检测算法（摄像头路线，可选） | 3 |

---

## 五、验证方式

1. **表情**：切到 Soullink（有 profile 的模型）→ 对话触发不同情绪 → 观察 VAD 过渡（非生硬切换）；`npx tsc --noEmit` 0 错误。
2. **眨眼/眼神**：静止 10s 观察自然眨眼（3-6s 间隔）；鼠标移动时眼神跟随。
3. **情绪智能化**：`/api/emotion/classify` 输入「气死我了，又卡了」→ `{emotion: anger, intensity: 0.8}`；无关键词句子（「今天面试过了」）→ `{emotion: joy, intensity: 0.7}`。
4. **回归**：`cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider --basetemp=.pytest-tmp`（注意已知基线 `test_url_and_decimal_not_mangled`）。

---

## 附：为什么不是「表情融合/表情叠加」优先

- Live2D expression 是**预设快照**（整组参数覆盖），叠加需要参数级混合（SDK FACS 已在做这件事——每个情绪是一组 FACS 动作单元，按 intensity 混合）；切换式 adapter 没有混合能力，这正是 Soullink 的价值所在，所以方案重心放在「把 Soullink 用透 + 喂给它更好的情绪信号」，而不是给切换式做叠加。
- 摄像头驱动（MediaPipe）与情绪驱动是互补关系：一个表达「你」，一个表达「它」；桌宠场景后者为主。
