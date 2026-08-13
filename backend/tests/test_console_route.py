"""console_route 单元测试：overview 聚合接口结构完整性 + fail-soft。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_console_route.py -q --basetemp=.pytest-tmp
"""
import unittest


class TestConsoleOverview(unittest.TestCase):
    """不依赖运行中后端：直接调 _build_overview() 校验聚合结构与类型。"""

    def test_build_overview_shape(self):
        from src.open_llm_vtuber.console_route import _build_overview

        ov = _build_overview()
        self.assertIsInstance(ov, dict)

        # 角色
        self.assertIn("name", ov["role"])
        self.assertIn("live2d", ov["role"])
        # LLM
        self.assertIn("is_configured", ov["llm"])
        self.assertIn("model", ov["llm"])
        # 引擎
        self.assertIn("voicevox", ov["engines"])
        self.assertIn("running", ov["engines"]["voicevox"])
        self.assertIn("deeplx", ov["engines"])
        # 记忆（fail-soft：None 允许，但类型必须合法）
        self.assertIn("facts", ov["memory"])
        self.assertIn("reflections", ov["memory"])
        # 情感
        self.assertIn("emotion", ov["emotion"])
        # 屏幕 / 任务 / MCP
        self.assertIn("enabled", ov["screen"])
        self.assertIn("enabled", ov["task"])
        self.assertIn("configured", ov["mcp"])
        # 系统
        self.assertIn("backend_online", ov["system"])
        self.assertIn("frontend_online", ov["system"])
        self.assertIn("cpu_percent", ov["system"])
        self.assertIn("process_mem_mb", ov["system"])

    def test_llm_fields_consistent(self):
        from src.open_llm_vtuber.console_route import _build_overview

        ov = _build_overview()
        llm = ov["llm"]
        self.assertIsInstance(llm["is_configured"], bool)
        self.assertIsInstance(llm["model"], str)

    def test_memory_fail_soft_types(self):
        from src.open_llm_vtuber.console_route import _build_overview

        mem = _build_overview()["memory"]
        for key in ("core_chars", "facts", "reflections", "vector_count"):
            self.assertTrue(mem[key] is None or isinstance(mem[key], int))

    def test_route_registration(self):
        from src.open_llm_vtuber.console_route import init_console_route

        router = init_console_route()
        paths = {getattr(r, "path", None) for r in router.routes}
        self.assertIn("/api/console/overview", paths)


if __name__ == "__main__":
    unittest.main()
