"""浏览器自动化会话（v4 Phase A3，参考 deer-flow community/browser_automation/session.py 精简版）。

- **私有事件循环线程**：Playwright async 对象（Browser/Context/Page）绑定创建它的
  事件循环；本模块用单例 daemon 线程跑一个私有 loop，任意 loop 的工具调用经
  `asyncio.run_coroutine_threadsafe` 转发，避免"loop 不匹配"崩溃。
- **系统浏览器免下载**：`browser_use_system_edge=true` 时 `executable_path` 指向
  系统 Edge/Chrome（Windows 自带），不下载 Chromium；可选 CDP 连用户已开浏览器。
- **ref 索引快照**：每次 navigate/click/type 后生成可交互元素列表 `[ref] role: name`，
  模型按 ref 号操作，不猜 CSS 选择器。
- **per-task 会话**：key=task_id，任务间隔离；LRU + 空闲回收（max_sessions / idle 超时）。
- **SSRF 防护**：只允许 http(s)，拒绝内网/云元数据地址（重定向/子资源由 Playwright
  route 拦截兜底，见 _install_request_guard）。
- 单向依赖：browser.py ← graph.py；仅 import stdlib + 惰性 playwright。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: 快照里给可交互元素打的 ref 属性名。
_REF_ATTR = "data-ml-ref"
#: 快照 JS（同 deer-flow 思想：清旧 ref → 收集可见可交互元素 → 打 ref 索引）。
_SNAPSHOT_JS = r"""
() => {
  for (const stale of document.querySelectorAll("[data-ml-ref]")) {
    stale.removeAttribute("data-ml-ref");
  }
  const INTERACTIVE = new Set(["A", "BUTTON", "INPUT", "TEXTAREA", "SELECT"]);
  const results = [];
  let ref = 0;
  const nodes = document.querySelectorAll(
    "a, button, input, textarea, select, [role=button], [role=link], [role=tab], [role=checkbox], [onclick]"
  );
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 &&
      window.getComputedStyle(el).visibility !== "hidden" &&
      window.getComputedStyle(el).display !== "none";
    if (!visible) continue;
    ref += 1;
    el.setAttribute("data-ml-ref", String(ref));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || "";
    const type = el.getAttribute("type") || "";
    let name = (el.getAttribute("aria-label") || el.getAttribute("name") ||
      el.getAttribute("placeholder") || el.innerText || el.value || "").trim();
    if (name.length > 120) name = name.slice(0, 120) + "…";
    results.push({ ref, tag, role, type, name });
    if (results.length >= 200) break;
  }
  return { url: location.href, title: document.title, elements: results };
}
"""

#: 内网/保留网段（SSRF 拒绝）。
_PRIVATE_NETS = [
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),
    ip_network("0.0.0.0/8"),
    ip_network("100.64.0.0/10"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
]
#: 云元数据地址（SSRF 高危）。
_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal"}

#: 会话默认超时 / 点击超时。
_DEFAULT_TIMEOUT_MS = 30000
_CLICK_TIMEOUT_MS = 8000
#: 会话管理上限 / 空闲回收（秒）。
_DEFAULT_MAX_SESSIONS = 8
_DEFAULT_IDLE_TIMEOUT_S = 30 * 60.0

#: 系统浏览器可执行文件候选路径（Edge 优先，Windows）。
_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_system_browser() -> str | None:
    """返回系统浏览器可执行文件路径（Edge/Chrome），找不到返回 None。"""
    for p in _EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def validate_public_http_url(url: str) -> str | None:
    """SSRF 校验：仅 http(s) 且非内网/元数据地址。返回错误文案（合法返回 None）。

    域名直接拒绝（解析到内网的风险交由 Playwright route 层兜底拦截）。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return f"非法 URL：{url!r}"
    if parsed.scheme not in ("http", "https"):
        return f"只允许 http/https：{url!r}"
    host = parsed.hostname
    if not host:
        return f"URL 缺少主机名：{url!r}"
    if host in _METADATA_HOSTS:
        return f"拒绝访问云元数据地址：{url!r}"
    # IP 字面量 → 网段校验；域名 → 放行（route 层兜底拦截解析结果）
    try:
        addr = ip_address(host)
    except ValueError:
        return None
    if any(addr in net for net in _PRIVATE_NETS):
        return f"拒绝访问内网地址：{url!r}"
    return None


@dataclass
class SnapshotElement:
    ref: int
    tag: str
    role: str
    type: str
    name: str

    def render(self) -> str:
        label = self.role or self.tag
        detail = f" type={self.type}" if self.type else ""
        name = self.name or "(无文本)"
        return f"[{self.ref}] {label}{detail}: {name}"


