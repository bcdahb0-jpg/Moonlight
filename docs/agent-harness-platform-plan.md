# Moonlight Agent Harness 构筑计划书（v2.5 · 详细实现方案）

> **任务制智能体平台（参考 deer-flow 2.0 / pi-agent / dwsy-agent 实现思路构筑，非合并）**
>
> - 版本：v2.5（可行性审核通过并修正：依赖补全 / 版本对齐参考仓库 / 参考路径修正 / 双写与 SSE 锚点设计补全）
> - 日期：2026-08-08
> - 状态：**审核通过**——5 个开放问题已由用户拍板（§10），双层交互模式（§5.7）、记忆边界（§5.8）、MCP 集成（§5.9）已确认；技术可行性已对照参考仓库逐项核实（审核报告 `docs/review-agent-harness-platform-plan.md`），5 处硬伤已修正（见 §9 风险、§8 Phase 0），从 Phase 0 开始执行
> - 上游参考（均已就位）：
>   - `reference/智能体平台/deer-flow` — bytedance/deer-flow 2.0（super agent harness，LangGraph）
>   - `reference/智能体平台/pi-agent` — earendil-works/pi（四层 Agent Harness，TypeScript monorepo）
>   - `reference/智能体平台/dwsy-agent` — dwsy/agent（Pi Agent 企业版：声明式 skill/agent/extension 资产分层）
> - 用户澄清：**「hermess」= harness（驾驭工程）**；执行环境=本地直执行+路径白名单；UI=桌宠内新增任务模式

---

## 1. 背景与目标

### 1.1 现状

Moonlight（AI 桌宠：FastAPI `:12393`、MCP `:12394`、React/Electron `:5173`）已有自研 tool-use 循环、MCP 双向、四层记忆、语音全链路。缺失：Skill 系统、任务制工作流、LangGraph 生态。

### 1.2 目标

- **G1** 新建会话 → 新建任务（绑定工作文件夹：已存在目录 | 默认路径）
- **G2** Skill 系统（SKILL.md 文档型 + 两级惰性加载）
- **G3** LangGraph 任务 Agent（单 lead agent + tools + goal 状态机）
- **G4** 本地直执行工具集 + 路径白名单安全模型
- **G5** 前端任务模式（**输入框模式切换（Codex plan 式）** + 工作目录指示 + 新建任务 Modal + 聊天区执行记录卡片）
- **G6**（Phase 6，优先级已定）上下文压缩（手动+自动）→ sub-agent → extensions 钩子
- **G7** **双层交互模式**（Persona Shell / Task Core）：聊天模式全程人设+语音（现状保留）；任务模式**执行内核零人设**（效率优先），**仅开始/结束/打断澄清时**由人设外壳语音汇报转述（§5.7）
- **G8** **MCP 工具接入任务内核**（智能体平台方案）：任务 agent 经 `langchain-mcp-adapters` 连接外部 MCP 服务器，MCP 工具并入任务工具列表——替换当前"双向都闲置/失效"的桌宠 MCP 用法（§5.9）

### 1.3 非目标

不合并任何参考代码；不替换现有桌宠对话链路；不做 Docker 沙箱、多用户、云网关。

---

## 2. 三平台实现思路提炼（研究结论）

### 2.1 deer-flow 2.0 — "super agent harness"

核心理念 4 条，全部可移植：

| # | 理念 | 具体实现（参考路径） | 移植到 Moonlight |
|---|---|---|---|
| A | **Skill 文档化 + 两级惰性加载**：system prompt 只注入 `<skill_index>` 名称列表，LLM 先 `describe_skill(name)` 取元数据、再 `read_file` 读 SKILL.md 全文 | `backend/packages/harness/deerflow/skills/{frontmatter,parser,catalog,describe,projection}.py`；`skills/public/<name>/SKILL.md` | `task_platform/skills/` 同构实现；frontmatter 白名单：`name/description/allowed-tools/required-secrets` |
| B | **LangGraph 装配 = `create_agent` + middleware 链**（14 个：Sandbox/DanglingToolCall/ToolErrorHandling/Summary/Todo/Title/Memory/Visual/SubagentLimit/LoopDetection/Clarification/SkillActivation/ModelLengthFinishReason…），链序即管线语义：错误处理靠前、澄清收尾 | `backend/langgraph.json`；`agents/factory.py`；`agents/lead_agent/agent.py`；`agents/middlewares/*`（**含 `model_length_finish_reason_middleware.py`**） | 只取 5 个必要 middleware：**ToolErrorHandling / LoopDetection / Clarification / Summary / ModelLengthFinishReason**（截断防护对 DeepSeek 场景重要，统一放 middleware 而非 hooks 层） |
| C | **ThreadState 单 TypedDict + reducer**（sandbox/thread_data/title/artifacts/todos/goal/delegations/skill_context/summary_text），goal 读写直接走 checkpointer 带乐观锁 | `agents/thread_state.py`；`agents/goal_state.py`；`runtime/goal.py`；`runtime/runs/worker.py` | `task_platform/state.py` 精简为：`messages/goal/workspace/skill_context/summary` |
| D | **Session Goals 防死循环**：`evaluate_goal_completion`（deer-flow 原版用独立小模型，**Moonlight 决策 #5 复用主模型**，输出 `{satisfied,blocker,reason}`）→ `should_continue_goal`（blocker=goal_not_met_yet 且计数未超限）→ **隐藏 continuation 消息**（`hide_from_ui=True`）再跑一轮；no_progress 用"最近 AI 文本 sha256 签名"判停滞 | `runtime/goal.py`；`runtime/runs/worker.py` | `task_platform/goal.py` 同构 |

**SSE 事件协议**（`contracts/run_event_stream_contract.json` + `backend/app/gateway/routers/thread_runs.py`）：事件类型 `run.start/run.end/run.error/llm.human.input/llm.ai.response/llm.tool.result/llm.error/context:memory/subagent.*/workspace_changes/middleware:*`；记录结构 `{thread_id, run_id, seq, event_type, category, content, metadata, created_at}`，**seq 按 thread 单调递增**（前端断线重连拉历史的锚点）；category 分 trace/message/outputs/error 四类（message 用于 UI 投影，trace 仅审计）。

**Sandbox 抽象**（`community/aio_sandbox/local_backend.py` + `sandbox_config.py`；无 `sandbox/local/` 目录）：`SandboxProvider ABC` + `LocalSandboxProvider`（按 thread_id 建独立沙箱 + `PathMapping(container_path→local_path, read_only)` 虚拟路径映射，agent 只见 `/mnt/user-data/{workspace,uploads,outputs}`，宿主机绝对路径经 `path_patterns.build_output_mask_pattern` **反写回虚拟路径**回显）；write_file 上限 80KB；`_reject_path_traversal` 拦 `..`；bash 超时 600s、输出 10MB 截断。

**压缩**（`runtime/context_compaction.py`）：`compact_thread_context` = 复用 SummarizationMiddleware 生成摘要 → `Overwrite(messages)` 写回新 checkpoint。

