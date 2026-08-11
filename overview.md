# 番茄钟任务反馈链路改进 — 已完成实施

基于 `experience-review-pomodoro-task.md` 的诊断，5 项改进全部落地。**前端 tsc 0 错误；后端 110 passed（仅 AGENTS.md 记录的已知基线 TTS 过滤器失败）**；新增接口用 TestClient + monkeypatch 做了端到端探针验证。

## 已实施的改动

### 1. 后端：任务结果协议拆分（P0）
- `basic_memory_agent.py:_call_delegate_task` 改为返回**结构化 dict**（ok/status/summary/error），不再拼裸字符串。
- `_simple_chat_with_builtin_tool`：
  - 成功 → `yield {"type": "task_result", "status": "completed", ...}` 直达聊天区；
  - 失败 → **不再 yield task_result**（HTTP 502 等不再以「【任务结果】」暴露给用户），错误只注入 tool message，由 LLM 口语转述 + 给可操作建议。
- `single_conversation.py`：task_result 仅 `status ∈ (completed, success, "")` 才推送；name 回退链补全 `character_name or conf_name`。

### 2. 任务产物路径写入 task brief（P0）
- `task_route.py:_collect_task_artifacts()`：从事件流解析 `write_file/str_replace/browser_screenshot` 成功写盘的文件绝对路径（配对 tool_result 排除 is_error，解析后只留存在的文件，去重限 8 条）。
- `_inject_task_brief()` 简报追加「工作目录：<path>」「生成文件：<paths>」——用户问"文件在哪"时 LLM 直接依据简报回答，不再重复委派。
- `_TOOL_GUIDANCE` 增加规则：追问历史任务的结果/文件位置时优先用【任务简报】，不要再次 delegate。

### 3. 前端：产物展示 + 打开入口（P1）
- `useTaskEventStream.tsx`：新增 `artifacts` 字段（与后端同款解析规则）+ `displayArtifactPath` 助手。
- `TaskStreamPanel.tsx`（聊天流内实际使用的卡片）：完成态展示「生成文件」列表（点击在资源管理器中定位）+「📂 打开工作目录」按钮 + 内联错误提示。
- `TaskRunCard.tsx`（右栏/旧版）：同步加了同样的能力。
- `tasks.ts:openTaskDir()` + 后端新路由 `POST /api/tasks/{id}/open`：Windows `explorer /select` / `os.startfile`，macOS `open -R`，Linux `xdg-open`；**目录穿越防护**（path 必须解析后在工作目录内，否则 400）；沿用 `_is_local_request` 本机守卫。

### 4. 统一角色名来源（P1）
- 后端 task-result 消息 name 回退链与 store_message/process_agent_output 对齐（`character_name → conf_name`）；前端本就 `name → confName → 'AI'` 回退，链路已一致。

### 5. delegate 瞬时重试（P2）
- `_call_delegate_task` 对 5xx / 网络异常最多重试 2 次（指数退避 1s/2s）；4xx 与明确任务失败不重试。

## 测试
- 新增 `test_success_yields_task_result_event` / `test_failure_does_not_yield_task_result`（后端 tests/test_chat_builtin_tool.py）。
- 后端：test_chat_builtin_tool + test_smoke + task_platform 系列 110 passed。
- 前端：`npx tsc --noEmit` 0 错误。
- 探针验证：`_collect_task_artifacts`（只收成功写盘）、open 路由（打开目录 / 穿越防护 400 / explorer /select）。

## ⚠️ 需要重启后端生效
后端改动需要重启 `backend`（`cd backend && ./.venv/Scripts/python.exe run_server.py`）。运行中的旧实例不会加载新代码；前端 Vite HMR 自动生效。重启时注意不要连带杀掉 VOICEVOX（`taskkill /F /PID` 不带 `/T`）。

## 关键文件
- backend/src/open_llm_vtuber/agent/agents/basic_memory_agent.py
- backend/src/open_llm_vtuber/conversations/single_conversation.py
- backend/src/open_llm_vtuber/task_platform/task_route.py
- backend/tests/test_chat_builtin_tool.py
- frontend/src/task/TaskStreamPanel.tsx / useTaskEventStream.tsx / TaskRunCard.tsx
- frontend/src/api/tasks.ts
- frontend/src/styles/global.css

---

## 第二轮：任务完成后 AI 无回复 + 前轮 4 点建议（2026-08-10 14:45 落地）

### 新根因（截图追问定位）
任务**完成后 AI 不回复**的根因不在后端——run_end 外壳播报后端正常发出，但前端 `handleAudio` 的 v6 语音绑定逻辑把它「吞」了：
纯 audio 路径（外壳播报）新建气泡**没标 `audioBound`**，run_end 播报到达时被当作"待绑定音频"绑进 run_start 的「好呀主人」气泡（只播放、不新建气泡、无字幕）→ 汇报文本完全不可见。
判定任务链路的新方法：**会话历史是否含用户消息**（chat 链路会落库用户+AI 消息；task.send 链路只落【任务简报】）。

### 7 处改动
| # | 文件 | 改动 |
|---|---|---|
| 1 | `frontend/src/ws/messageHandlers.ts` | `handleAudio` pending 查找加 `!m.audioData`；纯 audio 新建气泡标 `audioBound:true` → run_end 播报独立成气泡 ✅ 核心修复 |
| 2 | `backend/.../task_platform/task_route.py` | run_end 播报携带 `summary`（最后 AI 文本前 800 字），LLM 汇报真实结果 |
| 3 | `backend/.../task_platform/hooks.py` | 新增 `_is_error_result`：`[沙箱]`/`[沙箱错误]` 前缀字符串 → is_error=True（被拒 write_file 不再被判成功混入产物） |
| 4 | `frontend/src/task/useTaskEventStream.tsx` | 产物收集 `isUsableArtifact` 过滤：只留相对路径或 workspace 内绝对路径（`/workspace/...` 虚拟路径被拒） |
| 5 | `backend/.../task_platform/task_route.py` | open 路由：`/workspace/` 前缀反掩码为真实路径（历史/旧任务点击不再 400） |
| 6 | `backend/.../task_platform/intent_route.py` | `_ACTION_KEYWORDS` 加 做一个/做个/帮我做/整一个/生成一个（不加裸「做」防「怎么做」误判）→ 「帮我做一个番茄钟」稳定判 task |
| 7 | `frontend/src/components/WindowModeView.tsx` | chat 链路 delegate 任务事件到达且无锚点时，锚定到最后一条用户消息（卡片不再沉底） |

### 验证
- 前端 `npx tsc --noEmit` 0 错误；后端 110 passed（唯一失败 = AGENTS.md 已知基线 TTS 过滤器）。
- 探针验证：`_is_error_result("[沙箱]…")==True`、`_rule_classify("帮我做一个番茄钟")=='task'`、`/workspace` 反掩码解析到 workspace 内。
- **后端需重启生效**（hooks/task_route/intent_route）；前端 Vite HMR 自动生效。
