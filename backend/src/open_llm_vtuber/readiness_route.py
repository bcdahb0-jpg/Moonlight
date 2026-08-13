"""就绪度检查 API（Phase 3：Onboarding 第 2 步数据源）。

GET /api/readiness —— 四项后端可判定的检查（Live2D 模型 / LLM 连通 / ASR 引擎 /
Ollama 探测）+ ready 汇总。麦克风权限属于前端能力（navigator.mediaDevices），
由 Onboarding 向导在前端检测，不在此 API 内。

判定逻辑在 layers/domain/readiness.py（纯逻辑），本路由仅注入检查器实现。
"""
from typing import Any, Callable

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .service_context import ServiceContext
from .layers.domain.readiness import all_passed, check_async, to_message
from .llm_config_route import _forbidden, _is_local_request, _probe_ollama_models


def init_readiness_route(default_context_cache: ServiceContext) -> APIRouter:
    """GET /api/readiness（localhost-only）。"""
    router = APIRouter()

    async def _check_live2d() -> tuple[bool, str]:
        model = default_context_cache.live2d_model
        if model is None or not getattr(model, "model_info", None):
            return False, "未加载 Live2D 模型，请检查 live2d-models/ 目录"
        return True, "Live2D 模型已加载"

    async def _check_asr() -> tuple[bool, str]:
        if default_context_cache.asr_engine is None:
            return False, "语音识别引擎未加载，首次使用需联网下载 ASR 模型"
        return True, "语音识别引擎就绪"

    async def _check_llm() -> tuple[bool, str]:
        if default_context_cache.agent_engine is None:
            return False, "对话模型未配置，请到设置→高级→对话大脑完成 LLM 配置"
        from .llm_config_route import _get_openai_block

        try:
            block = _get_openai_block(
                (await _load_conf_safe()) or {}
            )
        except Exception:
            block = None
        if block is None:
            return True, "对话模型已就绪"
        from .llm_config_route import _is_configured

        if await _is_configured(block):
            return True, "对话模型已配置且连通"
        return False, "对话模型配置不完整（API Key 缺失或 Ollama 未运行）"

    async def _check_ollama() -> tuple[bool, str]:
        probe = await _probe_ollama_models()
        if probe.get("available"):
            return True, "检测到 Ollama，可本地运行模型"
        return False, "未检测到 Ollama（本地对话需要；使用云端 API 可忽略）"

    async def _ollama_required() -> bool:
        """当前 LLM endpoint 指向 Ollama 时，才把 Ollama 作为必需项。"""
        data = await _load_conf_safe()
        try:
            from .llm_config_route import _get_openai_block

            block = _get_openai_block(data or {}) or {}
            base_url = str(block.get("base_url") or "").lower()
            provider = str(block.get("provider") or "").lower()
            return (
                "ollama" in provider
                or ":11434" in base_url
                or "localhost:11434" in base_url
            )
        except Exception:
            return False

    @router.get("/api/readiness")
    async def get_readiness(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            checks: dict[str, Callable[[], Any]] = {
                "live2d": _check_live2d,
                "llm": _check_llm,
                "asr_model": _check_asr,
                "ollama": _check_ollama,
            }
            results = await check_async(checks)
            ollama_required = await _ollama_required()
            for result in results:
                result["required"] = result["id"] != "ollama" or ollama_required
            return JSONResponse({"ready": all_passed(results), "checks": results})
        except Exception as e:
            logger.error(f"readiness check failed: {type(e).__name__}: {e}")
            return JSONResponse(
                status_code=500,
                content={"ready": False, "checks": [], "error": "readiness check failed"},
            )

    return router


async def _load_conf_safe() -> dict | None:
    """尽力读取 conf.yaml（llm-config 探测用），失败返回 None。"""
    from .llm_config_route import _load_conf

    try:
        return _load_conf()
    except Exception:
        return None