**前端**：Next.js 15 + TanStack Query + `@langchain/langgraph-sdk` + React Flow；`thread-list-virtualizer`（虚拟滚动会话列表）、`use-thread-chat`（单 Hook 管消息/流状态）、`message-list-item`（tool_call 渲染）、`goal-status`、`todo-list`。

### 2.2 pi-agent — "极简核心 + 扩展系统"

核心理念 4 条：

| # | 理念 | 具体实现 | 移植到 Moonlight |
|---|---|---|---|
| A | **严格单向分层**：`ai ← agent ← coding-agent`；core 不依赖 provider（`StreamFn` 注入）；内部统一 `AgentMessage`，**只在 LLM 调用边界转 `Message[]`** | `packages/ai/src/{types,index}.ts`；`packages/agent/src/agent-loop.ts` | `task_platform/{llm_adapter,agent_engine,app_service}.py` 三层；所有 provider 适配器只实现一个 `stream()` |
| B | **失败不 throw，编码进流**：LLM 流以 `done`（成功）或 `error`（携带 stopReason + errorMessage）终止事件收尾；事件流 tagged union：`start/text_delta/toolcall_start/done/error` | `packages/ai/src/types.ts`（`AssistantMessageEvent`） | SSE 事件同样"终止事件表达失败"，前端按类型订阅 |
| C | **副作用边界 + 两级拦截**：模型只能"提议"工具调用；执行前 `validateToolArguments`（TypeBox/AJV）→ `beforeToolCall` 钩子（**可 block，生成错误 toolResult 回给模型而非执行**）；`stopReason==="length"` 时整批拒绝执行（防截断参数） | `packages/agent/src/agent-loop.ts`（prepareToolCall） | Python 版：`jsonschema` 校验 + `before_tool_call` 钩子（白名单/权限拦截），是**本地执行安全的落地关键** |
| D | **会话 = JSONL append-only 树**：每条 entry 带 `id/parentId` 形成树 + `leafId` 分支指针；entry 类型 `message/compaction/branch_summary/label/session_info`；header 存 `cwd`（**任务↔工作文件夹绑定**）；compaction entry 替换被摘要的旧消息 | `packages/coding-agent/src/core/session-manager.ts` | 任务目录下 `session.jsonl`，零依赖、可读、坏行跳过 |

**扩展系统**（`packages/coding-agent/src/core/extensions/`）：`ExtensionAPI.on(event, handler)` ~26 事件（`tool_call` 可 block/改 input、`tool_result` 可改 content、`context` 可改 messages、`before_agent_start` 可换 systemPrompt）+ `registerTool/registerCommand/registerShortcut/registerProvider`。

**Skill**（`packages/coding-agent/src/core/skills.ts`）：遵循 Agent Skills 规范——目录含 SKILL.md，frontmatter `name/description/disable-model-invocation`；扫描 `~/.pi/skills` + `<cwd>/.pi/skills`；**注入 = 系统提示 XML 索引**（`<available_skills><skill><name/><description/><location/></skill>`），模型用 read 工具按需读——与 deer-flow 完全同构，双平台印证了这一设计。

**协议**（`packages/protocol/src/schemas.ts`）：**全量 snapshot + 增量 progress**——`SessionSnapshot{phase/revision/transcript/queuedSteer/attached/locked}` + `TranscriptProgress{item_started/assistant_delta/item_updated/item_finished}`。

### 2.3 dwsy-agent — "声明式资产分层"

真正值得借鉴的是一套**互不耦合的资产分层**：`skills/`（文本能力）+ `agents/`（委托边界）+ `commands/`（触发入口）+ `prompts/`（工作流模板）+ `categories.json`（语义类别→agent+model 路由表）。

| 资产 | 契约格式 | 借鉴点 |
|---|---|---|
| skill | 单 md + frontmatter（`name` + **中文触发词式 description**）；复杂 skill 三级渐进披露：metadata 常驻 → body 触发加载（<500 行）→ references 按需 | frontmatter 即契约；`description` 兼作检索索引 |
| agent | frontmatter（`enabled/description/tools/imodel/prompt_mode`）+ 正文 prompt；`enabled: false` 灰度下线不删文件 | **agent = 能力边界（工具白名单）+ 模型 + prompt** |
| command | frontmatter(description) + 正文 = 委派哪个 agent + JSON task + `$@` 参数占位 | 命令与 agent 解耦 |
| extension | 目录（index.ts + package.json），与 skill 的本质区别：**prompt injection vs runtime hooks** | Python 版：`skills/`（文本）+ `plugins/`（代码钩子）两层 |
| 禁用 | `skills.disabled/`、`extensions.disabled/` 目录后缀 = 开关 | 目录改名即禁用，零删除 |
| 锁定 | `skills-lock.json`（source + computedHash） | sha256 校验 + 市场安装溯源（后期可选） |

---

## 3. Moonlight 构筑总体架构

### 3.1 模块划分（严格单向依赖）

```
backend/src/open_llm_vtuber/task_platform/
├── __init__.py
├── models.py          # pydantic v2：Task/Run/SkillSpec/事件模型（参考 pi protocol schemas）
├── llm_adapter.py     # 统一 LLM 接口（参考 pi-ai）：stream() 单一函数；适配器 = OpenAI 兼容
├── state.py           # TaskState(TypedDict) + reducer（参考 deer-flow ThreadState 精简）
├── goal.py            # GoalState + evaluate_goal_completion + should_continue_goal（参考 deer-flow runtime/goal.py）
├── sandbox.py         # PathMapping 虚拟路径 + 白名单 + 工具实现（参考 deer-flow community/aio_sandbox/local_backend.py）
├── skills/
│   ├── __init__.py
│   ├── parser.py      # SKILL.md frontmatter 解析（正则切 YAML，失败返回 None）
│   ├── catalog.py     # SkillCatalog 扫描 + search（select:/required terms/正则）
│   └── describe.py    # describe_skill 工具构建（闭包持有 catalog）
├── session.py         # JSONL 会话树（参考 pi session-manager：id/parentId/leafId）
├── mcp_client.py      # MCP 工具接入（langchain-mcp-adapters）：MultiServerMCPClient + 工具合并（G8）
├── graph.py           # LangGraph 装配：create_agent + 5 middleware（参考 deer-flow factory/lead_agent）
├── middleware.py      # ToolErrorHandling / LoopDetection / Clarification / Summary / ModelLengthFinishReason
├── hooks.py           # before_tool_call / after_tool_call 拦截注册表（参考 pi before/afterToolCall）
├── task_route.py      # FastAPI router：/api/tasks/*（REST + SSE）
└── conf_bridge.py     # 从 conf.yaml 读 LLM 配置（openai_compatible_llm / DeepSeek）

backend/skills/                          # 技能库（根目录，独立于 package）
└── public/<skill-name>/SKILL.md         # frontmatter: name/description/allowed-tools
backend/tasks/                           # 默认任务根（首次运行自动创建；可配置）
backend/task_platform.db                 # 任务元数据（sqlite3 stdlib）
<workspace>/.pi/tasks/<task-id>/session.jsonl   # 每任务会话（JSONL 树，header 存 cwd）
```

