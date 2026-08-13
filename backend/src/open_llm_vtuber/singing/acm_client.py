"""Auto-Convert-Music HTTP 客户端（P2 唱歌学歌）。

封装外部学歌服务（GitHub: MuBai-He/Auto-Convert-Music）的 4 个核心端点，
全程 aiohttp + fail-soft（网络错误抛 ACMError，由 sing_core 捕获转状态）。

端点契约（从 AI-YinMei func/sing/sing_core.py 反向确认）：
- GET /musicInfo/{query}             → {"id": int, "songName": str}（id=0 表示不存在）
- GET /append_song/{query}           → {"status": "processing|processed|waiting", "songName": str}
- GET /accompany_vocal_status        → {"converted_file": [str], "convertfail": [str]}
- GET /get_accompany/{name}          → wav 字节流（伴奏）
- GET /get_vocal/{name}              → wav 字节流（人声演唱）
- GET /download_origin_song/{name}   → 触发原曲下载
- GET /get_audio/{name}              → 原曲 wav 字节流（免学歌规则用）
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp
from loguru import logger

_TIMEOUT_SEC = 10


class ACMError(Exception):
    """Auto-Convert-Music 服务错误（网络 / 非 200 / 解析失败）。"""


async def _get_json(session: aiohttp.ClientSession, url: str, timeout: int = _TIMEOUT_SEC) -> Any:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                raise ACMError(f"HTTP {resp.status} @ {url}")
            return await resp.json(content_type=None)
    except asyncio.TimeoutError:
        raise ACMError(f"timeout @ {url}") from None
    except aiohttp.ClientError as e:
        raise ACMError(f"client error: {type(e).__name__}") from None


async def _get_bytes(session: aiohttp.ClientSession, url: str, timeout: int = 60) -> bytes:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                raise ACMError(f"HTTP {resp.status} @ {url}")
            return await resp.read()
    except asyncio.TimeoutError:
        raise ACMError(f"timeout @ {url}") from None
    except aiohttp.ClientError as e:
        raise ACMError(f"client error: {type(e).__name__}") from None


class ACMClient:
    """Auto-Convert-Music 客户端（每请求独立 session，线程安全）。"""

    def __init__(self, base_url: str):
        self.base_url = (base_url or "").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def music_info(self, query: str) -> dict:
        """真实歌名校验：id=0 表示歌不存在（拒绝 AI 编造歌名）。"""
        data = await _get_json(
            self._session(), f"{self.base_url}/musicInfo/{_quote(query)}"
        )
        if not isinstance(data, dict):
            raise ACMError("musicInfo: bad payload")
        return {"id": int(data.get("id") or 0), "songName": str(data.get("songName") or query)}

    async def append_song(self, query: str) -> dict:
        """发起学歌（非阻塞，服务端异步转换）。"""
        data = await _get_json(
            self._session(), f"{self.base_url}/append_song/{_quote(query)}"
        )
        if not isinstance(data, dict):
            raise ACMError("append_song: bad payload")
        return {
            "status": str(data.get("status") or "waiting"),
            "songName": str(data.get("songName") or query),
        }

    async def accompany_vocal_status(self) -> dict:
        """轮询学歌进度。"""
        data = await _get_json(self._session(), f"{self.base_url}/accompany_vocal_status")
        if not isinstance(data, dict):
            raise ACMError("status: bad payload")
        return {
            "converted_file": list(data.get("converted_file") or []),
            "convertfail": list(data.get("convertfail") or []),
        }

    async def get_vocal(self, songname: str) -> bytes:
        return await _get_bytes(self._session(), f"{self.base_url}/get_vocal/{_quote(songname)}")

    async def get_accompany(self, songname: str) -> bytes:
        return await _get_bytes(self._session(), f"{self.base_url}/get_accompany/{_quote(songname)}")

    async def download_origin_song(self, songname: str) -> None:
        """免学歌规则：触发服务端下载原曲。"""
        await _get_json(self._session(), f"{self.base_url}/download_origin_song/{_quote(songname)}")

    async def get_audio(self, songname: str) -> bytes:
        """免学歌规则：取原曲音频当 vocal。"""
        return await _get_bytes(self._session(), f"{self.base_url}/get_audio/{_quote(songname)}")

    # ------------------------------------------------------------------ #
    def _session(self) -> aiohttp.ClientSession:
        # 轻量：每次调用新 session（学歌轮询频率 1Hz，开销可忽略）。
        # 包级复用会导致事件循环绑定问题（asyncio 多 loop 场景）。
        return aiohttp.ClientSession()


def _quote(text: str) -> str:
    """URL 路径段转义（保持中文可读性 + 防注入）。"""
    from urllib.parse import quote

    return quote(text, safe="")


_instances: dict[str, ACMClient] = {}


def get_acm_client(base_url: str) -> ACMClient:
    """按 base_url 缓存客户端实例。"""
    key = base_url or ""
    if key not in _instances:
        _instances[key] = ACMClient(key)
    return _instances[key]
