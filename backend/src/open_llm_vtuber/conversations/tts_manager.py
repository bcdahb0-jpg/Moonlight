import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import List, Optional, Dict
from loguru import logger

from ..agent.output_types import DisplayText, Actions
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload
from .types import WebSocketSend


class TTSTaskManager:
    """Manages TTS tasks and ensures ordered delivery to frontend while allowing parallel TTS generation"""

    # 语音按「段落」聚合：累计多少个字符才合成一段音频。
    # 默认 100 字 ≈ 3~5 句，避免「逐句合成、句间停顿」造成的断断续续；
    # 一个回复通常 1~3 段，听感接近「每个气泡一口气说完」。
    TTS_BATCH_MIN_CHARS = 100

    def __init__(self) -> None:
        self.task_list: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        # Queue to store ordered payloads
        self._payload_queue: asyncio.Queue[Dict] = asyncio.Queue()
        # Task to handle sending payloads in order
        self._sender_task: Optional[asyncio.Task] = None
        # Counter for maintaining order
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        # 句子聚合缓冲（批量合成，减少碎段）
        self._batch_text = ""
        self._batch_display_text: Optional[DisplayText] = None
        self._batch_actions: Optional[Actions] = None
        self._batch_subtitle = ""
        self._last_engine: Optional[TTSInterface] = None
        self._last_send: Optional[WebSocketSend] = None

    async def speak(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        websocket_send: WebSocketSend,
        subtitle_text: Optional[str] = None,
    ) -> None:
        """
        Queue a TTS task while maintaining order of delivery.

        逐句文本先进入聚合缓冲，累计满 TTS_BATCH_MIN_CHARS 才整段合成；
        回复结束由 finalize 调用 flush() 把剩余文本合成。这样语音按「段落」
        而非「句子」播放，大幅减少逐句合成造成的断续感。

        Args:
            tts_text: Text to synthesize
            display_text: Text to display in UI
            actions: Live2D model actions
            live2d_model: Live2D model instance
            tts_engine: TTS engine instance
            websocket_send: WebSocket send function
            subtitle_text: Optional display-only translated subtitle. When None the
                frontend falls back to display_text.text (the canonical reply R).
                This NEVER replaces display_text.text, which memory/history rely on.
        """
        if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", tts_text)) == 0:
            logger.debug("Empty TTS text, sending silent display payload")
            # Get current sequence number for silent payload
            current_sequence = self._sequence_counter
            self._sequence_counter += 1

            # Start sender task if not running
            if not self._sender_task or self._sender_task.done():
                self._sender_task = asyncio.create_task(
                    self._process_payload_queue(websocket_send)
                )

            await self._send_silent_payload(
                display_text, actions, current_sequence, subtitle_text
            )
            return

        logger.debug(
            f"🏃Queuing TTS text for '''{tts_text}''' (by {display_text.name})"
        )

        # 记住最近一次引擎/发送器，flush() 时使用
        self._last_engine = tts_engine
        self._last_send = websocket_send

        # 聚合：合并相邻句子，累计满阈值再合成整段
        self._batch_text += tts_text
        # 中文原文：拼接（与字幕同长度），避免前端只显示最后一句而字幕/语音是整段。
        if self._batch_display_text is None:
            self._batch_display_text = display_text
        elif display_text and display_text.text:
            # 累加：句与句之间无明确分隔符时补一个空格，避免粘连
            sep = "" if (self._batch_display_text.text.endswith((" ", "，", "。", "！", "？", "～", "…"))
                          or display_text.text.startswith(("，", "。", "！", "？", "～", "…"))) else ""
            self._batch_display_text = DisplayText(
                text=self._batch_display_text.text + sep + display_text.text,
                name=display_text.name,
                avatar=display_text.avatar,
            )
        self._batch_actions = actions or self._batch_actions
        self._batch_subtitle = (self._batch_subtitle + " " + (subtitle_text or display_text.text)).strip()

        if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", self._batch_text)) >= self.TTS_BATCH_MIN_CHARS:
            await self.flush()

    async def flush(self) -> None:
        """把聚合缓冲中的剩余文本整段合成（回复结束或达到阈值时调用）。"""
        if not self._batch_text.strip():
            return
        text, display_text, actions, subtitle_text = (
            self._batch_text,
            self._batch_display_text,
            self._batch_actions,
            self._batch_subtitle,
        )
        self._batch_text = ""
        self._batch_display_text = None
        self._batch_actions = None
        self._batch_subtitle = ""

        if not self._last_engine or not self._last_send or display_text is None:
            logger.warning("[tts] flush skipped: no engine/sender available")
            return

        # 字幕/显示文本：整段合并（避免前端播放时字幕逐句跳动）。
        # 注意：display_text.text 必须始终是原文 R（memory/history 的唯一来源），
        # 字幕翻译只走独立的 subtitle_text 字段——若把字幕塞进 display_text，
        # 前端气泡会变成翻译文本（无双语气泡），历史也会被污染。
        merged_display = DisplayText(
            text=display_text.text,
            name=display_text.name,
            avatar=display_text.avatar,
        )

        # Get current sequence number
        current_sequence = self._sequence_counter
        self._sequence_counter += 1

        # Start sender task if not running
        if not self._sender_task or self._sender_task.done():
            self._sender_task = asyncio.create_task(
                self._process_payload_queue(self._last_send)
            )

        # Create and queue the TTS task (synthesize the whole segment at once)
        task = asyncio.create_task(
            self._process_tts(
                tts_text=text,
                display_text=merged_display,
                actions=actions,
                live2d_model=None,
                tts_engine=self._last_engine,
                sequence_number=current_sequence,
                subtitle_text=subtitle_text or None,
            )
        )
        self.task_list.append(task)

    async def _process_payload_queue(self, websocket_send: WebSocketSend) -> None:
        """
        Process and send payloads in correct order.
        Runs continuously until all payloads are processed.
        """
        buffered_payloads: Dict[int, Dict] = {}

        while True:
            try:
                # Get payload from queue
                payload, sequence_number = await self._payload_queue.get()
                buffered_payloads[sequence_number] = payload

                # Send payloads in order
                while self._next_sequence_to_send in buffered_payloads:
                    next_payload = buffered_payloads.pop(self._next_sequence_to_send)
                    await websocket_send(json.dumps(next_payload))
                    self._next_sequence_to_send += 1

                self._payload_queue.task_done()

            except asyncio.CancelledError:
                break

    async def _send_silent_payload(
        self,
        display_text: DisplayText,
        actions: Optional[Actions],
        sequence_number: int,
        subtitle_text: Optional[str] = None,
    ) -> None:
        """Queue a silent audio payload"""
        audio_payload = prepare_audio_payload(
            audio_path=None,
            display_text=display_text,
            actions=actions,
            subtitle_text=subtitle_text,
        )
        await self._payload_queue.put((audio_payload, sequence_number))

    async def _process_tts(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        sequence_number: int,
        subtitle_text: Optional[str] = None,
    ) -> None:
        """Process TTS generation and queue the result for ordered delivery"""
        audio_file_path = None
        try:
            audio_file_path = await self._generate_audio(tts_engine, tts_text)
            payload = prepare_audio_payload(
                audio_path=audio_file_path,
                display_text=display_text,
                actions=actions,
                subtitle_text=subtitle_text,
            )
            has_audio = payload.get("audio") is not None
            logger.info(f"Audio payload ready: has_audio={has_audio}, text='{tts_text[:30]}'")
            # Queue the payload with its sequence number
            await self._payload_queue.put((payload, sequence_number))

        except Exception as e:
            logger.error(f"Error preparing audio payload: {e}")
            # Queue silent payload for error case
            payload = prepare_audio_payload(
                audio_path=None,
                display_text=display_text,
                actions=actions,
                subtitle_text=subtitle_text,
            )
            await self._payload_queue.put((payload, sequence_number))

        finally:
            if audio_file_path:
                tts_engine.remove_file(audio_file_path)
                logger.debug("Audio cache file cleaned.")

    async def _generate_audio(self, tts_engine: TTSInterface, text: str) -> str:
        """Generate audio file from text"""
        logger.debug(f"🏃Generating audio for '''{text}'''...")
        return await tts_engine.async_generate_audio(
            text=text,
            file_name_no_ext=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}",
        )

    def clear(self) -> None:
        """Clear all pending tasks and reset state"""
        self.task_list.clear()
        self._batch_text = ""
        self._batch_display_text = None
        self._batch_actions = None
        self._batch_subtitle = ""
        self._last_engine = None
        self._last_send = None
        if self._sender_task:
            self._sender_task.cancel()
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        # Create a new queue to clear any pending items
        self._payload_queue = asyncio.Queue()
