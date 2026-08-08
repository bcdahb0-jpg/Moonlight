# Agent Harness 计划书审核报告（v2.4）

> 审核日期：2026-08-08
> 审核对象：`docs/agent-harness-platform-plan.md`（v2.4）
> 审核方式：对照参考仓库（deer-flow / pi-agent / dwsy-agent 实际代码）+ 现有 Moonlight 代码逐项核实，非纯纸面评审

---

## 一、总体结论

**可以实现。架构方向正确，关键技术假设在参考仓库与现有代码中均能找到锚点，无需推翻重来。**

但发现 **5 处必须修正的硬伤**（依赖遗漏 ×1、版本认知过时 ×1、参考路径错误 ×2、会话双写设计缺口 ×1）和 **8 项执行风险**（均可控）。硬伤不修会卡 Phase 2/3，设计缺口不补会在续接任务时出现状态漂移。修正后按 Phase 0→6 推进是可行的。

---

## 二、已证实成立的技术假设（绿）

| # | 计划书假设 | 核实结果 |
|---|---|---|
| 1 | `langchain.agents.create_agent` + middleware 链装配 | **证实**。deer-flow `agents/factory.py:168` 确实 `create_agent(state_schema=..., checkpointer=..., middlewares=...)` |
| 2 | goal 状态机：evaluate_goal_completion / should_continue_goal / hide_from_ui / sha256 无进展签名 | **逐项证实**。deer-flow `runtime/goal.py`（270/330/358/405 行）全部存在，可原样参考移植 |
| 3 | JSONL 会话树（id/parentId/leafId + header 存 cwd） | **证实**。pi-agent `packages/coding-agent/src/core/session-manager.ts` 存在 |
| 4 | Skill 两级惰性加载（索引注入 + describe/read） | **证实**。deer-flow `skills/*` + pi `skills.ts`（XML 索引）双平台印证 |
| 5 | MCP 经 `langchain-mcp-adapters` 接入 | **证实**。deer-flow pyproject 依赖 `langchain-mcp-adapters>=0.2.2`，`deerflow/mcp/*` 存在 |
| 6 | conf_bridge 读 `openai_compatible_llm` 块（DeepSeek） | **证实**。`backend/conf.yaml:132` 存在，base_url 默认 `https://api.deepseek.com/v1` |
| 7 | RAG 复用 `vector_embedding_*`（bge-m3） | **证实**。`conf.yaml:57-59` SiliconFlow bge-m3 配置在 |
| 8 | 路由注册 `init_xxx_route()` + `include_router` | **证实**。`server.py` setup_routes 已有 12+ 个同模式路由 |
| 9 | Python 3.12 venv 兼容 | **证实**。pyproject `requires-python = ">=3.10,<3.13"`，`.venv` 3.12.10 ✓ |
| 10 | MCP 生命周期可挂 server 启动 | **证实**。`server.py` 有 `@app.on_event("startup")` 挂点 |
| 11 | 契约可并列新增 | **证实**。`contracts/` 现有 3 文件，新增 2 个 json 无冲突 |
| 12 | 前端输入框可改 | **证实**。`frontend/src/chat/ChatInput.tsx`（96 行）存在；`docs/task-mode-ui-prototype.html` 存在 |

---

## 三、必须修正的硬伤（红，不修会卡住）

### 1. Phase 0 依赖清单漏 `langgraph-checkpoint-sqlite`（必卡 Phase 2）

计划书 §5.2 graph.py 用了 `SqliteSaver(conn)`，但 §8 Phase 0 的 `uv add` 只有 `langgraph langchain langchain-openai langchain-mcp-adapters`。deer-flow 明确依赖：

```toml
"langgraph>=1.2.9,<1.3",
"langchain>=1.3",
"langchain-mcp-adapters>=0.2.2",
"langgraph-checkpoint-sqlite>=3.1.0,<3.2",
```

**修正**：Phase 0 补 `langgraph-checkpoint-sqlite`（锁 `<3.2`）。建议直接照 deer-flow 的版本区间写进 pyproject，别裸 `uv add`。

### 2. §9.2 版本认知过时："0.2.x 有变动" → 参考仓库实际是 langgraph 1.2.9 / langchain 1.3+

计划书写"LangGraph 版本：create_agent/middleware API 在 0.2.x 有变动"，但参考仓库锁的是 **langgraph 1.2.9 + langchain>=1.3**（1.x 时代）。1.x 的 middleware 是 `AgentMiddleware` 异步协议，不是 0.2.x 那套。

**修正**：计划书 §9.2 改为"按 langgraph 1.2.x / langchain 1.3+ 语法实现，middleware 照 deer-flow `agents/middlewares/*` 的 AgentMiddleware 协议写"。好在实现时本来就以参考仓库为准，此条主要是防止执行者按 0.2.x 语法写废代码。

### 3. 参考路径错误 ×2（执行者会找不到文件）

- §5.3 / §8 Phase 2 写 "deer-flow `sandbox/local/*`" —— **实际在 `deerflow/community/aio_sandbox/local_backend.py`**（本地执行后端），无 `sandbox/local/` 路径。
- §7.1 写 `frontend/src/components/ChatInput.tsx` —— **实际在 `frontend/src/chat/ChatInput.tsx`**。
- 顺带：§2.1 说"14 个 middleware 取 4 个（ToolErrorHandling/LoopDetection/Clarification/Summary）"，但实际清单里还有 **`model_length_finish_reason_middleware.py`**（finish_reason=length 截断防护——对 DeepSeek 场景恰好是重要的）和 `skill_activation_middleware`。§5.2 把截断防护写在 hooks 层、middleware 列表却没它，逻辑重复且漏配。

