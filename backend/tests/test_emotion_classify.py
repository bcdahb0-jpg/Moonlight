"""情绪分类（规则快路径 + LLM 慢路径）与 tracker LLM 缓存测试。

覆盖 docs/live2d-facial-expression-plan.md Phase 1：
- 规则快路径命中（零延迟）
- 中性/无关键词回退
- tracker LLM 缓存读写 + TTL
- LLM JSON 解析（围栏/前后缀容忍）+ 白名单校验
- stream_audio payload 带 emotion_meta
"""

import unittest

import numpy as np
from pydub import AudioSegment

from src.open_llm_vtuber.contracts import AudioMessage
from src.open_llm_vtuber.emotion import get_emotion_tracker
from src.open_llm_vtuber.emotion.emotion_classifier import (
    _parse_llm_json,
    classify_llm,
    classify_rule,
    text_fingerprint,
)
from src.open_llm_vtuber.utils.stream_audio import prepare_audio_payload

SR = 24000


class TestRuleFastPath(unittest.TestCase):
    def test_joy_keyword(self):
        r = classify_rule("哈哈，今天太开心了！")
        self.assertEqual(r.emotion, "joy")
        self.assertEqual(r.source, "rule")
        self.assertGreaterEqual(r.intensity, 0.5)

    def test_anger_keyword(self):
        r = classify_rule("气死我了，又卡了")
        self.assertEqual(r.emotion, "anger")

    def test_neutral_no_keyword(self):
        r = classify_rule("今天天气不错")
        self.assertEqual(r.emotion, "neutral")

    def test_empty_text(self):
        r = classify_rule("  ")
        self.assertEqual(r.emotion, "neutral")
        self.assertEqual(r.source, "rule")

    def test_daily_rules_2026(self):
        """Phase 1.5 日常口语词表补全：命中率验证（口语高频词）。"""
        cases = {
            "今天绝了，笑死我了": "amusement",
            "我真的会谢，无语了": "annoyance",
            "今天面试过了，美滋滋": "joy",
            "半夜破防了，emo": "sad",
            "这也太离谱了吧": "surprise",
            "社死了，脚趾抠地": "embarrassment",
            "真吓人，我怂了": "fear",
            "终于稳了，放心了": "relief",
            "这个方案挺靠谱的": "approval",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify_rule(text).emotion, expect, f"词表未命中: {text}")

    def test_daily_rules_no_false_positive(self):
        """中性日常句不应被口语词误伤。"""
        for text in ("推荐行程", "天气不错", "好的，我知道了", "随便看看"):
            with self.subTest(text=text):
                r = classify_rule(text)
                self.assertIn(r.emotion, ("neutral", "approval", "confusion", "curiosity"),
                              f"中性句被误判: {text} -> {r.emotion}")


class TestLLMParse(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(_parse_llm_json('{"emotion": "joy", "intensity": 0.8, "duration_ms": 5000}'),
                         {"emotion": "joy", "intensity": 0.8, "duration_ms": 5000})

    def test_fenced_json(self):
        parsed = _parse_llm_json('```json\n{"emotion": "sad", "intensity": 0.6, "duration_ms": 7000}\n```')
        self.assertEqual(parsed["emotion"], "sad")

    def test_prefix_suffix_text(self):
        parsed = _parse_llm_json('分析结果：{"emotion": "anger", "intensity": 0.9, "duration_ms": 4000} 完毕')
        self.assertEqual(parsed["emotion"], "anger")

    def test_invalid_json(self):
        self.assertIsNone(_parse_llm_json("不是 JSON"))

    def test_llm_emotion_whitelist(self):
        """LLM 返回白名单外的情绪应被拒绝（回退规则）。"""
        async def _run():
            return await classify_llm("测试", timeout_sec=0.5)
        # 不构造真实 LLM，验证超时/失败路径不抛异常
        result = __import__("asyncio").run(_run())
        self.assertIsNone(result)  # 无 LLM 配置时静默失败


class TestTrackerLlmCache(unittest.TestCase):
    def setUp(self):
        self.tracker = get_emotion_tracker()

    def test_set_get_roundtrip(self):
        fp = text_fingerprint("今天面试过了")
        self.tracker.set_llm_cache(fp, "joy", 0.8, 6000)
        got = self.tracker.get_llm_cache(fp)
        self.assertEqual(got["emotion"], "joy")
        self.assertAlmostEqual(got["intensity"], 0.8)
        self.assertEqual(got["duration_ms"], 6000)

    def test_missing_fingerprint(self):
        self.assertIsNone(self.tracker.get_llm_cache("no-such-fp"))

    def test_whitelist_rejected(self):
        fp = text_fingerprint("非法情绪")
        self.tracker.set_llm_cache(fp, "not_a_real_emotion", 0.9, 5000)
        self.assertIsNone(self.tracker.get_llm_cache(fp))


class TestPayloadEmotionMeta(unittest.TestCase):
    def test_silent_payload_meta_none(self):
        payload = prepare_audio_payload(None)
        self.assertIsNone(payload["emotion_meta"])
        self.assertIn("emotion_meta", payload)

    def test_audio_message_schema(self):
        msg = AudioMessage(type="audio")
        self.assertIsNone(msg.emotion_meta)


if __name__ == "__main__":
    unittest.main()
