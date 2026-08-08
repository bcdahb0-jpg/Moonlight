# Moonlight 记忆系统进化实施计划书

**版本**：v1.0　**日期**：2026-08-07　**状态**：待评审
**目标**：在现有「核心记忆 + FTS 深度回忆（+ 可选向量）」基础上，引入**记忆类型化**（N.E.K.O 思路）与**睡眠合并**（Kokoro dreaming 思路），让桌宠从"每 3 轮重写一段记忆"进化到"事实沉淀 → 反思合成 → 精选固化"的完整记忆生命周期。

---

## 一、现状与问题

现有记忆体系（均已上线，`backend/src/open_llm_vtuber/`）：

| 模块 | 存储 | 机制 | 问题 |
|---|---|---|---|
| `memory_core.py` | `chat_history/<uid>/core_memory.md` | 每 3 轮后台 LLM 全量重写，1500 字上限 | ① 一锅粥：身份/偏好/事件混在一个纯文本文件，无类型无结构；② 全量重写：每次都是 LLM 重写整份，容易"记了又忘"（细节漂移）；③ 无证据强度：重要事实和随口一提同权重 |
| `memory_fts.py` | `chat_history/<uid>/fts_index.db` | SQLite FTS5 trigram 全历史检索 top3 | 检索源只有原始对话，无提炼后的"结论性记忆" |
| `vector_memory.py` | `chat_history/<uid>/vector_memory.db` | embedding + RRF 融合（默认关） | 已具备 RRF 融合能力，可复用 |

**三个痛点**：无类型化（记不住"关于用户的画像 vs 事件"）、无证据衰减（错记无法纠正/遗忘）、无批量合并（重复与矛盾事实不会自动归并）。

---

## 二、参考项目技术拆解（调研依据）

### 2.1 N.E.K.O —— 记忆分类 + 证据双时钟

来源：`reference/N.E.K.O/`（MIT）

| 技术点 | 实现思路 | 参考文件 |
|---|---|---|
| 记忆分类 | 记忆分 4 类：`facts`（事实）/ `reflections`（反思）/ `evidence`（证据，纯计算）/ `persona`（画像，即本项目 core_memory 的对应物）；按角色分目录 JSON 持久化 | `memory/facts.py`、`memory/reflection/synthesis.py`、`memory/persona/` |
| post-turn 写入 | 每轮对话结束后异步跑信号提取：LLM 抽 fact（importance 1-10 打分）→ 攒够数量合成 reflection | `app/memory_server/post_turn.py`、`signal_extraction.py` |
| 证据双时钟 | 每条记忆带 `reinforcement`（强化）与 `disputation`（反驳）两个独立时间戳，按半衰期衰减 `0.5^(age/半衰期)`，`evidence_score = rein - disp` 决定状态晋升（pending→confirmed→promoted）或淘汰 | `memory/evidence.py` |
| 晋升入画像 | reflection 分数+时间达标 → promoted → 并入 persona（= 本项目 core_memory.md） | `memory/reflection/promotion.py` |
| 混合检索 | BM25（CJK n-gram）+ embedding cosine 并行 → RRF(k=60) 融合 → top N | `memory/hybrid_recall.py` |

### 2.2 Kokoro-Engine —— Dreaming 睡眠合并

来源：`reference/Kokoro-Engine/`（MIT）

| 技术点 | 实现思路 | 参考文件 |
|---|---|---|
| 结构化记忆表 | SQLite `memories` 表：`content / embedding / importance / tier(core\|ephemeral) / consolidated_from / memory_type / entity_key / status / evidence_count / canonical_hash / last_dreamed_at` + FTS5 同步触发器 | `src-tauri/migrations/000{1,2,8,9}.sql` |
| dreaming 触发 | 每 10s 心跳检查：达到 `dream_daily_hour`、当日未跑、空闲 ≥60s、有 LLM → 后台跑 `run_dream_pass`；支持手动触发 | `src/ai/heartbeat.rs` |
| 三段式合并 | ① `canonical_hash` 完全相同 → 确定性合并；② 同 `entity_key` 槽位多记忆 → 合并；③ 两两 cosine ≥0.97 且 LLM 高置信 → 自动合并；0.90~0.97 → 生成审查提案；发现矛盾 → 提案挂起 | `src/ai/memory.rs`（`run_dream_pass` / `auto_merge_dream_group`） |
| 提案审计 | 低置信合并写 `memory_dream_proposals` 表，人工 `approve_dream_proposal` 批准后才应用；`memory_operations` 全审计 | `src/ai/memory.rs` |
| 半衰期衰减 | 半衰期 30 天：`score = cosine * 0.5^(age/30)`；core 不衰减；`importance*decay < 阈值` → archived | `src/ai/memory.rs` |
| 检索融合 | fastembed 向量 + FTS5 BM25 → RRF(k=60) + `MIN_RRF_SCORE` 过滤 → 按 importance 二次排序 | `src/ai/memory.rs`（`search_memories_with_observability`） |

