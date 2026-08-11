# 产品体验诊断：番茄钟任务生成与结果反馈

## 1. 截图描述

用户连续两轮输入：

1. **「帮我做一个番茄钟」** —— 系统创建并执行了一个任务，任务卡显示「完成 · 47.8s · 4 次工具调用」。
2. **「刚刚生成的 html 放到了哪里」** —— 系统回复了一条带 **「【任务结果】工具调用失败（HTTP 502）」** 的消息，随后角色又用口语解释「工具出了点小故障」，并让用户自己去下载文件夹或搜索文件名。

从代码层面看，这是 **第一次任务成功、第二次任务失败的体验断裂**，但 UI 把两次任务混在了一起，导致用户困惑。

---

## 2. 体验问题清单

| # | 问题 | 严重程度 | 表现 |
|---|---|---|---|
| 2.1 | **成功/失败状态混叠** | 高 | 任务卡显示「完成」，但紧接着出现「HTTP 502」报错，用户不知道哪一步失败。 |
| 2.2 | **原始错误直接暴露给用户** | 高 | 「HTTP 502」「工具调用失败」属于实现细节，直接出现在聊天区。 |
| 2.3 | **角色名称不一致** | 中 | 第一次回复署名「AI」，任务结果署名「小月」，失败恢复又变成「hiyori」，让用户产生「到底是谁在说话」的困惑。 |
| 2.4 | **任务结果未给出可定位信息** | 高 | 任务完成了，但用户必须再问一次才能知道文件位置；第二次查询本不该重新委派。 |
| 2.5 | **失败后的恢复建议不可操作** | 中 | 角色让用户「看浏览器下载文件夹」或「搜文件名」，但实际文件在工作目录，建议既不准确也不负责。 |
| 2.6 | **任务卡缺少产物/路径信息** | 中 | TaskRunCard 只显示「完成」和工具调用次数，没有最终生成文件的路径或打开入口。 |
| 2.7 | **第二次查询重复走 delegate 链路** | 中 | 「文件在哪」是对同一任务的追问，应该利用已注入会话的 task brief 直接回答，而不是再启一次子任务。 |

---

## 3. 代码层交互逻辑

### 3.1 任务委派链路

```
用户输入
  → basic_memory_agent._simple_chat_with_builtin_tool
    → LLM 决定调用 delegate_to_task
    → _call_delegate_task(goal)
      → POST /api/chat/delegate-task
        → task_route.chat_delegate_task
          → 创建临时 Task → start_run → 后台 agent 执行
          → 轮询到 run 完成/超时
      → 返回结果字符串（成功或失败）
    → yield {"type": "task_result", "content": result}
```

### 3.2 关键代码位置

- **委派工具执行器**：`backend/src/open_llm_vtuber/agent/agents/basic_memory_agent.py` 行 178-225（`_call_delegate_task`）
- **内置工具循环**：同文件 779-929（`_simple_chat_with_builtin_tool`）
- **任务结果推送聊天区**：`backend/src/open_llm_vtuber/conversations/single_conversation.py` 行 333-352
- **前端 task-result 渲染**：`frontend/src/ws/messageHandlers.ts` 行 379-399（`handleTaskResult`）
- **任务卡渲染**：`frontend/src/task/TaskRunCard.tsx`

### 3.3 截图中各条消息的代码来源

| 截图消息 | 来源 | 说明 |
|---|---|---|
| 任务卡「完成 47.8s · 4 次工具调用」 | `TaskRunCard` 解析 SSE `run_end` + `tool_call/tool_result` | 这是**第一次**番茄钟任务的真实状态。 |
| AI 说「好嘞，交给我吧…」 | 来自 `handleAudio` / `full-text` 的 LLM 回复 | 第一次请求时 LLM 接受任务。 |
| 小月「【任务结果】工具调用失败（HTTP 502）」 | `single_conversation.py` 对 `task_result` 统一加前缀后 WS 推送 | 这是**第二次**追问触发的子任务返回了错误。 |
| hiyori「啊，工具这边刚出了点小故障…」 | LLM 拿到错误 tool_result 后生成的口语化回复 | 角色名不一致说明历史/配置命名源不统一。 |

### 3.4 根因：`_call_delegate_task` 把错误当结果

失败时返回：

```python
return f"工具调用失败（HTTP {resp.status_code}）：{resp.text[:200]}"
```

成功时返回：

```python
return f"[任务结果 / status={status}]\n{summary[:2000]}"
```

两者都被 `yield {"type": "task_result", "content": result}` 送往 `single_conversation.py`，然后统一包装：

