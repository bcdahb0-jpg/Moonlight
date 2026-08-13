"""intent_route.py — 意图路由（方案 P1：消灭手动二选一）。

`POST /api/intent/classify`：把用户输入分类为 `chat`（闲聊，人设聊天链路）
或 `task`（工程指令，任务内核链路）。默认 `✨auto` 模式调用；显式锁
chat/task 时前端跳过本接口。

实现（2026-08-13 起 LLM 全量判别，fail-soft，零新增配置）：
1. **LLM 判别**：复用任务主模型（conf_bridge + llm_adapter）单次调用分类，
   输出 JSON `{kind, reason}`。实例按 (base_url, api_key, model) 缓存复用。
2. **彻底失败**：返回 `{kind: 'chat'}`（聊天链路对误判更安全——误判为任务
   会触发真实执行，误判为聊天只是多聊一句）。

历史：v1 曾用「正则规则预判（关键词五级优先级）→ LLM 兜底」。实测规则
存在系统性盲区（软询问动词/纯名词询问/否定句/试探句误判 chat→task），
2026-08-13 按用户决策移除规则层，改 LLM 全量判别；高频误判模式已在
prompt 中显式列举（见 _LLM_CLASSIFY_PROMPT「特别注意」）。

单向依赖：intent_route.py ← server.py；复用 conf_bridge/llm_adapter，
不 import conversations/memory/mcp。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from starlette.responses import JSONResponse

from . import conf_bridge, llm_adapter
from ..llm_config_route import _is_local_request, _forbidden

#: LLM 分类超时（秒）：分类只是路由，不值得卡住发送链路。超时直接回退 chat
#: （聊天链路比误触任务安全）。前端已改为「先回显、后台分类」，故 5s 足够：
#: 热调用实测 ~2s，首次冷启动（建连接+TLS）实测 ~10s——超时后下一条消息
#: 已命中 _MODEL_CACHE 热路径，不会持续失败。
_LLM_CLASSIFY_TIMEOUT = 5.0

_LLM_CLASSIFY_PROMPT = """你是输入分类器。判断用户消息是「闲聊」还是「工程任务」。
- 工程任务：涉及代码/文件/脚本/命令/系统操作/数据处理的明确**执行指令**，通常含动作动词（写/改/修/建/跑/删/查…）和对象（文件/接口/目录/脚本…）。
- 闲聊：问候、情感、日常话题、观点讨论、陪伴需求，无明确可执行对象。
- 特别注意（高频误判，务必避开）：
  1. 单纯**询问/介绍/列举**（如"本目录下有什么文件？""介绍一下XX""这段代码什么意思"）属闲聊，不是任务——任务必须有"去做某事"的执行意图，而非"问一下/看一下"；
  2. **否定/禁止句**（如"不要删除这个文件""别改了"）不是执行指令，属闲聊；
  3. **试探性询问**（如"测试一下你会不会生气"）属闲聊，不是任务；
  4. 仅**谈论对象的内容/含义**（如"解释一下这段代码""这个文件的内容"）属闲聊。
只输出 JSON：{"kind": "chat" | "task", "reason": "一句话理由（中文，≤20字）"}"""

#: ChatOpenAI 实例缓存（keyed by (base_url, api_key, model)）——LLM 全量判别下
#: 每条消息都调，避免反复重建客户端。角色切换后三元组变化自动换新实例。
_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


def _cached_model(cfg: conf_bridge.TaskPlatformConfig) -> Any:
    key = (cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = llm_adapter.build_chat_model(cfg)
        _MODEL_CACHE[key] = model
    return model


async def _llm_classify(
    text: str,
    *,
    cfg: conf_bridge.TaskPlatformConfig | None = None,
    model: Any | None = None,
) -> dict[str, Any]:
    """LLM 判别（复用任务主模型；失败抛错由调用方 fail-soft）。"""
    cfg = cfg or conf_bridge.task_config()
    model = model or _cached_model(cfg)
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = await model.ainvoke(
        [
            SystemMessage(content=_LLM_CLASSIFY_PROMPT),
            HumanMessage(content=f"用户消息：{text[:200]}"),
        ]
    )
    raw = str(getattr(resp, "content", "") or "").strip()
    # 提取 JSON（容忍 ```json 围栏 / 前后缀）
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"classify 响应无 JSON: {raw[:80]!r}")
    data = json.loads(m.group(0))
    kind = str(data.get("kind") or "").strip()
    if kind not in ("chat", "task"):
        raise ValueError(f"classify 非法 kind: {kind!r}")
    return {
        "kind": kind,
        "confidence": 0.7,
        "reason": str(data.get("reason") or "")[:50],
    }


async def classify_text(
    text: str,
    *,
    cfg: conf_bridge.TaskPlatformConfig | None = None,
    model: Any | None = None,
) -> dict[str, Any]:
    """公开入口：LLM 判别 → 兜底 chat。返回 {kind, confidence, reason, source}。"""
    text = (text or "").strip()
    if not text:
        return {"kind": "chat", "confidence": 1.0, "reason": "空输入", "source": "empty"}

    try:
        res = await asyncio.wait_for(
            _llm_classify(text, cfg=cfg, model=model),
            timeout=_LLM_CLASSIFY_TIMEOUT,
        )
        res["source"] = "llm"
        return res
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"[intent] LLM 分类失败（回退 chat）：{e}")
        return {"kind": "chat", "confidence": 0.5, "reason": "分类不可用", "source": "fallback"}


def init_intent_route() -> APIRouter:
    router = APIRouter()

    @router.post("/api/intent/classify")
    async def classify_route(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        text = str(body.get("text") or "")
        try:
            result = await classify_text(text)
        except Exception as e:  # 兜底：分类失败绝不阻塞发送
            logger.warning(f"[intent] classify failed（回退 chat）：{e}")
            result = {"kind": "chat", "confidence": 0.5, "reason": "分类不可用", "source": "fallback"}
        return JSONResponse({"ok": True, **result})

    return router
