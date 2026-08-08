from typing import Union, List, Dict, Any, Optional
import asyncio
import json
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
from ..service_context import ServiceContext
from ..contracts import ErrorCode, send_error, send_message

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

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            store_message(
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

        # 首次「真人」对话自动创建会话记录（历史持久化）。否则 context.history_uid 为空，
        # store_message 会跳过、消息不落盘，历史对话栏永远是空的。
        try:
            _is_proactive_turn = bool(metadata and metadata.get("proactive_speak"))
            if (
                not _is_proactive_turn
                and isinstance(input_text, str)
                and input_text.strip()
                and not context.history_uid
            ):
                from ..chat_history_manager import create_new_history

                _hid = create_new_history(context.character_config.conf_uid)
                if _hid:
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
        except Exception as _hist_e:
            logger.warning(f"[history] auto-create failed: {_hist_e}")

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
                if fresh_mem != getattr(context, "_core_mem_injected", None):
                    refreshed_prompt = await context.construct_system_prompt(
                        context.character_config.persona_prompt
                    )
                    agent.set_system(refreshed_prompt)
                    context._core_mem_injected = fresh_mem
                    logger.info(
                        "[core_memory] system prompt refreshed with latest core memory"
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
                        _embs = await embed_texts(
                            [input_text], _emb_base, _emb_model, _emb_key
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
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    await websocket_send(json.dumps(output_item))

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
            await asyncio.gather(*tts_manager.task_list)
            await send_message(websocket_send, {"type": "backend-synth-complete"})

        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        if context.history_uid and full_response:  # Check full_response before storing
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name
                or context.character_config.conf_name,
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
                _embs = await embed_texts(
                    [_mem_text], _emb_base, _emb_model, _emb_key
                )
                if _embs and _embs[0]:
                    import time as _t

                    store_memory(
                        context.character_config.conf_uid,
                        _mem_text,
                        _embs[0],
                        ts=_t.time(),
                    )
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