```python
"text": f"【任务结果】\n{task_result}"
```

**问题**：成功与失败没有协议区分，前端和会话历史都拿到同一段带「【任务结果】」前缀的文本。

### 3.5 根因：任务完成后没有把产物路径写回会话

`_run_task_agent` 在 `run_end(completed)` 后会把任务简报注入聊天历史：

```python
brief = f"【任务简报】{task.title}：{text}"
store_message(..., role="ai", content=brief, name=char_name)
```

但简报只取「最长的 AI 文本摘要」，**不提取文件路径**。因此用户问「html 在哪」时，LLM 看不到路径，只能再次调用 `delegate_to_task` 去查，结果碰上 HTTP 502。

---

## 4. 修改建议

### 4.1 协议层：区分任务成功与失败

在 `basic_memory_agent.py` 中，把工具结果拆成两种事件：

```python
# 成功
yield {"type": "task_result", "status": "completed", "content": summary}

# 失败
yield {"type": "task_error", "status": "error", "error_code": "HTTP_502", "content": user_friendly_msg}
```

`single_conversation.py` 对应处理：

- `task_result`：继续用「【任务结果】」前缀，显示完整清单/表格。
- `task_error`：**不**加「【任务结果】」前缀；改发 `error` 类型消息或生成一句角色道歉，由 LLM 转述；原始错误只进日志。

### 4.2 错误提示用户化

在 `_call_delegate_task` 中把技术性错误映射为用户语言：

```python
if resp.status_code >= 500:
    return "任务服务暂时不可用，请稍后再试。"
if resp.status_code == 408 or timeout:
    return "任务执行超时，可能需要简化目标后重试。"
```

同时给 LLM 的 tool result 保留技术细节，用于生成更准确的道歉/建议。

### 4.3 任务产物路径必须进入简报

修改 `task_route.py` 的 `_run_task_agent` 或 `graph.py` 工具层，在任务完成时：

1. 从 `tool_result` 事件中提取创建的文件路径（如 `pomodoro-timer.html`）。
2. 在 `_inject_task_brief` 里把路径追加到简报：

```python
brief = f"【任务简报】{task.title}：{text}\n生成文件：{workspace}/{filename}"
```

3. 前端 `TaskRunCard` 增加「打开文件」「打开目录」按钮（可调用 Electron shell.openPath）。

### 4.4 追问不再重复委派

在 `_TOOL_GUIDANCE` 中加入：

> 如果用户问的是刚才任务的结果/文件位置/状态，而你已经在对话历史中看到了【任务简报】，请直接根据简报回答，不要再次调用 delegate_to_task。

同时把简报格式固定化，方便 LLM 识别：

```
【任务简报】任务标题：摘要内容
工作目录：<path>
生成文件：<file1>, <file2>
```

### 4.5 统一角色名来源

问题：截图中出现了 AI / 小月 / hiyori 三个名字。

检查三个数据源：

- `frontend/src/ws/messageHandlers.ts` 的 `handleAudio`：用 `msg.display_text?.name ?? deps.getState().confName`。
- `handleTaskResult`：用 `msg.name`（来自后端 `context.character_config.character_name`）。
- 历史加载 `handleHistoryData`：用 `m.name`。

建议：后端 `single_conversation.py` 发送 task-result 时，如果 `character_name` 为空，回退到 `conf_name`；前端所有 AI 消息统一优先使用 `confName`，仅当消息显式带 name 且与当前配置一致时才覆盖。

### 4.6 TaskRunCard 增强

在 `frontend/src/task/TaskRunCard.tsx` 中：

- 解析 `tool_result` 里包含文件路径的结果，列出「生成文件」。
- 完成态显示「打开工作目录」按钮。
- 失败态显示重试/查看日志入口，而不是只显示状态 chip。

### 4.7 对 HTTP 502 做防御性重试

`delegate_to_task` 调用的是本机 `http://127.0.0.1:12393`，502 通常是瞬时问题。可以加一次指数退避重试（最多 2 次），避免一次抖动就导致用户看到失败。

---

## 5. 优先级建议

1. **P0**：区分 `task_result` / `task_error`，不再把 HTTP 502 直接展示给用户。
2. **P0**：任务产物路径写入 task brief，让「文件在哪」类追问能直接回答。
3. **P1**：统一 AI 消息的角色名来源，消除 AI/小月/hiyori 混用。
4. **P1**：TaskRunCard 增加产物路径与打开入口。
5. **P2**：delegate 调用加瞬时重试。