### 2.3 本项目已有可复用资产

- `vector_memory.py` 已实现 RRF 融合（向 Kokoro / N.E.K.O 思路移植的原创实现）——检索融合层直接复用。
- `memory_core.py` 的后台 fire-and-forget 模式、`memory_fts.py` 的 FTS5 trigram 增量索引——写入/检索骨架直接复用。
- `chat_history/<uid>/` 每角色一目录的隔离约定——新库沿用。

---

## 三、目标架构设计

**总原则**：新增一层"结构化记忆库"，**不动现有 core_memory.md 的文件格式**（它继续作为 persona 注入源，用户可在记忆分页直接编辑），只改变它的**写入来源**——从"每 3 轮全量重写"升级为"dreaming 定期把高置信反思精选写入"。

```
┌────────────────────────── 每轮对话 ──────────────────────────┐
│  用户消息 ──→ FTS 检索(对话历史) + 向量检索(可选)              │
│             → core_memory.md(persona 注入)                   │
│             → 【新增】facts/reflections 检索注入               │
│              └──→ LLM 生成回复                                  │
└──────────────────────────────┬───────────────────────────────┘
                               │ post-turn（异步，fire-and-forget）
                               ▼
┌────────────────── 结构化记忆库 memory_v2.db ──────────────────┐
│  facts 表：单条事实（text/importance/entity/source/hash/      │
│           reinforcement/disputation/status/embedding）        │
│  reflections 表：高层反思（text/status/source_fact_ids/        │
│           reinforcement/disputation）                          │
│  dream_proposals 表：合并/矛盾提案（pending/applied/rejected）│
└──────────────────────────────┬───────────────────────────────┘
                               │ dreaming（空闲/定时，异步）
                               ▼
        ① 确定性合并(canonical_hash) → ② LLM 判定合并 → ③ 提案
        ④ 半衰期衰减归档 → ⑤ 高置信反思精选写入 core_memory.md
```

### 3.1 存储设计（新增 `chat_history/<conf_uid>/memory_v2.db`）

```sql
-- 事实：细粒度、可溯源
CREATE TABLE facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  importance REAL DEFAULT 5,          -- 1-10，LLM 打分
  entity TEXT DEFAULT 'user',          -- 主体（user / 角色 / 关系）
  source TEXT DEFAULT 'llm_extract',  -- user_observation | ai_disclosure | llm_extract
  hash TEXT,                           -- canonical 去重
  created_at REAL, last_seen_at REAL,
  reinforcement REAL DEFAULT 0,        -- 强化计数（带时间戳衰减）
  disputation REAL DEFAULT 0,          -- 反驳计数
  rein_last_at REAL, disp_last_at REAL, -- 各自最近信号时间（双时钟）
  status TEXT DEFAULT 'active'         -- active / archived
);

-- 反思：事实的归纳结论，待晋升
CREATE TABLE reflections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  entity TEXT DEFAULT 'user',
  status TEXT DEFAULT 'pending',      -- pending → confirmed → promoted(写入core) → archived
  source_fact_ids TEXT,               -- 支撑的 fact id 列表
  reinforcement REAL DEFAULT 0, disputation REAL DEFAULT 0,
  created_at REAL, last_signal_at REAL
);

-- 合并/矛盾提案（Kokoro 式审计）
CREATE TABLE dream_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                           -- merge | conflict | contradiction
  source_ids TEXT,                     -- 涉及的 fact/reflection id
  proposed_text TEXT,
  confidence REAL,
  status TEXT DEFAULT 'pending',       -- pending / applied / rejected
  created_at REAL
);
```

### 3.2 写入流程（post-turn 提取，N.E.K.O 简化版）

