"""P4 游戏陪玩：game 匹配 / 事件分类 / 喝彩 / 知识库 单元测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_playmate.py -q --basetemp=.pytest-tmp
"""
import asyncio
import unittest


class TestGameMatch(unittest.TestCase):
    """窗口匹配规则（内置游戏表 + 自定义正则）。"""

    def test_builtin_match(self):
        from src.open_llm_vtuber.playmate.game import match_game

        g = match_game("Minecraft 1.20 - 多人游戏", "java")
        self.assertIsNotNone(g)
        self.assertEqual(g["id"], "minecraft")
        g2 = match_game("原神", "GenshinImpact.exe")
        self.assertEqual(g2["id"], "genshin")

    def test_custom_regex_priority(self):
        from src.open_llm_vtuber.playmate.game import match_game

        g = match_game("某自定义游戏窗口", "App", window_regex=r"自定义")
        self.assertIsNotNone(g)  # 命中自定义正则 → 返回模板游戏

    def test_no_match(self):
        from src.open_llm_vtuber.playmate.game import match_game

        self.assertIsNone(match_game("记事本 - 无标题", "notepad.exe"))

    def test_invalid_regex(self):
        from src.open_llm_vtuber.playmate.game import match_game

        self.assertIsNone(match_game("随便", "app", window_regex="["))

    def test_get_game(self):
        from src.open_llm_vtuber.playmate.game import get_game

        self.assertEqual(get_game("minecraft")["name"], "我的世界")
        self.assertIsNone(get_game("unknown"))


class TestEventBroker(unittest.TestCase):
    """事件流 + 喝彩判定 + 冷却。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_highlight_cheer_and_cooldown(self):
        from src.open_llm_vtuber.playmate.events import EventBroker

        b = EventBroker()
        b.set_cooldown(10)
        r1 = b.record({"event_type": "kill", "confidence": 0.9, "summary": "击杀末影龙"})
        self.assertIsNotNone(r1["cheer"])
        r2 = b.record({"event_type": "victory", "confidence": 0.9, "summary": "通关"})
        self.assertIsNone(r2["cheer"])  # 冷却内不重复喝彩

    def test_other_event_no_cheer(self):
        from src.open_llm_vtuber.playmate.events import EventBroker

        b = EventBroker()
        r = b.record({"event_type": "other", "confidence": 0.5, "summary": "日常"})
        self.assertIsNone(r["cheer"])

    def test_recent_events(self):
        from src.open_llm_vtuber.playmate.events import EventBroker

        b = EventBroker()
        b.record({"event_type": "drop", "confidence": 0.8, "summary": "掉落圣遗物"})
        self.assertEqual(len(b.recent_events), 1)


class TestKbChunk(unittest.TestCase):
    def test_chunk_by_paragraph_and_length(self):
        from src.open_llm_vtuber.playmate.kb import _chunk

        text = "第一段攻略内容。\n\n第二段攻略内容。" * 30
        chunks = _chunk(text, size=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 110 for c in chunks))

    def test_chunk_empty(self):
        from src.open_llm_vtuber.playmate.kb import _chunk

        self.assertEqual(_chunk("   "), [])


if __name__ == "__main__":
    unittest.main()
