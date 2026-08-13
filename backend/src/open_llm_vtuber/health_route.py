"""进程级健康探针。

探针故意与 ``/api/readiness`` 分开：健康探针只回答进程是否还活着，不能因为
模型、LLM 或可选本地引擎尚未就绪而把存活进程误报成宕机。这样桌面端、安装器
和后续打包运行时都可以使用稳定、低成本的 HTTP 检查。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .service_context import ServiceContext


def init_health_route(context: ServiceContext) -> APIRouter:
    """注册 ``/healthz``（存活）与 ``/readyz``（初始化完成）探针。"""

    router = APIRouter(tags=["health"])

    @router.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "moonlight-backend"}

    @router.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        initialized = bool(
            context.config is not None
            and context.system_config is not None
            and context.character_config is not None
        )
        payload = {
            "status": "ready" if initialized else "starting",
            "service": "moonlight-backend",
            "initialized": initialized,
        }
        return JSONResponse(status_code=200 if initialized else 503, content=payload)

    return router