### 3.2 依赖单向性约束

- `models.py` ← 全部（事件/实体类型）
- `llm_adapter.py` ← graph/session（不反向依赖）
- `sandbox.py` ← graph/middleware（工具执行唯一副作用出口）
- `mcp_client.py` ← graph（只做工具合并；连接失败 fail-soft，不阻塞主流程）
- `task_route.py` → 只调 session/graph/models，不碰 `conversations/`、`memory/`、`mcp/`
- 复用：`conf_bridge.py` 只读 `conf.yaml`（openai_compatible_llm 块）；不 import 现有 agent/mcpp 实现

---

## 4. 数据模型

### 4.1 任务元数据（SQLite，`sqlite3` stdlib）

```sql
CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,          -- uuid4
    title       TEXT NOT NULL,
    workspace   TEXT NOT NULL,             -- realpath 绝对路径（任务根）
    goal        TEXT DEFAULT '',
    status      TEXT DEFAULT 'active',     -- active|paused|completed|archived
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_run_id TEXT
);
CREATE TABLE runs (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status     TEXT DEFAULT 'running',     -- running|completed|interrupted|error
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    error      TEXT,
    summary    TEXT
);
CREATE TABLE task_events (
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,           -- 按 task 单调递增（SSE 断线重连锚点）
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL,              -- JSON
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);
```

> **SQLite 并发（v2.5 补）**：FastAPI 多请求并发访问 → 连接开 `PRAGMA journal_mode=WAL` + `check_same_thread=False`，**每请求/每协程新建连接**（不用长连接共享），写操作串行化。

### 4.2 会话内容（JSONL 树，参考 pi session-manager）

```
<workspace>/.pi/tasks/<task-id>/session.jsonl
{"type":"session","version":1,"id":"...","timestamp":"...","cwd":"D:/proj/tasks/t1"}
{"type":"message","id":"m1","parentId":null,"timestamp":"...","message":{"role":"user","content":"整理这个目录"}}
{"type":"message","id":"m2","parentId":"m1","message":{"role":"assistant","content":[{"type":"toolCall","id":"tc1","name":"bash","arguments":{"command":"ls"}}],"stopReason":"toolUse"}}
{"type":"message","id":"m3","parentId":"m2","message":{"role":"toolResult","toolCallId":"tc1","content":"...","isError":false}}
{"type":"compaction","id":"c1","parentId":"m5","summary":"已整理完成...","firstKeptEntryId":"m6"}
```

- 每行独立 JSON，坏行跳过（容错）
- **任务↔文件夹绑定** = session header 的 `cwd`（pi 原版设计，直接采用）
- 压缩 = compaction entry 替换旧消息（`firstKeptEntryId` 之后保留）
- **事件条目**：`{"type":"event","id":"e1","parentId":...,"seq":12,"event":{...SSE 事件体...}}`——`status/run_start/run_end` 等不可从消息重建的事件落盘为 event 条目（tool_call/tool_result 可从 message 的 toolCall 块重建，但为统一重放也落 event）

> **双写真相源规则（硬约束，v2.5 补）**：JSONL 与 LangGraph checkpoint（SqliteSaver）双存储，职责如下——
> - **JSONL = 审计 / UI 投影源（append-only）**：每轮消息/事件**先落 JSONL，再执行 agent**；重放历史、断线重连一律以 JSONL 为准。
> - **checkpoint = 执行源**：每次 run 结束持久化（`create_agent` 自带 checkpointer）；续接任务的上下文从 checkpoint 恢复。
> - **compaction 两边同时改**：JSONL 写 compaction entry + `Overwrite(messages)` 写新 checkpoint（deer-flow `compact_thread_context` 同构），且**先 JSONL 后 checkpoint**，任一步失败则本次压缩不生效并报错。

### 4.3 事件模型（SSE，pydantic）

```python
class TaskEvent(BaseModel):
    seq: int                      # 按 task 单调递增（断线重连锚点）
    event_type: str               # run_start|message|tool_call|tool_result|status|clarify_requested|progress|run_end|run_error|error
    task_id: str
    run_id: str
    category: str = "message"     # message|trace|outputs|error
    origin: str = "core"          # core（任务内核）| shell（人设外壳）——G7 分流
    payload: dict
    created_at: str
```

> **seq 持久化方案（v2.5 定稿）**：`seq` 按 task 单调递增，由**事件日志表 + JSONL event 条目双落**保证重启不丢：
> - SQLite 建 `task_events(task_id, seq, ...)` 表（追加写，seq 为该 task 内自增计数器）；SSE 发射前先落库。
> - JSONL 同步写 event 条目（与消息同文件，作为审计/回放源）。
> - 断线重连：`GET /api/tasks/{id}/runs/stream?after_seq=N` → 先回放 `seq>N` 的已落库事件（按序），再实时。后端重启后仍可回放（数据在 SQLite/JSONL）。

---

## 5. 核心机制详细设计

### 5.1 Skill 系统（G2）

```
backend/skills/public/<skill-name>/SKILL.md
---
name: python-script
description: 运行 Python 脚本完成任务（触发词：python、脚本、py）
allowed-tools: [bash, write_file, read_file]
---
## 用途 / ## 命令 / ## 示例 / ## 输出要求
```

1. **扫描**：启动 + 变更时 `catalog.scan()` 扫 `backend/skills/{public,custom}/`，建索引 `{name → (description, path)}`；目录名后缀 `.disabled` 跳过（dwsy 约定）。
2. **注入**：system prompt 只放 `<skill_index>`（`名称（描述前 40 字）`），描述用**触发词式**（dwsy 借鉴）提高命中率。
3. **发现**：工具 `describe_skill(name)` → frontmatter 元数据；不够 → `read_file(SKILL.md)`（read_file 白名单**额外放行 skills 根**）。
4. **契约**：`contracts/skill_contract.json` 固化 frontmatter 字段与校验规则（deer-flow 同款）。
5. 内置 3 个示例 skill：`python-script` / `file-operations` / `research-notes`（写法参考 dwsy `skills/` 样例）。

### 5.2 Agent 循环（G3，LangGraph）

```python
# graph.py —— 结构示意（实现用 langchain.agents.create_agent，不手写节点/边）
# 版本对齐：langgraph 1.2.x / langchain 1.3+（deer-flow 同款；1.x 的 middleware 是 AgentMiddleware 协议，
#            写前先读 deer-flow agents/middlewares/*，勿按 0.2.x 语法）
model = llm_adapter.build_chat_model(conf_bridge.llm_config())   # ChatOpenAI(base_url, key, model)
tools = sandbox.tools() + [describe_skill, read_skill] + mcp_client.tools()  # 工具注册（含 MCP，G8）
middlewares = [ToolErrorHandling(), LoopDetection(), Clarification(), Summary(), ModelLengthFinishReason()]
agent = create_agent(model, tools, state_schema=TaskState, middlewares=middlewares,
                     checkpointer=SqliteSaver(conn))   # 参数名 middlewares（复数）；SqliteSaver 来自 langgraph-checkpoint-sqlite
```

