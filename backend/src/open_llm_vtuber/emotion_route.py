"""情绪系统 REST 端点。

Localhost-only 端点，让前端/插件查询和覆盖当前情绪状态：
- GET  /api/emotion           -> 当前情绪 + 直方图
- GET  /api/emotion/history   -> 最近情绪事件
- POST /api/emotion/override  {emotion} -> 手动设置情绪
- POST /api/emotion/reset              -> 重置为 neutral
"""

import re
from typing import Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from .emotion import get_emotion_tracker, EMOTIONS


def _validate_emotion(emotion: Optional[str]) -> Optional[str]:
    if not emotion:
        return None
    if emotion in EMOTIONS:
        return emotion
    # 宽松匹配：允许带连字符/下划线的变体，取最接近的情绪
    normalized = re.sub(r"[-_]", "", emotion).lower()
    for e in EMOTIONS:
        if normalized in e or e in normalized:
            return e
    return None


def init_emotion_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/emotion")
    async def get_emotion(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return get_emotion_tracker().get_current()

    @router.get("/api/emotion/history")
    async def get_emotion_history(request: Request, limit: int = 10):
        if not _is_local_request(request):
            return _forbidden()
        return {"events": get_emotion_tracker().recent_events(limit)}

    @router.post("/api/emotion/override")
    async def override_emotion(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        emotion = _validate_emotion(body.get("emotion"))
        if not emotion:
            return JSONResponse(
                {"error": f"invalid emotion, allowed: {EMOTIONS}"}, status_code=400
            )
        event = get_emotion_tracker().set(emotion)
        logger.info(f"Emotion overridden -> {emotion}")
        return {"ok": True, "event": event}

    @router.post("/api/emotion/reset")
    async def reset_emotion(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        event = get_emotion_tracker().reset()
        return {"ok": True, "event": event}

    return router
