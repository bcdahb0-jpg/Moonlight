"""extensions 钩子注册表（plan §8 Phase 6c，pi ExtensionAPI 子集）。

- `plugins/` 目录约定（dwsy extensions 思想）：`plugins/` = 启用的插件根，
  `plugins.disabled/` = 目录改名即禁用（零删除）；根内单个插件名带 `.disabled` 后缀
  （`foo.disabled.py` / `foo.py.disabled`）同样跳过。
- 每个插件是一个 Python 模块（单文件 `.py` 或包 `__init__.py`），定义
  `register(api: ExtensionAPI)` 注册钩子。加载失败 / 无 register / register 抛异常 →
  跳过并告警（fail-skip，绝不炸 run）。
- 事件（pi ExtensionAPI 子集，共 3 个）：
  - `before_agent_start(system_prompt, ctx) -> str | None`：改写主 system prompt。
  - `before_tool_call(tool_name, args, ctx) -> dict | None`：返回 `{"block": True, "reason"}`
    拦截执行（生成 error ToolMessage 回环），或 `{"args": ...}` 改写参数后执行。
  - `after_tool_call(tool_name, result, ctx) -> dict | None`：返回 `{"result": ...}` 改写
    工具返回内容。
  - ctx = `{"task_id", "workspace", "state", ...}` 只读上下文。
- 钩子内异常被捕获并告警（不中断 run）；无 handler 时 emit 返回空列表（零开销）。
- `ExtensionHookMiddleware`：按 plan 6c 把 before/after_tool_call 接到 agent 的
  wrap_tool_call 链；before_agent_start 由 graph.build_agent 组装 system prompt 时调用。
- 单向依赖：extensions.py ← graph.py；仅 import hooks（_result_text）+ langchain + stdlib。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from loguru import logger

from .hooks import _result_text

#: 支持的事件集合（pi ExtensionAPI 子集）。
EVENTS = ("before_agent_start", "before_tool_call", "after_tool_call")


class ExtensionAPI:
    """插件注册表 API：`on(event, handler)` 注册，`emit(event, ...)` 顺序调用。

    与 pi `ExtensionAPI.on(event, handler)` 同构；仅暴露 3 个事件子集。
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {e: [] for e in EVENTS}

    def on(self, event: str, handler: Callable) -> None:
        if event not in self._handlers:
            raise ValueError(f"未知事件 {event!r}；支持：{', '.join(EVENTS)}")
        self._handlers[event].append(handler)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> list[Any]:
        """顺序调用事件处理器；返回非 None 返回值列表（异常跳过并告警）。"""
        out: list[Any] = []
        for handler in self._handlers[event]:
            try:
                r = handler(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 钩子异常不炸 run
                logger.warning(f"extension 钩子 {event} 失败（已跳过）：{e}")
                r = None
            if r is not None:
                out.append(r)
        return out

    def has_event(self, event: str) -> bool:
        return bool(self._handlers[event])


class PluginRegistry:
    """`plugins/` 目录扫描注册表：`scan()` 重建 ExtensionAPI + 加载全部插件。

    幂等，支持 hot-reload（每次调用重扫）。约定：
    - 单文件插件：`<root>/<name>.py`；包插件：`<root>/<name>/__init__.py`。
    - 根内任何名字含 `.disabled` 的条目跳过（`plugins.disabled/` 是另一个目录，本应不在 root 下）。
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.api = ExtensionAPI()
        self.loaded: list[str] = []

    def scan(self) -> "PluginRegistry":
        """扫描 root 下的插件并注册到新 ExtensionAPI；返回 self。"""
        self.api = ExtensionAPI()
        self.loaded = []
        if not self.root.is_dir():
            return self
        for p in sorted(self.root.iterdir()):
            if ".disabled" in p.name:
                logger.info(f"plugin: {p.name} 被 .disabled 后缀禁用，跳过")
                continue
            if p.is_dir():
                self._load_one(p / "__init__.py", display=p.name)
            elif p.suffix == ".py":
                self._load_one(p, display=p.name)
        return self

    def _load_one(self, entry: Path, *, display: str) -> None:
        if not entry.is_file():
            return
        mod_name = f"_moonlight_plugin_{_path_digest(entry)}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, entry)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法加载 {entry}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            if not hasattr(module, "register"):
                logger.warning(f"plugin {display} 未定义 register(api)，跳过")
                return
            module.register(self.api)
        except Exception as e:  # noqa: BLE001 插件加载失败不影响主链路
            logger.warning(f"plugin {display} 加载失败，跳过：{e}")
            return
        self.loaded.append(display)
        logger.info(f"plugin: {display} 已加载")

    @staticmethod
    def scan_root(root: str | Path) -> "PluginRegistry":
        """便捷：scan 一次返回 registry。"""
        return PluginRegistry(root).scan()


def _path_digest(path: Path) -> str:
    """插件模块唯一名：路径摘要（deterministic，跨进程稳定）。"""
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]


def apply_before_agent_start(api: ExtensionAPI, system_prompt: str, ctx: dict[str, Any]) -> str:
    """`before_agent_start`：最后一个非空返回值覆盖 system prompt。"""
    if not api.has_event("before_agent_start"):
        return system_prompt
    for r in api.emit("before_agent_start", system_prompt, ctx):
        if isinstance(r, str) and r.strip():
            system_prompt = r
    return system_prompt


class ExtensionHookMiddleware(AgentMiddleware):
    """工具调用钩子：before_tool_call（可 block / 改 args）+ after_tool_call（可改结果）。

    同步/异步双实现（langchain 1.3 只实现其一会在异步路径 raise NotImplementedError）。
    无 handler 时透传，零行为变化。
    """

    def __init__(self, registry: PluginRegistry):
        super().__init__()
        self.registry = registry

    def wrap_tool_call(self, request: Any, execute: Callable) -> Any:
        new_request, block_reason = self._before(request)
        if block_reason is not None:
            return _block_message(new_request, block_reason)
        result = execute(new_request)
        return self._after(new_request, result)

    async def awrap_tool_call(self, request: Any, execute: Callable) -> Any:
        new_request, block_reason = self._before(request)
        if block_reason is not None:
            return _block_message(new_request, block_reason)
        result = await execute(new_request)
        return self._after(new_request, result)

    # ------------------------------------------------------------------ //
    # 内部
    # ------------------------------------------------------------------ //
    def _before(self, request: Any) -> tuple[Any, str | None]:
        """before_tool_call：block → (req, reason)；改 args → override 新 request。"""
        api = self.registry.api
        name = request.tool_call.get("name", "")
        args = dict(request.tool_call.get("args") or {})
        ctx = {"state": request.state}
        for r in api.emit("before_tool_call", name, args, ctx):
            if not isinstance(r, dict):
                continue
            if r.get("block"):
                return request, str(r.get("reason", "被扩展拦截"))
            if "args" in r and isinstance(r["args"], dict):
                request = request.override(
                    tool_call={**request.tool_call, "args": r["args"]}
                )
        return request, None

    def _after(self, request: Any, result: Any) -> Any:
        """after_tool_call：最后一个非 None `{"result": ...}` 改写返回内容。"""
        api = self.registry.api
        name = request.tool_call.get("name", "")
        ctx = {"state": request.state}
        text = _result_text(result)
        for r in api.emit("after_tool_call", name, text, ctx):
            if isinstance(r, dict) and "result" in r:
                result = _rewrite_result(result, str(r["result"]))
        return result


def _block_message(request: Any, reason: str) -> ToolMessage:
    """block → error ToolMessage 回环给模型（status=error，不执行工具）。"""
    call = request.tool_call
    return ToolMessage(
        content=f"工具 `{call.get('name', '')}` 被扩展拦截：{reason}",
        status="error",
        tool_call_id=call.get("id"),
        name=call.get("name", ""),
    )


def _rewrite_result(result: Any, new_content: str) -> Any:
    """改写工具返回：ToolMessage 重建为新实例（immutable），否则整体替换为字符串。"""
    if isinstance(result, ToolMessage):
        return ToolMessage(
            content=new_content,
            status=result.status,
            tool_call_id=result.tool_call_id,
            name=result.name,
        )
    return new_content


__all__: list[str] = [
    "EVENTS",
    "ExtensionAPI",
    "PluginRegistry",
    "ExtensionHookMiddleware",
    "apply_before_agent_start",
]