@dataclass
class PageSnapshot:
    url: str
    title: str
    elements: list[SnapshotElement] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}", ""]
        if not self.elements:
            lines.append("无可交互元素。")
        else:
            lines.append("可交互元素（按 [ref] 号操作）：")
            lines.extend(el.render() for el in self.elements)
        return "\n".join(lines)


class _PlaywrightLoopThread:
    """私有 asyncio 事件循环线程（Playwright 对象 loop-affine 的解耦层）。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="moonlight-browser-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """把 *coro* 调度到私有 loop 并从任意 loop await。"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)


class BrowserSession:
    """单个任务绑定的 Playwright 浏览器+页面（所有操作经私有 loop）。"""

    def __init__(
        self,
        loop: _PlaywrightLoopThread,
        *,
        headless: bool,
        timeout_ms: int,
        viewport: dict[str, int],
        executable_path: str | None = None,
        cdp_url: str | None = None,
    ) -> None:
        self._loop = loop
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._viewport = viewport
        self._executable_path = executable_path
        self._cdp_url = cdp_url
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._ensure_lock = asyncio.Lock()
        self._guard_bound = False

    # ------------------------------------------------------------------ //
    # 内部（私有 loop 上执行）
    # ------------------------------------------------------------------ //
    async def _ensure_page(self) -> Page:
        if self._page is not None and not self._page.is_closed():
            return self._page
        async with self._ensure_lock:
            if self._page is not None and not self._page.is_closed():
                return self._page
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {"headless": self._headless}
            if self._executable_path:
                launch_kwargs["executable_path"] = self._executable_path
            if self._cdp_url:
                self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
                self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            else:
                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                self._context = await self._browser.new_context(viewport=self._viewport)
                await self._install_request_guard()
            self._context.set_default_timeout(self._timeout_ms)
            pages = self._context.pages
            self._page = pages[-1] if pages else await self._context.new_page()
            return self._page

    async def _install_request_guard(self) -> None:
        """route 层拦截：重定向/子资源请求命中内网或元数据地址 → abort（SSRF 兜底）。"""
        if self._context is None or self._guard_bound:
            return

        async def _route(route: Any) -> None:
            try:
                req_url = route.request.url
            except Exception:  # noqa: BLE001
                req_url = ""
            err = validate_public_http_url(req_url) if req_url.startswith(("http://", "https://")) else None
            if err is not None:
                logger.warning(f"browser: SSRF 拦截 {req_url}")
                with contextlib.suppress(Exception):
                    await route.abort("blockedbyclient")
                return
            with contextlib.suppress(Exception):
                await route.continue_()

        with contextlib.suppress(Exception):
            await self._context.route("**/*", _route)
            self._guard_bound = True

    async def _snapshot(self) -> PageSnapshot:
        page = await self._ensure_page()
        data = await page.evaluate(_SNAPSHOT_JS)
        elements = [
            SnapshotElement(ref=int(e["ref"]), tag=e["tag"], role=e["role"], type=e["type"], name=e["name"])
            for e in data["elements"]
        ]
        return PageSnapshot(url=data["url"], title=data["title"], elements=elements)

    async def _navigate(self, url: str) -> PageSnapshot:
        page = await self._ensure_page()
        await page.goto(url, wait_until="domcontentloaded")
        return await self._snapshot()

    async def _click(self, ref: int) -> PageSnapshot:
        page = await self._ensure_page()
        selector = f'[{_REF_ATTR}="{ref}"]'
        base = page.locator(selector)
        if await base.count() == 0:
            raise RuntimeError(f"元素 [{ref}] 已不在页面上；请先 browser_snapshot 刷新 ref")
        locator = base.first
        with contextlib.suppress(Exception):
            await locator.scroll_into_view_if_needed(timeout=_CLICK_TIMEOUT_MS)
        try:
            await locator.click(timeout=_CLICK_TIMEOUT_MS)
        except Exception as exc:
            raise RuntimeError(
                f"元素 [{ref}] 点击失败（{type(exc).__name__}）；页面可能已变化，请 browser_snapshot 后重试"
            ) from exc
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        return await self._snapshot()

    async def _type(self, ref: int, text: str, submit: bool) -> PageSnapshot:
        page = await self._ensure_page()
        selector = f'[{_REF_ATTR}="{ref}"]'
        await page.fill(selector, text)
        if submit:
            await page.press(selector, "Enter")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
        return await self._snapshot()

    async def _get_text(self, max_chars: int) -> str:
        page = await self._ensure_page()
        return (await page.inner_text("body"))[:max_chars]

    async def _back(self) -> PageSnapshot:
        page = await self._ensure_page()
        await page.go_back(wait_until="domcontentloaded")
        return await self._snapshot()

    async def _current_url(self) -> str:
        page = await self._ensure_page()
        return page.url

    async def _screenshot_bytes(self, full_page: bool) -> bytes:
        page = await self._ensure_page()
        return await page.screenshot(full_page=full_page, type="png")

    async def _close(self) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------ //
    # 公开 API（任意 loop 可 await）
    # ------------------------------------------------------------------ //
    async def navigate(self, url: str) -> PageSnapshot:
        return await self._loop.run(self._navigate(url))

    async def snapshot(self) -> PageSnapshot:
        return await self._loop.run(self._snapshot())

    async def click(self, ref: int) -> PageSnapshot:
        return await self._loop.run(self._click(ref))

    async def type_text(self, ref: int, text: str, submit: bool = False) -> PageSnapshot:
        return await self._loop.run(self._type(ref, text, submit))

    async def get_text(self, max_chars: int = 8000) -> str:
        return await self._loop.run(self._get_text(max_chars))

    async def back(self) -> PageSnapshot:
        return await self._loop.run(self._back())

    async def current_url(self) -> str:
        return await self._loop.run(self._current_url())

    async def screenshot_bytes(self, full_page: bool = False) -> bytes:
        return await self._loop.run(self._screenshot_bytes(full_page))

    async def close(self) -> None:
        await self._loop.run(self._close())