每轮对话结束后后台异步（复用现有 fire-and-forget 模式）：
1. **事实提取**：LLM 读本轮（用户消息 + AI 回复），抽取 0~3 条值得长期记住的事实，每条含 `importance` 打分。规则：不记寒暄、一次性闲聊；只记身份/偏好/习惯/重要事件/称呼。
2. **去重**：与已有 facts 做 `hash` 撞库 + 简单相似判定（初版用 LLM 判断，后续可加 cosine）。
3. **反馈信号**（初版可后置）：用户在后续对话中确认/否认时更新 reinforcement/disputation（初版先不做 UI 反馈按钮，靠 LLM 每轮顺带判定一次）。
4. **反思合成**：新 facts 攒够 K=5 条（可配）→ LLM 归纳为一条高层 reflection（pending 状态）。

### 3.3 证据与晋升（N.E.K.O evidence 模型）

- 每条记忆的 `evidence_score = reinforcement - disputation`，均按半衰期衰减：`score(t) = score * 0.5^(age / 30)`（30 天半衰期，参考 Kokoro）。
- 状态机：`pending → confirmed（score≥阈值且时间达标）→ promoted（score 高且被 dreaming 选中）→ archived（score 衰减过低）`。
- promoted 的 reflection → **写入 core_memory.md**（与现有 persona 合并，走现有注入通道），形成闭环。

### 3.4 Dreaming 睡眠合并（Kokoro 简化版）

- **触发**：空闲定时（如连续空闲 ≥60s 且距上次 ≥30min，或每天一次）+ 手动触发命令（开发期用后端接口触发）。
- **流程**：
  1. `canonical_hash` 完全相同 → 确定性合并（保留 importance 高、时间新的那条）。
  2. 候选对（同 entity 的 facts，初版按 entity 分组即可，不加 cosine 预筛，直接交 LLM 批量判定）。
  3. LLM 批量判定（一次喂多对）：输出 `keep / merge(目标文本) / conflict`。merge 且 LLM 置信 ≥ 0.88 → 自动应用；conflict / 低置信 → 写 `dream_proposals` 挂起（后续人工批准或二次判定）。
  4. 衰减清理：`importance * 0.5^(age/30) < 0.05` → archived（从检索池移除，不硬删）。
  5. **固化**：confirmed 且 score 靠前的 reflections → 合并进 core_memory.md（触发现有 persona 注入自动刷新机制，`single_conversation.py` 的 phase-1.5 每轮重读逻辑无需改动）。
- **可观测**：dream 全程打日志（`[dream] merged 2 facts -> ...` / `[dream] proposal #N conflict`），便于验证。

### 3.5 检索与注入

- 检索池升级：`FTS5(对话历史) + facts + reflections` 联合检索（memory_v2.db 建 FTS 索引，或查询时 UNION 进现有 memory_fts 流程），向量记忆开启时同样纳入 RRF 融合池。
- 注入格式：保持现有「可能相关的过去对话」标注块风格，新增「关于你的事实」「过去的反思」标注块。
- `memory_route.py` 记忆分页扩展：可查看 facts/reflections、手动批准/拒绝 proposals（Kokoro 式人工审查入口）。

---

## 四、分阶段实施计划

> 复杂度：低 < 中 < 高。每阶段完成即验证，不堆积。
> **实施状态：2026-08-07 全部完成。** 验证方式：`backend/tests/test_memory_v2.py`（18 个单测，`python -m unittest tests.test_memory_v2 -v`）+ `backend/tests/e2e_memory_v2.py`（端到端，隔离临时目录）。

### 阶段 1：记忆类型化（facts 提取 + 存储）　复杂度：中
- [x] `memory_v2.py`：SQLite schema + 读写封装（复用 memory_fts 的路径安全/每角色隔离约定）
- [x] post-turn 事实提取：`single_conversation.py` 对话结束后后台调 LLM 抽取 facts 入库（fire-and-forget，失败静默）
- [x] conf 开关：`memory_v2_enabled`（默认开）、`memory_v2_max_facts`（默认 500）
- **验证**：`tests/test_memory_v2.py` TestFacts/TestEvidenceSignals；LLM 无输出/报错时对话不受影响（fail-soft）

### 阶段 2：反思合成 + 证据双时钟　复杂度：中
- [x] facts 攒够 K 条 → LLM 合成 reflections（pending）
- [x] `evidence_score` 计算：reinforcement/disputation 双时钟半衰期衰减
- [x] 状态机：pending → confirmed（规则阈值）
- **验证**：TestEvidenceMath（衰减）+ TestEvidenceSignals（分数传导：reflection 分数 = 支撑 facts 证据加总）

