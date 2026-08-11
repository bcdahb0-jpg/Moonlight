"""工具输出预算（v4 Phase B1，参考 deer-flow tool_output_budget_middleware）。

问题：单条工具输出过大（bash 日志、web_fetch 全文、grep 大目录）会把模型上下文瞬间打爆，
100+ 轮长任务尤其致命。现有 bash 输出截断（64KB）只覆盖 bash，read_file/grep/web_fetch
等仍可能返回巨大文本。

机制（wrap_tool_call + wrap_model_call 双钩子，只 patch 结果/输入，不改 checkpoint）：
1. **落盘换预览**：工具结果文本 > `externalize_min_chars`（默认 12000）→ 完整内容原子写
   到 `<workspace>/.pi/tasks/<task_id>/outputs/.tool-results/<tool>-<id>.txt`，上下文里
   替换为结构化 preview：文件虚拟路径（/workspace/...，模型可 read_file 读回）+
   head/tail 节选 + 统计信息。
2. **落盘失败兜底**：磁盘不可写/越界 → head+tail 截断（`fallback_max_chars`，默认 30K），
   保证任何情况下单条结果有硬上限。
3. **历史兜底**：wrap_model_call 进模型前，对历史中仍超限的 ToolMessage（早期版本未
   拦截的输出 / checkpoint 恢复的老消息）做 fallback 截断——与 compaction 互补：
   compaction 管"消息条数/token 总量"，本模块管"单条消息体积"。
4. **豁免 read_file**（deer-flow 同款）：落盘文件本身要能 read_file 读回，豁免防
   persist→read→persist 死循环。

与既有中间件兼容：
- 事件层（ToolCallEventMiddleware）在其外层 → tool_result 事件展示的即模型看到的 preview；
- read_before_write 只拦 write_file/str_replace 工具调用，本模块是宿主侧 os 写入，不冲突；
- 落盘目录在 workspace 内（`.pi/tasks/<tid>/outputs/`），模型 read_file 经 sandbox 白名单可读。

单向依赖：output_budget.py ← graph.py；仅 import langchain + stdlib + conf_bridge。
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from dataclasses import replace as dc_replace
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.messages import ToolMessage
from loguru import logger

from .conf_bridge import TaskPlatformConfig
from .session import session_dir

#: 落盘子目录名（相对 <workspace>/.pi/tasks/<task_id>/outputs/）。
_TOOL_RESULTS_SUBDIR = ".tool-results"
#: 工具名 → 落盘扩展名（日志类用 .log，其余 .txt）。
_EXT_MAP = {"bash": "log", "web_fetch": "log"}

#: 预览文本里绝对路径 → 虚拟路径的掩码前缀（与 sandbox.PathMapping 同语义）。
_CONTAINER_ROOT = "/workspace"


class ToolOutputBudgetMiddleware(AgentMiddleware):
    """单条工具输出预算：超限落盘换预览 / 截断兜底。

    Args:
        workspace: 任务工作目录绝对路径（落盘目录锚点）。
        task_id: 任务 id（落盘目录一部分，任务间天然隔离）。
        cfg: task_platform 配置（阈值；None 时用默认值）。
    """

    def __init__(
        self,
        workspace: str,
        task_id: str,
        cfg: TaskPlatformConfig | None = None,
        *,
        enabled: bool = True,
        externalize_min_chars: int = 12000,
        preview_head_chars: int = 2000,
        preview_tail_chars: int = 1000,
        fallback_max_chars: int = 30000,
        exempt_tools: tuple[str, ...] = ("read_file",),
    ):
        super().__init__()
        self.workspace = os.path.realpath(str(workspace))
        self.task_id = str(task_id)
        self.enabled = enabled
        if cfg is not None:
            externalize_min_chars = cfg.tool_output_externalize_min_chars
            preview_head_chars = cfg.tool_output_preview_head_chars
            preview_tail_chars = cfg.tool_output_preview_tail_chars
            fallback_max_chars = cfg.tool_output_fallback_max_chars
        self.externalize_min_chars = externalize_min_chars
        self.preview_head_chars = preview_head_chars
        self.preview_tail_chars = preview_tail_chars
        self.fallback_max_chars = fallback_max_chars
        self.exempt_tools = frozenset(exempt_tools)

    # ------------------------------------------------------------------ //
    # 工具调用钩子：结果超限 → 落盘 / 截断
    # ------------------------------------------------------------------ //
    def wrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        result = execute(request)
        return self._maybe_budget_result(result)

    async def awrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        result = await execute(request)
        return self._maybe_budget_result(result)

    # ------------------------------------------------------------------ //
    # 模型调用钩子：历史超限 ToolMessage 兜底截断（进模型前）
    # ------------------------------------------------------------------ //
    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._patch_historical(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Callable) -> Any:
        if self.enabled:
            self._patch_historical(request)
        return await handler(request)

    # ------------------------------------------------------------------ //
    # 核心
    # ------------------------------------------------------------------ //
    def _maybe_budget_result(self, result: Any) -> Any:
        """对工具执行结果应用预算（ToolMessage 或 Command）。无改动原样返回。"""
        if not self.enabled:
            return result
        if isinstance(result, ToolMessage):
            return self._budget_tool_message(result)
        update = getattr(result, "update", None)
        if isinstance(update, dict) and isinstance(update.get("messages"), list):
            messages = update["messages"]
            new_messages: list[Any] = []
            changed = False
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    patched = self._budget_tool_message(msg)
                    if patched is not msg:
                        changed = True
                    new_messages.append(patched)
                else:
                    new_messages.append(msg)
            if changed:
                return dc_replace(result, update={**update, "messages": new_messages})
        return result

    def _budget_tool_message(self, msg: ToolMessage) -> ToolMessage:
        """单条 ToolMessage 预算：超限 → 落盘换预览；落盘失败 → head+tail 截断。"""
        name = msg.name or "unknown"
        if name in self.exempt_tools:
            return msg
        text = _message_text(msg.content)
        if text is None:
            return msg  # 非纯文本（多模态块）不处理
        if len(text) <= self.externalize_min_chars and len(text) <= self.fallback_max_chars:
            return msg

        # 1) 尝试落盘换预览（完整内容保留在磁盘，模型可 read_file 读回）
        virtual_path = self._externalize(text, name, str(msg.tool_call_id or ""))
        if virtual_path is not None:
            preview = self._build_preview(text, name, virtual_path)
            return self._with_content(msg, preview)

        # 2) 落盘失败 → head+tail 截断兜底（硬上限 fallback_max_chars）
        fallback = self._build_fallback(text, name)
        return self._with_content(msg, fallback)

    # ------------------------------------------------------------------ //
    # 落盘
    # ------------------------------------------------------------------ //
    def _externalize(self, content: str, tool_name: str, tool_call_id: str) -> str | None:
        """完整内容写盘，返回虚拟路径（模型 read_file 可用）；失败返回 None。"""
        out_dir = (
            session_dir(self.workspace, self.task_id) / "outputs" / _TOOL_RESULTS_SUBDIR
        )
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"output_budget: 落盘目录不可用（{e}），走截断兜底")
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_name) or "tool"
        ext = _EXT_MAP.get(tool_name, "txt")
        filename = f"{safe}-{uuid.uuid4().hex[:12]}.{ext}"
        filepath = out_dir / filename
        try:
            # 原子写（Windows 安全）：临时文件 + os.replace
            tmp = filepath.with_suffix(filepath.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, filepath)
        except OSError as e:
            logger.warning(f"output_budget: 落盘失败（{e}），走截断兜底")
            return None
        logger.info(
            f"output_budget: {tool_name} 输出 {len(content)} 字符已落盘 {filepath}"
        )
        # 虚拟路径：workspace 内相对路径加 /workspace/ 前缀（正斜杠，模型 read_file 语义）
        try:
            rel = os.path.relpath(str(filepath), self.workspace).replace("\\", "/")
        except ValueError:  # 不同盘符（理论上不可能：目录锚定在 workspace 内）
            return None
        return f"{_CONTAINER_ROOT}/{rel}"

    # ------------------------------------------------------------------ //
    # 预览 / 兜底
    # ------------------------------------------------------------------ //
    def _build_preview(self, content: str, tool_name: str, virtual_path: str) -> str:
        """结构化 preview：文件引用 + 统计 + head/tail 节选（行边界）。"""
        total = len(content)
        est_tokens = max(1, total // 4)
        head = _snap_head(content, self.preview_head_chars)
        tail = _snap_tail(content, self.preview_tail_chars)
        lines = [
            f"[工具 `{tool_name}` 输出过大（{total} 字符，约 {est_tokens} tokens），"
            f"完整内容已保存到 {virtual_path}，可用 read_file 读取]",
            f"[以下为节选：开头 {self.preview_head_chars} 字符 + 结尾 {self.preview_tail_chars} 字符]",
            "--- 开头 ---",
            head,
        ]
        if tail:
            lines += ["--- 结尾 ---", tail]
        return "\n".join(lines)

    def _build_fallback(self, content: str, tool_name: str) -> str:
        """head+tail 截断（磁盘不可用时的硬兜底，总长 ≤ fallback_max_chars）。"""
        max_chars = self.fallback_max_chars
        total = len(content)
        marker = (
            f"\n\n[... 已省略 {total - self.preview_head_chars - self.preview_tail_chars} 字符"
            f"（{tool_name} 输出过大且落盘不可用，仅保留首尾节选）]\n\n"
        )
        budget = max_chars - len(marker)
        if budget <= 0:
            return content[:max_chars]
        head = _snap_head(content, min(self.preview_head_chars, budget))
        remaining = max(0, budget - len(head))
        tail = _snap_tail(content, min(self.preview_tail_chars, remaining))
        return f"{head}{marker}{tail}" if tail else f"{head}{marker}"

    # ------------------------------------------------------------------ //
    # 历史兜底
    # ------------------------------------------------------------------ //
    def _patch_historical(self, request: ModelRequest) -> None:
        """进模型前：历史超限 ToolMessage → fallback 截断（早期未拦截的输出）。"""
        msgs = request.messages
        if not msgs:
            return
        needs_patch = any(
            isinstance(m, ToolMessage)
            and (m.name or "") not in self.exempt_tools
            and (t := _message_text(m.content)) is not None
            and len(t) > self.fallback_max_chars
            for m in msgs
        )
        if not needs_patch:
            return
        out: list[Any] = []
        for m in msgs:
            if isinstance(m, ToolMessage):
                text = _message_text(m.content)
                if text is not None and len(text) > self.fallback_max_chars:
                    m = self._with_content(m, self._build_fallback(text, m.name or "unknown"))
            out.append(m)
        request.messages = out

    # ------------------------------------------------------------------ //
    # 辅助
    # ------------------------------------------------------------------ //
    @staticmethod
    def _with_content(msg: ToolMessage, content: str) -> ToolMessage:
        update: dict[str, Any] = {"content": content}
        if getattr(msg, "response_metadata", None):
            update["response_metadata"] = dict(msg.response_metadata)
        if getattr(msg, "additional_kwargs", None):
            update["additional_kwargs"] = dict(msg.additional_kwargs)
        return msg.model_copy(update=update)


# --------------------------------------------------------------------------- #
# 纯函数辅助（可独立测试）
# --------------------------------------------------------------------------- #
def _message_text(content: Any) -> str | None:
    """ToolMessage content → 纯文本；非 str / 多模态块返回 None（跳过预算）。"""
    if isinstance(content, str):
        return content
    if content is None:
        return None
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
            else:
                return None
        return "\n".join(pieces) if pieces else None
    return None


def _snap_head(text: str, budget: int) -> str:
    """开头节选（预算内最近换行处截断，保持整行）。"""
    if budget <= 0 or len(text) <= budget:
        return text[: max(0, budget)] if budget >= 0 else ""
    end = text.rfind("\n", 0, budget)
    return text[: end + 1] if end > 0 else text[:budget]


def _snap_tail(text: str, budget: int) -> str:
    """结尾节选（预算内最近换行处开始，保持整行）。"""
    if budget <= 0 or len(text) <= budget:
        return text[-max(0, budget):] if budget > 0 else ""
    start = text.find("\n", max(0, len(text) - budget))
    return text[start + 1 :] if start >= 0 else text[-budget:]


__all__: list[str] = ["ToolOutputBudgetMiddleware"]
