"""shell.py — 角色外壳播报器（G7 · 任务开始/结束/出错/澄清的人设转述）。

任务内核零人设（效率优先）；角色只在关键节点出场，用"自己语气"把任务状态
转述给用户。本模块实现计划书 §5.7 的「外壳汇报」链路：

- 输入：TaskEvent（run_start / run_end / run_error / clarify_requested）
- 处理：LLM 以角色 persona + 事件上下文生成一句人设转述 → TTS 合成 → WS audio 广播
- 前端零改动：复用现有 WS `audio` 消息链路（气泡 + 语音 + Live2D 表情全自动）
- 全程 fail-soft：LLM 失败 → 模板文案兜底；TTS 失败 → 静默 payload（气泡仍显示）

单向依赖：shell.py ← task_route.py；不 import conversations/memory/mcp
（复用 conf_bridge / llm_adapter / graph，保持 task_platform 独立性）。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from loguru import logger

from . import conf_bridge, graph
from ..utils.tts_preprocessor import tts_filter as _tts_filter_fn
from ..translate.translate_factory import TranslateFactory

#: TTS 引擎类型（conf.yaml tts_config.tts_model；注入式，避免重复建引擎）。
_tts_engine: Optional[Any] = None
_tts_engine_guard = asyncio.Lock()

#: 语音翻译引擎（conf.yaml translator_config；与主对话链路同源，懒加载缓存）。
_translator: Optional[Any] = None
_translator_guard = asyncio.Lock()


def _chat_ctx(task: Any) -> tuple[str, str] | None:
    """Resolve chat-history identity without importing task_route circularly."""
    try:
        import yaml
        from pathlib import Path

        conf_path = Path(conf_bridge.CONF_PATH)
        if not conf_path.exists():
            return None
        data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        character = data.get("character_config") or {}
        conf_uid = str(character.get("conf_uid") or character.get("conf_name") or "").strip()
        if not conf_uid:
            return None
        # Use the selected character card name consistently. Legacy
        # character_name may describe a different persona and must not leak
        # into task/chat output.
        return conf_uid, str(character.get("conf_name") or "AI")
    except Exception:
        return None

#: 轻量「有实质内容」检测（CJK/假名/谚文/拉丁/数字任一即有意义；
#: 纯 emoji/纯符号 → False，跳过翻译，避免把提示语当结果）。
_RE_MEANINGFUL = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff぀-ゟ゠-ヿ가-힣A-Za-z0-9]"
)
#: 文本已含日文假名 → 视为已是日语语音语言，不再翻译。
_RE_KANA = re.compile(r"[぀-ゟ゠-ヿ]")


def set_tts_engine(engine: Optional[Any]) -> None:
    """注入现有 TTS 引擎（由 server.py 在启动时传入 default_context_cache.tts_engine）。

    复用主对话链路的同一个引擎实例，避免重复初始化（VOICEVOX 等引擎较重）。
    传 None 表示不播报语音（仅文字气泡）。
    """
    global _tts_engine
    _tts_engine = engine


async def _get_tts_engine() -> Optional[Any]:
    """惰性取 TTS 引擎：未注入时按 conf.yaml tts_config 建（fail-soft）。"""
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    async with _tts_engine_guard:
        if _tts_engine is not None:
            return _tts_engine
        try:
            import yaml
            from pathlib import Path

            from ..tts.tts_factory import TTSFactory

            conf_path = Path(conf_bridge.CONF_PATH)
            if not conf_path.exists():
                return None
            data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
            cc = data.get("character_config") or {}
            tts_cfg = cc.get("tts_config") or {}
            model_name = str(tts_cfg.get("tts_model") or "voicevox_tts")
            engine_kwargs = dict(tts_cfg.get(model_name) or {})
            engine = TTSFactory.get_tts_engine(model_name, **engine_kwargs)
            if engine is not None:
                _tts_engine = engine
            return _tts_engine
        except Exception as e:
            logger.warning(f"[shell] TTS 引擎初始化失败（跳过语音）：{e}")
            return None


def _has_meaningful_text(text: str) -> bool:
    return bool(_RE_MEANINGFUL.search(text or ""))


def _tts_filter_text(text: str) -> str:
    """按 conf.yaml tts_preprocessor_config 做 TTS 前置净化（与主对话链路一致）。

    NFKC 归一化 → 剥离 markdown 残留 → 括号/星号/尖括号内容 → 特殊字符移除
    → 中文标点恢复。任一环节异常都不影响后续（fail-soft，原样返回）。
    """
    try:
        import yaml
        from pathlib import Path

        conf_path = Path(conf_bridge.CONF_PATH)
        if not conf_path.exists():
            return text
        data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        cc = data.get("character_config") or {}
        tpp = cc.get("tts_preprocessor_config") or {}
        return _tts_filter_fn(
            text=text,
            remove_special_char=bool(tpp.get("remove_special_char", True)),
            ignore_brackets=bool(tpp.get("ignore_brackets", True)),
            ignore_parentheses=bool(tpp.get("ignore_parentheses", True)),
            ignore_asterisks=bool(tpp.get("ignore_asterisks", True)),
            ignore_angle_brackets=bool(tpp.get("ignore_angle_brackets", True)),
        )
    except Exception as e:
        logger.warning(f"[shell] TTS 文本净化失败（原样合成）：{e}")
        return text


async def _get_translator() -> Optional[Any]:
    """按 conf.yaml translator_config 建语音翻译引擎（与主对话链路同源）。

    仅当 translate_audio=True 时启用；fail-soft：任何异常 → None（跳过翻译）。
    """
    global _translator
    if _translator is not None:
        return _translator
    async with _translator_guard:
        if _translator is not None:
            return _translator
        try:
            import yaml
            from pathlib import Path

            conf_path = Path(conf_bridge.CONF_PATH)
            if not conf_path.exists():
                return None
            data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
            cc = data.get("character_config") or {}
            tr_cfg = (cc.get("tts_preprocessor_config") or {}).get(
                "translator_config"
            ) or {}
            if not tr_cfg.get("translate_audio", True):
                return None
            provider = str(tr_cfg.get("translate_provider") or "llm")
            block = tr_cfg.get(provider) or {}
            if not block:
                logger.warning("[shell] translator 配置块为空，跳过语音翻译")
                return None
            translator = TranslateFactory.get_translator(provider, block)
            if translator is not None:
                _translator = translator
            return _translator
        except Exception as e:
            logger.warning(f"[shell] 翻译引擎初始化失败（跳过语音翻译）：{e}")
            return None


async def _prepare_speech_text(text: str) -> str:
    """把外壳转述文本处理成可合成语音的文本：净化 + 语音翻译。

    与主对话链路一致：display 保持原文 R（气泡），语音用「净化→翻译」后的文本。
    纯 emoji/纯符号（无实质台词）不翻译；已是日文（含假名）不重复翻译。
    """
    cleaned = _tts_filter_text(text)
    if not _has_meaningful_text(cleaned):
        return cleaned
    if _RE_KANA.search(cleaned):
        return cleaned  # 已是日语语音语言，直接合成
    translator = await _get_translator()
    if translator is None:
        return cleaned
    try:
        translated = translator.translate(cleaned)
        if translated and translated.strip():
            return translated.strip()
    except Exception as e:
        logger.warning(f"[shell] 语音翻译失败（用净化后原文合成）：{e}")
    return cleaned


async def _llm_rephrase(
    event_type: str,
    payload: dict[str, Any],
    task: Any,
    *,
    cfg: Optional[conf_bridge.TaskPlatformConfig] = None,
) -> str:
    """用角色 persona + 事件上下文生成一句人设转述。

    复用 conf_bridge 读到的角色 persona_prompt（character_config.persona_prompt），
    调用任务主模型（build_model）生成一句话。失败返回 "" → 调用方用模板兜底。
    """
    try:
        import yaml
        from pathlib import Path

        from langchain_core.messages import SystemMessage, HumanMessage

        cfg = cfg or conf_bridge.task_config()
        conf_path = Path(conf_bridge.CONF_PATH)
        if not conf_path.exists():
            return ""
        data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        cc = data.get("character_config") or {}
        persona = str(cc.get("persona_prompt") or "").strip()
        char_name = str(cc.get("conf_name") or "AI")
        if not persona:
            return ""

        # 事件 → 转述指令（人设语气，一句话）
        if event_type == "run_start":
            goal = str(payload.get("goal") or "").strip() or "一项任务"
            instruction = (
                f"主人给你交办了一个任务：「{goal}」。"
                "请用你的语气简短确认接受任务，一句话，自然口语化，不解释步骤。"
            )
        elif event_type == "run_end":
            status = str(payload.get("status") or "completed")
            if status == "completed":
                summary = str(payload.get("summary") or "").strip()
                instruction = (
                    "任务执行完成了。"
                    + (f"结果摘要：{summary}。" if summary else "")
                    + "请用你的语气向主人汇报一句（可带小得意/开心），一句话，口语化。"
                )
            else:
                instruction = (
                    f"任务被中断了（{status}）。"
                    "请用你的语气告诉主人任务没跑完，一句话，安慰的语气。"
                )
        elif event_type == "run_error":
            err = str(payload.get("error") or "未知错误")
            instruction = (
                f"任务执行出错了：{err[:120]}。"
                "请用你的语气向主人道歉并说明遇到了问题，一句话，不沮丧但要诚实。"
            )
        elif event_type == "clarify_requested":
            q = str(payload.get("question") or "需要澄清一些问题")
            instruction = (
                f"任务执行中需要向主人澄清：「{q}」。"
                "请用你的语气把这个问题转述给主人，一句话，口语化。"
            )
        else:
            return ""

        model = graph.build_model(cfg)
        resp = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        f"你是「{char_name}」，以下是你的角色设定：\n{persona}\n\n"
                        "现在你在向主人汇报一个【后台任务】的状态。"
                        "请只用一句话回复，严格保持你的人设语气，不要加引号，不要加前后缀。"
                    )
                ),
                HumanMessage(content=instruction),
            ]
        )
        text = str(getattr(resp, "content", "") or "").strip()
        return text[:200]
    except Exception as e:
        logger.warning(f"[shell] LLM 转述失败（回退模板）：{e}")
        return ""


#: 事件 → 兜底模板（LLM 失败/未配置 persona 时用；不含 emoji，前端可再点缀）。
_FALLBACK: dict[str, str] = {
    "run_start": "好的，我来处理这个任务～",
    "run_end": "任务完成了，你看看结果吧。",
    "run_error": "这个任务执行出错了，你来看看哪里有问题？",
    "clarify_requested": "做这个任务前，我需要先问你几个问题。",
}


async def speak(
    event_type: str,
    payload: dict[str, Any],
    task: Any,
    *,
    cfg: Optional[conf_bridge.TaskPlatformConfig] = None,
    tts_engine: Optional[Any] = None,
    send_func=None,
) -> None:
    """外壳播报：LLM 转述 → TTS 合成 → WS audio 广播。全程 fail-soft。

    Args:
        event_type: run_start | run_end | run_error | clarify_requested
        payload: 事件 payload（goal/status/error/question 等）
        task: Task 模型（title/workspace 用于上下文，可选）
        cfg: task_platform 配置（默认 task_config()）
        tts_engine: 注入的 TTS 引擎（默认 _tts_engine 全局）
        send_func: 发送回调 `async def send(ws_send, payload)`——由 task_route 传入
            实际发送到前端连接的函数（保持 shell 不直接依赖 websocket_handler）。
    """
    if tts_engine is None:
        tts_engine = await _get_tts_engine()
    text = await _llm_rephrase(event_type, payload, task, cfg=cfg)
    if not text.strip():
        text = _FALLBACK.get(event_type, "")

    # Shell speech is transient UI narration.  It is already delivered through
    # the live WS audio/display path; persisting it duplicates task cards and
    # makes internal task plumbing leak into refreshed chat history.
    # 构造 audio payload（复用现有链路：display_text + actions.expressions 表情）
    if send_func is None:
        return

    from ..agent.output_types import Actions, DisplayText
    from ..utils.stream_audio import prepare_audio_payload

    expression = None
    if event_type == "run_end" and str(payload.get("status") or "completed") == "completed":
        expression = "joy"
    elif event_type == "run_error":
        expression = "sad"
    elif event_type == "clarify_requested":
        expression = "confusion"

    actions = Actions(expressions=[expression]) if expression else None
    display = DisplayText(text=text, name="AI")

    audio_path = None
    if tts_engine is not None:
        try:
            # 与主对话链路一致：语音只读「净化+翻译」后的文本（display 仍显示原文 R，
            # 否则中文文本直接进 VOICEVOX 日语引擎会读出大量杂音/语义不明）。
            speech_text = await _prepare_speech_text(text)
            audio_path = await tts_engine.async_generate_audio(
                speech_text, file_name_no_ext="shell_reporter"
            )
        except Exception as e:
            logger.warning(f"[shell] TTS 合成失败（静默气泡）：{e}")
            audio_path = None

    payload_out = prepare_audio_payload(
        audio_path=audio_path,
        display_text=display,
        actions=actions,
        subtitle_text=None,
    )
    if audio_path and tts_engine is not None:
        try:
            tts_engine.remove_file(audio_path)
        except Exception:
            pass

    if send_func is None:
        logger.warning("[shell] 未注入 send_func，跳过广播")
        return
    try:
        await send_func(payload_out)
    except Exception as e:
        logger.warning(f"[shell] 广播失败：{e}")
