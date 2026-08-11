"""网页搜索 / 抓取工具（v3 Phase 2 + v4 Phase A2 加固）。

- `web_search`：Tavily API 优先（有 key），失败自动降级 DuckDuckGo（无 key 免费源）。
  provider=auto → 有 tavily_api_key 用 tavily，否则 ddg；失败链式兜底（tavily → ddg）。
- `web_fetch`：**Jina Reader → HTTP 直抓兜底**（v4 Phase A2）：
  1. Jina Reader（https://r.jina.ai/<url>，有 key 更稳；匿名可能 403/限流）；
  2. Jina 失败 → httpx GET 原 URL + stdlib HTMLParser 提取正文（带浏览器 UA）。
  去标签化 + 截断到 web_fetch_max_bytes。
- TLS 适配（v4 Phase A2）：`web_verify_tls=false` 时 httpx 跳过证书校验
  （Clash 等代理做 TLS 拦截且证书不受信的场景）。
- 工具**永不抛异常**（deer-flow 铁律）：失败返回 `[web] ...` 错误文案，模型可重试/换源。
- `allow_network=False`（conf.yaml 总开关）时返回空列表（工具不注册），与 MCP 降级一致。

单向依赖：web.py ← graph.py；仅 import httpx + conf_bridge + stdlib。
"""

from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from .conf_bridge import TaskPlatformConfig, task_config

#: Jina Reader 端点（无 key 匿名可用，限流更严；有 JINA_API_KEY 更稳）。
_JINA_READER = "https://r.jina.ai/{url}"
#: Tavily 搜索端点。
_TAVILY_SEARCH = "https://api.tavily.com/search"
#: 抓取正文上限（配置可覆盖）。
_DEFAULT_FETCH_LIMIT = 524288
#: 搜索超时（秒）。
_SEARCH_TIMEOUT = 15.0
#: 直抓超时（秒；页面比搜索接口慢）。
_FETCH_TIMEOUT = 20.0
#: 浏览器 UA（绕过简单反爬；Jina/直抓共用）。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 剥标签 + 压缩空白。
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


class _HtmlTextExtractor(HTMLParser):
    """直抓兜底的正文提取：去标签 + 块级标签换行 + 跳过 script/style（stdlib，无 bs4）。"""

    _BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "br", "section", "article", "main",
        "header", "footer", "blockquote", "pre", "table",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "head"}

    def __init__(self, doc: str) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self.feed(doc)  # HTMLParser 需 feed 后才解析

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return _NL_RE.sub("\n\n", _WS_RE.sub(" ", "".join(self._parts))).strip()