- **状态**：`TaskState(messages, goal, workspace, skill_context, summary)`（deer-flow ThreadState 精简版）
- **工具清单**：`sandbox`（bash/文件）+ `skill`（describe/read）+ **`MCP`（外部服务器工具，经 langchain-mcp-adapters 合并，见 §5.9）**
- **工具执行**（hooks.py）：每个工具过 `before_tool_call(tool, args)` → 白名单/路径校验 → 执行 → `after_tool_call` → 统一封装 `ToolResultMessage` 回环（pi 两级拦截思想）；MCP 工具同样过拦截（外部工具只读优先）
- **截断防护**：由 `ModelLengthFinishReason` middleware 处理——LLM 返回 `finish_reason == "length"` 时**整批拒绝执行工具**（pi 原版，防残缺参数），与 goal/压缩逻辑统一在 middleware 层
- **goal 状态机**（goal.py，评估复用主模型 `goal_evaluator_model: main`，决策 #5）：
  ```
  run 结束 → evaluate_goal_completion(main model, objective+messages) → JSON{satisfied, blocker, reason}
    ├─ satisfied → run_end(completed)
    ├─ blocker=goal_not_met_yet 且 no_progress_count < max(默认5) → 注入隐藏 continuation 消息再跑一轮
    └─ 无进展（最近可见 AI 文本 sha256 签名相同）→ no_progress_count+=1，超限 → run_end 提示用户澄清
  ```

### 5.3 Sandbox 本地执行（G4）

```python
class PathMapping:          # container_path ↔ local_path
class Sandbox:              # 绑定 workspace
    def resolve_local(self, container_path) -> str   # realpath + commonpath 前缀校验，拒绝 '..'/symlink 逃逸
    def to_container(self, local_path) -> str        # 输出反写：宿主路径 → 虚拟路径（掩码正则）
    async def bash(self, command, timeout=120)       # subprocess(cwd=workspace, shell, timeout)；stdout 截断 64KB
    async def read_file(self, path)                  # 上限 512KB
    async def write_file(self, path, content)        # 上限 1MB（deer-flow 80KB 放大，可配置）
    def ls(self, path) / glob(self, pattern) / grep(self, pattern, path)   # 纯 Python 实现，跨平台
```

- **白名单规则**：`resolve_local` 用 `os.path.realpath` 后校验 `os.path.commonpath([p, workspace]) == workspace`；symlink 解析后同样校验；skills 根目录单独放行（只读）。
- **回显掩码**：工具输出里的宿主绝对路径替换成 `/workspace/...`（deer-flow `path_patterns` 段边界 lookahead 防误匹配）。
- **Windows 适配**：`ls/glob/grep` 纯 Python；`bash` 走 `subprocess`（`shell=True` → cmd 语义），`timeout` 强制；异常 fail-soft 回填错误 toolResult。
- **system prompt 硬约束（v2.5 补，防首轮撞墙）**：任务内核 system prompt 必须写死——"文件操作用 ls/glob/grep/read_file/write_file 工具；`bash` 仅作无替代命令时的最后手段（且避免 shell 语法：管道/通配符在 Windows cmd 下语义不同）"。否则 LLM 首轮 `bash ls` 在 cmd 下直接失败，demo 观感极差。

### 5.4 上下文压缩（G6a）

- 手动：`POST /api/tasks/{id}/compact` → 复用 Summary middleware 生成摘要 → JSONL 写 compaction entry + `Overwrite(messages)` 新 checkpoint（deer-flow `compact_thread_context` 同构）。
- 自动（Phase 6 可选）：token 估算超阈值（参考 pi：reserve 16K / keep 20K）触发。

### 5.5 SSE 事件协议（G5 支撑）

| 事件 | 触发 | 前端动作 |
|---|---|---|
| `run_start` | 新建 run | 标记运行中 |
| `message` | LLM 文本增量 / 用户消息 | 流式渲染（复用 ChatBubble） |
| `tool_call` | 工具被提议 | ToolCallCard 展开（名称/参数） |
| `tool_result` | 工具执行完 | ToolCallCard 结果（截断显示，可折叠） |
| `status` | goal 评估 / 压缩 / 打断 | 状态条更新 |
| `run_end` | 完成/中断/超限 | 复位运行状态 |
| `run_error` | 异常 | 红色错误提示 |
| `clarify_requested` | 内核需要澄清（G7，详见 §5.7） | 外壳转述语音询问 + 选项按钮 |
| `progress` | 阶段进度（G7） | 任务面板进度条/日志（不语音） |

事件体统一带 `origin: core|shell`（G7 分流）；`seq` 持久化见 §4.3（SQLite task_events + JSONL event 条目双落，断线重连 `after_seq` 回放）。

契约固化到 `contracts/task_event_contract.json`（前后端各一份 schema + 兼容性条款：新增事件=兼容、改名/删=破坏）。

### 5.6 配置项设计（数据库 / RAG / 执行参数）— conf.yaml 新增 `task_platform` 块

**数据库与路径**：任务元数据走 SQLite（`sqlite3` stdlib，路径可配），会话内容走任务目录内 JSONL；两者**零外部服务**，与现有桌宠记忆（`chat_history/`、`memory_fts`）完全隔离。

**RAG（检索增强）分两层，均默认关闭、开箱可配**：
- **L1 Skill 语义检索**：catalog 默认用 `name+description` 关键词匹配（零成本）；`embedding_enabled: true` 时**复用现有 `vector_embedding_*` 配置**（SiliconFlow `BAAI/bge-m3`，已实测可用，见 8-07 记忆）给 skill 描述建 embedding 索引，LLM 可调 `search_skills(query)` 语义检索——这是"复用不侵入"：不新建 embedding 基建，只加消费方。
- **L2 任务知识检索**（Phase 6 可选）：workspace 内文档检索（grep 为主，可选 embedding 入库），供 agent 回答"这个项目里 X 在哪"类问题。

```yaml
# conf.yaml 新增（独立顶层块，与 character_config 平级）
task_platform:
  enabled: true
  # ---- 数据库 / 路径 ----
  db_path: task_platform.db          # 相对 backend/，可改绝对路径
  tasks_root: tasks/                 # 默认任务根（用户已确认）；新建任务可选任意已存在目录
  # ---- 执行参数 ----
  tool_timeout_sec: 120              # bash 超时（deer-flow 默认 600，本地直执行收紧）
  bash_output_limit: 65536           # bash 输出截断 64KB
  write_limit_bytes: 1048576         # write_file 上限 1MB（deer-flow 80KB 放大）
  read_limit_bytes: 524288           # read_file 上限 512KB
  allow_network: true                # 任务 agent 允许联网（用户已确认；如收紧改 false 或加命令黑名单）
  # ---- Goal 状态机 ----
  max_no_progress: 5                 # 无进展轮数上限
  goal_evaluator_model: main         # 复用主模型（用户已确认；预留字段支持未来换小模型）
  # ---- Skill / RAG ----
  skills:
    root: skills/                    # 技能库根（relative to backend/）
    embedding_enabled: false         # L1 语义检索开关；true 时复用 vector_embedding_base_url/model/api_key
    embedding_top_k: 5
```

