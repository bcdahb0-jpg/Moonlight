"""任务内核事件总线 + 工具调用事件封装（plan §4.3 / §5.2 / §5.5）。

- `EventBus`：按 task 单调递增分配 seq；`emit()` = **双落**（SQLite `task_events` 权威
  + JSONL event 条目审计/回放源）再广播给订阅者（SSE 消费者）。断线重连 `after_seq` 以
  SQLite 为锚点（plan §4.3 双写真相源规则）。
- `ToolCallEventMiddleware`：`wrap_tool_call` / `awrap_tool_call` 在工具执行前后发射
  `tool_call` / `tool_result` 事件（含 `is_error`）；同步异步双实现（langchain 1.3
  只实现其一会在异步路径 raise NotImplementedError）。

单向依赖：hooks.py ← graph.py；仅 import models / session / langchain middleware types。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from loguru import logger

from . import models
from .models import TaskEvent, now_iso
from .session import SessionFile

#: tool_result 事件里结果内容的显示截断上限（前端可折叠展开，见 plan §5.5）
_RESULT_DISPLAY_LIMIT = 4000


class EventBus:
    """SSE 事件总线：双落 + 广播。

    每个 run 一个实例（绑定 task_id + run_id）。多 run 并发时 seq 以 SQLite 权威值为锚，
    保持按 task 单调递增（plan §4.3）。
    """

    def __init__(self, workspace: str, task_id: str, run_id: str):
        self.task_id = task_id
        self.run_id = run_id
        self.session = SessionFile(workspace, task_id)
        self.session.ensure()
        self._subscribers: list[asyncio.Queue[TaskEvent]] = []

    # ------------------------------------------------------------------ //
    # 订阅（SSE 消费者）
    # ------------------------------------------------------------------ //
    def subscribe(self) -> asyncio.Queue[TaskEvent]:
        """注册一个事件订阅者，返回收件队列（需在运行中的事件循环内调用）。"""
        q: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[TaskEvent]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    # ------------------------------------------------------------------ //
    # 发射
    # ------------------------------------------------------------------ //
    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        category: str = "message",
        origin: str = "core",
    ) -> TaskEvent:
        """构造并双落一条事件，再广播给全部订阅者。"""
        event = TaskEvent(
            seq=self.session.next_seq(),
            event_type=event_type,
            task_id=self.task_id,
            run_id=self.run_id,
            category=category,
            origin=origin,
            payload=payload,
            created_at=now_iso(),
        )
        try:
            models.append_event(event)  # SQLite 权威（SSE 重连锚点）
            self.session.add_event(event)  # JSONL 审计/回放源
        except Exception as e:  # 双落失败不阻断 run，仅记录
            logger.error(f"task_events: 双落失败 task={self.task_id} seq={event.seq}: {e}")
        for q in self._subscribers:
            q.put_nowait(event)
        return event

    # ---- 便捷方法（事件体按 contracts/task_event_contract.json 固化）----
    def emit_run_start(
        self,
        goal: str = "",
        message: str = "",
        run_number: int = 0,
    ) -> TaskEvent:
        """run_start 事件。`message` 为本次 run 的触发指令（续跑时与任务标题不同，
        前端据此展示「本次执行」摘要）；`run_number` 为该任务第几次执行（1 起）。"""
        payload: dict[str, Any] = {"goal": goal}
        if message:
            payload["message"] = message
        if run_number:
            payload["run_number"] = run_number
        return self.emit("run_start", payload)

    def emit_run_end(
        self,
        status: str,
        summary: str | None = None,
    ) -> TaskEvent:
        payload: dict[str, Any] = {"status": status}
        if summary is not None:
            payload["summary"] = summary
        return self.emit("run_end", payload)

    def emit_run_error(self, error: str) -> TaskEvent:
        return self.emit("run_error", {"error": error}, category="error")

    def emit_error(self, message: str, code: str | None = None) -> TaskEvent:
        payload: dict[str, Any] = {"message": message}
        if code is not None:
            payload["code"] = code
        return self.emit("error", payload, category="error")

    def emit_status(self, state: str, detail: str | None = None) -> TaskEvent:
        payload: dict[str, Any] = {"state": state}
        if detail is not None:
            payload["detail"] = detail
        return self.emit("status", payload)

    def emit_message(self, role: str, content: str, delta: bool = False) -> TaskEvent:
        payload: dict[str, Any] = {"role": role, "content": content}
        if delta:
            payload["delta"] = True
        return self.emit("message", payload)

    def emit_clarify(self, question: str, options: list[str] | None = None) -> TaskEvent:
        payload: dict[str, Any] = {"question": question}
        if options:
            payload["options"] = options
        return self.emit("clarify_requested", payload)

    def emit_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
    ) -> TaskEvent:
        return self.emit(
            "tool_call",
            {"name": name, "arguments": arguments, "tool_call_id": tool_call_id},
            category="trace",
        )

    def emit_tool_result(
        self,
        name: str,
        result: str,
        is_error: bool,
        tool_call_id: str,
    ) -> TaskEvent:
        return self.emit(
            "tool_result",
            {
                "name": name,
                "result": result[:_RESULT_DISPLAY_LIMIT],
                "is_error": is_error,
                "tool_call_id": tool_call_id,
            },
            category="outputs",
        )


def _tool_call_identity(request: ToolCallRequest) -> tuple[str, dict[str, Any], str]:
    """从 ToolCallRequest 提取 (name, args, call_id)。"""
    call = request.tool_call
    return (
        call.get("name", ""),
        call.get("args", {}) or {},
        call.get("id", ""),
    )


class ToolCallEventMiddleware(AgentMiddleware):
    """工具调用事件封装：执行前发射 tool_call，执行后发射 tool_result（含 is_error）。

    同时实现 `wrap_tool_call` / `awrap_tool_call`（langchain 1.3 只实现同步会令异步
    工具路径 raise NotImplementedError）。错误检测兼容 ToolNode 默认
    `handle_tool_errors=True`：异常在 `execute` 内部已转成 `status="error"` 的
    ToolMessage，此处据此置 is_error，不重复捕获。
    """

    def __init__(self, bus: EventBus):
        super().__init__()
        self.bus = bus

    def wrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        name, args, call_id = _tool_call_identity(request)
        self.bus.emit_tool_call(name, args, call_id)
        try:
            result = execute(request)
        except Exception as e:
            self.bus.emit_tool_result(name, str(e), True, call_id)
            raise
        is_error = _is_error_result(result)
        self.bus.emit_tool_result(name, _result_text(result), is_error, call_id)
        return result

    async def awrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        name, args, call_id = _tool_call_identity(request)
        self.bus.emit_tool_call(name, args, call_id)
        try:
            result = await execute(request)
        except Exception as e:
            self.bus.emit_tool_result(name, str(e), True, call_id)
            raise
        is_error = _is_error_result(result)
        self.bus.emit_tool_result(name, _result_text(result), is_error, call_id)
        return result


def _result_text(result: Any) -> str:
    """工具返回转可显示文本（ToolMessage 取 content）。"""
    if isinstance(result, ToolMessage):
        content = result.content
        return str(content) if content is not None else ""
    return str(result)


#: 工具以字符串形式返回错误的判定前缀（沙箱工具用 `[沙箱] ...` / `[沙箱错误] ...`
#: 返回"软错误"而不抛异常——此前 is_error 只认 ToolMessage.status，导致被拒的
#: write_file 被判成功、虚拟路径混进产物列表（2026-08-10 番茄钟截图问题）。
_SANDBOX_ERROR_PREFIXES = ("[沙箱]", "[沙箱错误]")


def _is_error_result(result: Any) -> bool:
    """工具结果是否为错误：ToolMessage status=error，或沙箱类错误字符串前缀。"""
    if isinstance(result, ToolMessage):
        return result.status == "error"
    text = _result_text(result).lstrip()
    return text.startswith(_SANDBOX_ERROR_PREFIXES)
