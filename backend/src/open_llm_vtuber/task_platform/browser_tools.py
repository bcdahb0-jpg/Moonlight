"""浏览器自动化工具（v4 Phase A3，参考 deer-flow browser_automation/tools.py 精简版）。

有状态 navigate → observe → act 循环：`browser_navigate` 起一个 per-task 浏览器会话，
每次操作返回可交互元素快照（`[ref] 角色: 名称`），模型按 ref 号点击/输入，无需猜选择器。

- 会话 per-task（key=task_id），跨工具调用持久，`browser_close` 显式释放；
- `browser_screenshot` 落盘到 `<workspace>/.pi/tasks/<tid>/browser/`（workspace 内，
  模型可 read_file 读回；同时返回虚拟路径）；
- SSRF：`browser_navigate` 只放行公网 http(s)（内网/元数据拒绝），重定向/子资源由
  session 层 route 兜底；
- 工具**永不抛异常**：失败返回 `[browser] ...` 文案，模型可换 URL/重试；
- 依赖 playwright + 系统 Edge/Chrome（`find_system_browser`），未安装时返回错误提示。
- 单向依赖：browser_tools.py ← graph.py；仅 import browser + conf_bridge + stdlib。
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.tools import BaseTool, tool
from loguru import logger

from .browser import (
    BrowserSession,
    PageSnapshot,
    find_system_browser,
    get_browser_manager,
    validate_public_http_url,
)
from .conf_bridge import TaskPlatformConfig
from .session import session_dir

#: 截图子目录（相对 <workspace>/.pi/tasks/<task_id>/）。
_BROWSER_SUBDIR = "browser"
#: 截图安全文件名。
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_filename(name: str | None) -> str:
    stem = _SAFE_NAME_RE.sub("_", (name or "browser-capture").strip()).strip("._-") or "browser-capture"
    return f"{stem[:80]}.png"


def _snapshot_text(snapshot: PageSnapshot, prefix: str = "") -> str:
    body = snapshot.render()
    return f"{prefix}\n\n{body}" if prefix else body


def _session_kwargs(cfg: TaskPlatformConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headless": cfg.browser_headless,
        "timeout_ms": 30000,
        "viewport": {"width": cfg.browser_viewport_width, "height": cfg.browser_viewport_height},
    }
    if cfg.browser_use_system_edge:
        exe = find_system_browser()
        if exe:
            kwargs["executable_path"] = exe
        else:
            logger.warning("browser: 未找到系统 Edge/Chrome，将尝试 Playwright 自带 Chromium")
    return kwargs


def browser_tools(
    cfg: TaskPlatformConfig,
    workspace: str,
    task_id: str,
) -> list[BaseTool]:
    """构建浏览器工具（cfg.browser_enabled=False 时返回空列表）。"""
    if not cfg.browser_enabled:
        return []
    manager = get_browser_manager()
    key = task_id
    s_kwargs = _session_kwargs(cfg)
    shots_dir = session_dir(workspace, task_id) / _BROWSER_SUBDIR

    def _session() -> BrowserSession:
        return manager.get_session(key, **s_kwargs)

    @tool
    async def browser_navigate(url: str) -> str:
        """打开 URL，开始有状态浏览（仅公网 http/https）。

        返回页面可交互元素快照（[ref] 编号）；后续用 browser_click / browser_type 操作。
        会话跨调用保持，直到 browser_close。JS 重页面（GitHub、后台管理）用它而不是 web_fetch。
        """
        err = validate_public_http_url(url)
        if err:
            return f"[browser] {err}"
        try:
            snap = await _session().navigate(url)
            return _snapshot_text(snap, f"已打开 {url}。")
        except Exception as e:  # noqa: BLE001
            return f"[browser] 打开失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_snapshot() -> str:
        """重新读取当前页面可交互元素（刷新 [ref] 列表；页面异步加载后/操作失败后用）。"""
        try:
            return _snapshot_text(await _session().snapshot())
        except Exception as e:  # noqa: BLE001
            return f"[browser] 快照失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_click(ref: int) -> str:
        """按 [ref] 号点击元素（来自最近一次快照）。返回点击后新快照。"""
        try:
            snap = await _session().click(ref)
            return _snapshot_text(snap, f"已点击 [{ref}]。")
        except Exception as e:  # noqa: BLE001
            return f"[browser] 点击 [{ref}] 失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_type(ref: int, text: str, submit: bool = False) -> str:
        """向 [ref] 号输入框填入文本（submit=true 时回车提交）。返回新快照。"""
        try:
            snap = await _session().type_text(ref, text, submit=submit)
            action = f"已填入 [{ref}]{' 并回车提交' if submit else ''}。"
            return _snapshot_text(snap, action)
        except Exception as e:  # noqa: BLE001
            return f"[browser] 输入失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_get_text(max_chars: int = 8000) -> str:
        """读取当前页面可见文本（提取正文/结果用；大页面截断）。"""
        try:
            text = await _session().get_text(max_chars=max_chars)
            return text or "（页面无可见文本）"
        except Exception as e:  # noqa: BLE001
            return f"[browser] 读取文本失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_back() -> str:
        """返回上一页。返回新快照。"""
        try:
            snap = await _session().back()
            return _snapshot_text(snap, "已返回上一页。")
        except Exception as e:  # noqa: BLE001
            return f"[browser] 返回失败（{type(e).__name__}）：{e}"

    @tool
    async def browser_screenshot(filename: Optional[str] = None) -> str:
        """截取当前页面截图（PNG）并保存；返回文件虚拟路径（可用 read_file 读取）。"""
        try:
            content = await _session().screenshot_bytes(full_page=False)
        except Exception as e:  # noqa: BLE001
            return f"[browser] 截图失败（{type(e).__name__}）：{e}"
        name = _safe_filename(filename or f"shot-{uuid.uuid4().hex[:8]}")
        try:
            shots_dir.mkdir(parents=True, exist_ok=True)
            (shots_dir / name).write_bytes(content)
        except OSError as e:
            return f"[browser] 截图保存失败：{e}"
        rel = os.path.relpath(str(shots_dir / name), workspace).replace("\\", "/")
        return f"截图已保存：/workspace/{rel}（{len(content)} 字节）"

    @tool
    async def browser_close() -> str:
        """关闭当前浏览器会话并释放资源（浏览结束后调用；之后 browser_navigate 开新会话）。"""
        try:
            closed = await manager.close_session(key)
        except Exception as e:  # noqa: BLE001
            return f"[browser] 关闭失败（{type(e).__name__}）：{e}"
        return "浏览器会话已关闭。" if closed else "没有活动浏览器会话。"

    return [
        browser_navigate,
        browser_snapshot,
        browser_click,
        browser_type,
        browser_get_text,
        browser_back,
        browser_screenshot,
        browser_close,
    ]


__all__: list[str] = ["browser_tools"]