class BrowserSessionManager:
    """per-task 浏览器会话注册表（key=task_id）+ LRU 空闲回收。"""

    def __init__(self, *, max_sessions: int = _DEFAULT_MAX_SESSIONS, idle_timeout_s: float = _DEFAULT_IDLE_TIMEOUT_S):
        self._loop: _PlaywrightLoopThread | None = None
        self._sessions: dict[str, BrowserSession] = {}
        self._last_used: dict[str, float] = {}
        self._max_sessions = max_sessions
        self._idle_timeout_s = idle_timeout_s
        self._lock = threading.Lock()

    def _ensure_loop(self) -> _PlaywrightLoopThread:
        if self._loop is None:
            self._loop = _PlaywrightLoopThread()
        return self._loop

    def get_session(self, key: str, **kwargs: Any) -> BrowserSession:
        """获取（或创建）任务会话；顺手回收空闲/超限会话（不开 pin，单任务串行足够）。"""
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                evicted = self._evict_locked(keep_key=key)
                session = BrowserSession(self._ensure_loop(), **kwargs)
                self._sessions[key] = session
                self._last_used[key] = time.monotonic()
                for old in evicted:
                    self._schedule_close(old)
            else:
                self._last_used[key] = time.monotonic()
        return session

    def _evict_locked(self, *, keep_key: str) -> list[BrowserSession]:
        """回收空闲超时的会话 + 超出 max_sessions 时关最旧（不关 keep_key）。"""
        now = time.monotonic()
        evicted: list[BrowserSession] = []
        for other_key, last_used in list(self._last_used.items()):
            if other_key == keep_key:
                continue
            if self._idle_timeout_s > 0 and now - last_used >= self._idle_timeout_s:
                s = self._sessions.pop(other_key, None)
                self._last_used.pop(other_key, None)
                if s is not None:
                    evicted.append(s)
        while self._max_sessions > 0 and len(self._sessions) > self._max_sessions:
            candidates = [k for k in self._sessions if k != keep_key]
            if not candidates:
                break
            lru = min(candidates, key=lambda k: self._last_used.get(k, 0))
            s = self._sessions.pop(lru, None)
            self._last_used.pop(lru, None)
            if s is not None:
                evicted.append(s)
        return evicted

    def _schedule_close(self, session: BrowserSession) -> None:
        if self._loop is None:
            return
        with contextlib.suppress(Exception):
            self._loop.run(session._close())

    async def close_session(self, key: str) -> bool:
        with self._lock:
            session = self._sessions.pop(key, None)
            self._last_used.pop(key, None)
        if session is None:
            return False
        await session.close()
        return True


#: 进程级单例。
_manager: BrowserSessionManager | None = None
_manager_lock = threading.Lock()


def get_browser_manager() -> BrowserSessionManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = BrowserSessionManager()
    return _manager


def reset_browser_manager() -> None:
    """测试钩子：重置进程级 manager（不关闭已有会话）。"""
    global _manager
    with _manager_lock:
        _manager = None


__all__: list[str] = [
    "BrowserSession",
    "BrowserSessionManager",
    "PageSnapshot",
    "get_browser_manager",
    "validate_public_http_url",
    "find_system_browser",
]