> 注：`goal_evaluator_model` 与 `skills.embedding_enabled` 均设计为"默认零新增配置"——主模型复用 conf 现有 `openai_compatible_llm`，embedding 复用现有 `vector_embedding_*`，用户不需要配任何新 key。

### 5.7 双层交互模式（G7 · 人设外壳 / 任务内核）

**用户需求**：执行 Codex 式工程任务时会带大量上下文与提示词，**角色人设提示词会扰乱任务执行**。因此任务执行内核必须"没人设、效率优先"；角色卡只负责在任务两端（开始/结束）和打断澄清时，用人设把结果"告诉我"。

**架构 = 双层提示词 + 语音闸门**：

```
[用户] "帮我重构这个模块的接口"
   │
   ▼
┌─ 角色外壳层 Persona Shell（带人设，复用现有桌宠链路）─────────┐
│  LLM(system = 角色 persona + 意图分类)                        │
│  识别为任务意图 → 创建/唤醒 Task → 语音确认（人设语气，走 TTS）  │
└──────────────────┬───────────────────────────────────────────┘
                   │ 创建 run（仅传 goal + 用户原话，不传 persona）
                   ▼
┌─ 任务内核层 Task Core（零人设，LangGraph）────────────────────┐
│  system prompt = 纯工程指令（工作目录/技能索引/工具/规则）       │
│  执行中：工具调用/推理/进度事件 → SSE（仅 UI 展示，静默不语音）   │
│  需要澄清 → 结构化 {type:"clarify", question, options} 事件     │
└──────────────────┬───────────────────────────────────────────┘
                   │ run_end（summary + artifacts）
                   ▼
┌─ 角色外壳层 Persona Shell（汇报/转述）────────────────────────┐
│  LLM(system = 角色 persona + "转述以下任务结果，简洁自然")      │
│  → tts_manager.speak（人设语气语音汇报）→ 聊天区角色气泡        │
└──────────────────────────────────────────────────────────────┘
```

**要点**：

1. **提示词完全隔离**：任务内核的 system prompt 不注入任何 persona 字段；角色卡的 persona 只进入外壳层两次 LLM 调用（意图确认 / 结果转述）。同一模型、不同 system prompt，**零额外配置**。
2. **语音闸门（TTS 触发规则）**：任务执行中**任何内部输出不触发 TTS**（不走 `tts_manager.speak`）；只允许三种场景发声——任务开始确认、任务结束汇报、打断澄清询问。执行进度只以文字/图标/工具卡片呈现在任务面板。（实现注：外壳语音汇报是 task_route 内新写的轻量链路，TTS 引擎经现有 `tts/tts_factory.py` 创建、复用 VOICEVOX 合成，不动现有 `websocket_handler` 对话链路。）
3. **澄清回环**：内核发出 `clarify_requested` → 外壳转述成人设语气询问（语音）→ 用户回答 → 作为普通 user 消息注入内核（**不包 persona**）→ 继续执行。澄清选项以 UI 按钮呈现（用户点选/语音说都行）。
4. **中途主动问进度**：用户可随时问"进度如何"（聊天界面或桌宠模式都行）→ 外壳用当前 `status` 事件做简短语声回复（带人设），**不打断内核执行**。
5. **输入框模式切换（用户定稿，Codex plan 式，界面零变化）**：**不新增视图、不切换窗口**——现有聊天界面完全保留，只在**输入框区域**加一个模式选择器（chip：`💬 聊天 | 🛠 任务`，仿 Codex 的 auto/plan 切换）。选择「任务」后**整个 UI 无任何变化**，仅输入框语义变化：placeholder 变为任务指令提示、输入框旁出现工作目录指示 chip（点击弹目录选择 Modal）。发送的内容作为**任务指令**发给任务内核（零人设执行）；执行细节（`origin=core`）以聊天区**一条可折叠的「任务执行记录」卡片**呈现（工具调用序列/进度/日志，无角色痕迹，不刷屏）；角色开始/结束/澄清汇报（`origin=shell`）仍为聊天区普通角色气泡 + TTS 语音 + Live2D 表情。桌宠模式语音交互维持现状（聊天语义），任务模式入口在窗口模式输入框（桌宠语音识别为任务意图 → 提示去窗口模式开启，后期可自动创建）。

**新增 SSE 事件**：

| 事件 | 触发 | 前端动作 |
|---|---|---|
| `clarify_requested` | 内核需要澄清 | 外壳转述为语音询问 + 选项按钮 |
| `progress` | 阶段进度 | 任务面板进度条/日志（**不语音**） |
| `run_end` | 完成/中断/超限 | 触发外壳汇报（语音） |

### 5.8 记忆边界与任务记忆体系（G7 配套 · 用户确认 2026-08-08）

**结论先行**：现有四层记忆（core 画像 / FTS / 向量 RRF / memory_v2）是**角色域（关系记忆）**——记录"主人喜欢美式咖啡、自称哥哥"这类**用户画像与偏好**，服务聊天人设；它**不适合工程任务**（工程记忆需要的是"这个项目怎么改的、测试怎么跑、决策是什么"）。任务平台**不复用、也不污染**角色记忆，各自独立：

| 记忆域 | 内容 | 载体 | 读取方 |
|---|---|---|---|
| 角色域（现状，不动） | 用户画像/关系/偏好（美式咖啡…） | `chat_history/` + memory_fts + vector + memory_v2 | 聊天模式的 persona 注入 |
| 任务域（新增） | 任务消息/工具调用/决策/摘要 | JSONL 会话树 + SQLite runs.summary | 任务内核（LangGraph checkpoint） |

**任务域记忆体系（三层）**：

1. **任务级**：每任务 JSONL 会话树（完整消息 + 工具调用 + 结果，可回放/分支/续接）——任务"自包含"记忆，LangGraph checkpoint 同库持久化。
2. **执行级**：`runs.summary`（每次 run 的摘要）+ compaction entry（长任务旧消息摘要化）——跨 run 续接时上下文不丢。
3. **项目级**（Phase 6+ 可选）：`project-notes.md`（任务域长期约定：测试命令、提交规范、技术栈笔记——**与 AGENTS.md 同构**），任务 agent 启动时可选注入，实现"跨任务的工程经验"。

**隔离规则（硬约束）**：

- 任务内核 system prompt **不注入任何角色记忆内容**（与 persona 隔离同理，避免"美式咖啡"这类信息干扰工程执行）。
- 角色聊天 **不读取**任务 JSONL（聊天历史与任务历史各自独立）。
- 可选单向桥（Phase 6+）：任务完成后用户明确要求时，把 run summary 沉淀进 project-notes——只出不进，且需用户主动。

### 5.9 MCP 集成（G8 · 用户确认 2026-08-08：采用智能体平台方案）

