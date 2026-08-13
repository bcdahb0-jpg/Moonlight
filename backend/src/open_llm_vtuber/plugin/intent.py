"""plugin/intent.py — 意图与情绪识别（P5 插件生态，AI-Desktop-Pet analyze_intent 思路）。

- `analyze_intent(text)`：轻量 LLM 输出 `{intent, emotion}`——
  intent ∈ {chat, silence, task}（勿扰/聊天/工程指令），emotion 复用
  emotion 模块情绪集。复用 task_platform graph.build_model（同
  playmate/events.py::classify_event 模式），LLM 不可用 → 规则兜底
  `{intent: chat, emotion: neutral}`（对聊天链路最安全）。
- 规则预判（零成本）：勿扰关键词（别烦/安静/忙/勿扰/闭嘴/走开）→ silence；
  工程动词（写/改/删/运行/部署/修复/报错/代码）→ task。
- `intent_config`：conf system_config.intent（enabled/model 选择）。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from loguru import logger

#: 勿扰关键词（中文为主，命中即 silence）。
_SILENCE_WORDS = (
    "别烦", "别吵", "安静", "勿扰", "忙", "闭嘴", "走开", "滚", "停一下",
    "别说话", "没空", "忙死了", "我在忙",
)
#: 工程指令关键词（命中即 task）。
_TASK_WORDS = (
    "写个", "写一", "改一下", "帮我写", "运行", "执行", "部署", "修复",
    "报错", "代码", "脚本", "文件", "目录", "建一个", "创建", "删除",
)

_EMOTIONS = (
    "neutral", "joy", "anger", "sadness", "surprise", "fear", "disgust",
)

#: LLM prompt（保持极简，强制 JSON，输出只有两个键）。
_PROMPT = """你是意图分析器。分析用户对 AI 桌宠说的话，输出 JSON（只含两个键）：
- "intent": "silence"（用户表示勿扰/忙/不想被打扰）| "chat"（普通闲聊）| "task"（工程/任务指令）
- "emotion": 从 [neutral, joy, anger, sadness, surprise, fear, disgust] 选一个

示例：
用户：别烦我 → {"intent": "silence", "emotion": "anger"}
用户：今天天气真好 → {"intent": "chat", "emotion": "joy"}
用户：帮我写个 Python 脚本 → {"intent": "task", "emotion": "neutral"}
只输出 JSON。"""


def _rule_intent(text: str) -> Optional[str]:
    if any(w in text for w in _SILENCE_WORDS):
        return "silence"
    if any(w in text for w in _TASK_WORDS):
        return "task"
    return None


def _parse_llm_output(raw: str) -> Optional[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "chat")
    if intent not in ("chat", "silence", "task"):
        intent = "chat"
    emotion = str(data.get("emotion") or "neutral")
    if emotion not in _EMOTIONS:
        emotion = "neutral"
    return {"intent": intent, "emotion": emotion}


async def analyze_intent(text: str, model_name: str = "") -> dict:
    """意图分析：规则预判 → LLM 兜底 → neutral 兜底（绝不抛错）。"""
    rule = _rule_intent(text)
    if rule is not None:
        emotion = "anger" if rule == "silence" else "neutral"
        return {"intent": rule, "emotion": emotion, "source": "rule"}

    try:
        from ..task_platform import conf_bridge, graph  # noqa: PLC0415
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        model = graph.build_model(conf_bridge.task_config())
        raw = await asyncio.wait_for(
            model.ainvoke(
                [SystemMessage(content=_PROMPT), HumanMessage(content=text)]
            ),
            timeout=10.0,
        )
        content = getattr(raw, "content", None) or str(raw)
        parsed = _parse_llm_output(content)
        if parsed:
            parsed["source"] = "llm"
            return parsed
        logger.debug(f"intent: LLM 输出无法解析: {str(content)[:80]}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"intent: LLM 不可用（{e}），规则兜底")
    return {"intent": "chat", "emotion": "neutral", "source": "fallback"}


def intent_config() -> dict:
    """conf system_config.intent 读取（缺省回退）。"""
    try:
        from ..translator_route import read_yaml, CONF_PATH  # noqa: PLC0415

        conf = read_yaml(CONF_PATH) or {}
        block = (conf.get("system_config") or {}).get("intent") or {}
        return {
            "enabled": bool(block.get("enabled", True)),
            "model": str(block.get("model") or "auto"),
        }
    except Exception:
        return {"enabled": True, "model": "auto"}


__all__: list[str] = ["analyze_intent", "intent_config"]
