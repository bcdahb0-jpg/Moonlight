"""控制台聚合 API（P0 接线工程）。

- GET /api/console/overview —— 顶栏 + 概览分区 + 系统性能的全部实时数据。

聚合现有 service 内存态 / conf.yaml / 本地端口探测，全程 fail-soft：单个
子项失败只返回 None，绝不拖垮整个接口、绝不阻塞启动（与 readiness/screen
路由同一风格）。

数据源（均为既有模块，不新开探测线程）：
- 角色 / LLM       ：conf.yaml + llm_config_route 的 block 读取
- VOICEVOX / DeepLX：voicevox_manager / deeplx_manager 的 status()
- 记忆             ：memory_route 的 conf_uid 解析 + memory_v2.stats / _vector_count
- 情感             ：emotion.get_emotion_tracker().get_current()
- 屏幕             ：screen_awareness.metrics + store
- 任务             ：conf.yaml task_platform.enabled + mcp.servers 数量
- 系统             ：psutil（CPU/内存/进程内存）+ 端口探测（前端 5173 / MCP 12394）

注：功能完成度（overview-roadmap）由前端基于 controlData.ts 计算（单一事实源
在数据文件，后端硬编码会漂移），本路由不提供 completion 接口。
"""
from __future__ import annotations

import socket
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden

# 端口探测超时（秒）——概览轮询不应拖慢任何请求。
_PROBE_TIMEOUT = 0.3


# --------------------------------------------------------------------------- #
# 只读辅助（全部 fail-soft）
# --------------------------------------------------------------------------- #

def _read_conf() -> dict:
    """尽力读取 conf.yaml，失败返回 {}（路由自身不依赖配置强校验）。"""
    try:
        from .config_manager.utils import read_yaml  # noqa: PLC0415

        return read_yaml("conf.yaml") or {}
    except Exception:
        return {}


