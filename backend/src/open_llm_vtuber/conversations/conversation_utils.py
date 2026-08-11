import asyncio
import re
from typing import Optional, Union, Any, List, Dict, Callable, Awaitable
import numpy as np
import json
from loguru import logger

from ..message_handler import message_handler
from ..contracts import ErrorCode, send_error, send_message
from .types import WebSocketSend, BroadcastContext
from .tts_manager import TTSTaskManager
from ..agent.output_types import SentenceOutput, AudioOutput
from ..agent.input_types import BatchInput, TextData, ImageData, TextSource, ImageSource
from ..asr.asr_interface import ASRInterface
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload
from ..utils.tts_preprocessor import strip_action_notes
from ..translate.translate_interface import TranslationError


# Coarse language buckets used by both the voice-language derivation (V) and the
# light per-sentence reply detector (R). Anything outside this set is treated as
# "unknown" (no gating -> speak verbatim, the conservative default).
_KNOWN_LANGS = {"ja", "zh", "en", "ko"}

# Light per-sentence reply-language detector. Regex only (zero network / no model),
# safe to run in the hot async loop. Order matters: kana (Hiragana/Katakana) -> ja
# FIRST, then Hangul -> ko, then Han-without-kana -> zh, then Latin -> en.
_RE_KANA = re.compile(r"[぀-ゟ゠-ヿ]")  # Hiragana + Katakana
_RE_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")  # Hangul
_RE_HAN = re.compile(r"[一-鿿㐀-䶿]")  # CJK Han (treat as Chinese)
_RE_LATIN = re.compile(r"[A-Za-z]")


def _detect_lang(text: str) -> Optional[str]:
    """Lightly detect the language bucket of a reply sentence (R).

    Returns one of {'ja','zh','en','ko'} or None when nothing recognizable is found
    (e.g. pure punctuation/numbers) -> caller treats None as "do not gate".

    KNOWN LIMITATION: a Japanese sentence written in pure kanji with no kana detects
    as 'zh' (Han -> Chinese). This is unfixable with a light regex detector; accept it.
    """
    if not text:
        return None
    if _RE_KANA.search(text):
        return "ja"
    if _RE_HANGUL.search(text):
        return "ko"
    if _RE_HAN.search(text):
        return "zh"
    if _RE_LATIN.search(text):
        return "en"
    return None


# 排除纯标点/空白后仍含「实质文字」才视为可翻译内容。纯 emoji/表情（如 😊）、
# 纯符号（如 —— 或（笑）被 strip 后的空串）送去翻译引擎会得到垃圾输出：
# 实测 LLM 翻译 '😊' -> 'Smile.'（语音）/「請傳送要翻譯的中文台詞...」（字幕）。
_RE_MEANINGFUL = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afA-Za-z0-9]")


def _has_meaningful_text(text: str) -> bool:
    """文本含 CJK/日韩假名/拉丁字母/数字中任一实质字符即视为有意义。

    - 空串 / 纯空白 / 纯标点 / 纯 emoji → False（跳过翻译，保留原文直读）。
    - 只有 emoji 或符号组成的句子不是「台词」，翻译引擎会把提示语当结果返回。
    """
    return bool(_RE_MEANINGFUL.search(text or ""))


