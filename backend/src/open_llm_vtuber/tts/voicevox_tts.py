"""
VOICEVOX 本地日语 TTS 引擎（含萝莉音）。

VOICEVOX 是开源的本地日语语音合成引擎（AGPL/MIT 双授权，见其仓库），提供本地 HTTP API。
本引擎调用标准 VOICEVOX 引擎 API：
  1. POST {base_url}/audio_query?text=...&speaker=<id>  -> 合成参数 JSON
  2. POST {base_url}/synthesis?speaker=<id>            -> WAV 音频字节

默认音色 speaker=3 即「ずんだもん（ノーマル）」——知名的可爱/萝莉系日语声线。
其他常见 speaker：0 四国めたん（ノーマル）、3 ずんだもん（ノーマル）、
4 ずんだもん（あまあま/甜甜的）、8 春日部つむぎ、61 雨晴はう、等。

使用前需先本地启动 VOICEVOX 引擎（默认 http://127.0.0.1:50021）。
"""

from __future__ import annotations

import requests
from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    def __init__(self, base_url: str = "http://127.0.0.1:50021", speaker: int = 3):
        self.base_url = (base_url or "http://127.0.0.1:50021").rstrip("/")
        try:
            self.speaker = int(speaker)
        except (TypeError, ValueError):
            self.speaker = 3  # ずんだもん（ノーマル）

    def _audio_query(self, text: str) -> dict:
        """向 VOICEVOX 请求合成参数 JSON。"""
        r = requests.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": self.speaker},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        cache_file = self.generate_cache_file_name(file_name_no_ext, "wav")
        try:
            query = self._audio_query(text)
            r = requests.post(
                f"{self.base_url}/synthesis",
                params={"speaker": self.speaker},
                json=query,
                timeout=60,
            )
            r.raise_for_status()
            with open(cache_file, "wb") as f:
                f.write(r.content)
            logger.info(f"[voicevox] generated: {cache_file}")
            return cache_file
        except requests.RequestException as e:
            logger.error(
                f"[voicevox] synthesis failed (is VOICEVOX engine running at "
                f"{self.base_url}?): {e}"
            )
            return ""
