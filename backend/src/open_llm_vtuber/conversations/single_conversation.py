from __future__ import annotations

from typing import TYPE_CHECKING, Union, List, Dict, Any, Optional
import asyncio
import time
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message
from ..contracts import ErrorCode, send_error, send_message
from ..agent.input_types import ImageSource

if TYPE_CHECKING:
    from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput

# 保存背景核心記憶整理 task 的 reference，避免被 GC
_BG_MEMORY_TASKS: set = set()

# Per-session turn counter for consolidation throttling. Keyed by
# (conf_uid, client_uid) so each character + client tracks its own cadence.
# In-memory only (resets on restart -> interval cycle restarts, acceptable).
# One int per active client_uid -> negligible memory; cleared in cleanup below.
_TURN_COUNTS: "dict[tuple[str, str], int]" = {}


def _embedding_llm(character_config) -> tuple[str, str, str]:
    """向量记忆的 embedding 配置：(base_url, model, api_key)。

    优先取 character_config.vector_embedding_*（独立 embedding 端点，如
    SiliconFlow bge-m3——DeepSeek 等纯对话 provider 没有 /embeddings）；
    未配置时回退到 openai_compatible_llm（对话模型，可能不支持 embedding）。
    """
    llm = character_config.agent_config.llm_configs.openai_compatible_llm
    base = getattr(character_config, "vector_embedding_base_url", "") or llm.base_url
    model = getattr(character_config, "vector_embedding_model", "") or llm.model
    key = getattr(character_config, "vector_embedding_api_key", "") or llm.llm_api_key
    return base, model, key


# 用户「明确要求看屏幕」的关键词（Phase 3：命中才附带原始图像）。
_SCREEN_LOOK_KEYWORDS = (
    "屏幕",
    "看下屏幕",
    "看看屏幕",
    "看屏幕",
    "我的屏幕",
    "我屏幕",
    "屏幕上",
    "正在看",
    "这个页面",
    "这个窗口",
    "这里",
    "这报错",
    "这个报错",
    "报错",
    "看一下",
    "帮我看看",
    "你看",
)


def _attach_screen_context(
    client_uid: str,
    input_text: str,
    images,
    is_proactive: bool,
) -> tuple[str, object]:
    """Phase 3：把屏幕上下文作为临时 turn context 注入（不污染长期记忆）。

    - 未开启屏幕感知 / 摘要过期 / 窗口切换 → 原样返回；
    - 摘要有效 → 以「[屏幕上下文]」一行附加到 LLM 输入（不进聊天历史、
      不进核心记忆，仅本回合临时可见）；
    - 用户明确提到屏幕/报错/这里 且最近有效帧带图像 → 附带图像进多模态对话；
    - 完全 fail-soft：任何异常都按无屏幕上下文处理。
    """
    try:
        from ..screen_awareness.service import get_store

        store = get_store()
        if not store.is_enabled():
            return input_text, images
        if is_proactive:
            # 主动陪聊场景由 policy hint 注入（Phase 4），这里跳过摘要。
            return input_text, images

        snap = store.latest_snapshot(client_uid)
        if snap is None:
            return input_text, images

        summary = (snap.summary or "").strip()
        scene = snap.scene or "unknown"
        parts = []
        if summary:
            parts.append(summary)
        if snap.possible_topic:
            parts.append(f"话题：{snap.possible_topic}")
        if not parts:
            return input_text, images
        ctx_line = (
            f"\n[屏幕上下文] 用户当前在{scene}场景，画面摘要：{ '；'.join(parts) }。"
            "（这是临时屏幕信息，仅本回合参考，不要写进记忆）"
        )

        # 用户明确要求看屏幕 → 默认只注入文本摘要（上方已附加）。
        # 可选附带最近有效帧图像：仅当 conf attach_screen_image_to_llm=True
        # （对话 LLM 支持视觉时，如 gpt-4o/qwen-vl）。修复（2026-08-11）：
        # 无视觉 LLM（DeepSeek）收到图像会 chat 报错崩溃；摘要由 screen_awareness
        # 视觉模型（Qwen3-VL）产出，已足够回答「看看屏幕/这里报错」类问题。
        screen_cfg = store.config()
        # 屏幕视觉模型和对话模型是两个独立 provider。只有用户同时显式开启
        # 图片注入并确认当前对话模型支持视觉时，才把原始帧交给对话链路；
        # 否则只使用已结构化的屏幕摘要，避免文本模型收到 image_url 后整轮失败。
        if (
            images is None
            and screen_cfg.attach_screen_image_to_llm
            and screen_cfg.chat_model_supports_vision
        ):
            low = input_text.lower()
            if any(k in low for k in _SCREEN_LOOK_KEYWORDS):
                frame = store.latest_frame(client_uid)
                if frame is not None and frame.image:
                    images = [
                        {
                            "source": ImageSource.SCREEN.value,
                            "data": frame.image,
                            "mime_type": "image/jpeg",
                        }
                    ]
        return input_text + ctx_line, images
    except Exception:
        return input_text, images


