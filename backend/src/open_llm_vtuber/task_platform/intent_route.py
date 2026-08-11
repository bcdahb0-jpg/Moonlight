"""intent_route.py — 意图路由（方案 P1：消灭手动二选一）。

`POST /api/intent/classify`：把用户输入分类为 `chat`（闲聊，人设聊天链路）
或 `task`（工程指令，任务内核链路）。默认 `✨auto` 模式调用；显式锁
chat/task 时前端跳过本接口。

实现（分级 fail-soft，零新增配置）：
1. **规则预判**：关键词命中（工程动词/文件/代码/脚本/目录等）→ task；
   问候/情感/闲聊词 → chat。规则结果置信度高时直接返回（不耗 LLM）。
2. **LLM 兜底**：规则未命中 → 复用任务主模型（conf_bridge + llm_adapter）
   单次调用分类，输出 JSON `{kind, reason}`。
3. **彻底失败**：返回 `{kind: 'chat'}`（聊天链路对误判更安全——误判为任务
   会触发真实执行，误判为聊天只是多聊一句）。

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

#: 强动作词（词面含明确执行语义，命中即 task）。注意只放「动词/动词短语」，
#: 纯名词（文件/目录/代码…）放 _TASK_NOUN_KEYWORDS —— 否则「本目录下有什么文件？」
#: 这种查询句会被误判成任务（2026-08-09 修复：截图反馈"简单介绍一下"被当任务执行）。
_ACTION_KEYWORDS: tuple[str, ...] = (
    "重构", "写个", "写一个", "写一个脚本", "写脚本",
    "改一下", "修改", "修复", "新建", "创建", "整理", "清理", "移动", "复制",
    "删除", "重命名", "编译", "构建", "运行", "测试", "部署", "发布", "搭建",
    "下载", "上传", "抓取", "爬",
    # 2026-08-10：「做/做一个/做个」——番茄钟截图实测「帮我做一个番茄钟」LLM 分类
    # 不稳定（偶发回退 chat → delegate 链路 → 任务卡沉底无锚点）。祈使式做任务
    # 指令稳定判 task；裸「做」不加（防「怎么做/应该怎么做」查询句误判）。
    "做一个", "做个", "帮我做", "给我做", "帮我做一个", "帮我做个", "帮我整一个",
    "整一个", "生成一个",
    # 2026-08-09：用户反馈"不能使用工具"——闲聊时 AI 编造搜索结果。
    # 含「搜索/查证/检索」语义（包括网上搜索、上网查、帮我查/搜）一律走任务内核，
    # 任务内核 bash 可真去 curl/检索，不再让 basic_memory_agent 编造。
    "搜索", "搜一下", "搜搜", "网上搜索", "上网查", "网上查",
    "查证", "核实", "确认一下", "查一下", "查查",
    "帮我搜", "帮我查", "检索",
    "搜索文件", "找文件", "查文件",
    "git", "commit", "push", "pull", "merge",
    "封装", "拆分", "合并", "优化", "安装", "升级",
)

#: 工程名词（对象词）：单独出现**不**判 task（可能是询问/闲聊），
#: 仅当句子无强动作词且非疑问/查询式时才判 task。
_NOUN_KEYWORDS: tuple[str, ...] = (
    "脚本", "代码", "接口", "目录", "文件夹", "文件", "数据库", "分支",
    "日志", "报错", "错误", "性能", "依赖",
    "接口文档", "README",
    ".py", ".js", ".ts", ".json", ".yaml", ".md",
)

#: 疑问/查询式短语：无强动作词时命中即 chat（"有什么文件""介绍一下"= 询问，
#: 不是执行指令；让聊天链路直接回答，必要时由 delegate_to_task 工具真实查证）。
_QUERY_KEYWORDS: tuple[str, ...] = (
    "什么", "哪些", "有哪些", "是什么", "什么样", "怎么样", "怎样", "怎么",
    "如何", "为啥", "为什么", "干嘛", "吗", "哪", "几个", "多少",
    "介绍一下", "介绍", "说说", "了解",
)

#: 闲聊关键词（命中即 chat，覆盖问候/情感/日常）
_CHAT_KEYWORDS: tuple[str, ...] = (
    "你好", "嗨", "哈喽", "早安", "晚安", "再见", "拜拜", "谢谢", "辛苦了",
    "心情", "难过", "开心", "生气", "无聊", "想你", "爱你", "抱抱",
    "天气", "吃什么", "吃饭", "饿", "睡觉", "起床", "累", "困", "今天", "周末",
    "电影", "音乐", "笑话", "故事", "在吗", "聊聊", "陪我",
)

_ACTION_RE = re.compile("|".join(re.escape(k) for k in _ACTION_KEYWORDS))
_NOUN_RE = re.compile("|".join(re.escape(k) for k in _NOUN_KEYWORDS))
#: 问号（全半角）单独作为查询特征；`吗` 已在 _QUERY_KEYWORDS。
_QUERY_RE = re.compile("|".join(re.escape(k) for k in _QUERY_KEYWORDS) + r"|[?？]")
_CHAT_RE = re.compile("|".join(re.escape(k) for k in _CHAT_KEYWORDS))

#: LLM 分类超时（秒）：分类只是路由，不值得卡住发送链路。超时直接回退 chat
#: （聊天链路比误触任务安全）。前端已改为「先回显、后台分类」，但后端仍需防挂死。
_LLM_CLASSIFY_TIMEOUT = 3.0

_LLM_CLASSIFY_PROMPT = """你是输入分类器。判断用户消息是「闲聊」还是「工程任务」。
- 工程任务：涉及代码/文件/脚本/命令/系统操作/数据处理的明确**执行指令**，通常含动作动词（写/改/修/建/跑/删/查…）和对象（文件/接口/目录/脚本…）。
- 闲聊：问候、情感、日常话题、观点讨论、陪伴需求，无明确可执行对象。
- 特别注意：单纯**询问/介绍/列举**（如"本目录下有什么文件？""介绍一下XX""这段代码什么意思"）属闲聊，不是任务——任务必须有"去做某事"的执行意图，而非"问一下/看一下"。
只输出 JSON：{"kind": "chat" | "task", "reason": "一句话理由（中文，≤20字）"}"""


def _rule_classify(text: str) -> str | None:
    """规则预判：返回 'chat' | 'task' | None（未命中走 LLM）。

    优先级：强动作词 → task；疑问/查询式 → chat；工程名词 → task；闲聊词 → chat。
    强动作词先于疑问句：真正的执行指令即使带问号（"怎么写脚本？"）
    也不会被查询式误吞——那类指令词面有明确执行动词。
    """
    if _ACTION_RE.search(text):
        return "task"
    if _QUERY_RE.search(text):
        return "chat"
    if _NOUN_RE.search(text):
        return "task"
    if _CHAT_RE.search(text):
        return "chat"
    return None


async def _llm_classify(
    text: str,
    *,
    cfg: conf_bridge.TaskPlatformConfig | None = None,
    model: Any | None = None,
) -> dict[str, Any]:
    """LLM 分类兜底（复用任务主模型；失败抛错由调用方 fail-soft）。"""
    cfg = cfg or conf_bridge.task_config()
    model = model or llm_adapter.build_chat_model(cfg)
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
    """公开入口：规则 → LLM → 兜底 chat。返回 {kind, confidence, reason, source}。"""
    text = (text or "").strip()
    if not text:
        return {"kind": "chat", "confidence": 1.0, "reason": "空输入", "source": "empty"}

    rule = _rule_classify(text)
    if rule is not None:
        return {
            "kind": rule,
            "confidence": 0.9,
            "reason": "关键词命中" if rule == "task" else "闲聊特征",
            "source": "rule",
        }

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