**现状问题**（用户指出"MCP 完全没用上"）：现有 MCP 双向都闲置/失效——对外服务端（`:12394`，给外部 Agent 反向控制桌宠）无实际调用方；对内客户端 `mcpp/` 默认关，且 `mcp_servers.json` 里 `time`（`mcp-server-time` 与新版 mcp SDK 的 `McpError` 命名冲突 ImportError）、`ddg-search`（PyPI 无此包）全部连接失败 → MCP 工具数为 0。

**智能体平台方案**（deer-flow 同款思路）：MCP 不是"桌宠装饰"，而是**任务内核的工具来源**——任务 agent 需要时通过 MCP 调用外部能力（网页抓取、搜索、时间、文件系统等），与 sandbox/skill 工具同列表、同拦截、同展示。

```python
# mcp_client.py —— 结构示意（参考 deer-flow backend/packages/harness/deerflow/mcp/）
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({
    "fetch":  {"command": "uvx", "args": ["mcp-server-fetch"], "transport": "stdio"},
    "time":   {"command": "uvx", "args": ["mcp-server-time"],  "transport": "stdio"},
})
mcp_tools = await client.get_tools()          # -> list[BaseTool]，并入 agent tools
```

**设计要点**：

1. **依赖**：`langchain-mcp-adapters`（官方适配器，deer-flow 同款），Phase 0 一并安装。
2. **配置**（conf.yaml `task_platform.mcp.servers`，与现有 `mcp_servers.json` 解耦）：
   ```yaml
   task_platform:
     mcp:
       servers:
         - name: fetch            # 网页抓取（免费无 key）
           transport: stdio
           command: uvx
           args: [mcp-server-fetch]
           enabled: true
         - name: time             # 时间/时区（修复版，规避 McpError 冲突）
           transport: stdio
           command: uvx
           args: [mcp-server-time]
           enabled: true
         # - name: my-remote      # streamable-http 远程服务器
         #   transport: http
         #   url: http://127.0.0.1:xxxx/mcp
         #   headers: {}
         #   enabled: false
   ```
3. **生命周期**：任务服务启动时按配置连接各服务器，**单个失败 fail-soft**（记日志、跳过），不阻塞后端启动；工具动态合并进 agent tools，可通过 `GET /api/tasks/tools` 查看可用性。
4. **安全**：MCP 工具与 sandbox 工具同等过 `before_tool_call` 拦截；外部服务器**只读优先**（fetch/search 类），写入型服务器（filesystem 等）默认不启用。
5. **与现有 mcpp 的关系**：任务平台用官方适配器；`mcpp/` 保留给桌宠对话域（遗留，**不再修复**）；`mcp_servers.json` 的坏配置不迁移，任务平台用自己配置。
6. **候选内置服务器**（执行时逐一实测 PyPI 包名/版本，易漂移）：`fetch`（网页抓取）、`time`（时间）、搜索类（duckduckgo 变体，PyPI 实测为准）；**全部失败也不影响主链路**（sandbox+skill 已够跑通演示）。

---

## 6. API 设计（完整清单）

| 方法 | 路径 | 说明 | Phase |
|---|---|---|---|
| GET | `/api/tasks` | 任务列表 | 1 |
| POST | `/api/tasks` | 新建 `{title, goal?, workspace?}`；缺省 → 默认根建 `task-<id>/` | 1 |
| GET | `/api/tasks/{id}` | 详情 + 会话历史（JSONL 投影） | 1 |
| PATCH | `/api/tasks/{id}` | 改 title/goal/status | 1 |
| DELETE | `/api/tasks/{id}` | 删任务（**不删工作目录**） | 1 |
| POST | `/api/tasks/{id}/runs` | 发起执行 `{message?}` | 2 |
| GET | `/api/tasks/{id}/runs/stream` | **SSE** 事件流（含历史回放：先发已有消息事件，再实时） | 2 |
| POST | `/api/tasks/{id}/interrupt` | 打断（AbortController 取消 LLM 流 + 工具） | 2 |
| POST | `/api/tasks/{id}/compact` | 手动上下文压缩 | 6 |
| GET | `/api/workspaces/scan?path=` | 目录扫描（深度 2，新建任务对话框用） | 1 |
| GET | `/api/workspaces/root` | 默认任务根路径 | 1 |
| GET | `/api/skills` | skill 列表（索引） | 3 |
| GET | `/api/skills/{name}` | skill 详情 | 3 |
| GET | `/api/tasks/tools` | 任务 agent 可用工具列表（sandbox/skill/MCP 及各自状态） | 2 |

---

## 7. 前端设计（G5）

### 7.1 结构

```
frontend/src/
├── chat/ChatInput.tsx            # 输入框内嵌工具栏 + 模式下拉菜单：💬 聊天 | 🛠 任务（仿 WorkBuddy，参考 docs/task-mode-ui-prototype.html；现有文件 96 行，原位改造）
│                                  # 任务模式：placeholder 变任务指令提示 + 工具栏出现工作目录 chip（点击弹目录选择）
├── dashboard/TaskCreateModal.tsx  # 新建任务 Modal：名称 + 目标 + 文件夹选择（默认路径 Tab | 已存在目录树 Tab）
├── components/TaskRunCard.tsx     # 聊天区「任务执行记录」卡片（可折叠：ToolCallCard 序列/进度/日志，无角色痕迹）
├── core/api/tasks.ts              # REST + SSE 消费（fetch + ReadableStream，AbortController）
├── components/ToolCallCard.tsx    # 工具调用卡片（参数 JSON 折叠 / 结果截断 / 状态点）
└── styles/global.css              # task-* 样式（沿用暗夜月光设计体系）
```

**视图模型（零新视图）**：现有聊天界面（窗口模式/桌宠模式）**不做任何结构性改动**；仅 ChatInput 增加模式 state（`chatMode: 'chat' | 'task'`，持久化）。任务模式时聊天区消息流同时承载：用户任务指令气泡、`TaskRunCard`（执行细节）、角色汇报气泡（开始/结束/澄清）。

### 7.2 关键交互

- **模式切换**：ChatInput 顶部 chip（💬 聊天 | 🛠 任务，仿 Codex auto/plan）——切换只改输入框语义，**界面无变化**；选择持久化（localStorage）。
- **新建任务**：任务模式下首次发送前无活动任务 → 自动弹 `TaskCreateModal`（默认路径 Tab：`backend/tasks/<任务名>` 自动创建 | 已有目录 Tab：`/api/workspaces/scan` 两级树点选）→ 创建后进入任务流程；已有活动任务时输入框旁显示工作目录 chip（`📁 <workspace>`），直接发送 = **续接该任务**。
- **任务执行展示**：`origin=core` 事件 → 更新聊天区当前 `TaskRunCard`（可折叠，内含 ToolCallCard 序列 + 进度 + 日志，**不刷屏**）；`origin=shell` 事件 → 角色气泡 + TTS 语音 + Live2D 表情；`clarify_requested` → 角色气泡询问 + 选项按钮（点选/回复即回内核）。
- **运行指示**：任务运行中，发送键临时变「打断」（复用现有 isReplying 交互模式）+ 输入框旁呼吸状态点。
- **SSE 消费**：`useTaskStream(taskId, runId)` hook——连接 → 按 `seq` 去重/排序 → 断线后带 `last_seq` 重连 → 历史回放（deer-flow `useStream` 思路）。
- **G7 消息分流**：SSE `message` 事件带 `origin`——`core`（进 TaskRunCard）与 `shell`（角色气泡 + TTS）。

