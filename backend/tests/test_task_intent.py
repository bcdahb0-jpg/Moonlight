"""意图路由测试（P1）：规则分类 + LLM 兜底 + fail-soft。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_intent.py -q
"""
import unittest

from src.open_llm_vtuber.task_platform import intent_route


class TestRuleClassify(unittest.TestCase):
    """规则预判：工程指令 → task；闲聊 → chat；未命中 → None（走 LLM）。"""

    def test_task_keywords(self):
        cases = [
            "帮我重构一下 utils 目录的接口",
            "写个 Python 脚本批量重命名文件",
            "把 README.md 翻译成英文",
            "查一下这个项目里的报错日志",
            "新建一个 git 分支并提交",
            "优化一下这个函数的性能",
            "整理下载目录",
            "修复登录接口的 bug",
        ]
        for text in cases:
            self.assertEqual(intent_route._rule_classify(text), "task", text)

    def test_chat_keywords(self):
        cases = [
            "今天天气怎么样",
            "小月，我好难过",
            "你吃饭了吗",
            "我饿了",
            "好累啊",
            "陪我聊聊天",
            "谢谢你",
            "晚安",
        ]
        for text in cases:
            self.assertEqual(intent_route._rule_classify(text), "chat", text)

    def test_unmatched_returns_none(self):
        # 无关键词 → 交给 LLM 兜底（疑问式已由 _QUERY 吸收，这里用无任何特征词的句子）
        self.assertIsNone(intent_route._rule_classify("人类未来发展的方向难以预测"))
        self.assertIsNone(intent_route._rule_classify("我对未来感到迷茫"))

    def test_query_asking_is_chat(self):
        """2026-08-09 回归：纯查询/介绍式语句不得误判为任务。
        （截图反馈：「本目录下有什么文件？简单介绍一下」被当任务执行）"""
        cases = [
            "本目录下有什么文件？简单介绍一下",
            "这个项目里有几个文件",
            "文件在哪里",
            "介绍一下 DeepSeek",
            "这段代码什么意思",
            "这个报错是什么原因",
            "你的工作目录在哪",
        ]
        for text in cases:
            self.assertEqual(intent_route._rule_classify(text), "chat", text)

    def test_action_verb_still_task(self):
        """动作动词（含搜索/查证类）仍必须走任务内核：保住 2026-08-09 上午的修复。"""
        cases = [
            "写个脚本列出当前目录文件",
            "帮我查一下 DeepSeek 最新版本",
            "搜索今天 AI 新闻",
            "整理一下这个文件夹",
            "修一下这个接口的 bug",
        ]
        for text in cases:
            self.assertEqual(intent_route._rule_classify(text), "task", text)


class TestClassifyText(unittest.TestCase):
    """classify_text 全链路：规则短路 + 空输入 + 兜底 chat。"""

    def test_empty_input(self):
        import asyncio

        r = asyncio.run(intent_route.classify_text(""))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "empty")

    def test_rule_shortcut_task(self):
        import asyncio

        r = asyncio.run(intent_route.classify_text("写个脚本处理这些文件"))
        self.assertEqual(r["kind"], "task")
        self.assertEqual(r["source"], "rule")

    def test_rule_shortcut_chat(self):
        import asyncio

        r = asyncio.run(intent_route.classify_text("今天心情不好"))
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "rule")

    def test_llm_failure_falls_back_to_chat(self):
        """LLM 不可用 → 回退 chat（聊天比误触任务安全）。"""
        import asyncio

        class Boom:
            async def ainvoke(self, messages):
                raise RuntimeError("llm down")

        orig = intent_route.llm_adapter.build_chat_model
        intent_route.llm_adapter.build_chat_model = lambda cfg: Boom()
        try:
            r = asyncio.run(intent_route.classify_text("深度学习对人类社会的影响"))
        finally:
            intent_route.llm_adapter.build_chat_model = orig
        self.assertEqual(r["kind"], "chat")
        self.assertEqual(r["source"], "fallback")


if __name__ == "__main__":
    unittest.main()