**修正**：路径改为 `community/aio_sandbox/local_backend.py`、`chat/ChatInput.tsx`；middleware 取 **5 个**（+ModelLengthFinishReason），截断防护逻辑统一放 middleware，hooks 层只做 before/after_tool_call。

### 4. 会话双写未定义"真相源"与写入时序（续接任务会状态漂移）

JSONL 会话树 + LangGraph SqliteSaver checkpoint 双存储，计划书没定义谁是真源、何时写哪边：

- 续接任务：agent 上下文从 checkpoint 恢复，UI 历史从 JSONL 投影 → 两处不一致时（如 compaction 只改了一边）历史与执行状态漂移。
- 建议：**JSONL = append-only 审计/UI 投影源**（每轮消息先落 JSONL 再执行 agent）；**checkpoint = 执行源**（每 run 结束持久化）；compaction 两边同时改（JSONL 写 compaction entry + `Overwrite(messages)` 写新 checkpoint），这个计划书提了，但要加"写入顺序"约束。

### 5. SSE seq 锚点无持久化载体（重启后端断线重连失效）

§5.5 seq 按 task 单调递增、断线重连拉历史，但 JSONL 只设计了 `message/compaction` 两类条目——`run_start/status/tool_call/tool_result` 事件断线后从哪重放？tool_call 可从 message 的 toolCall 块重建，但 status 类事件无源。

**修正（二选一，Phase 2 前敲定）**：
- a) JSONL 增加事件级条目（`{"type":"event","event_type":...}`），seq = 文件行号派生，全量可重放；
- b) 明确"重放 = JSONL 消息投影 + 工具调用块重建，status/run_start 不重放，前端只显示当前 run 状态"，并在契约里写死。

---

## 四、执行风险（黄，可控，提前认账）

1. **Windows bash 工具 = cmd 语义**（§9.4 提了但不够）：LLM 生成的 `ls/grep/管道` 在 cmd 下大量失败。**必须在任务内核 system prompt 写死**："文件操作用 ls/glob/grep/write_file/read_file 工具，bash 仅作无替代命令时的最后手段"。否则 agent 首轮 `bash ls` 就撞墙，demo 观感极差。
2. **`uvx` 跑 MCP stdio 服务器首次调用联网下载**：沙箱/代理环境下 Phase 2 实测前先 `uvx mcp-server-fetch` 预热（或直接 pip 装进 .venv 再用 `python -m` 启动），否则 MCP 实测会卡网络。fail-soft 设计正确，demo 不依赖 MCP（计划书自己也这么写了）。
3. **sqlite3 多线程**：FastAPI 多请求并发访问 `task_platform.db` → 开 WAL + `check_same_thread=False` + 每请求新连接。计划书没提。
4. **DeepSeek 工具数量/描述长度**：sandbox + skill_index + MCP 工具可能逼近工具上限，skill_index 保持精简 ✓ 计划书已提，MCP 工具描述再裁剪一层。
5. **外壳语音汇报是新链路，非复用现有 WS**：§5.7 的 Persona Shell（转述 LLM 调用 + TTS 合成 + origin=shell 事件）实际是 task_route 内新写的一条轻量链，要接 TTS 引擎（`tts/tts_factory.py`）与 Live2D 表情。工作量略低估，Phase 5 预留半天。
6. **`backend/tasks/`、`task_platform.db` 进 .gitignore**：用户 git 惯例（target/ 大文件教训），别让任务目录和 DB 被提交。
7. **时间线 6-8 天偏乐观**：Phase 2 塞了 SSE+interrupt+白名单逃逸测试+MCP 实测四件事，建议拆 2a（agent+sandbox+SSE+interrupt）/ 2b（MCP+tools 列表）。
8. **create_agent + 自定义 TaskState 的 checkpoint 模式限制**：deer-flow factory 里有"state_schema + checkpointer 组合下 delta 模式互斥"的警告逻辑（`adapt_state_schema_for_mode`），1.x 下带 reducer 的 TypedDict 才需要关注，照抄 deer-flow 的处理即可。

---

## 五、建议动作（按序）

1. **改计划书**：补 §8 Phase 0 依赖（+`langgraph-checkpoint-sqlite>=3.1.0,<3.2`）；§9.2 版本改 1.2.x/1.3+；§5.3/§8 参考路径改 `community/aio_sandbox/local_backend.py`；§7.1 改 `chat/ChatInput.tsx`；§2.1/§5.2 middleware 改 5 个。
2. **§4.2/§5.5 补设计**：真相源规则 + seq 持久化方案（二选一），Phase 2 开工前定稿。
3. **§5.2 system prompt 加 Windows 命令约束**（bash 兜底、工具优先）。
4. Phase 0 顺带验证 `uvx mcp-server-fetch/time` 在本机代理下能否跑通，跑不通就在 conf 里关掉，不阻塞。

---

## 六、结论

计划书研究扎实（三平台提炼准确、决策已拍板、模块边界清晰），**可实现**。修正第三节 5 处硬伤 + 补第四节 2 个设计细节后，按 Phase 0→6 推进；预计实际工期 **7-9 天**（比计划多 1 天，主要给 Phase 2 的 MCP 实测和 SSE 重连调试）。
