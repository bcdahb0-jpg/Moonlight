"""
角色台词集管理 REST 端点（localhost-only）。

- GET   /api/quotes?conf_uid=xxx   -> 读取该角色台词集
- PUT   /api/quotes                -> 覆盖更新 {conf_uid, scenario_quotes, keyword_quotes}
- POST  /api/quotes/reset          -> 恢复默认台词集 {conf_uid}

防护复用 llm_config_route 的 localhost+proxy 守卫（与 character_route 一致，禁止发散）。
"""

from typing import Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from . import quotes


def _conf_uid_from_query(request: Request) -> Optional[str]:
    uid = (request.query_params.get("conf_uid") or "").strip()
    return uid or None


def init_quotes_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/quotes")
    async def get_quotes(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        conf_uid = _conf_uid_from_query(request)
        if not conf_uid:
            return JSONResponse(status_code=400, content={"error": "Missing conf_uid"})
        try:
            data = await _thread(quotes.get_quotes, conf_uid)
            return JSONResponse({"ok": True, "conf_uid": conf_uid, "quotes": data})
        except Exception as e:
            logger.error(f"[quotes] GET failed: {type(e).__name__}")
            return JSONResponse(status_code=500, content={"error": "read failed"})

    @router.put("/api/quotes")
    async def update_quotes(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})
        conf_uid = (body.get("conf_uid") or "").strip()
        if not conf_uid:
            return JSONResponse(status_code=400, content={"error": "Missing conf_uid"})
        try:
            ok = await _thread(
                quotes.update_quotes,
                conf_uid,
                body.get("scenario_quotes"),
                body.get("keyword_quotes"),
            )
        except Exception as e:
            logger.error(f"[quotes] PUT failed: {type(e).__name__}")
            ok = False
        if not ok:
            return JSONResponse(status_code=500, content={"error": "write failed"})
        return JSONResponse({"ok": True, "conf_uid": conf_uid})

    @router.post("/api/quotes/reset")
    async def reset_quotes(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})
        conf_uid = (body.get("conf_uid") or "").strip()
        if not conf_uid:
            return JSONResponse(status_code=400, content={"error": "Missing conf_uid"})
        ok = await _thread(quotes.reset_quotes, conf_uid)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "reset failed"})
        return JSONResponse({"ok": True, "conf_uid": conf_uid})

    return router


async def _thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)
