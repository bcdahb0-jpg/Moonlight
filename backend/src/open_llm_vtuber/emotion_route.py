"""情绪系统 REST 端点。

Localhost-only 端点，让前端/插件查询和覆盖当前情绪状态：
- GET  /api/emotion           -> 当前情绪 + 直方图
- GET  /api/emotion/history   -> 最近情绪事件
- POST /api/emotion/override  {emotion} -> 手动设置情绪
- POST /api/emotion/reset              -> 重置为 neutral
- POST /api/emotion/classify {text, use_llm?} -> 规则快路径 + LLM 慢路径分类
     返回 {emotion, intensity, duration_ms, source}（Phase 1 情绪智能化）
- GET  /api/emotion/state     -> 当前情绪 + 最近事件（调试/前端轮询）
"""

import re
from typing import Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from .emotion import get_emotion_tracker, EMOTIONS
from .emotion.emotion_classifier import classify, classify_rule


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

    @router.post("/api/emotion/classify")
    async def classify_emotion(request: Request):
        """规则快路径 + LLM 慢路径情绪分类（Phase 1）。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        text = str(body.get("text") or "").strip()[:1000]
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)
        use_llm = body.get("use_llm", True)
        result = await classify(text) if use_llm else classify_rule(text)
        # 同步全局情绪跟踪器（供表情状态复用）
        get_emotion_tracker().update(result.emotion, result.intensity, source=result.source)
        return result.to_dict()

    @router.get("/api/emotion/state")
    async def emotion_state(request: Request):
        """当前情绪 + 最近事件（调试/前端轮询）。"""
        if not _is_local_request(request):
            return _forbidden()
        return {
            "current": get_emotion_tracker().get_current(),
            "recent": get_emotion_tracker().recent_events(10),
        }

    @router.get("/api/emotion/state-machine")
    async def emotion_state_machine(request: Request, uid: str = ""):
        """情感状态机视图（emotion-machine 卡片）：状态集 + 当前会话情绪 + 衰减配置。"""
        if not _is_local_request(request):
            return _forbidden()
        return get_emotion_tracker().state_machine(uid or None)

    return router