async def _auto_create_history(
    context: ServiceContext,
    metadata: Optional[Dict[str, Any]],
    websocket_send: WebSocketSend,
    *,
    input_text: Union[str, np.ndarray],
) -> Optional[str]:
    """首次「真人」对话自动创建会话记录（历史持久化）。

    Phase 1（pet-ptt-workflow）：透传 metadata.workspace —— 新会话全部绑定工作目录；
    metadata 无 workspace（旧行为）时留空，由前端在输入时引导绑定，不静默伪造目录。

    返回新 history_uid；无创建条件 / 失败返回 None（fail-soft）。
    """
    from ..chat_history_manager import create_new_history

    _is_proactive_turn = bool(metadata and metadata.get("proactive_speak"))
    if (
        _is_proactive_turn
        or not isinstance(input_text, str)
        or not input_text.strip()
        or context.history_uid
    ):
        return None

    _ws = ""
    try:
        _ws = str(metadata.get("workspace") or "").strip() if metadata else ""
    except Exception:
        _ws = ""
    _hid = create_new_history(context.character_config.conf_uid, workspace=_ws)
    if not _hid:
        return None
    context.history_uid = _hid
    _ag = context.agent_engine
    if _ag is not None and hasattr(_ag, "set_memory_from_history"):
        try:
            _ag.set_memory_from_history(
                conf_uid=context.character_config.conf_uid,
                history_uid=_hid,
            )
        except Exception:
            pass
    await send_message(
        websocket_send,
        {
            "type": "new-history-created",
            "history_uid": _hid,
            # 对话中途自动创建：前端不能清空聊天区，否则刚回显的
            # user-input-transcription（语音识别文本）会被立即清掉。
            "auto": True,
        },
    )
    logger.info(f"[history] auto-created session {_hid} on first message")
    return _hid


