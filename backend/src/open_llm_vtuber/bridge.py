"""bridge.py — P5.1 外部事件 → 主对话桥接（弹幕闲聊 / 游戏喝彩 TTS）。

核心问题：弹幕/喝彩触发点在 live/playmate 路由（独立 APIRouter），
拿不到 WS 连接与 ServiceContext。方案：复用 `websocket_handler.get_ws_handler()`
模块级单例（routes.py 已 set），外部事件即可拿 client_connections /
client_contexts / default_context_cache，走 `process_single_conversation`
完整对话链路（LLM 回复 + TTS 音频出站）。

- `feed_as_user_input(text, source)`：把外部文本作为「用户消息」喂给
  第一个活跃连接（弹幕闲聊场景）；目标连接已有对话任务在跑 → 跳过（不打扰）。
- `speak_line(text)`：让 AI 直接说出某句台词（喝彩场景，proactive 模式，
  skip_memory/skip_history 不污染记忆历史）。

任何一步失败静默返回 False（桥接绝不干扰主服务）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger


def get_ws_handler() -> Optional[Any]:
    """取 websocket_handler 模块级单例（未启动/未连接时可能为 None）。"""
    try:
        from .websocket_handler import get_ws_handler as _get  # noqa: PLC0415

        return _get()
    except Exception:
        return None


def _first_active_uid() -> Optional[tuple[str, Any]]:
    """返回 (client_uid, websocket)；无活跃连接 → None。"""
    handler = get_ws_handler()
    if handler is None:
        return None
    conns = getattr(handler, "client_connections", None) or {}
    if not conns:
        return None
    uid = next(iter(conns))
    ws = conns[uid]
    if ws is None:
        return None
    return uid, ws


def _target_busy(uid: str) -> bool:
    """目标连接是否已有对话任务在跑（避免打断正在进行的回复）。"""
    try:
        handler = get_ws_handler()
        tasks = getattr(handler, "current_conversation_tasks", None) or {}
        task = tasks.get(uid)
        return bool(task and not task.done())
    except Exception:
        return False


async def _run_conversation(uid: str, ws: Any, text: str, metadata: dict) -> bool:
    """复用 process_single_conversation 走完整对话链路。"""
    try:
        from .conversations.single_conversation import process_single_conversation  # noqa: PLC0415

        handler = get_ws_handler()
        contexts = getattr(handler, "client_contexts", None) or {}
        ctx = contexts.get(uid) or getattr(handler, "default_context_cache", None)
        if ctx is None:
            return False

        async def ws_send(raw: str) -> None:
            # process_single_conversation 的 websocket_send 期望 str（send_message 已序列化）
            if ws is not None:
                try:
                    await ws.send_text(raw)
                except Exception:  # noqa: BLE001 — 连接断了静默
                    pass

        await process_single_conversation(
            context=ctx,
            websocket_send=ws_send,
            client_uid=uid,
            user_input=text,
            session_emoji="🎤",
            metadata=metadata,
        )
        return True
    except Exception as e:  # noqa: BLE001 — 桥接失败绝不外泄
        logger.warning(f"bridge: 对话注入失败: {e}")
        return False


async def feed_as_user_input(text: str, source: str = "external") -> bool:
    """外部文本作为用户消息注入（弹幕闲聊等）；目标忙 → 跳过。"""
    if not text or not text.strip():
        return False
    found = _first_active_uid()
    if found is None:
        return False
    uid, ws = found
    if _target_busy(uid):
        logger.info(f"bridge: {source} 跳过（目标连接正在回复）")
        return False
    metadata = {
        "skip_history": True,  # 外部消息不进本地历史
        "skip_memory": True,  # 不进 AI 内部记忆
        "bridge_source": source,
    }
    return await _run_conversation(uid, ws, text, metadata)


async def speak_line(text: str, source: str = "cheer") -> bool:
    """让 AI 说出指定台词（游戏喝彩等）；proactive 模式不污染记忆。"""
    if not text or not text.strip():
        return False
    found = _first_active_uid()
    if found is None:
        return False
    uid, ws = found
    if _target_busy(uid):
        logger.info(f"bridge: {source} 跳过（目标连接正在回复）")
        return False
    # 台词注入：直接要求角色以口吻说出，不走自由发挥 prompt。
    prompt_text = (
        f"{text}\n"
        "（这是需要你说出口的一句台词，直接用角色口吻说出，简短自然，不要转述、"
        "不要加括号注释，不要说『我』以外的解释）"
    )
    metadata = {
        "proactive_speak": True,
        "skip_history": True,
        "skip_memory": True,
        "bridge_source": source,
    }
    return await _run_conversation(uid, ws, prompt_text, metadata)


__all__: list[str] = [
    "get_ws_handler",
    "feed_as_user_input",
    "speak_line",
]
