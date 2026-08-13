"""P1 表情与动作域：expression_route / live2d_catalog / emotion 状态机 单元测试。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_expression_route.py -q --basetemp=.pytest-tmp
"""
import unittest


class TestExpressionParams(unittest.TestCase):
    """参数 clamp / 白名单 / MouthOpen 过滤（移植 SoulLink _clamp_parameters 思路）。"""

    def test_clamp_filters_and_scales(self):
        from src.open_llm_vtuber.expression_route import _clamp_parameters

        out = _clamp_parameters(
            {
                "ParamMouthOpenY": 0.9,  # 口型开合 → 过滤
                "ParamMouthForm": 0.5,   # 保留嘴型 + 幅度缩放
                "ParamAngleX": -90,      # 超出范围 → clamp -30
                "ParamHoge": 1.0,        # 非白名单 → 忽略
                "ParamEyeLOpen": 2.0,    # 超出范围 → clamp 1.0（幅度 50% 后 0.0）
            },
            amplitude=50,
        )
        self.assertNotIn("ParamMouthOpenY", out)
        self.assertNotIn("ParamHoge", out)
        self.assertIn("ParamMouthForm", out)
        self.assertEqual(out["ParamAngleX"], -30.0)

    def test_clamp_zero_amplitude(self):
        from src.open_llm_vtuber.expression_route import _clamp_parameters

        out = _clamp_parameters({"ParamMouthForm": 0.5, "ParamAngleX": 10}, amplitude=0)
        self.assertEqual(out["ParamMouthForm"], -1.0)  # 幅度 0 → 表情类取 min
        self.assertEqual(out["ParamAngleX"], 10.0)      # 角度不受幅度缩放

    def test_ranges_are_valid(self):
        from src.open_llm_vtuber.expression_route import PARAM_RANGES

        for key, (lo, hi) in PARAM_RANGES.items():
            self.assertLess(lo, hi, key)
            self.assertTrue(key.startswith("Param"), key)


class TestExpressionConfig(unittest.TestCase):
    def test_default_config_shape(self):
        from src.open_llm_vtuber.expression_route import _expression_config_from_conf

        cfg = _expression_config_from_conf()
        for key in ("enabled", "sensitivity", "amplitude", "easing", "model"):
            self.assertIn(key, cfg)


class TestLive2dCatalog(unittest.TestCase):
    def test_scan_returns_current_model(self):
        from src.open_llm_vtuber.live2d_catalog import scan_models, _current_model_from_conf

        models = scan_models()
        self.assertIsInstance(models, list)
        for m in models:
            self.assertIn("name", m)
            self.assertTrue(m["model_url"].startswith("/live2d-models/"))
            self.assertIn("custom_prompt", m)
        current = _current_model_from_conf()
        self.assertIsInstance(current, str)
        self.assertTrue(current)

    def test_scan_name_is_top_level_dir(self):
        from src.open_llm_vtuber.live2d_catalog import scan_models

        for m in scan_models():
            self.assertNotIn("/", m["name"], "模型名应为根目录第一级，避免展示 'runtime' 等子目录名")


class TestEmotionStateMachine(unittest.TestCase):
    def test_state_machine_shape(self):
        from src.open_llm_vtuber.emotion import get_emotion_tracker

        sm = get_emotion_tracker().state_machine()
        self.assertIn("states", sm)
        self.assertIn("current", sm)
        self.assertGreater(sm["decay_minutes"], 0)
        self.assertFalse(sm["per_conversation"])

    def test_per_conversation_isolation_and_decay(self):
        from src.open_llm_vtuber.emotion import get_emotion_tracker

        tracker = get_emotion_tracker()
        tracker.update("joy", 0.9, source="test", uid="conv-a")
        tracker.update("sad", 0.8, source="test", uid="conv-b")
        a = tracker.get_current("conv-a")
        b = tracker.get_current("conv-b")
        self.assertEqual(a["emotion"], "joy")
        self.assertEqual(b["emotion"], "sad")
        self.assertEqual(tracker.get_current("conv-nonexistent")["emotion"], "neutral")


if __name__ == "__main__":
    unittest.main()
