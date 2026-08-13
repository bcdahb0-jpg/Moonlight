"""意图路由测试（P1，2026-08-13 起 LLM 全量判别）：LLM 判别 + fail-soft。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_intent.py -q
"""
import asyncio
import unittest

from src.open_llm_vtuber.task_platform import intent_route


class _FakeModel:
    """注入用假模型：ainvoke 返回固定 content。"""

    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return type("Resp", (), {"content": self._content})()


class _BoomModel:
    """注入用假模型：ainvoke 恒抛错。"""

    async def ainvoke(self, messages):
        raise RuntimeError("llm down")


class _SleepModel:
    """注入用假模型：ainvoke 挂起（配合短超时验证 timeout 兜底）。"""

    async def ainvoke(self, messages):
        await asyncio.sleep(30)


class TestClassifyText(unittest.TestCase):
    """classify_text 全链路：LLM 判别 + 空输入 + 失败/超时兜底 chat。"""

    def test_empty_input(self):
        r = asyncio.run(intent_route.classify_text(""))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "empty")
        self.assertEqual(r["confidence"], 1.0)

    def test_llm_task(self):
        """LLM 判 task：工程指令进任务内核。"""
        fake = _FakeModel('{"kind": "task", "reason": "写脚本是执行指令"}')
        r = asyncio.run(intent_route.classify_text("写个脚本处理这些文件", model=fake))
        self.assertEqual(r["kind"], "task")
        self.assertEqual(r["source"], "llm")

    def test_llm_chat(self):
        """LLM 判 chat：闲聊进人设聊天链路。"""
        fake = _FakeModel('{"kind": "chat", "reason": "天气是闲聊"}')
        r = asyncio.run(intent_route.classify_text("今天天气怎么样", model=fake))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "llm")

    def test_llm_json_fence_tolerated(self):
        """LLM 返回 ```json 围栏包裹的 JSON 也应解析。"""
        fake = _FakeModel('```json\n{"kind": "task", "reason": "重构"} \n```')
        r = asyncio.run(intent_route.classify_text("重构这个模块", model=fake))
        self.assertEqual(r["kind"], "task")

    def test_llm_bad_json_falls_back_to_chat(self):
        """LLM 输出无法解析 → 回退 chat（聊天比误触任务安全）。"""
        fake = _FakeModel("我没有理解你的意思")
        r = asyncio.run(intent_route.classify_text("随便聊聊", model=fake))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "fallback")

    def test_llm_failure_falls_back_to_chat(self):
        """LLM 不可用 → 回退 chat。"""
        r = asyncio.run(intent_route.classify_text("深度学习对人类社会的影响", model=_BoomModel()))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "fallback")

    def test_llm_timeout_falls_back_to_chat(self):
        """LLM 挂起超时（_LLM_CLASSIFY_TIMEOUT）→ 回退 chat，不阻塞链路。"""
        orig = intent_route._LLM_CLASSIFY_TIMEOUT
        intent_route._LLM_CLASSIFY_TIMEOUT = 0.3
        try:
            r = asyncio.run(intent_route.classify_text("这个话题很复杂", model=_SleepModel()))
        finally:
            intent_route._LLM_CLASSIFY_TIMEOUT = orig
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "fallback")

    def test_rule_layer_removed(self):
        """2026-08-13：规则层已移除，模块不再有 _rule_classify。"""
        self.assertFalse(hasattr(intent_route, "_rule_classify"))


if __name__ == "__main__":
    unittest.main()
