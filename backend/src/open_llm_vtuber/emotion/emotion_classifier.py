"""情绪分类：规则快路径 + LLM 慢路径增强。

Phase 1（docs/live2d-facial-expression-plan.md）：
- 快路径：emotion_analyzer 的加权关键词规则（零延迟、零外部依赖）
- 慢路径：规则未命中或低置信时，用任务主模型（graph.build_model）做一次
  LLM 情绪分类，输出 {emotion, intensity, duration_ms}，白名单校验后返回；
  LLM 失败/超时/解析失败 → 回退规则结果（fail-soft，绝不抛异常）

参考：
- reference/soullink-emotion-sdk/packages/planner-openai（LLM 输出情绪+动作规划）
- reference/SoulLink_Live2D/l2dagent.py（LLM 直接输出表情参数）
本实现只做「情绪 + 强度 + 时长」三要素，动作/表情映射交给前端引擎。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from .emotion_analyzer import EMOTIONS, EmotionAnalyzer

# 规则快路径的置信度门槛：≥ 此值直接返回（零延迟优先）
_RULE_FAST_THRESHOLD = 0.55
# LLM 调用超时（毫秒/秒）：3s，避免拖慢链路
_LLM_TIMEOUT_SEC = 3.0
# LLM 结果的合理范围
_INTENSITY_MIN, _INTENSITY_MAX = 0.0, 1.0
_DURATION_MIN, _DURATION_MAX = 500, 30000
# 规则结果的默认情绪时长（ms）：说话一句的正常表情驻留
_RULE_DURATION_MS = 4000


@dataclass
class ClassifiedEmotion:
    emotion: str = "neutral"
    intensity: float = 0.5
    duration_ms: int = _RULE_DURATION_MS
    source: str = "rule"  # "rule" | "llm"
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "intensity": round(max(_INTENSITY_MIN, min(_INTENSITY_MAX, self.intensity)), 3),
            "duration_ms": int(
                max(_DURATION_MIN, min(_DURATION_MAX, self.duration_ms))
            ),
            "source": self.source,
            "matched_keywords": self.matched_keywords[:5],
        }


_analyzer: Optional[EmotionAnalyzer] = None


def _get_analyzer() -> EmotionAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = EmotionAnalyzer()
    return _analyzer


def classify_rule(text: str) -> ClassifiedEmotion:
    """规则快路径：加权关键词评分。"""
    if not text or not text.strip():
        return ClassifiedEmotion("neutral", 0.5, _RULE_DURATION_MS, "rule")
    res = _get_analyzer().analyze(text)
    return ClassifiedEmotion(
        emotion=res.emotion,
        intensity=res.confidence,
        duration_ms=_RULE_DURATION_MS,
        source="rule",
        matched_keywords=list(res.matched_keywords),
    )


async def classify_llm(text: str, timeout_sec: float = _LLM_TIMEOUT_SEC) -> Optional[ClassifiedEmotion]:
    """LLM 慢路径：任务主模型输出 {emotion, intensity, duration_ms}。

    失败返回 None（调用方回退规则结果）。惰性 import task_platform 避免循环依赖。
    """
    try:
        from ..task_platform import conf_bridge, graph

        cfg = conf_bridge.task_config()
        model = graph.build_model(cfg)
    except Exception as e:
        logger.debug(f"[emotion-classify] LLM 构建失败，跳过慢路径: {type(e).__name__}")
        return None

    prompt = (
        "分析下面这句角色台词的情绪。只输出一个 JSON 对象，不要任何其他文字。\n"
        '格式：{"emotion": "<情绪>", "intensity": 0.0到1.0的浮点数, '
        '"duration_ms": 3000到15000的整数(表情预计持续毫秒数)}\n'
        f"可选情绪（只许用这些）：{', '.join(EMOTIONS)}\n"
        f"台词：{text[:300]}"
    )
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=prompt), HumanMessage(content="")]),
            timeout=timeout_sec,
        )
        raw = str(getattr(resp, "content", "") or "").strip()
        parsed = _parse_llm_json(raw)
        if not parsed:
            return None
        emotion = str(parsed.get("emotion") or "").lower()
        if emotion not in EMOTIONS:
            return None
        return ClassifiedEmotion(
            emotion=emotion,
            intensity=_clamp(parsed.get("intensity"), 0.0, 1.0, 0.5),
            duration_ms=int(_clamp(parsed.get("duration_ms"), _DURATION_MIN, _DURATION_MAX, _RULE_DURATION_MS)),
            source="llm",
        )
    except asyncio.TimeoutError:
        logger.debug("[emotion-classify] LLM 超时，回退规则")
        return None
    except Exception as e:
        logger.debug(f"[emotion-classify] LLM 失败: {type(e).__name__}: {e}")
        return None


async def classify(text: str) -> ClassifiedEmotion:
    """规则快路径 → 低置信才走 LLM 慢路径 → 失败回退规则。

    纯 async 版本（路由/句子流用）。
    """
    rule = classify_rule(text)
    if rule.emotion != "neutral" and rule.intensity >= _RULE_FAST_THRESHOLD:
        return rule
    if not text or not text.strip():
        return rule
    llm = await classify_llm(text)
    if llm is not None and llm.emotion != "neutral":
        return llm
    return rule


def _parse_llm_json(raw: str) -> Optional[dict]:
    """解析 LLM 输出中的 JSON 对象（容忍 ```json 围栏 / 前后缀文字）。"""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp(value: object, lo: float, hi: float, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


# 供 tts_manager / 句子流使用的同步指纹工具
def text_fingerprint(text: str) -> str:
    t = (text or "").strip()
    return f"{t[:40]}|{len(t)}"