async def _wait_for_screen_snapshot(client_uid: str, timeout_sec: float = 8.0) -> None:
    """等待该客户端出现有效屏幕快照（轮询，最多 timeout_sec 秒）。

    场景：语音回合 PTT 结束时前端 fire-and-forget 上传了采集帧，视觉分析
    （SiliconFlow 约 12s）还在后台跑；ASR 转写完成时快照可能刚过期/尚未
    生成。命中「看屏幕」关键词时短暂等待，让对话 LLM 拿到新鲜屏幕摘要。
    """
    from ..screen_awareness.service import get_store

    store = get_store()
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        if store.latest_snapshot(client_uid) is not None:
            return
        await asyncio.sleep(0.5)


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        str: Complete response text
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager()
    full_response = ""  # Initialize full_response here

    try:
        # Send initial signals
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # The AI brain can be unset if it failed to initialize (graceful init_agent).
        # The app still opens so the user can fix it — give a clear notice here rather
        # than letting a raw NoneType error surface.
        if context.agent_engine is None:
            await send_error(
                websocket_send,
                ErrorCode.LLM_UNREACHABLE,
                "尚未配置对话模型，请打开设置完成 LLM 配置后再开始聊天。",
            )
            return ""

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )

        # P5.1 意图副模型（fire-and-forget，不阻塞对话）：勿扰/闲聊/任务 + 情绪
        # → WS 出站 intent-event（前端状态条实时显示）。失败静默（聊天链路不受影响）。
        if isinstance(input_text, str) and input_text.strip():
            try:
                from ..plugin.intent import analyze_intent  # noqa: PLC0415
                from ..plugin_route import intent_config  # noqa: PLC0415

                _intent_enabled = bool(intent_config().get("enabled", True))
            except Exception:
                _intent_enabled = False
            if _intent_enabled:

                async def _publish_intent(_text: str) -> None:
                    try:
                        _r = await analyze_intent(_text)
                        await send_message(
                            websocket_send,
                            {
                                "type": "intent-event",
                                "intent": _r.get("intent", "chat"),
                                "emotion": _r.get("emotion", "neutral"),
                                "source": _r.get("source", "rule"),
                                "text": _text[:40],
                            },
                        )
                    except Exception:  # noqa: BLE001 — 副模型失败绝不阻塞对话
                        pass

                asyncio.create_task(_publish_intent(input_text))

        # Phase 3：屏幕上下文融合（摘要进 LLM 不进历史；仅明确要求时附带图像）。
        # 修复（2026-08-11）：用户明确问屏幕（关键词命中）但快照尚未就绪时
        # （语音回合前端 fire-and-forget 采集的分析还在飞），短暂等待新快照——
        # 否则 AI 拿不到屏幕内容只能「猜/说不知道」。普通对话零等待。
        _is_proactive_turn = bool(metadata and metadata.get("proactive_speak"))
        if not _is_proactive_turn and isinstance(input_text, str) and input_text.strip():
            try:
                from ..screen_awareness.service import get_store

                _store = get_store()
                _wants_screen = any(
                    k in input_text.lower() for k in _SCREEN_LOOK_KEYWORDS
                )
                if _wants_screen and _store.latest_snapshot(client_uid) is None:
                    await _wait_for_screen_snapshot(client_uid, timeout_sec=8.0)
            except Exception:
                pass  # fail-soft：等不到也不阻塞对话

        _llm_input, images = _attach_screen_context(
            client_uid, input_text, images, _is_proactive_turn
        )

        # Create batch input
        batch_input = create_batch_input(
            input_text=_llm_input,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )

        # 首次真人输入必须先创建会话，再写入首条用户消息；否则 history_uid
        # 为空时 store_message 会静默跳过，首轮消息会永久丢失。
        try:
            await _auto_create_history(
                context, metadata, websocket_send, input_text=input_text
            )
        except Exception as _hist_e:
            logger.warning(f"[history] auto-create failed: {_hist_e}")

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            # JSON history persistence is synchronous file I/O. Keep it off the
            # event loop so the websocket/LLM stream can continue promptly.
            await asyncio.to_thread(
                store_message,
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
            )

        if skip_history:
            logger.debug("Skipping storing user input to history (proactive speak)")

        logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")

        # 睡眠勿擾（只看真人發話，不看主動觸發）。見 quiet_mode.py
        # 規則：說「晚安」→ 進勿擾、暫停主動說話；之後使用者「下一次主動搭話」（任何非空訊息）
        #       就自動恢復，不必特地說早安。
        try:
            from ..quiet_mode import set_quiet, is_quiet

            _is_proactive = bool(metadata and metadata.get("proactive_speak"))
            if not _is_proactive and isinstance(input_text, str) and input_text.strip():
                _conf = context.character_config.conf_uid
                if "晚安" in input_text:
                    set_quiet(_conf, True)
                elif is_quiet(_conf):
                    set_quiet(_conf, False)  # 醒了，第一句話就恢復
        except Exception as _q_e:
            logger.warning(f"[quiet_mode] toggle failed: {_q_e}")

        # P5 插件钩子 + P2 唱歌触发（替代回复，跳过 LLM）：
        # - 插件 on_message 返回非 None → 吞掉消息，直接以插件文案回复（含 TTS）；
        # - 「唱歌+歌名」触发词 → 点歌入队（sing_core 真实歌名校验），回复安排结果。
        # 任何异常静默回落到正常 LLM 对话（插件烂不拖垮聊天）。
        _direct_reply: Optional[str] = None
        if isinstance(input_text, str) and input_text.strip():
            try:
                from ..plugin.manager import get_plugin_manager  # noqa: PLC0415

                _plug_reply = get_plugin_manager().on_message(
                    {"text": input_text, "source": "user", "ts": time.time()}
                )
                if _plug_reply:
                    _direct_reply = str(_plug_reply)
            except Exception as _plug_e:
                logger.warning(f"[plugin] on_message 分发失败: {_plug_e}")
            if _direct_reply is None:
                try:
                    from ..singing.sing_core import (  # noqa: PLC0415
                        extract_sing_request,
                        get_sing_core,
                    )

                    if extract_sing_request(input_text):
                        _core = await get_sing_core()
                        _req = await _core.request(input_text)
                        if _req.get("ok"):
                            _direct_reply = (
                                f"好呀，这就安排唱《{_req.get('songname')}》！"
                            )
                        else:
                            _direct_reply = _req.get("reason") or "这首歌暂时点不了呢"
                except Exception as _sing_e:
                    logger.warning(f"[singing] 对话触发失败: {_sing_e}")
        if _direct_reply:
            try:
                await send_message(
                    websocket_send, {"type": "full-text", "text": _direct_reply}
                )
                await tts_manager.speak(_direct_reply)
                await tts_manager.flush()
                await finalize_conversation_turn(
                    tts_manager=tts_manager,
                    websocket_send=websocket_send,
                    client_uid=client_uid,
                )
                if context.history_uid:
                    await asyncio.to_thread(
                        store_message,
                        conf_uid=context.character_config.conf_uid,
                        history_uid=context.history_uid,
                        role="ai",
                        content=_direct_reply,
                        name=context.character_config.conf_name,
                        avatar=context.character_config.avatar,
                    )
            except Exception as _dr_e:
                logger.warning(f"[direct-reply] 发送失败: {_dr_e}")
            return _direct_reply

        # 對話前刷新核心記憶到 system prompt（phase 1.5）：
        # agent_engine 在 server 開機時烤死 system prompt、新連線只 pass by reference 不重讀，
        # 導致背景 consolidation 寫入的新記憶要等重啟才生效。這裡在每輪對話前重讀 core_memory.md，
        # 只有記憶真的變了（或這個 session context 第一次跑）才重建 prompt 並 set_system，
        # 讓「越聊越認識你」免重啟即時生效，又不動到 agent 的對話歷史。見 MEMORY_SYSTEM_DESIGN.md
        # 長期記憶關閉時跳過 phase-1.5 重注入（construct_system_prompt 本身也已 gate，
        # 這裡短路避免無謂重建 prompt）。
        try:
            from ..memory_core import load_core_memory

            _mem_on = getattr(
                context.character_config, "long_term_memory_enabled", True
            )
            agent = context.agent_engine
            if _mem_on and hasattr(agent, "set_system"):
                fresh_mem = load_core_memory(context.character_config.conf_uid)
                # P2 上下文桥：任务简报也纳入 prompt 重建触发因子——简报有/无变化时
                # 也必须重建（否则任务完成后角色"失忆"，见 service_context._recent_task_brief）。
                # 这里仅取"是否有简报"作布尔因子，避免每轮全量比对。
                fresh_brief = ""
                try:
                    fresh_brief = context._recent_task_brief()
                except Exception:
                    fresh_brief = ""
                _sig = (fresh_mem, bool(fresh_brief))
                _prev_sig = getattr(context, "_core_mem_injected", None)
                if _sig != _prev_sig:
                    refreshed_prompt = await context.construct_system_prompt(
                        context.character_config.persona_prompt
                    )
                    agent.set_system(refreshed_prompt)
                    context._core_mem_injected = _sig
                    logger.info(
                        f"[core_memory] system prompt refreshed (mem_changed={_sig[0] != (_prev_sig or (None,))[0]}, brief={_sig[1]})"
                    )
        except Exception as _refresh_e:
            logger.warning(f"[core_memory] refresh failed: {_refresh_e}")

        # 深度回憶（opt-in 全歷史 FTS5 檢索，預設關閉）：開啟時，用這輪使用者輸入去搜
        # 這個角色「全部過去對話」，把 top-K 相關片段「額外」附到 system prompt（核心記憶照舊）。
        # 完全 fail-soft：任何錯誤都當沒命中、照常對話。關閉時這段完全短路、行為與原本逐位元組相同。
        # 因為 FTS 結果每輪不同，開啟時必須「每輪」重設 system prompt（不像核心記憶只在變動時重設），
        # 並把 _core_mem_injected 記號清掉，讓之後關閉 FTS 的那一輪能重新同步乾淨的核心記憶 prompt。
        try:
            # 混合检索（FTS5 全文 + 向量语义，RRF 融合）。见 memory_fts.py / vector_memory.py。
            # 記憶 v2（memory_v2_enabled=True 時）：facts/reflections 也納入注入池（見 memory_v2.py）。
            _fts_on = getattr(
                context.character_config, "fts_memory_enabled", False
            )
            _vec_on = getattr(
                context.character_config, "vector_memory_enabled", False
            )
            _v2_on = getattr(
                context.character_config, "memory_v2_enabled", True
            )
            agent = context.agent_engine
            if (
                (_fts_on or _vec_on or _v2_on)
                and hasattr(agent, "set_system")
                and isinstance(input_text, str)
                and input_text.strip()
            ):
                from .. import memory_fts

                _fts_k = getattr(context.character_config, "fts_memory_top_k", 3)
                fts_snippets: list = []
                vec_snippets: list = []
                if _fts_on:
                    fts_snippets = memory_fts.search(
                        context.character_config.conf_uid, input_text, k=_fts_k
                    )
                if _vec_on:
                    try:
                        from ..vector_memory import embed_texts, search, RETRIEVAL_LABEL

                        _vec_k = getattr(
                            context.character_config, "vector_memory_top_k", 3
                        )
                        _emb_base, _emb_model, _emb_key = _embedding_llm(
                            context.character_config
                        )
                        # Vector retrieval is a best-effort enhancement. Never
                        # make the first LLM token wait on a remote embedding
                        # endpoint; FTS/memory_v2 results remain usable on timeout.
                        _embs = await asyncio.wait_for(
                            embed_texts(
                                [input_text], _emb_base, _emb_model, _emb_key
                            ),
                            # Vector memory is an enhancement, not a reason to
                            # hold the first model token. FTS/memory-v2 remain
                            # available when a remote embedding endpoint is slow.
                            timeout=0.2,
                        )
                        if _embs and _embs[0]:
                            vec_snippets = search(
                                context.character_config.conf_uid,
                                _embs[0],
                                k=_vec_k,
                            )
                    except Exception as _vec_e:
                        logger.warning(
                            f"[vector_memory] retrieval failed: {_vec_e}"
                        )
                # 記憶 v2 檢索（facts + reflections，帶各自標籤的完整塊）
                v2_blocks: list = []
                if _v2_on:
                    try:
                        from ..memory_v2 import search as _v2_search

                        v2_blocks = _v2_search(
                            context.character_config.conf_uid, input_text, k=_fts_k
                        )
                    except Exception as _v2_retr_e:
                        logger.warning(
                            f"[memory_v2] retrieval failed: {_v2_retr_e}"
                        )
                # RRF 融合两份结果；只注入有命中的部分。
                from ..vector_memory import rrf_fuse

                fused = rrf_fuse(fts_snippets, vec_snippets, k=_fts_k)
                if fused or v2_blocks:
                    base_prompt = await context.construct_system_prompt(
                        context.character_config.persona_prompt
                    )
                    parts = []
                    if fts_snippets:
                        parts.append(
                            f"\n\n{memory_fts.RETRIEVAL_LABEL}"
                            "（僅供參考，未必準確；若與當下無關就忽略）\n"
                            + "\n".join(fts_snippets)
                        )
                    if vec_snippets:
                        parts.append(
                            f"\n\n{RETRIEVAL_LABEL}"
                            "（僅供參考，未必準確；若與當下無關就忽略）\n"
                            + "\n".join(vec_snippets)
                        )
                    if v2_blocks:
                        parts.append("\n\n" + "\n\n".join(v2_blocks))
                    if parts:
                        agent.set_system(base_prompt + "".join(parts))
                        # 改了 prompt -> 清核心記憶記號，逼下一個 no-retrieval turn 重新同步。
                        context._core_mem_injected = None
                        logger.info(
                            f"[retrieval] injected {len(fused) + len(v2_blocks)} snippet(s) "
                            f"(fts={len(fts_snippets)}, vec={len(vec_snippets)}, v2={len(v2_blocks)})"
                        )
        except Exception as _fts_e:
            logger.warning(f"[retrieval] injection failed: {_fts_e}")

        try:
            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
            agent_output_stream = context.agent_engine.chat(batch_input)

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message
                    output_item["name"] = context.character_config.conf_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    await send_message(websocket_send, output_item)

                elif (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "task_result"
                ):
                    # The task card/state stream is the single UI surface for
                    # structured task output. Do not duplicate it as a chat
                    # bubble or persist the internal result in chat history.
                    continue

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    # Handle SentenceOutput or AudioOutput
                    response_part = await process_agent_output(
                        output=output_item,
                        character_config=context.character_config,
                        live2d_model=context.live2d_model,
                        tts_engine=context.tts_engine,
                        websocket_send=websocket_send,  # Pass websocket_send for audio/tts messages
                        tts_manager=tts_manager,
                        translate_engine=context.translate_engine,
                        subtitle_translate_engine=context.subtitle_translate_engine,
                    )
                    # Ensure response_part is treated as a string before concatenation
                    response_part_str = (
                        str(response_part) if response_part is not None else ""
                    )
                    full_response += response_part_str  # Accumulate text response
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

        except Exception as e:
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            await send_error(
                websocket_send,
                ErrorCode.INTERNAL_ERROR,
                "AI 回复处理失败，请查看后端日志。",
            )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        # 先把聚合缓冲里的剩余文本整段合成（句子已按段落聚合，见 tts_manager.speak）
        await tts_manager.flush()
        if tts_manager.task_list:
            # 阶段反馈（v6）：LLM 文本已全部流式上屏，接下来是语音合成（本地引擎
            # 可能数秒）——明确提示「正在合成语音」，避免用户以为回复已结束/卡住。
            await send_message(
                websocket_send,
                {"type": "tool_call_status", "text": "正在合成语音…"},
            )
        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )
        if tts_manager.task_list:
            await send_message(websocket_send, {"type": "tool_call_status", "text": ""})

        if context.history_uid and full_response:  # Check full_response before storing
            await asyncio.to_thread(
                store_message,
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.conf_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {full_response}")

        # 對話一輪後背景整理核心記憶（fire-and-forget，不阻塞使用者）。見 MEMORY_SYSTEM_DESIGN.md
        # 長期記憶關閉時（long_term_memory_enabled=False）跳過整理，連背景 task 都不建。
        # 節流：memory_consolidation_interval 控制每幾輪才整理一次（1=每輪、預設、行為不變；
        # 3/5 給弱機/本地模型省一半以上「整理用」的 LLM 呼叫）。用 per-session 計數器，
        # 只有 turn % interval == 0 那一輪才排整理 task。整段 fail-soft：計數器出錯絕不弄壞這輪對話。
        try:
            from ..memory_core import consolidate_core_memory, _clamp_interval

            _llm = context.character_config.agent_config.llm_configs.openai_compatible_llm
            _mem_on = getattr(
                context.character_config, "long_term_memory_enabled", True
            )
            if _mem_on and isinstance(input_text, str) and input_text.strip():
                _conf_uid = context.character_config.conf_uid
                _interval = _clamp_interval(
                    getattr(
                        context.character_config, "memory_consolidation_interval", 1
                    )
                )
                _key = (str(_conf_uid), str(client_uid))
                _n = _TURN_COUNTS.get(_key, 0) + 1
                _TURN_COUNTS[_key] = _n
                if _n % _interval == 0:
                    _cap = getattr(
                        context.character_config, "core_memory_max_chars", 1500
                    )
                    _t = asyncio.create_task(
                        consolidate_core_memory(
                            _conf_uid,
                            input_text,
                            full_response,
                            _llm.base_url,
                            _llm.model,
                            cap=_cap,
                            api_key=_llm.llm_api_key,
                        )
                    )
                    # 保存 reference 避免 fire-and-forget task 被 GC（Python asyncio 已知坑）
                    _BG_MEMORY_TASKS.add(_t)
                    _t.add_done_callback(_BG_MEMORY_TASKS.discard)
                else:
                    logger.debug(
                        f"[core_memory] consolidation skipped "
                        f"(turn {_n}, every {_interval})"
                    )
        except Exception as _mem_e:
            logger.warning(f"[core_memory] schedule failed: {_mem_e}")

        # 向量记忆：每轮「真人」对话后把使用者说的话嵌入并存入语义记忆（见 vector_memory.py）。
        # 默认关闭（vector_memory_enabled）；fire-and-forget，绝不影响对话。
        try:
            _vec_on = getattr(
                context.character_config, "vector_memory_enabled", False
            )
            if (
                _vec_on
                and isinstance(input_text, str)
                and input_text.strip()
                and full_response
            ):
                from ..vector_memory import embed_texts, store_memory

                _mem_text = f"{input_text.strip()}（AI 回：{full_response.strip()[:120]}）"
                _emb_base, _emb_model, _emb_key = _embedding_llm(
                    context.character_config
                )
                async def _store_vector_memory() -> None:
                    _embs = await embed_texts(
                        [_mem_text], _emb_base, _emb_model, _emb_key
                    )
                    if _embs and _embs[0]:
                        import time as _t

                        await asyncio.to_thread(
                            store_memory,
                            context.character_config.conf_uid,
                            _mem_text,
                            _embs[0],
                            _t.time(),
                        )

                _vm_task = asyncio.create_task(_store_vector_memory())
                _BG_MEMORY_TASKS.add(_vm_task)
                _vm_task.add_done_callback(_BG_MEMORY_TASKS.discard)
        except Exception as _vm_e:
            logger.warning(f"[vector_memory] store failed: {_vm_e}")

        # 記憶 v2（類型化事實 + 反思 + dreaming）：每輪對話後背景抽取 facts（fire-and-forget）。
        # 見 memory_v2.py / docs/memory-evolution-plan.md。預設開啟（memory_v2_enabled=True）。
        # 流程：facts 提取 + 證據信號（一次 LLM 呼叫）→ 攢夠 K 條合成 reflection →
        #       距上次 dream ≥ 30min 時順帶跑一次睡眠合併（對話已結束，不搶資源）。
        # 完全 fail-soft：任何錯誤只記 warning，絕不阻塞對話。關閉時整段短路。
        try:
            _v2_on = getattr(
                context.character_config, "memory_v2_enabled", True
            )
            if (
                _v2_on
                and isinstance(input_text, str)
                and input_text.strip()
                and full_response
            ):
                from ..memory_v2 import post_turn as _v2_post_turn

                _v2_llm = (
                    context.character_config.agent_config.llm_configs.openai_compatible_llm
                )
                _max_facts = getattr(
                    context.character_config, "memory_v2_max_facts", 500
                )
                _t2 = asyncio.create_task(
                    _v2_post_turn(
                        context.character_config.conf_uid,
                        input_text,
                        full_response,
                        _v2_llm.base_url,
                        _v2_llm.model,
                        api_key=_v2_llm.llm_api_key,
                        max_facts=_max_facts,
                    )
                )
                _BG_MEMORY_TASKS.add(_t2)
                _t2.add_done_callback(_BG_MEMORY_TASKS.discard)
        except Exception as _v2_e:
            logger.warning(f"[memory_v2] schedule failed: {_v2_e}")

        # 好感度：每輪「真人」對話後依訊息長度加分（見 affection.py）。
        # 主動搭話（proactive_speak）不加好感度；整段 fail-soft，失敗絕不弄壞對話。
        try:
            _is_proactive_turn = bool(metadata and metadata.get("proactive_speak"))
            if not _is_proactive_turn and isinstance(input_text, str):
                from ..affection import apply_turn

                _aff_result = apply_turn(
                    context.character_config.conf_uid, input_text
                )
                if _aff_result and _aff_result.get("summary"):
                    _payload = {
                        "type": "affection-update",
                        "affection": _aff_result["summary"],
                    }
                    if _aff_result.get("milestone"):
                        _payload["milestone"] = _aff_result["milestone"]
                    await send_message(websocket_send, _payload)
        except Exception as _aff_e:
            logger.warning(f"[affection] turn update failed: {_aff_e}")

        return full_response  # Return accumulated full_response

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} cancelled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        await send_error(
            websocket_send,
            ErrorCode.INTERNAL_ERROR,
            "对话处理出错，请查看后端日志。",
        )
        raise
    finally:
        cleanup_conversation(tts_manager, session_emoji)
