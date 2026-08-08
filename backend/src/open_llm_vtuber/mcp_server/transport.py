"""MCP Streamable HTTP 传输层。

把 FastMCP 实例包装成可被 uvicorn 托管的 ASGI 应用，监听 127.0.0.1:12394，
并对所有 HTTP 请求做 Bearer Token 校验（token 来自环境变量
``MOONLIGHT_MCP_TOKEN``；未设置时自动生成 UUID 并打印到日志）。
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from loguru import logger

# MCP 服务监听地址与端口（与主 WebSocket 服务 12393 分开）。
HOST = "127.0.0.1"
PORT = 12394


def resolve_token() -> str:
    """返回 Bearer Token；若未设置则生成随机 UUID 并写入环境变量 + 打印日志。"""
    token = os.environ.get("MOONLIGHT_MCP_TOKEN", "").strip()
    if not token:
        token = str(uuid.uuid4())
        os.environ["MOONLIGHT_MCP_TOKEN"] = token
        logger.info(
            "MOONLIGHT_MCP_TOKEN 未设置——已为本轮运行生成随机 token。"
            f" 外部 MCP 客户端需在请求头携带 'Authorization: Bearer {token}'。"
        )
    return token


class _BearerAuthMiddleware:
    """纯 ASGI 中间件：非 HTTP 请求透传，HTTP 请求校验 Bearer token。

    之所以不用 Starlette 的 BaseHTTPMiddleware，是为了避免它对 SSE 流式响应
    的缓冲/包装问题——这里直接透传 scope/receive/send，对上游完全透明。
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = b"Bearer " + token.encode("utf-8")

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"authorization") != self._expected:
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


def create_app(mcp: Any, token: Optional[str] = None) -> Any:
    """构建 MCP Streamable HTTP 的 ASGI 应用（已包裹 Bearer 鉴权中间件）。

    ``mcp`` 需为 FastMCP 实例（提供 ``streamable_http_app()`` 方法，mcp>=1.9）。
    若实例未暴露该方法，退回用底层 ``_mcp_server`` 手动组装。
    """
    token = token or resolve_token()
    try:
        app = mcp.streamable_http_app()
    except AttributeError:
        # 旧版 mcp SDK 兼容写法：从底层 Server 组装 Streamable HTTP 应用。
        low_level = getattr(mcp, "_mcp_server", mcp)
        from mcp.server.streamable_http import (
            StreamableHTTPSessionManager,
            StreamableHTTPASGIApp,
        )

        app = StreamableHTTPASGIApp(StreamableHTTPSessionManager(app=low_level))
    return _BearerAuthMiddleware(app, token)
