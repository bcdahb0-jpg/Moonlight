"""轻量关键词情绪分类器。

参考 Soul-of-Waifu 的 28 情绪体系【GPL v3，仅借鉴思路，全部原创实现】，
结合 warashi 已有的 LLM expressions 标签机制，作为其补充：
当 AI 回复没有明确表情标签时，用规则分类器推断情绪，驱动前端表情与情绪显示。

情绪集采 Plutchik 轮盘 28 维（SoW 同源分类），另保留 Moonlight 自有的
``affection``（爱慕/亲昵）以兼容既有 Live2D 映射。

纯 stdlib，无外部依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# 情绪集：SoW 28 情绪 + 保留 affection（Moonlight 自有）。与前端表情映射表对应。
EMOTIONS: Tuple[str, ...] = (
    # 既有（Moonlight 原有，保留）
    "joy",
    "amusement",
    "affection",
    "surprise",
    "confusion",
    "sad",
    "anger",
    "fear",
    "gratitude",
    "neutral",
    # 新增（SoW 28 情绪体系补齐）
    "admiration",
    "annoyance",
    "approval",
    "caring",
    "curiosity",
    "desire",
    "disappointment",
    "disapproval",
    "disgust",
    "embarrassment",
    "excitement",
    "grief",
    "love",
    "nervousness",
    "optimism",
    "pride",
    "realization",
    "relief",
    "remorse",
)

# 每个情绪的触发关键词（中英混合），权重表示强度
_RULES: Dict[str, List[Tuple[str, int]]] = {
    # ---- 既有规则（保留） ----
    "joy": [
        ("哈哈", 3), ("哈哈哈", 4), ("嘿嘿", 2), ("嘻嘻", 2), ("太好了", 3),
        ("真开心", 3), ("开心", 2), ("喜欢", 2), ("好耶", 3), ("耶", 2),
        ("万岁", 3), ("lol", 2), ("haha", 2), ("😄", 3), ("😊", 2), ("❤", 2),
    ],
    "amusement": [
        ("搞笑", 3), ("好笑", 3), ("有趣", 2), ("逗", 2), ("有意思", 2),
        ("笑死", 4), ("笑死我了", 4), ("wow", 2), ("funny", 3),
    ],
    "affection": [
        ("喜欢你", 4), ("爱你", 4), ("想你", 3), ("抱抱", 3), ("亲亲", 3),
        ("摸摸", 2), ("心疼", 3), ("陪着你", 3), ("永远", 2), ("在乎", 3),
        ("宝贝", 3), ("亲爱的", 2),
    ],
    "surprise": [
        ("哇", 2), ("居然", 3), ("没想到", 3), ("真的吗", 2), ("难以置信", 3),
        ("惊讶", 3), ("吓一跳", 3), ("omg", 3), ("wow", 2), ("咦", 2),
    ],
    "confusion": [
        ("不懂", 3), ("不明白", 3), ("搞不懂", 3), ("什么意思", 2), ("困惑", 3),
        ("迷茫", 3), ("奇怪", 2), ("hmm", 2), ("嗯？", 2), ("为啥", 2), ("为什么", 1),
    ],
    "sad": [
        ("呜呜", 3), ("难过", 3), ("伤心", 3), ("失落", 3), ("孤独", 3),
        ("想哭", 3), ("哭", 2), ("遗憾", 2), ("可惜", 2), ("痛苦", 3),
        ("寂寞", 3), ("(:", 2), ("so sad", 3),
    ],
    "anger": [
        ("生气", 3), ("可恶", 3), ("讨厌", 3), ("烦", 2), ("恼火", 3),
        ("愤怒", 3), ("气死", 4), ("受够了", 3), ("过分", 2), ("😠", 3), ("😡", 4),
    ],
    "fear": [
        ("害怕", 3), ("恐惧", 3), ("好怕", 3), ("担心", 2), ("不安", 3),
        ("危险", 2), ("救命", 3), ("别吓我", 3), ("😱", 3),
    ],
    "gratitude": [
        ("谢谢", 3), ("感谢", 3), ("辛苦", 2), ("多亏", 3), ("感恩", 3),
        ("太感谢", 3), ("thx", 2), ("thanks", 2), ("thank you", 3),
    ],
    # ---- 新增（SoW 28 情绪体系） ----
    "admiration": [
        ("好帅", 3), ("好棒", 3), ("了不起", 3), ("厉害", 2), ("敬佩", 3),
        ("崇拜", 3), ("佩服", 3), ("太强了", 3), ("awesome", 2),
    ],
    "annoyance": [
        ("烦死了", 3), ("真烦", 3), ("吵死了", 3), ("受够了", 3), ("别闹", 2),
        ("啰嗦", 3), ("闭嘴", 2), ("annoying", 2),
    ],
    "approval": [
        ("同意", 2), ("赞成", 3), ("说得对", 3), ("好主意", 3), ("支持", 2),
        ("没问题", 2), ("ok", 1), ("好的", 1),
    ],
    "caring": [
        ("照顾好自己", 3), ("多喝热水", 3), ("注意休息", 3), ("别累着", 3),
        ("我来帮你", 2), ("没事的", 2), ("有我在", 3),
    ],
    "curiosity": [
        ("好奇", 3), ("想知道", 2), ("怎么回事", 2), ("然后呢", 2), ("为啥会", 2),
        ("真的假的", 2), ("有意思诶", 2), ("告诉我", 1),
    ],
    "desire": [
        ("想要", 3), ("好想", 3), ("渴望", 3), ("想要你", 4), ("馋", 2),
        ("忍不住", 2), ("想要更多", 3),
    ],
    "disappointment": [
        ("失望", 3), ("真可惜", 3), ("白高兴", 3), ("白费", 2), ("没戏了", 3),
        ("唉", 2), ("算了", 2), ("失望透顶", 4),
    ],
    "disapproval": [
        ("不行", 2), ("反对", 3), ("不应该", 2), ("太过分了", 3), ("不可以", 2),
        ("不同意", 3), ("拒绝", 2),
    ],
    "disgust": [
        ("恶心", 3), ("好脏", 3), ("受不了", 3), ("倒胃口", 3), ("yuck", 3),
        ("噁", 3), ("嫌弃", 3), ("🤢", 3),
    ],
    "embarrassment": [
        ("害羞", 3), ("不好意思", 3), ("丢脸", 3), ("好丢人", 3), ("难为情", 3),
        ("尴尬", 3), ("脸红", 2), ("好羞", 3),
    ],
    "excitement": [
        ("超兴奋", 4), ("好激动", 3), ("太期待了", 3), ("终于", 2), ("万岁", 3),
        ("爽", 2), ("太棒了", 3), ("冲鸭", 3), ("yay", 3),
    ],
    "grief": [
        ("好难过", 3), ("心碎", 3), ("崩溃", 3), ("失去", 2), ("再也", 2),
        ("想哭", 3), ("好痛", 3), ("悲伤", 3), ("悼念", 3),
    ],
    "love": [
        ("我爱你", 4), ("永远爱你", 4), ("最喜欢你", 4), ("想你了", 3),
        ("你是我的", 3), ("恋人", 2), ("结婚", 3), ("♥", 2), ("爱你一万年", 4),
    ],
    "nervousness": [
        ("紧张", 3), ("忐忑", 3), ("心慌", 3), ("坐立不安", 3), ("压力大", 2),
        ("焦虑", 3), ("心跳加速", 2), ("nervous", 3),
    ],
    "optimism": [
        ("会好的", 3), ("没问题", 2), ("有信心", 3), ("一定能行", 3), ("乐观", 3),
        ("加油", 2), ("明天会更好", 3),
    ],
    "pride": [
        ("骄傲", 3), ("自豪", 3), ("我赢了", 3), ("厉害吧", 3), ("不愧是我", 3),
        ("超有成就感", 3), ("得意", 2), ("proud", 3),
    ],
    "realization": [
        ("原来如此", 3), ("明白了", 3), ("懂了", 3), ("恍然大悟", 4), ("原来", 2),
        ("啊对", 2), ("想通了", 3), ("get it", 2),
    ],
    "relief": [
        ("松了口气", 3), ("终于放心", 3), ("还好", 2), ("虚惊一场", 3),
        ("如释重负", 3), ("好险", 2), ("总算", 2), ("phew", 3),
    ],
    "remorse": [
        ("对不起", 3), ("抱歉", 3), ("我错了", 3), ("懊悔", 3), ("后悔", 3),
        ("内疚", 3), ("良心不安", 3), ("sorry", 3),
    ],
}

# 反触发词：当出现时降低对应情绪分数（简单否定处理）
_NEGATIONS = ("不", "没", "别", "无", "并非", "不是", "不会")


@dataclass
class EmotionResult:
    emotion: str
    confidence: float
    matched_keywords: List[str]

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "confidence": round(self.confidence, 3),
            "matched_keywords": self.matched_keywords[:5],
        }


class EmotionAnalyzer:
    """基于加权关键词评分的情绪分类器。"""

    def __init__(self, negations: Tuple[str, ...] = _NEGATIONS) -> None:
        self._negations = negations

    def analyze(self, text: str) -> EmotionResult:
        if not text or not text.strip():
            return EmotionResult("neutral", 1.0, [])
        text_lower = text.lower()
        scores: Dict[str, int] = {}
        matched: Dict[str, List[str]] = {}

        for emotion, rules in _RULES.items():
            score = 0
            hits: List[str] = []
            for keyword, weight in rules:
                if keyword in text_lower:
                    idx = text_lower.find(keyword)
                    prefix = text_lower[max(0, idx - 2):idx]
                    if any(neg in prefix for neg in self._negations):
                        score += max(0, weight - 1)
                    else:
                        score += weight
                    hits.append(keyword)
            if score > 0:
                scores[emotion] = score
                matched[emotion] = hits

        if not scores:
            return EmotionResult("neutral", 0.5, [])

        best = max(scores, key=scores.get)
        total = sum(scores.values())
        confidence = min(1.0, scores[best] / max(total, 1) * 2)
        return EmotionResult(best, confidence, matched[best])


# 供 WS 事件使用的情感→动作组映射（无标签时驱动 Live2D 动作）
# SoW 28 情绪映射到通用 Live2D 动作组（Happy/Anger/Sad/Surprise/Doubt/Cry/Idle/Shame/Pride）。
EMOTION_MOTION_GROUPS: Dict[str, str] = {
    "joy": "Happy",
    "amusement": "Happy",
    "affection": "Happy",
    "admiration": "Happy",
    "approval": "Happy",
    "love": "Happy",
    "gratitude": "Happy",
    "optimism": "Happy",
    "pride": "Pride",
    "excitement": "Happy",
    "desire": "Happy",
    "relief": "Idle",
    "caring": "Idle",
    "surprise": "Surprise",
    "realization": "Surprise",
    "confusion": "Doubt",
    "curiosity": "Doubt",
    "nervousness": "Doubt",
    "sad": "Sad",
    "grief": "Cry",
    "disappointment": "Sad",
    "remorse": "Sad",
    "embarrassment": "Shame",
    "anger": "Anger",
    "annoyance": "Anger",
    "disapproval": "Anger",
    "disgust": "Anger",
    "fear": "Sad",
    "neutral": "Idle",
}