def _port_open(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    """TCP 端口连通探测（0.3s 超时，不抛异常）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_process_stats() -> dict:
    """psutil 采样：系统 CPU/内存 + 本进程内存（MB）。缺 psutil 时返回 None 字段。"""
    try:
        import psutil  # noqa: PLC0415

        vm = psutil.virtual_memory()
        proc = psutil.Process()
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=0.1) or 0, 1),
            "mem_percent": round(getattr(vm, "percent", 0.0) or 0.0, 1),
            "process_mem_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
            "uptime_sec": int(__import__("time").time() - proc.create_time()),
        }
    except Exception:
        return {
            "cpu_percent": None,
            "mem_percent": None,
            "process_mem_mb": None,
            "uptime_sec": None,
        }


def _role_from_conf(data: dict) -> dict:
    cc = (data.get("character_config", {}) or {})
    return {
        "name": str(cc.get("character_name") or "桌宠"),
        "live2d": str(cc.get("live2d_model_name") or ""),
        "conf_uid": str(cc.get("conf_uid") or "default_001"),
    }


def _llm_from_conf(data: dict) -> dict:
    """读对话 LLM 配置块（provider/model/base_url/api_key 掩码），判是否已配置。"""
    try:
        from .llm_config_route import _get_openai_block, _mask_key  # noqa: PLC0415

        block = _get_openai_block(data) or {}
        provider = str(block.get("provider") or "").strip()
        model = str(block.get("model") or "")
        key = str(block.get("llm_api_key") or block.get("api_key") or "")
        base = str(block.get("base_url") or "")
        is_ollama = "ollama" in (base or provider).lower()
        configured = bool(model and (key or base or is_ollama))
        if not provider:
            provider = "Ollama 本地" if is_ollama else "OpenAI 兼容"
        return {
            "provider": provider,
            "model": model or "—",
            "base_url": base,
            "api_key_masked": _mask_key(key) if key else "",
            "is_configured": configured,
        }
    except Exception:
        return {"provider": "—", "model": "—", "is_configured": False}


def _memory_stats() -> dict:
    """记忆条数（核心画像字符数 / 事实 / 反思 / 向量条数）。"""
    out = {"core_chars": None, "facts": None, "reflections": None, "vector_count": None}
    try:
        from .memory_route import _resolve_conf_uid, _vector_count  # noqa: PLC0415
        from . import memory_core, memory_v2  # noqa: PLC0415

        uid, err = _resolve_conf_uid(None)
        if err or not uid:
            return out
        v2 = memory_v2.stats(uid)
        out["core_chars"] = len(memory_core.load_core_memory(uid))
        out["facts"] = v2.get("facts", 0)
        out["reflections"] = v2.get("reflections", 0)
        out["vector_count"] = _vector_count(uid)
    except Exception:
        pass
    return out


def _screen_stats() -> dict:
    """屏幕感知：开关 + 累计指标 + 成本估算。"""
    out = {"enabled": None, "analyze_count": 0, "proactive_count": 0, "cost_usd": None}
    try:
        from .screen_awareness import metrics as metrics_mod  # noqa: PLC0415
        from .screen_awareness.service import get_store  # noqa: PLC0415

        out["enabled"] = get_store().is_enabled()
        snap = metrics_mod.get_metrics().snapshot()
        out["analyze_count"] = snap.get("analyze_count", 0)
        out["proactive_count"] = snap.get("proactive_count", 0)
        out["cost_usd"] = snap.get("estimated_cost_usd", None)
    except Exception:
        pass
    return out


def _task_stats(data: dict) -> dict:
    """任务平台：启用开关 + MCP 已配置服务器数（可用数受已知遗留影响，如实显示）。"""
    tp = (data.get("task_platform", {}) or {})
    servers = ((tp.get("mcp", {}) or {}).get("servers") or [])
    enabled_list = [s for s in servers if s.get("enabled")]
    return {
        "enabled": bool(tp.get("enabled", False)),
        "mcp_configured": len(servers),
        "mcp_enabled": len(enabled_list),
    }


def _emotion_stats() -> dict:
    try:
        from .emotion import get_emotion_tracker  # noqa: PLC0415

        cur = get_emotion_tracker().get_current()
        return {
            "emotion": str(cur.get("emotion") or "neutral"),
            "confidence": float(cur.get("confidence") or 0.0),
        }
    except Exception:
        return {"emotion": None, "confidence": None}


def _engine_stats() -> dict:
    """VOICEVOX / DeepLX 引擎状态（本地端口探测 + 管理器状态）。"""
    out = {"voicevox": {"state": None, "running": False}, "deeplx": {"running": False}}
    try:
        from .voicevox_manager import VoiceVoxManager  # noqa: PLC0415

        vv = VoiceVoxManager().status()
        out["voicevox"] = {
            "state": vv.get("state", "missing"),
            "running": bool(vv.get("running", False)),
            "progress": vv.get("progress", 0),
        }
    except Exception:
        pass
    try:
        from .deeplx_manager import DeeplxManager  # noqa: PLC0415

        dl = DeeplxManager.status()
        out["deeplx"] = {
            "running": bool(dl.get("running", False)),
            "rate_limited": bool(dl.get("rate_limited", False)),
            "port": dl.get("port", 1188),
        }
    except Exception:
        out["deeplx"] = {"running": False, "rate_limited": False, "port": 1188}
    return out


def _build_overview() -> dict:
    data = _read_conf()
    mem = _memory_stats()
    engine = _engine_stats()
    task = _task_stats(data)
    return {
        "role": _role_from_conf(data),
        "llm": _llm_from_conf(data),
        "engines": engine,
        "memory": mem,
        "emotion": _emotion_stats(),
        "screen": _screen_stats(),
        "task": {"enabled": task["enabled"]},
        "mcp": {
            "configured": task["mcp_configured"],
            "enabled": task["mcp_enabled"],
        },
        "system": {
            "backend_online": True,  # 本接口被请求即在线
            "frontend_online": _port_open("127.0.0.1", 5173),
            "mcp_port_open": _port_open("127.0.0.1", 12394),
            **_probe_process_stats(),
        },
    }


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_console_route() -> APIRouter:
    """GET /api/console/overview（localhost-only）。"""
    router = APIRouter()

    @router.get("/api/console/overview")
    async def console_overview(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            return JSONResponse(_build_overview())
        except Exception as e:  # 兜底：聚合接口绝不允许 500
            logger.error(f"console overview failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=200, content={"error": "aggregate failed"})

    return router
