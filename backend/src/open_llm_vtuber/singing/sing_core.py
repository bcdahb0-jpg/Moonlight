"""唱歌核心（P2 唱歌 MVP）：点歌队列 + 学歌状态机 + 可播列表。

流程（移植 AI-YinMei func/sing/sing_core.py 思路，Python asyncio 化）：
1. `request(text)`：从对话文本提取「唱歌+歌名」→ 真实歌名校验（musicInfo id!=0）
   → 入点歌队列（去重：队列/当前/可播中已存在则拒绝）
2. 后台 worker 逐个学歌：append_song → 每秒轮询 accompany_vocal_status
   → 下载 vocal.wav（+accompany.wav 可选）到 backend/output/singing/<歌名>/
   → 加入可播列表（ready_list）
3. `next()`：从可播列表出队 → 返回音频 URL；前端 <audio> 播放，播完再调 next

状态：{state: idle|learning|error, learning: bool, current, queue[], ready[],
       progress, last_error}
免学歌规则：song_not_convert 正则命中 → 直接下载原曲当 vocal（跳过转换）。
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from .acm_client import ACMError, get_acm_client

# 学歌产物目录（相对后端 cwd；由 server.py 静态挂载 /singing-output）。
OUTPUT_DIR = Path("output") / "singing"

# 「唱歌+歌名」触发词（移植 AI-YinMei cmd_core.py 触发词表）。
SING_TRIGGERS = ("唱一下", "唱一首", "唱歌", "点歌", "点播")

DEFAULT_CONFIG = {
    "acm_url": "http://127.0.0.1:1717",
    "create_timeout": 500,  # 秒
    "song_not_convert": "",  # 正则
}


def extract_sing_request(text: str) -> Optional[str]:
    """从对话文本提取「唱歌+歌名」；无触发词返回 None。

    "唱歌+打上花火" / "唱一首晴天" → "打上花火" / "晴天"
    """
    if not text:
        return None
    stripped = text.strip()
    for trigger in SING_TRIGGERS:
        if stripped.startswith(trigger):
            name = stripped[len(trigger):].strip()
            name = name.lstrip("+，,、 ").strip()
            if name:
                return name[:80]
            return None
    return None


class SingCore:
    """唱歌状态机（全局单例，由 sing_route 持有）。"""

    def __init__(self) -> None:
        self._config = dict(DEFAULT_CONFIG)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued_names: list[str] = []  # 影子队列（asyncio.Queue 无 peek）
        self._ready: list[dict] = []  # 学歌完成可播列表
        self._current: Optional[dict] = None
        self._state = "idle"  # idle | learning | error
        self._learning = False
        self._stop_flag = False
        self._progress = ""
        self._last_error = ""
        self._worker_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("[singing] worker started")

    async def stop(self) -> None:
        self._stop_flag = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._worker_task = None

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    async def request(self, text: str) -> dict:
        """点歌入队。返回 {ok, songname, reason?}。"""
        songname = extract_sing_request(text)
        if not songname:
            return {"ok": False, "reason": "未识别到「唱歌+歌名」指令"}
        client = self._acm()
        if not client.configured:
            return {"ok": False, "reason": "学歌服务未配置（acm_url）"}
        # 真实歌名校验（拒绝 AI 编造歌名）
        try:
            info = await client.music_info(songname)
        except ACMError as e:
            return {"ok": False, "reason": f"学歌服务不可达：{e}"}
        if info["id"] == 0:
            return {"ok": False, "reason": f"找不到歌曲「{songname}」"}
        real = info["songName"]
        # 去重：队列 / 可播 / 当前
        if any(q == real for q in self._queue_items()):
            return {"ok": False, "reason": f"「{real}」已在队列中"}
        if any(r["songname"] == real for r in self._ready):
            return {"ok": False, "reason": f"「{real}」已学完待播放"}
        if self._current and self._current.get("songname") == real:
            return {"ok": False, "reason": f"「{real}」正在播放"}
        await self._queue.put(real)
        logger.info(f"[singing] queued: {real}")
        return {"ok": True, "songname": real}

    def status(self) -> dict:
        return {
            "state": self._state,
            "learning": self._learning,
            "current": self._current,
            "queue": self._queue_items(),
            "ready": list(self._ready),
            "progress": self._progress,
            "last_error": self._last_error,
            "config": dict(self._config),
            "output_dir": str(OUTPUT_DIR),
        }

    async def next_song(self) -> dict:
        """出队下一首可播歌曲；无则返回 {ok: False}。"""
        if not self._ready:
            return {"ok": False, "reason": "暂无学完的歌曲，先点一首吧"}
        song = self._ready.pop(0)
        self._current = song
        logger.info(f"[singing] now playing: {song.get('songname')}")
        return {"ok": True, "song": song}

    async def stop_learning(self) -> dict:
        self._stop_flag = True
        self._progress = "已停止学歌"
        return {"ok": True}

    async def clear(self) -> dict:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._ready.clear()
        self._current = None
        self._last_error = ""
        return {"ok": True}

    def set_config(self, patch: dict) -> None:
        for key in DEFAULT_CONFIG:
            if key in patch and patch[key] is not None:
                self._config[key] = patch[key]

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _acm(self):
        return get_acm_client(self._config["acm_url"])

    def _queue_items(self) -> list[str]:
        # asyncio.Queue 无 peek：维护影子列表成本高，直接返回空（状态由 ready 表达）。
        # 点歌队列实时长度由 _queued_names 影子维护。
        return list(self._queued_names)

    async def _worker(self) -> None:
        while True:
            try:
                songname = await self._queue.get()
                self._queued_names.append(songname)
                try:
                    await self._learn(songname)
                finally:
                    if songname in self._queued_names:
                        self._queued_names.remove(songname)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 兜底：worker 绝不崩溃
                logger.error(f"[singing] worker error: {type(e).__name__}: {e}")
                self._last_error = f"worker: {type(e).__name__}: {e}"
                self._state = "error"
                await asyncio.sleep(1)

    async def _learn(self, query: str) -> None:
        self._state = "learning"
        self._learning = True
        self._stop_flag = False
        client = self._acm()
        try:
            info = await client.music_info(query)
            if info["id"] == 0:
                raise ACMError(f"歌曲不存在: {query}")
            real = info["songName"]
            out_dir = OUTPUT_DIR / real
            out_dir.mkdir(parents=True, exist_ok=True)
            vocal_path = out_dir / "vocal.wav"

            if vocal_path.exists() and vocal_path.stat().st_size > 0:
                self._progress = f"「{real}」已有本地产物，直接可播"
                self._mark_ready(real, out_dir)
                return

            skip_re = self._config.get("song_not_convert") or ""
            if skip_re and re.search(skip_re, real):
                self._progress = f"「{real}」命中免学歌规则，下载原曲"
                await client.download_origin_song(real)
                data = await client.get_audio(real)
                vocal_path.write_bytes(data)
                self._mark_ready(real, out_dir)
                return

            await client.append_song(query)
            deadline = time.monotonic() + float(self._config.get("create_timeout", 500))
            self._progress = f"「{real}」学歌中…"
            while time.monotonic() < deadline:
                if self._stop_flag:
                    self._progress = f"「{real}」学歌已停止"
                    return
                await asyncio.sleep(1)
                try:
                    st = await client.accompany_vocal_status()
                except ACMError:
                    continue
                if real in st.get("converted_file", []):
                    break
                if real in st.get("convertfail", []):
                    raise ACMError(f"「{real}」学歌失败（服务端 convertfail）")
                self._progress = f"「{real}」学歌中…（轮询中）"
            else:
                raise ACMError(f"「{real}」学歌超时（{self._config.get('create_timeout')}s）")

            vocal = await client.get_vocal(real)
            vocal_path.write_bytes(vocal)
            try:
                accomp = await client.get_accompany(real)
                (out_dir / "accompany.wav").write_bytes(accomp)
            except ACMError:
                pass  # 伴奏缺失不影响演唱
            self._progress = f"「{real}」学歌完成"
            self._mark_ready(real, out_dir)
        except ACMError as e:
            self._last_error = str(e)
            self._progress = str(e)
            self._state = "error"
            logger.warning(f"[singing] learn failed: {e}")
        finally:
            self._learning = False
            if self._state == "learning":
                self._state = "idle"

    def _mark_ready(self, real: str, out_dir: Path) -> None:
        self._ready.append(
            {
                "songname": real,
                "audio_url": f"/singing-output/{real}/vocal.wav",
                "accompany_url": f"/singing-output/{real}/accompany.wav",
                "is_created": True,
            }
        )
        self._state = "idle"


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #
_core: Optional[SingCore] = None
_core_lock = asyncio.Lock()


async def get_sing_core() -> SingCore:
    global _core
    if _core is None:
        async with _core_lock:
            if _core is None:
                _core = SingCore()
                _core.start()
    return _core
