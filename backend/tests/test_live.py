"""P3 直播与弹幕：danmaku_dispatch 单元测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_live.py -q --basetemp=.pytest-tmp
"""
import asyncio
import unittest


class TestParseDanmaku(unittest.TestCase):
    """弹幕指令解析（唱歌/切歌/普通）。"""

    def test_sing_intent(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import parse_danmaku

        r = parse_danmaku("唱歌+晴天")
        self.assertEqual(r["intent"], "sing")
        self.assertEqual(r["songname"], "晴天")

    def test_next_intent(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import parse_danmaku

        for text in ("切歌", "下一首"):
            self.assertEqual(parse_danmaku(text)["intent"], "next", text)

    def test_chat_intent(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import parse_danmaku

        r = parse_danmaku("主播好可爱")
        self.assertEqual(r["intent"], "chat")
        self.assertNotIn("songname", r)


class TestDanmakuDispatcher(unittest.TestCase):
    """弹幕流 + 指令回调 + 事件去重。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_stream_and_handler(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import DanmakuDispatcher, DANMAKU_INTENT_SING

        d = DanmakuDispatcher()
        replies: list[str] = []

        async def sing_handler(payload: dict):
            replies.append(payload["songname"])
            return f"已点播《{payload['songname']}》"

        d.register_handler(DANMAKU_INTENT_SING, sing_handler)

        async def scenario():
            await d.on_danmaku("唱歌+晴天", uname="观众A")
            await d.on_danmaku("主播好可爱", uname="观众B")

        self._run(scenario())
        self.assertEqual(replies, ["晴天"])
        kinds = [m["kind"] for m in d.recent()]
        self.assertIn("system", kinds)    # 指令回执以 system 类型入流
        self.assertIn("chat", kinds)      # 普通弹幕入流
        self.assertTrue(any("已点播" in m["text"] for m in d.recent()))

    def test_interact_and_gift(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import DanmakuDispatcher

        d = DanmakuDispatcher()

        async def scenario():
            await d.on_interact("新观众", uid="1")
            await d.on_interact("新观众", uid="1")  # TTL 去重
            await d.on_gift("土豪", "小花花", 2, uid="2")
            await d.on_gift("土豪", "小花花", 2, uid="2")  # TTL 去重

        self._run(scenario())
        msgs = d.recent()
        self.assertEqual(len(msgs), 2)
        self.assertTrue(any("欢迎" in m["text"] for m in msgs))
        self.assertTrue(any(m["kind"] == "gift" and "小花花" in m["text"] for m in msgs))

    def test_clear(self):
        from src.open_llm_vtuber.live.danmaku_dispatch import DanmakuDispatcher

        d = DanmakuDispatcher()
        d.push("chat", "hello")
        d.clear()
        self.assertEqual(d.recent(), [])


if __name__ == "__main__":
    unittest.main()
