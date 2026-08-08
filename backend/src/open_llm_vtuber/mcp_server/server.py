"""FastMCP 服务定义：Moonlight 桌宠控制工具。

暴露 4 个工具，供外部 MCP Agent（Claude、OpenClaw 等）反向控制桌宠：
``moonlight.say`` / ``moonlight.express`` / ``moonlight.get_state`` /
``moonlight.capture_screen``。

传输层（Streamable HTTP + 鉴权）在 ``transport.py``，进程层（后台 uvicorn
线程）在 ``launcher.py``。本文件不依赖具体传输方式。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from loguru import logger
from mcp.server.fastmcp import FastMCP

from ..config_manager.utils import read_yaml, scan_config_alts_directory
from ..contracts import send_message
from ..emotion import EMOTIONS, get_emotion_tracker
from ..message_handler import message_handler

# 前端截屏往返的超时秒数。
SCREEN_CAPTURE_TIMEOUT_SECONDS = 10


def _get_connections() -> Dict[str, Any]:
    """返回 ``{client_uid: websocket}`` 的已连接前端字典（空 dict 表示无连接）。

    ``websocket_handler`` 依赖较重的后端链路，故延迟导入，避免 MCP 模块在
    导入阶段就耦合整个 WS 栈。
    """
    from ..websocket_handler import get_ws_handler

    handler = get_ws_handler()
    if handler is None:
        return {}
    connections = getattr(handler, "client_connections", None)
    return dict(connections) if connections else {}


def _first_client() -> Optional[Tuple[str, Any]]:
    """返回第一个已连接前端的 ``(client_uid, websocket)``，无连接则返回 None。"""
    for uid, ws in _get_connections().items():
        return uid, ws
    return None


async def _broadcast(payload: Dict[str, Any]) -> Tuple[int, int]:
    """向所有已连接前端发送 ``payload``，返回 ``(成功数, 失败数)``。"""
    sent = 0
    failed = 0
    for uid, ws in _get_connections().items():
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            sent += 1
        except Exception as exc:
            logger.warning(f"广播 {payload.get('type')} 到 {uid} 失败: {exc}")
            failed += 1
    return sent, failed


def _state_snapshot() -> Dict[str, Any]:
    """从 conf.yaml 构建 get_state 的返回。绝不含 API key / 聊天记录。"""
    data: Dict[str, Any] = {}
    try:
        loaded = read_yaml("conf.yaml")
        if isinstance(loaded, dict):
            data = loaded
    except Exception as exc:
        logger.warning(f"moonlight.get_state: 读取 conf.yaml 失败: {exc}")

    system_cfg = data.get("system_config", {}) if isinstance(data, dict) else {}
    char_cfg = data.get("character_config", {}) if isinstance(data, dict) else {}

    # characters：characters/ 目录（config_alts_dir）下的角色名列表。
    characters: list[str] = []
    alts_dir = system_cfg.get("config_alts_dir", "characters")
    try:
        for entry in scan_config_alts_directory(alts_dir):
            name = entry.get("name")
            if name:
                characters.append(name)
    except Exception as exc:
        logger.warning(f"moonlight.get_state: 扫描角色目录失败: {exc}")

    agent_settings = (
        char_cfg.get("agent_config", {}).get("agent_settings", {}) or {}
    )
    basic_memory = agent_settings.get("basic_memory_agent", {}) or {}

    return {
        "emotion": get_emotion_tracker().get_current().get("emotion", "neutral"),
        "characters": characters,
        "llm_provider": basic_memory.get("llm_provider"),
        "memory_enabled": bool(char_cfg.get("long_term_memory_enabled", True)),
    }


def build_server() -> FastMCP:
    """构建名为 ``moonlight`` 的 FastMCP 服务端并注册全部工具。"""
    mcp = FastMCP("moonlight")

    @mcp.tool(name="moonlight.say")
    async def say(text: str, emotion: str = "neutral") -> Dict[str, Any]:
        """让桌宠说出 ``text``。

        先把情绪跟踪器更新为 ``emotion``（若属于情绪集），再向已连接前端推送
        ``{"type": "full-text", "text": text}``，由前端负责显示并驱动 TTS 播放。
        """
        if emotion in EMOTIONS:
            get_emotion_tracker().set(emotion)
        else:
            logger.warning(f"moonlight.say: 忽略未知情绪 {emotion!r}")

        client = _first_client()
        if client is None:
            return {"status": "no-client"}
        uid, ws = client
        try:
            await send_message(ws.send_text, {"type": "full-text", "text": text})
        except Exception as exc:
            logger.error(f"moonlight.say: 发送到 {uid} 失败: {exc}")
            return {"status": "error", "error": str(exc)}
        logger.info(f"moonlight.say -> {uid}: {text!r} (emotion={emotion})")
        return {"status": "ok", "client_uid": uid}

    @mcp.tool(name="moonlight.express")
    async def express(emotion: str) -> Dict[str, Any]:
        """触发桌宠的情绪/表情。emotion 必须是情绪集之一。"""
        if emotion not in EMOTIONS:
            return {
                "status": "error",
                "error": f"无效情绪，可选: {list(EMOTIONS)}",
            }
        event = get_emotion_tracker().set(emotion)
        sent, failed = await _broadcast({"type": "emotion", "emotion": emotion})
        if sent == 0:
            return {"status": "no-client", "event": event}
        logger.info(
            f"moonlight.express -> 广播情绪 {emotion} (sent={sent}, failed={failed})"
        )
        return {"status": "ok", "sent": sent, "failed": failed, "event": event}

    @mcp.tool(name="moonlight.get_state")
    async def get_state() -> Dict[str, Any]:
        """返回桌宠当前状态摘要：情绪、可用角色、LLM 提供商、长期记忆开关。

        绝不包含 API key 或聊天记录。
        """
        return _state_snapshot()

    @mcp.tool(name="moonlight.capture_screen")
    async def capture_screen() -> Dict[str, Any]:
        """请求已连接前端截屏并返回 base64 图片。

        通过 WebSocket 发送 ``{"type": "screen-capture-request"}``，等待前端回
        ``{"type": "screen-capture-result", "image": "<base64>"}``。超时 10 秒。
        """
        client = _first_client()
        if client is None:
            return {"status": "no-client"}
        uid, ws = client
        try:
            await ws.send_text(json.dumps({"type": "screen-capture-request"}))
        except Exception as exc:
            logger.error(f"moonlight.capture_screen: 发送到 {uid} 失败: {exc}")
            return {"status": "error", "error": str(exc)}
        try:
            response = await message_handler.wait_for_response(
                uid, "screen-capture-result", timeout=SCREEN_CAPTURE_TIMEOUT_SECONDS
            )
        except Exception as exc:  # 防御：wait_for_response 本身是超时安全的
            logger.error(f"moonlight.capture_screen: 等待响应失败: {exc}")
            return {"status": "error", "error": str(exc)}
        if not response or not response.get("image"):
            return {"status": "timeout"}
        logger.info(f"moonlight.capture_screen -> 从 {uid} 收到图片")
        return {"status": "ok", "client_uid": uid, "image": response["image"]}

    return mcp


# 允许以 stdio MCP server 方式直接运行本模块（例如由 mcp_servers.json 拉起）：
#   uv run python -m open_llm_vtuber.mcp_server.server
# 当外部 MCP 客户端用 stdio 方式连接时，无需 12394 端口与 token。
if __name__ == "__main__":
    build_server().run()
