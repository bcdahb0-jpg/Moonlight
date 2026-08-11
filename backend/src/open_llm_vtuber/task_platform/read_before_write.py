"""read_before_write 版本门（v3 Phase 1，参考 deer-flow read_before_write_middleware）。

写前必须读过：`write_file` / `str_replace` 修改已有文件前，模型必须先 `read_file` 读过
**当前版本**（内容 sha256 校验）。防止覆盖外部改动/记忆错位的文件。

机制（消息历史 mark + 内存 store 双轨，deer-flow 同思想，跨轮生效）：
- `after`（read_file 成功）→ 对**文件完整内容**算 sha256，存入 `ReadMarkStore`（per-path
  最新 mark）；若本次 ToolMessage 是 langchain 生成的，无法改 additional_kwargs（只读），
  因此 mark 只存 store（跨轮由 store 提供），不依赖消息回读。
- `before`（write_file/str_replace 命中已有文件）→ 比对当前文件 hash 与 store 中该路径
  最近 mark：不一致 → 返回 `ToolMessage(status="error")` 阻断（模型会重读重试）。

fail-open 白名单（deer-flow 同思想，防误伤）：
- 新建文件（不存在）→ 不校验直接放行；
- 读失败 / 输出以 "[沙箱]" 开头（错误提示）→ 不落 mark；
- 路径越界/解析失败 → 不校验（沙箱层已拒，这里只做轻量存在性判断）；
- store 无该路径 mark（checkpoint 恢复后 / 首次写）→ 放行（deer-flow 对无 mark 放行）。

实现细节：
- 同步/异步双实现（hooks.py 惯例，防 langchain 1.3 异步路径 NotImplementedError）。
- 组装时传入 workspace，相对路径按 workspace 解析（与沙箱 resolve_local 同语义）。

单向依赖：read_before_write.py ← graph.py；仅 import langchain + stdlib。
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from loguru import logger

#: 需要版本门校验的写工具。
WRITE_TOOLS = ("write_file", "str_replace")
#: 记录 read mark 的工具（读成功后落 mark）。
READ_TOOLS = ("read_file",)
#: 二进制/失败读结果（以 [沙箱] 开头）不落 mark 的判定前缀。
_ERR_PREFIXES = ("[沙箱]", "[沙箱错误]")


class ReadMarkStore:
    """per-path 最近一次成功 read 的内容 hash（线程安全，单进程内有效）。

    key = 文件绝对路径（os.path.realpath），多任务天然隔离（路径含 workspace）。
    """

    def __init__(self):
        self._marks: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, real_path: str, digest: str) -> None:
        with self._lock:
            self._marks[real_path] = digest

    def get(self, real_path: str) -> str | None:
        with self._lock:
            return self._marks.get(real_path)

    def clear(self) -> None:
        with self._lock:
            self._marks.clear()


#: 全局共享 store。
_STORE = ReadMarkStore()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ReadBeforeWriteMiddleware(AgentMiddleware):
    """写前读版本门：wrap_tool_call 拦截 write_file/str_replace。

    Args:
        workspace: 任务工作目录绝对路径（相对路径解析基准）。
        enabled: 总开关（conf.yaml read_before_write）。
    """

    def __init__(self, workspace: str, *, enabled: bool = True):
        super().__init__()
        self.workspace = os.path.realpath(str(workspace))
        self.enabled = enabled

    # ------------------------------------------------------------------ //
    # 同步/异步双实现（hooks.py 惯例，防 NotImplementedError）
    # ------------------------------------------------------------------ //
    def wrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        if not self.enabled:
            return execute(request)
        gate = self._gate_for(request)
        if gate is not None:
            return gate
        call = request.tool_call
        name = call.get("name", "")
        args = call.get("args", {}) or {}
        if name in READ_TOOLS:
            result = execute(request)
            self._maybe_record_mark(result, str(args.get("path", "")))
        else:
            result = execute(request)
        return result

    async def awrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        if not self.enabled:
            return await execute(request)
        gate = self._gate_for(request)
        if gate is not None:
            return gate
        call = request.tool_call
        name = call.get("name", "")
        args = call.get("args", {}) or {}
        result = await execute(request)
        if name in READ_TOOLS:
            self._maybe_record_mark(result, str(args.get("path", "")))
        return result

    # ------------------------------------------------------------------ //
    # 写前版本门判定（同步/异步共用）
    # ------------------------------------------------------------------ //
    def _gate_for(self, request: ToolCallRequest) -> ToolMessage | None:
        """write_file/str_replace 命中已有文件且 hash 与 read mark 不一致 → 阻断消息。"""
        call = request.tool_call
        name = call.get("name", "")
        args = call.get("args", {}) or {}
        if request.tool is None:
            return None
        if name not in WRITE_TOOLS:
            return None
        path = str(args.get("path", ""))
        if not path:
            return None
        real = self._resolve(path)
        if real is None or not os.path.isfile(real):
            return None  # 新建/越界 → 沙箱层处理，不校验
        return self._check_gate(real, str(call.get("id", "") or ""))

    def _maybe_record_mark(self, result: Any, path: str) -> None:
        """read_file 成功 → 对文件完整内容算 hash 落 mark（不 hash 返回文本，防截断差异）。"""
        text = _result_text(result)
        if not text or text.startswith(_ERR_PREFIXES):
            return  # 读失败不落 mark
        real = self._resolve(path)
        if real is None or not os.path.isfile(real):
            return
        try:
            with open(real, "rb") as f:
                digest = _sha256_bytes(f.read())
        except OSError:
            return
        _STORE.put(real, digest)
        logger.debug(f"read_before_write: 记录 read mark {real}")

    # ------------------------------------------------------------------ //
    # write 之前：版本门
    # ------------------------------------------------------------------ //
    def _check_gate(self, real_path: str, tool_call_id: str = "") -> ToolMessage | None:
        try:
            with open(real_path, "rb") as f:
                current = _sha256_bytes(f.read())
        except OSError:
            return None  # 读失败 fail-open
        last = _STORE.get(real_path)
        if last is None:
            logger.debug(f"read_before_write: 无 read mark（首次写）→ 放行 {real_path}")
            return None  # 无 mark → 放行（保守，防误伤）
        if last == current:
            return None  # 已读过当前版本 → 放行
        return ToolMessage(
            content=(
                f"[沙箱] 写前校验未通过：文件 {real_path} 在您上次 read_file 之后已被修改，"
                f"直接覆盖将丢失改动。请重新 read_file 读取当前内容后重试。"
            ),
            status="error",
            tool_call_id=tool_call_id,
        )

    def _resolve(self, path: str) -> str | None:
        """`/workspace/...` 虚拟路径反掩码 → 相对路径；相对路径按 workspace 解析，
        绝对路径原样；realpath 后校验在 workspace 内（与 sandbox.resolve_local 同语义）。"""
        raw = str(path).replace("\\", "/")
        if raw == "/workspace" or raw.startswith("/workspace/"):
            # 与 sandbox.PathMapping.from_container 保持一致（_CONTAINER_ROOT="/workspace"）
            path = raw[len("/workspace"):].lstrip("/")
        p = os.path.expanduser(str(path))
        if not os.path.isabs(p):
            p = os.path.join(self.workspace, p)
        real = os.path.realpath(p)
        try:
            common = os.path.commonpath([real, self.workspace])
        except ValueError:  # 不同盘符（Windows）
            return None
        if common != self.workspace:
            return None  # 越界 → 沙箱层负责报错，这里不校验
        return real


def _result_text(result: Any) -> str:
    if isinstance(result, ToolMessage):
        content = result.content
        return str(content) if content is not None else ""
    return str(result)


def read_mark_store() -> ReadMarkStore:
    """暴露全局 store（测试注入/清理用）。"""
    return _STORE