---

## 8. 分阶段实施计划

> 每阶段：改动文件 → 动作 → 验收标准（执行 AI 逐阶段完成并汇报）。

### Phase 0 — 依赖与冒烟（0.5 天）
- **改动**：`backend/pyproject.toml`、`backend/uv.lock`、`task_platform/__init__.py`
- **动作**：按 deer-flow 锁的版本区间 `uv add`（**版本对齐，勿裸装**）：
  `langgraph>=1.2.9,<1.3`、`langchain>=1.3`、`langchain-openai`、`langchain-mcp-adapters>=0.2.2`、**`langgraph-checkpoint-sqlite>=3.1.0,<3.2`**（SqliteSaver 必需，Phase 0 漏装 Phase 2 必卡）；
  `pip check`；最小 LangGraph graph（ChatOpenAI + 1 fake tool）跑通
- **验收**：`.venv/Scripts/python.exe -c "import langgraph, langchain, langchain_openai, langchain_mcp_adapters, langgraph.checkpoint.sqlite"` OK；冒烟脚本通过
- **参考**：deer-flow `backend/langgraph.json`、`backend/pyproject.toml`（含完整依赖版本区间）

### Phase 1 — 任务 CRUD + 存储（1 天）
- **改动**：`task_platform/{models.py, session.py, task_route.py}`、`server.py`、`contracts/task_event_contract.json`
- **动作**：SQLite tasks/runs 表 + JSONL 会话骨架 + REST CRUD + workspaces scan + 默认根自动创建
- **验收**：curl CRUD 全通；scan 返回真实目录；`backend/task_platform.db` 与任务 JSONL 生成
- **参考**：deer-flow `routers/threads.py`；pi `session-manager.ts`（JSONL 格式）

### Phase 2 — LangGraph Agent + Sandbox + MCP（2-2.5 天，拆 2a/2b 两小步）
- **Phase 2a — Agent + Sandbox + SSE + interrupt（1.5 天）**：`task_platform/{llm_adapter.py, state.py, graph.py, middleware.py, sandbox.py, hooks.py}`、`task_route.py`
  - 动作：ChatOpenAI 接入 conf；create_agent + **5** middleware（含 ModelLengthFinishReason）；白名单沙箱工具；runs/stream SSE + interrupt
  - 验收：SSE 收到完整事件流；agent 白名单内 bash/写文件/读文件成功；**`../`、绝对路径逃逸、symlink 越界被拒**；打断生效；`finish_reason=length` 拒绝执行工具；断线带 `after_seq` 重连可回放（含后端重启场景）
- **Phase 2b — MCP 工具接入（G8，0.5-1 天）**：`task_platform/mcp_client.py`、conf.yaml（`task_platform.mcp.servers`）
  - 动作：`uvx mcp-server-fetch/time` 本机**预热**（首次调用联网下载，走 Clash 代理 7897 / `UV_INDEX_URL` 镜像）；`MultiServerMCPClient` 合并工具；失败 fail-soft；`GET /api/tasks/tools` 列出 sandbox/skill/MCP 工具及状态
  - 验收：MCP 可用工具并入 agent tools 并同过拦截；**全部 MCP 失败也不阻塞**（sandbox+skill 已够跑通演示）
- **参考**：deer-flow `agents/factory.py` + `lead_agent/agent.py` + `agents/middlewares/*`（AgentMiddleware 协议）+ `community/aio_sandbox/local_backend.py` + `mcp/*`；pi `agent-loop.ts`（beforeToolCall block）

### Phase 3 — Skill 系统（0.5-1 天）
- **改动**：`task_platform/skills/*`、`graph.py`（skill_index + 2 工具）、`backend/skills/public/{python-script,file-operations,research-notes}/SKILL.md`、`contracts/skill_contract.json`
- **动作**：catalog 扫描 + describe_skill/read_skill + system prompt 索引注入
- **验收**：agent 主动 describe_skill 定位 python-script 并按其指引完成任务；`.disabled` 目录被跳过
- **参考**：deer-flow `skills/*`；pi `skills.ts`（XML 索引格式）

### Phase 4 — Goal 状态机（0.5-1 天）
- **改动**：`task_platform/goal.py`、`middleware.py`（Clarification 扩展）
- **动作**：evaluate_goal_completion 复用主模型评估（决策 #5，预留 `goal_evaluator_model` 换小模型） + no_progress sha256 签名检测 + 隐藏 continuation
- **验收**：目标达成自动 run_end(completed)；无进展 5 轮后提示用户澄清；无死循环
- **参考**：deer-flow `runtime/goal.py` + `runtime/runs/worker.py`

### Phase 5 — 前端任务模式（1.5-2 天）
- **改动**：`chat/ChatInput.tsx`（输入框工具栏 + 模式下拉菜单：💬聊天|🛠任务，参考 `docs/task-mode-ui-prototype.html`）、`TaskCreateModal.tsx`、`TaskRunCard.tsx`（可折叠执行记录）、`core/api/tasks.ts`、`ToolCallCard.tsx`、`global.css`
- **动作**：输入框内嵌工具栏（模式按钮上拉菜单 + 工作目录 chip + 附件/提示词/语音图标）+ 首发送自动弹新建任务 Modal + 聊天区执行记录卡片 + SSE hook + G7 分流（origin=core→TaskRunCard / shell→角色气泡+TTS）
- **验收**：模式菜单切换仅输入框语义变化（placeholder/目录 chip），界面零改动；任务模式发送→自动建任务/续接→聊天区出现执行记录卡片（可折叠工具调用）+ 角色汇报气泡；刷新持久；断线重连（seq 锚点）；HMR 生效
- **参考**：`docs/task-mode-ui-prototype.html`（交互已定稿）；deer-flow `frontend/src/components/workspace/*`

### Phase 6 — 打磨与扩展（1-1.5 天，优先级已拍板）
按 **①压缩 → ②sub-agent → ③extensions 钩子** 顺序实施（理由：压缩解决长任务上下文爆掉的刚需；sub-agent 解决复杂任务拆分；extensions 钩子是放大器、非必需品，最后做；MCP 已提前到 Phase 2）：

- **6a 上下文压缩**（必做）：`POST /api/tasks/{id}/compact` 手动 + token 估算自动阈值（参考 pi：reserve 16K / keep 20K）；复用 Summary middleware + JSONL compaction entry + `Overwrite(messages)` 新 checkpoint
- **6b sub-agent**（推荐做）：dwsy `agents/*.md` frontmatter 风格定义（`enabled/description/tools/prompt_mode`）+ 委派工具 `delegate(agent_name, task)`，先内置 2 个：`explore`（只读检索）/ `worker`（执行）
- **6c extensions 钩子**（最后，可选）：Python 注册表 `before_tool_call / after_tool_call / before_agent_start`（pi ExtensionAPI 子集），`plugins/` 目录约定（dwsy extensions 思想：`plugins.disabled/` 后缀即开关）
- **验收**：压缩后长任务可续跑；`delegate` 委派成功
- **参考**：deer-flow `runtime/context_compaction.py`；pi `extensions/*`；dwsy `agents/*.md`