def _normalize_lang(raw: Optional[str]) -> Optional[str]:
    """Normalize a raw engine language string to a known bucket {'ja','zh','en','ko'}.

    Handles GPT-SoVITS style tags ('all_ja' / 'all_zh' / 'all_ko' / 'yue' / 'auto' /
    'auto_yue') as well as plain codes ('ja' / 'zh' / 'en' / 'ko'). 'yue' (Cantonese)
    maps to 'zh'; 'auto'/'auto_yue' map to None (engine auto-detects -> don't gate).
    Returns None when nothing recognizable is found.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("auto", "auto_yue"):
        return None
    if "ja" in s:
        return "ja"
    if "zh" in s or "yue" in s:
        return "zh"
    if "ko" in s:
        return "ko"
    if "en" in s:
        return "en"
    return None


def derive_voice_lang(character_config: Any) -> Optional[str]:
    """Derive the active character's voice language V (one of {'ja','zh','en','ko'}).

    V now comes FROM THE VOICE PACK (the active TTS config), NOT from a per-character
    field — the voice pack already declares the language of the voice it speaks, so that
    is the authoritative source. When a character swaps its voice pack it automatically
    gets the new pack's language (no separate field to keep in sync).

    Resolution: read the active engine from ``character_config.tts_config.tts_model``,
    then read that engine's own language declaration:
    - ``edge_tts``    -> the locale language subtag (part before the FIRST hyphen) of
                         ``tts_config.edge_tts.voice`` (e.g. 'ja-JP-NanamiNeural' -> 'ja',
                         'zh-TW-HsiaoChenNeural' -> 'zh').
    - ``gpt_sovits_tts`` -> ``tts_config.gpt_sovits_tts.text_lang`` (e.g. 'all_ja' -> 'ja',
                         'all_zh' -> 'zh', 'all_ko' -> 'ko'); 'auto'/'auto_yue' -> None.
    - ``x_tts`` -> its ``.language`` field, normalized.

    Everything is clamped to {'ja','zh','en','ko'}. Returns None when V cannot be derived
    (no tts_config / engine has no readable language / unrecognized value). A None V means
    "skip gating" -> speak the reply verbatim, since cross-language voice is the opt-in
    case (an unknown voice should not silently trigger translation).
    """
    try:
        tts_config = getattr(character_config, "tts_config", None)
        if tts_config is None:
            return None

        engine = getattr(tts_config, "tts_model", None)

        # edge_tts: parse the locale subtag off the voice name (KEEP existing logic).
        if engine == "edge_tts":
            edge_tts = getattr(tts_config, "edge_tts", None)
            if edge_tts is None:
                return None
            voice = getattr(edge_tts, "voice", None)
            if not voice:
                return None
            subtag = str(voice).split("-", 1)[0].strip().lower()
            return subtag if subtag in _KNOWN_LANGS else None

        # GPT-SoVITS: read+normalize the declared text language ('all_ja' -> 'ja', etc.).
        if engine == "gpt_sovits_tts":
            gpt_sovits = getattr(tts_config, "gpt_sovits_tts", None)
            if gpt_sovits is None:
                return None
            return _normalize_lang(getattr(gpt_sovits, "text_lang", None))

        # Other engines that expose a plain language field.
        if engine == "x_tts":
            x_tts = getattr(tts_config, "x_tts", None)
            if x_tts is None:
                return None
            return _normalize_lang(getattr(x_tts, "language", None))

        # VOICEVOX 是纯日语合成引擎（OpenJTalk）：无 voice_lang 推导分支时 V=None，
        # 翻译门控（R≠V）会短路 → 中文原文直接喂日语引擎 → 音素错乱（刺耳电流音）
        # + 读不顺/跳过（说不完整）。必须声明 'ja'，让中文回复走翻译后再合成。
        if engine == "voicevox_tts":
            return "ja"

        # Unknown / unsupported engine -> can't read a voice language -> skip gating.
        return None
    except Exception as e:
        logger.debug(f"Could not derive voice language: {type(e).__name__}: {e}")
        return None


# Convert class methods to standalone functions
def create_batch_input(
    input_text: str,
    images: Optional[List[Dict[str, Any]]],
    from_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> BatchInput:
    """Create batch input for agent processing"""
    return BatchInput(
        texts=[
            TextData(source=TextSource.INPUT, content=input_text, from_name=from_name)
        ],
        images=[
            ImageData(
                source=ImageSource(img["source"]),
                data=img["data"],
                mime_type=img["mime_type"],
            )
            for img in (images or [])
        ]
        if images
        else None,
        metadata=metadata,
    )


async def process_agent_output(
    output: Union[AudioOutput, SentenceOutput],
    character_config: Any,
    live2d_model: Live2dModel,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    tts_manager: TTSTaskManager,
    translate_engine: Optional[Any] = None,
    subtitle_translate_engine: Optional[Any] = None,
) -> str:
    """Process agent output with character information and optional translation"""
    # Display name shown on the AI side of the chat: prefer the explicit
    # character_name, else fall back to conf_name. Never let it be empty (which the
    # frontend would render as a hardcoded 'AI'/'A').
    output.display_text.name = (
        character_config.character_name or character_config.conf_name
    )
    output.display_text.avatar = character_config.avatar

    # Derive the voice language V once per output (not per sentence): the audio
    # translate gate translates a sentence only when V differs from the detected
    # reply language R. None V -> gating skipped (speak verbatim).
    voice_lang = derive_voice_lang(character_config)

    full_response = ""
    try:
        if isinstance(output, SentenceOutput):
            full_response = await handle_sentence_output(
                output,
                live2d_model,
                tts_engine,
                websocket_send,
                tts_manager,
                translate_engine,
                subtitle_translate_engine,
                voice_lang,
            )
        elif isinstance(output, AudioOutput):
            full_response = await handle_audio_output(output, websocket_send)
        else:
            logger.warning(f"Unknown output type: {type(output)}")
    except Exception as e:
        logger.error(f"Error processing agent output: {e}")
        await send_error(
            websocket_send,
            ErrorCode.INTERNAL_ERROR,
            "AI 回复处理失败，请查看后端日志。",
        )

    return full_response


#: LLM 情绪预取节流：3s 内最多一次（句子多时避免刷 LLM）
_emotion_prefetch_lock_ts = 0.0
_EMOTION_PREFETCH_MIN_INTERVAL = 3.0


def _prefetch_emotion_async(text: str) -> None:
    """句子级 LLM 情绪预取（fire-and-forget，零阻塞）。

    规则快路径由 prepare_audio_payload 同步完成；这里只做慢路径增强：
    规则低置信/未命中时调 LLM 分类，结果写入 tracker 的 LLM 缓存
    （text_fingerprint 指纹），音频 payload 生成时命中即用。
    """
    global _emotion_prefetch_lock_ts
    if not text or not text.strip():
        return
    import time as _time

    now = _time.monotonic()
    if now - _emotion_prefetch_lock_ts < _EMOTION_PREFETCH_MIN_INTERVAL:
        return
    _emotion_prefetch_lock_ts = now

    async def _run() -> None:
        try:
            from ..emotion.emotion_classifier import classify, text_fingerprint
            from ..emotion import get_emotion_tracker

            result = await classify(text)
            if result.source == "llm" and result.emotion != "neutral":
                get_emotion_tracker().set_llm_cache(
                    text_fingerprint(text),
                    result.emotion,
                    result.intensity,
                    result.duration_ms,
                )
        except Exception:
            # 预取失败静默：规则结果始终兜底，不阻塞主链路
            pass

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


# 整段翻译 hook（方案 1，2026-08-10）：由 TTSTaskManager.flush() 在整段合成前调用。
# 句子先在 TTS 管理器的聚合缓冲（100 字）里攒着，满阈值/回复结束时整段调一次翻译——
# 之前逐句 LLM 翻译每句一次 API 往返（8~9s/句），4 句回复 ≈ 60s+；整段一次降到
# 1~2 次调用。hook 内部做 V != R 判断，同语言直接跳过；跨语言翻译失败时返回 None，
# 由 flush() 跳过该段语音（避免把原文喂给外语 TTS 出杂音），文字仍由 full-text 上屏。
def make_translate_hook(
    translate_engine: Optional[Any], voice_lang: Optional[str]
) -> Callable[[str], Awaitable[Optional[str]]]:
    async def _hook(text: str) -> Optional[str]:
        if translate_engine is None or not _has_meaningful_text(text):
            return text
        reply_lang = _detect_lang(text)
        if voice_lang and reply_lang and reply_lang != voice_lang:
            try:
                translated = await translate_engine.translate_async(text)
            except TranslationError as e:
                # 跨语言翻译失败（限流/连接拒/解析错）→ 跳过该段语音，不出杂音；
                # 文字已由 full-text 上屏，对话不中断。
                logger.warning(
                    f"🚫 Audio translation failed (R={reply_lang} != V={voice_lang}), "
                    f"skipping TTS for this segment: {e}"
                )
                return None
            logger.info(
                f"🏃 Audio translated (R={reply_lang} != V={voice_lang}, "
                f"{len(text)} chars): '''{translated[:40]}'''..."
            )
            return translated
        logger.debug(
            f"🚫 Audio translation skipped (R={reply_lang}, V={voice_lang}); "
            "speaking reply verbatim."
        )
        return text

    return _hook


async def handle_sentence_output(
    output: SentenceOutput,
    live2d_model: Live2dModel,
    tts_engine: TTSInterface,
    websocket_send: WebSocketSend,
    tts_manager: TTSTaskManager,
    translate_engine: Optional[Any] = None,
    subtitle_translate_engine: Optional[Any] = None,
    voice_lang: Optional[str] = None,
) -> str:
    """Handle sentence output type with optional translation support.

    Two INDEPENDENT translations may happen per sentence:
    - AUDIO: ``translate_engine`` rewrites ``tts_text`` for the spoken voice. This is
      now AUTOMATIC (no user toggle): the engine runs ONLY when the character's voice
      language ``voice_lang`` (V) differs from the detected reply language R of the
      sentence. Same-language (e.g. Japanese reply + Japanese voice) -> skip + speak R
      verbatim. When V cannot be derived (None) the gate is skipped (speak verbatim).
      The engine's TARGET is V itself (the character's voice language): it is built in
      service_context.init_translate with target = V (mapped per provider), so when the
      gate fires the reply is translated INTO V. So a character with a Japanese voice
      always speaks Japanese; one with a Chinese voice always speaks Chinese — regardless
      of the reply language. (Falls back to the conf's global target_lang only when V
      can't be derived.) V here only drives the skip/translate DECISION.
    - SUBTITLE (display-only): ``subtitle_translate_engine`` rewrites a SEPARATE
      ``subtitle_text`` for the on-screen subtitle. UNCHANGED: gated only by the user's
      explicit subtitle language pick (built in init_translate). The canonical reply
      text (``display_text.text`` == R) is NEVER mutated, so ``full_response`` — the sole
      source for memory + history — stays on the original reply.

    PERFORMANCE (2026-08-10 重构，解决「聊天回复慢」根因):
    - 文本先上屏：``full-text`` 在翻译**之前**立即发送原文 R，用户无需等翻译完成
      才能看到文字（之前逐句翻译 8~9s/句，感知延迟 = 翻译延迟）。
    - 整段聚合翻译：语音翻译交给 tts_manager 的聚合缓冲（flush 时整段调一次
      ``translate_async``），不再逐句调 API。见 ``make_translate_hook``。
    - 异步化：``translate_async`` 用 httpx.AsyncClient，不再同步阻塞事件循环。
    """
    # 注入整段翻译 hook（每句调用幂等；group 对话同路径自动受益）
    tts_manager.set_translator(make_translate_hook(translate_engine, voice_lang))

    full_response = ""
    async for display_text, tts_text, actions in output:
        logger.debug(f"🏃 Processing output: '''{tts_text}'''...")

        # Canonical reply text (R). Accumulated for memory/history — DO NOT mutate.
        full_response += display_text.text

        # Phase 1（面部表情）：LLM 情绪慢路径预取（fire-and-forget，零阻塞）。
        # 规则快路径在 prepare_audio_payload 同步跑；这里对每句做 LLM 增强分类
        # 并写入 tracker 缓存（带文本指纹），音频 payload 生成时命中即用——
        # 让「句子级情绪 + 强度 + 时长」能驱动下一拍的表情（微表情基础）。
        _prefetch_emotion_async(display_text.text)

        # 文本流式先行（v6 增强）：LLM 句子一到【立即】上屏原文（full-text），
        # 完全不等翻译。翻译只影响语音文本（tts_text），display_text.text 永远是
        # 原文 R（memory/history 唯一来源）——full-text 永远推原文。
        await send_message(websocket_send, {"type": "full-text", "text": display_text.text})

        # Display-only subtitle translation: compute a SEPARATE field; never touch
        # display_text.text. If no subtitle engine, the subtitle stays = R.
        # 输入先剥离动作/表情描写（如（微笑）、[叹气]），只翻译台词正文——
        # 否则翻译器会把动作翻成 (smiling) 等冗长内容，导致字幕比中文气泡长得多。
        # 纯 emoji/表情（如 😊）没有实质文字，送去翻译会得到 LLM 的「提示语」垃圾
        # （实测 '😊' -> '請傳送要翻譯的中文台詞...'）——必须跳过。
        # 字幕逐句异步翻译（translate_subtitle 默认关闭；开启时避免同步阻塞）。
        subtitle_text = display_text.text
        if subtitle_translate_engine:
            subtitle_source = strip_action_notes(display_text.text)
            if _has_meaningful_text(subtitle_source):
                try:
                    # Subtitle translation is optional display polish. Bound it
                    # tightly so a slow/remote translator cannot hold the LLM/TTS
                    # stream hostage for several seconds per sentence.
                    subtitle_text = await asyncio.wait_for(
                        subtitle_translate_engine.translate_async(subtitle_source),
                        timeout=1.0,
                    )
                except (TranslationError, asyncio.TimeoutError) as e:
                    # 字幕翻译失败：回退原文（纯显示，无副作用），不阻断对话。
                    logger.warning(
                        f"🚫 Subtitle translation failed, falling back to original: {e}"
                    )
            logger.info(f"🏃 Subtitle after translation: '''{subtitle_text}'''...")

        # 交给 TTS 管理器：tts_text（中文原文）先入聚合缓冲，满阈值/回复结束时
        # flush() 整段翻译（hook）+ 整段合成——语音按「段落」而非「句子」播放。
        await tts_manager.speak(
            tts_text=tts_text,
            display_text=display_text,
            actions=actions,
            live2d_model=live2d_model,
            tts_engine=tts_engine,
            websocket_send=websocket_send,
            subtitle_text=subtitle_text,
        )
    return full_response


async def handle_audio_output(
    output: AudioOutput,
    websocket_send: WebSocketSend,
) -> str:
    """Process and send AudioOutput directly to the client"""
    full_response = ""
    async for audio_path, display_text, transcript, actions in output:
        full_response += transcript
        audio_payload = prepare_audio_payload(
            audio_path=audio_path,
            display_text=display_text,
            actions=actions.to_dict() if actions else None,
        )
        await send_message(websocket_send, audio_payload)
    return full_response


async def send_conversation_start_signals(websocket_send: WebSocketSend) -> None:
    """Send initial conversation signals"""
    await send_message(
        websocket_send, {"type": "control", "text": "conversation-chain-start"}
    )
    await send_message(websocket_send, {"type": "full-text", "text": "Thinking..."})


async def process_user_input(
    user_input: Union[str, np.ndarray],
    asr_engine: ASRInterface,
    websocket_send: WebSocketSend,
) -> str:
    """Process user input, converting audio to text if needed"""
    if isinstance(user_input, np.ndarray):
        if asr_engine is None:
            # Voice input is disabled (no speech engine could be loaded). Don't
            # dereference None — tell the user and let them type instead. Text chat
            # and voice output keep working.
            logger.warning("Received audio input but no ASR engine is loaded; ignoring.")
            await send_error(
                websocket_send,
                ErrorCode.ASR_LOAD_FAILED,
                "语音输入不可用（未加载语音识别引擎），请改用文字输入。",
            )
            return ""
        logger.info("Transcribing audio input...")
        input_text = await asr_engine.async_transcribe_np(user_input)
        await send_message(
            websocket_send, {"type": "user-input-transcription", "text": input_text}
        )
        return input_text
    return user_input


async def finalize_conversation_turn(
    tts_manager: TTSTaskManager,
    websocket_send: WebSocketSend,
    client_uid: str,
    broadcast_ctx: Optional[BroadcastContext] = None,
) -> None:
    """Finalize a conversation turn"""
    if tts_manager.task_list:
        await asyncio.gather(*tts_manager.task_list)
        await send_message(websocket_send, {"type": "backend-synth-complete"})

        response = await message_handler.wait_for_response(
            client_uid, "frontend-playback-complete"
        )

        if not response:
            logger.warning(f"No playback completion response from {client_uid}")
            return

    await send_message(websocket_send, {"type": "force-new-message"})

    if broadcast_ctx and broadcast_ctx.broadcast_func:
        await broadcast_ctx.broadcast_func(
            broadcast_ctx.group_members,
            {"type": "force-new-message"},
            broadcast_ctx.current_client_uid,
        )

    await send_conversation_end_signal(websocket_send, broadcast_ctx)


async def send_conversation_end_signal(
    websocket_send: WebSocketSend,
    broadcast_ctx: Optional[BroadcastContext],
    session_emoji: str = "😊",
) -> None:
    """Send conversation chain end signal"""
    chain_end_msg = {
        "type": "control",
        "text": "conversation-chain-end",
    }

    await websocket_send(json.dumps(chain_end_msg))

    if broadcast_ctx and broadcast_ctx.broadcast_func and broadcast_ctx.group_members:
        await broadcast_ctx.broadcast_func(
            broadcast_ctx.group_members,
            chain_end_msg,
        )

    logger.info(f"😎👍✅ Conversation Chain {session_emoji} completed!")


def cleanup_conversation(tts_manager: TTSTaskManager, session_emoji: str) -> None:
    """Clean up conversation resources"""
    tts_manager.clear()
    logger.debug(f"🧹 Clearing up conversation {session_emoji}.")


EMOJI_LIST = [
    "🐶",
    "🐱",
    "🐭",
    "🐹",
    "🐰",
    "🦊",
    "🐻",
    "🐼",
    "🐨",
    "🐯",
    "🦁",
    "🐮",
    "🐷",
    "🐸",
    "🐵",
    "🐔",
    "🐧",
    "🐦",
    "🐤",
    "🐣",
    "🐥",
    "🦆",
    "🦅",
    "🦉",
    "🦇",
    "🐺",
    "🐗",
    "🐴",
    "🦄",
    "🐝",
    "🌵",
    "🎄",
    "🌲",
    "🌳",
    "🌴",
    "🌱",
    "🌿",
    "☘️",
    "🍀",
    "🍂",
    "🍁",
    "🍄",
    "🌾",
    "💐",
    "🌹",
    "🌸",
    "🌛",
    "🌍",
    "⭐️",
    "🔥",
    "🌈",
    "🌩",
    "⛄️",
    "🎃",
    "🎄",
    "🎉",
    "🎏",
    "🎗",
    "🀄️",
    "🎭",
    "🎨",
    "🧵",
    "🪡",
    "🧶",
    "🥽",
    "🥼",
    "🦺",
    "👔",
    "👕",
    "👜",
    "👑",
]