### 阶段 3：Dreaming 合并 + 固化　复杂度：中-高
- [x] dreaming 后台任务：确定性合并（hash）→ LLM 批量判定 → 自动应用/提案
- [x] 空闲定时触发（post-turn 顺带检查，距上次 ≥30min）+ 后端手动触发接口（`POST /api/memory/dream`）
- [x] 高置信 reflections 写入 core_memory.md（闭环，status → promoted 幂等）
- [x] 衰减清理：importance*衰减 < 阈值 → archived
- **验证**：TestDreaming + `tests/e2e_memory_v2.py`（hash_dedup / reflections_fused / core_memory.md 内容断言）

### 阶段 4：检索融合 + 记忆分页 UI　复杂度：低-中
- [x] facts/reflections 纳入检索池（FTS5 trigram + LIKE 兜底）＋ 标注块注入（single_conversation 检索块）
- [x] 记忆分页新增 facts/reflections 查看、proposals 批准/拒绝、dream 手动按钮、fact 证据信号 ±
- [x] conf 文档与默认值收尾（CharacterConfig DESCRIPTIONS + `POST /api/memory/v2` 开关接口）
- **验证**：TestSearch（问"我之前说过 X"能命中 facts）；前端 `tsc --noEmit` 通过

---

## 五、风险与取舍

| 风险/取舍 | 说明 | 对策 |
|---|---|---|
| LLM 成本增加 | 每轮多一次 facts 提取调用（DeepSeek 很便宜） | 提取合并进现有 consolidation 的 LLM 调用（同一请求多输出）；consolidation 间隔可调 3/5 |
| 复杂度膨胀 | 全套 N.E.K.O 太复杂（多角色 JSON/outbox/多阶段信号） | 只取骨架：单 SQLite + 单次 post-turn 提取 + 简化 dreaming |
| 与现有核心记忆冲突 | core_memory.md 每 3 轮仍被现有 consolidation 重写 | 阶段 3 后把 consolidation 降级为"保底通道"（dreaming 不可用时兜底），或用户可关 |
| embedding 依赖 | 合并判定若依赖 cosine 需要 embedding（向量记忆默认关） | 初版合并判定纯 LLM 批量，不依赖 embedding；cosine 留作阶段 4 增强 |
| 记忆分页兼容 | UI 现有编辑/清空核心记忆逻辑不动 | memory_v2 独立文件，清空核心记忆不影响 facts（设计如此：facts 是原始层，core 是固化层） |

---

## 六、参考文件索引

- N.E.K.O：`reference/N.E.K.O/memory/facts.py`（fact schema/提取）、`memory/reflection/synthesis.py`（反思合成）、`memory/evidence.py`（双时钟衰减）、`memory/hybrid_recall.py`（RRF 融合）、`app/memory_server/post_turn.py`（post-turn 信号）
- Kokoro：`reference/Kokoro-Engine/src-tauri/migrations/0008_dream_memory_v2.sql`（记忆表设计）、`src-tauri/src/ai/memory.rs`（dream 三段式/衰减/检索）、`src/ai/heartbeat.rs`（空闲触发）
- 本项目复用：`backend/src/open_llm_vtuber/memory_core.py`（fire-and-forget 模式）、`memory_fts.py`（FTS5 增量索引）、`vector_memory.py`（RRF 融合）、`conversations/single_conversation.py`（post-turn 注入点、phase-1.5 记忆刷新）

---

## 七、评审确认点

1. 阶段 1 的 facts 提取是否并入现有 consolidation 调用（省一次 LLM 请求）？→ **已决策：不并入**。合并会让 consolidation 的 prompt 复杂化且难调试；DeepSeek 成本低，保持独立一次调用（提取 + 证据信号同请求，reflection 合成按需）。
2. 现有 consolidation（每 3 轮重写 core_memory.md）在阶段 3 后如何处理：保留兜底 / 关闭？→ **保留（兜底通道）**。consolidation 的 prompt 自带"保留现有记忆、合并提炼"语义，dreaming 写入的内容大概率被保留；且 dreaming 不可用时 consolidation 仍保证核心记忆更新。用户可手动关 `memory_v2_enabled` 回到旧行为。
3. dreaming 触发频率：空闲 30min 一次 + 每天一次，是否合适？→ **已实现为「post-turn 顺带检查，距上次 ≥30min」+ 手动 `POST /api/memory/dream`**。不做独立后台心跳（避免服务端新增常驻任务 + 需全局 LLM 配置），对话结束后即视为空闲窗口。
4. 记忆分页 UI 的 proposals 人工审查，是否要 v1 就做（可后置到阶段 4.5）？→ **v1 已做**。提案审批（批准/拒绝）在记忆分页内直接操作，成本低价值高（矛盾事实不会无限滞留）。