**总计约 7-9 个工作日**（以执行 AI 实际节奏为准；比 v2.4 多 1 天，主要给 Phase 2b 的 MCP 实测预热与 SSE 断线重连调试）。

---

## 9. 风险与坑

1. **Python 运行时**：必须系统 Python 3.12 venv（`backend/.venv` 已是 3.12.10 ✓）；managed 3.13 装不了 pydantic_core，LangGraph 必炸。
2. **LangGraph 版本（v2.5 对齐）**：参考仓库实际锁 **langgraph 1.2.9 / langchain>=1.3**（1.x 时代，非 0.2.x）。1.x 的 middleware 是 `AgentMiddleware` 异步协议，实现前先读 deer-flow `agents/middlewares/*` 与 `factory.py` 的 `create_agent(...)` 调用（参数名 `middlewares` 复数、`state_schema`、`checkpointer`）；Phase 0 按 deer-flow 版本区间锁进 pyproject。
3. **DeepSeek 工具调用**：支持 function calling（现有 basic_memory_agent 实证）；但注意并发限制与 tools 描述长度（skill_index 保持精简，MCP 工具描述可裁剪）。
4. **Windows bash 语义**：`bash` 工具走 `subprocess` cmd 语义（管道/通配符与 Linux 不同）→ **system prompt 硬约束"文件操作优先 ls/glob/grep 工具，bash 仅最后手段"**（§5.3）；文件类工具纯 Python 实现；路径用 `Path` 而非字符串拼接。
5. **白名单逃逸**：realpath + commonpath 双重校验；symlink 解析后校验；write_file 上限；bash 输出截断；禁止 `cd` 出 workspace 后执行（执行前强校验 cwd）。
6. **沙箱环境限制**（WorkBuddy 托管后端时）：任务 subprocess 受 WorkBuddy 沙箱约束，`PermissionError` fail-soft；用户终端运行后端（README 标准方式）无此问题。
7. **SSE 与现有 WS 并存**：任务模式独立 SSE，不碰 `websocket_handler.py`；前端 hook 需处理断线重连（`after_seq`）+ `AbortController`；seq 由 SQLite `task_events` 表 + JSONL event 条目双落（§4.3），后端重启不丢锚点。
8. **JSONL 坏行**：解析容错跳过；compaction entry 引用完整性（firstKeptEntryId 必须存在）；双写一致性按 §4.2 真相源规则执行（先 JSONL 后 checkpoint）。
9. **SQLite 并发（v2.5 补）**：WAL + `check_same_thread=False` + 每请求新连接（§4.1）。
10. **MCP 服务器可用性**（G8）：PyPI 包名/版本漂移 + SDK 兼容坑（`time` 的 McpError 冲突是前车之鉴）——候选服务器逐一实测，`uvx` 首次调用需联网（Phase 2b 先预热，走 Clash 代理 7897/镜像）；每个 server 独立 fail-soft，绝不因 MCP 失败阻塞后端或任务主链路。
11. **Electron 前端**：纯渲染进程改动，HMR 生效；不要动主进程（除非需要新 IPC，本期不需要）。
12. **git 提交边界（v2.5 补）**：`backend/tasks/`（任务工作目录）、`backend/task_platform.db` 必须进 `.gitignore`（项目大文件教训：构建产物/数据文件不入库）。

---

## 10. 决策确认记录（用户已拍板，2026-08-08）

| # | 开放问题 | 决策 | 影响 |
|---|---|---|---|
| 1 | 默认任务根 | **`backend/tasks/`**（项目内） | §5.6 `tasks_root`；新建任务可选任意已存在目录 |
| 2 | 任务 agent 联网 | **允许**（bash curl 等） | §5.6 `allow_network: true`；如需收紧改配置即可 |
| 3 | 纯聊天轻量模式 | **不需要**——任务必绑工作目录 | 不做"无目录会话"，简化任务模型 |
| 4 | Phase 6 优先级 | **压缩 → sub-agent → extensions 钩子**（MCP 提前到 Phase 2，由执行者拍板） | §8 Phase 6 已按序拆 6a-6c |
| 5 | goal 评估模型 | **复用主模型**（`goal_evaluator_model: main`，`max_no_progress: 5`） | 零新增配置，§5.2/§5.6 |
| 6 | 任务执行是否带人设 | **双层交互（G7，用户 2026-08-08 确认）**：任务内核零人设（效率优先），仅开始/结束/打断澄清由人设外壳语音转述 | §5.7 双层架构 + SSE 新增 `clarify_requested`/`progress` |
| 7 | 任务记忆用什么 | **不复用角色记忆**：任务域独立三层记忆（JSONL 会话 + runs.summary/compaction + project-notes 可选），与角色域隔离不互读 | §5.8 记忆边界 |
| 8 | 项目 MCP 怎么用 | **采用智能体平台方案（G8，用户 2026-08-08 确认）**：MCP 作为任务内核工具来源（`langchain-mcp-adapters` 接入外部服务器），替换当前闲置/失效的桌宠 MCP 用法；mcpp 保留但不再修复 | §5.9 MCP 集成，Phase 2 落地 |

---

## 附录 A：执行 AI 的开工清单

1. 通读本文档 §2-§8，对照阅读三份参考报告（本会话研究产出）与参考仓库关键文件：
   - deer-flow：`skills/*`、`agents/{thread_state,goal_state,factory}.py`、`agents/middlewares/*`（AgentMiddleware 协议 + `model_length_finish_reason_middleware.py`）、`community/aio_sandbox/local_backend.py`（无 `sandbox/local/`）、`runtime/{goal,context_compaction}.py`、`app/gateway/routers/{threads,thread_runs}.py`、`contracts/run_event_stream_contract.json`
   - pi：`packages/ai/src/types.ts`、`packages/agent/src/agent-loop.ts`、`packages/coding-agent/src/core/{session-manager,skills,system-prompt,compaction}.ts`
   - dwsy：`skills/skill-creator/SKILL.md`（三级披露范例）、`agents/explore.md`（frontmatter 契约）、`categories.json`
2. 执行 Phase 0 验证依赖（含 `langgraph-checkpoint-sqlite`），然后逐阶段推进，每阶段结束更新本文档状态并汇报。

## 附录 B：术语

- harness = 驾驭工程（skill + agent + 工具编排能力）
- lead agent = 主智能体（任务执行的单一控制者）
- 惰性加载 = 技能内容不预载入上下文，按需读取
- middleware = LangGraph 请求管线中间件（错误处理/循环检测等横切能力）
- compaction = 上下文压缩（旧消息摘要化）
