"""MCP 服务端的进程拉起/停止。

用 ``uvicorn.Server`` + ``threading.Thread`` 在后台线程托管 MCP 的
Streamable HTTP 应用，与主 FastAPI 服务（127.0.0.1:12393）共存于同一进程。
"""

from __future__ import annotations

import threading
from typing import Optional

import uvicorn
from loguru import logger

from .server import build_server
from .transport import HOST, PORT, create_app, resolve_token

_uvicorn_server: Optional[uvicorn.Server] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _run_server(server: uvicorn.Server) -> None:
    """后台线程目标：运行 uvicorn，异常时记录日志而非静默消失。"""
    try:
        server.run()
    except Exception as exc:
        logger.error(f"MCP server 线程异常退出: {type(exc).__name__}: {exc}")


def start_mcp_server() -> None:
    """后台拉起 MCP 服务端（幂等；已运行则直接返回）。"""
    global _uvicorn_server, _thread
    with _lock:
        if _uvicorn_server is not None:
            logger.info("MCP server 已在运行，跳过启动。")
            return
        token = resolve_token()
        mcp = build_server()
        app = create_app(mcp, token)
        _uvicorn_server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
        )
        _thread = threading.Thread(
            target=_run_server,
            args=(_uvicorn_server,),
            name="moonlight-mcp-server",
            daemon=True,
        )
        _thread.start()
        logger.info(f"MCP server 监听于 http://{HOST}:{PORT}")


def stop_mcp_server() -> None:
    """优雅停止后台 MCP 服务端（幂等）。"""
    global _uvicorn_server, _thread
    with _lock:
        server = _uvicorn_server
        thread = _thread
        _uvicorn_server = None
        _thread = None
    if server is None:
        return
    server.should_exit = True
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    logger.info("MCP server 已停止。")