class WebClient:
    """绑定配置的网页搜索/抓取客户端（测试注入 httpx mock）。"""

    def __init__(
        self,
        cfg: Optional[TaskPlatformConfig] = None,
        *,
        client_factory: Optional[callable] = None,
    ):
        self.cfg = cfg or task_config()
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._client_factory:
                self._client = self._client_factory()
            else:
                self._client = httpx.AsyncClient(
                    timeout=_SEARCH_TIMEOUT,
                    verify=self.cfg.web_verify_tls,  # v4 Phase A2：Clash TLS 拦截可关
                )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ //
    # web_search
    # ------------------------------------------------------------------ //
    async def search(self, query: str, max_results: Optional[int] = None) -> str:
        """网页搜索：Tavily → DDG 兜底。返回 标题/链接/摘要 列表。"""
        limit = max_results or self.cfg.web_search_max_results
        provider = self.cfg.web_search_provider
        if provider not in ("auto", "ddg", "tavily"):
            provider = "auto"
        errors: list[str] = []
        if provider in ("auto", "tavily") and self.cfg.tavily_api_key:
            r = await self._search_tavily(query, limit)
            if r is not None:
                return r
            errors.append("tavily")
        if provider in ("auto", "ddg") or errors:
            r = await self._search_ddg(query, limit)
            if r is not None:
                return r
            errors.append("ddg")
        return f"[web] 搜索失败：{', '.join(errors)} 均不可用，请稍后重试或换关键词"

    async def _search_tavily(self, query: str, max_results: int) -> str | None:
        try:
            client = await self._get_client()
            resp = await client.post(
                _TAVILY_SEARCH,
                json={
                    "api_key": self.cfg.tavily_api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = data.get("results") or []
            lines = [f"[tavily] {query}："]
            for r in results[:max_results]:
                title = str(r.get("title", "")).strip()
                url = str(r.get("url", "")).strip()
                snippet = str(r.get("content", "")).strip()
                lines.append(f"- {title}\n  {url}\n  {snippet[:200]}")
            return "\n".join(lines) if len(lines) > 1 else "(无结果)"
        except Exception as e:  # noqa: BLE001 网络/JSON 异常 → 降级
            return None

    async def _search_ddg(self, query: str, max_results: int) -> str | None:
        """DuckDuckGo 免费搜索（duckduckgo_search 同步库，丢线程池防阻塞）。"""
        try:
            from duckduckgo_search import DDGS

            def _run() -> str:
                with DDGS(timeout=_SEARCH_TIMEOUT) as ddgs:
                    hits = list(ddgs.text(query, max_results=max_results))
                if not hits:
                    return "(无结果)"
                lines = [f"[ddg] {query}："]
                for r in hits:
                    title = str(r.get("title", "")).strip()
                    url = str(r.get("href", "") or r.get("url", "")).strip()
                    snippet = str(r.get("body", "")).strip()
                    lines.append(f"- {title}\n  {url}\n  {snippet[:200]}")
                return "\n".join(lines)

            return await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 网络/库缺失 → 降级
            return None

    # ------------------------------------------------------------------ //
    # web_fetch
    # ------------------------------------------------------------------ //
    async def fetch(self, url: str) -> str:
        """抓取网页正文：Jina Reader → HTTP 直抓兜底（v4 Phase A2）。"""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"[web] 拒绝抓取非 http(s) 协议：{url}"
        limit = self.cfg.web_fetch_max_bytes or _DEFAULT_FETCH_LIMIT

        # 1) Jina Reader（有 key 更稳；匿名可能 403/限流）
        jina_error = ""
        try:
            client = await self._get_client()
            headers = {}
            if self.cfg.jina_api_key:
                headers["Authorization"] = f"Bearer {self.cfg.jina_api_key}"
            resp = await client.get(_JINA_READER.format(url=url), headers=headers)
            if resp.status_code == 200 and resp.text.strip():
                return self._finalize(resp.text, limit, url)
            jina_error = f"Jina HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            jina_error = f"Jina {type(e).__name__}"

        # 2) HTTP 直抓兜底（带浏览器 UA + stdlib 正文提取）
        direct = await self._fetch_direct(url)
        if direct is not None:
            if direct:
                return self._finalize(direct, limit, url)
            return f"[web] 抓取到空内容：{url}"

        return f"[web] 抓取失败（{jina_error}，直抓也不可用）：{url}"

    async def _fetch_direct(self, url: str) -> Optional[str]:
        """直接 GET 目标 URL 并提取正文。

        Returns:
            str: 正文（可能为空串 = 200 但无内容）；None = 请求失败（触发上层报错）。
        """
        try:
            client = await self._get_client()
            resp = await client.get(
                url,
                headers={"User-Agent": _BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                timeout=_FETCH_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            text = resp.text
            if not text.strip():
                return ""
            if "<html" in text.lower() or "<body" in text.lower():
                return _HtmlTextExtractor(text).text() or ""
            return text.strip()  # 非 HTML（纯文本/JSON）原样返回
        except Exception:  # noqa: BLE001 直抓失败 → 上层报错文案
            return None

    @staticmethod
    def _finalize(text: str, limit: int, url: str) -> str:
        """统一后处理：剥标签（Jina 偶发 HTML）+ 压缩空白 + 截断 + 空检查。"""
        if "<html" in text.lower() or "<body" in text.lower():
            text = _TAG_RE.sub("", text)
        text = html.unescape(text)
        text = _NL_RE.sub("\n\n", _WS_RE.sub(" ", text)).strip()
        if not text:
            return f"[web] 抓取到空内容：{url}"
        if len(text) > limit:
            text = text[:limit] + "\n...(内容截断)"
        return text


def web_tools(
    cfg: Optional[TaskPlatformConfig] = None,
    *,
    client_factory: Optional[callable] = None,
) -> list:
    """构建 web 工具（allow_network=False 时返回空列表）。测试注入 client_factory。"""
    cfg = cfg or task_config()
    if not cfg.allow_network or not cfg.web_search_enabled:
        return []
    wc = WebClient(cfg, client_factory=client_factory)

    @tool
    async def web_search(query: str, max_results: Optional[int] = None) -> str:
        """网页搜索（Tavily 优先，失败自动降级 DuckDuckGo）。

        返回 标题/链接/摘要 列表。调研类任务用：查最新版本、找文档、搜错误信息等。
        """
        return await wc.search(query, max_results)

    @tool
    async def web_fetch(url: str) -> str:
        """抓取网页正文（Jina Reader，自动去标签，上限 512KB）。

        只接受 http/https；配合 web_search 结果中的链接使用。
        """
        return await wc.fetch(url)

    return [web_search, web_fetch]
