"""singing 包单元测试（P2 唱歌 MVP）：指令提取 + 状态机 + 配置。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_singing.py -q --basetemp=.pytest-tmp
"""
import asyncio
import unittest


class TestExtractSingRequest(unittest.TestCase):
    """「唱歌+歌名」指令提取（移植 AI-YinMei 触发词表）。"""

    def test_basic_triggers(self):
        from src.open_llm_vtuber.singing.sing_core import extract_sing_request

        self.assertEqual(extract_sing_request("唱歌+打上花火"), "打上花火")
        self.assertEqual(extract_sing_request("唱一首晴天"), "晴天")
        self.assertEqual(extract_sing_request("点歌 夜曲"), "夜曲")
        self.assertEqual(extract_sing_request("点播，月半小夜曲"), "月半小夜曲")

    def test_no_trigger(self):
        from src.open_llm_vtuber.singing.sing_core import extract_sing_request

        self.assertIsNone(extract_sing_request("今天天气怎么样"))
        self.assertIsNone(extract_sing_request(""))
        self.assertIsNone(extract_sing_request("唱歌"))  # 无歌名

    def test_trim_separators(self):
        from src.open_llm_vtuber.singing.sing_core import extract_sing_request

        self.assertEqual(extract_sing_request("唱歌 + 稻香"), "稻香")
        self.assertEqual(extract_sing_request("唱歌，晴天"), "晴天")


class TestSingCoreStatusMachine(unittest.TestCase):
    """状态机（不依赖外部服务：学歌必然失败 → error 状态 fail-soft）。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_status_shape(self):
        from src.open_llm_vtuber.singing.sing_core import SingCore

        core = SingCore()
        status = core.status()
        for key in ("state", "learning", "current", "queue", "ready", "progress", "last_error", "config"):
            self.assertIn(key, status)
        self.assertEqual(status["state"], "idle")
        self.assertIn("acm_url", status["config"])

    def test_request_rejects_no_service(self):
        """acm_url 不可达 → 入队失败但状态机不崩（fail-soft）。"""
        from src.open_llm_vtuber.singing.sing_core import SingCore

        core = SingCore()
        core.set_config({"acm_url": "http://127.0.0.1:1"})  # 不可达端口

        async def scenario():
            r = await core.request("唱歌+晴天")
            # 服务不可达 → ok=False 且带 reason（不抛异常）
            self.assertFalse(r["ok"])
            self.assertIn("reason", r)
            return r

        r = self._run(scenario())
        self.assertFalse(r["ok"])

    def test_request_invalid_text(self):
        from src.open_llm_vtuber.singing.sing_core import SingCore

        core = SingCore()

        async def scenario():
            return await core.request("随便聊聊")

        r = self._run(scenario())
        self.assertFalse(r["ok"])

    def test_next_empty(self):
        from src.open_llm_vtuber.singing.sing_core import SingCore

        core = SingCore()

        async def scenario():
            return await core.next_song()

        r = self._run(scenario())
        self.assertFalse(r["ok"])

    def test_stop_learning_and_clear(self):
        from src.open_llm_vtuber.singing.sing_core import SingCore

        core = SingCore()

        async def scenario():
            await core.stop_learning()
            return await core.clear()

        r = self._run(scenario())
        self.assertTrue(r["ok"])


class TestSingingConfig(unittest.TestCase):
    def test_default_config_shape(self):
        from src.open_llm_vtuber.singing.sing_core import DEFAULT_CONFIG

        for key in ("acm_url", "create_timeout", "song_not_convert"):
            self.assertIn(key, DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()
