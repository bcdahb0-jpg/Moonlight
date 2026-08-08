"""契约层冒烟测试（Phase 0/1）。

运行：uv run python -m unittest discover -s tests -v
"""
import asyncio
import json
import unittest

from src.open_llm_vtuber.contracts import (
    ErrorCode,
    build_server_message,
    send_error,
    send_message,
    validate_client_message,
    _MESSAGE_MODELS,
)
from src.open_llm_vtuber.config_route import (
    _deep_merge,
    _is_sensitive,
    _mask_config,
)
from src.open_llm_vtuber.config_manager.main import Config
from src.open_llm_vtuber.config_manager.utils import validate_config
from src.open_llm_vtuber.layers.domain.readiness import check, all_passed
from src.open_llm_vtuber.utils.tts_preprocessor import (
    tts_filter,
    remove_special_characters,
    restore_cjk_punctuation,
)


class TestMessageRegistry(unittest.TestCase):
    def test_all_types_registered(self):
        expected = {
            "affection-update", "audio", "backend-synth-complete", "background-files",
            "config-files", "config-updated", "control", "error", "force-new-message",
            "full-text", "group-update", "heartbeat-ack", "history-data",
            "history-deleted", "history-list", "history-title-updated",
            "new-history-created",
            "set-model-and-conf", "transcript", "user-input-transcription",
        }
        self.assertEqual(set(_MESSAGE_MODELS.keys()), expected)

    def test_valid_messages_build(self):
        m = build_server_message({"type": "full-text", "text": "hi", "quote": True})
        self.assertEqual(m.model_dump()["type"], "full-text")
        m = build_server_message(
            {"type": "audio", "audio": None, "volumes": [], "slice_length": 20}
        )
        self.assertEqual(m.model_dump()["audio"], None)

    def test_invalid_messages_rejected(self):
        with self.assertRaises(Exception):
            build_server_message({"type": "full-text"})  # 缺 text
        with self.assertRaises(ValueError):
            build_server_message({"type": "nope"})  # 未知类型

    def test_control_literal_enum(self):
        for ok in ("start-mic", "interrupt", "conversation-chain-start"):
            build_server_message({"type": "control", "text": ok})
        with self.assertRaises(Exception):
            build_server_message({"type": "control", "text": "bogus-signal"})


class TestSendMessage(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_send_message_valid(self):
        frames = []

        async def fake_send(text):
            frames.append(json.loads(text))

        self._run(send_message(fake_send, {"type": "control", "text": "interrupt"}))
        self.assertEqual(frames[0]["type"], "control")

    def test_invalid_outbound_degrades_to_protocol_error(self):
        frames = []

        async def fake_send(text):
            frames.append(json.loads(text))

        self._run(send_message(fake_send, {"type": "bogus"}))
        self.assertEqual(frames[0]["type"], "error")
        self.assertEqual(frames[0]["code"], "PROTOCOL_ERROR")

    def test_send_error_structured(self):
        frames = []

        async def fake_send(text):
            frames.append(json.loads(text))

        self._run(send_error(fake_send, ErrorCode.LLM_UNREACHABLE, "无法连接"))
        # 契约层序列化保留 null 字段（与旧协议逐字节兼容）。
        self.assertEqual(frames[0], {
            "type": "error", "code": "LLM_UNREACHABLE", "message": "无法连接",
            "recover": None,
        })


class TestClientValidation(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(validate_client_message({"type": "text-input", "text": "hi"}))
        self.assertIsNone(validate_client_message({"type": "heartbeat"}))

    def test_invalid(self):
        self.assertIn("缺少 type", validate_client_message({"text": "x"}))
        self.assertIn("未知消息类型", validate_client_message({"type": "unknown"}))
        self.assertIn("缺少必需字段", validate_client_message({"type": "text-input"}))


class TestConfigPatch(unittest.TestCase):
    def test_deep_merge(self):
        base = {"system_config": {"host": "127.0.0.1", "port": 12393}, "extra": 1}
        patch = {"system_config": {"port": 9999}, "new_key": True}
        merged = _deep_merge(base, patch)
        self.assertEqual(merged["system_config"]["host"], "127.0.0.1")  # 未触碰键保留
        self.assertEqual(merged["system_config"]["port"], 9999)
        self.assertEqual(merged["new_key"], True)

    def test_deep_merge_non_dict_replaces(self):
        base = {"a": {"b": 1}, "list": [1, 2]}
        merged = _deep_merge(base, {"list": [3], "a": 5})
        self.assertEqual(merged["list"], [3])  # list 直接替换
        self.assertEqual(merged["a"], 5)  # 非 dict 值替换 dict

    def test_deep_merge_null_sets_none(self):
        merged = _deep_merge({"a": 1}, {"a": None})
        self.assertIsNone(merged["a"])

    def test_sensitive_key_detection(self):
        self.assertTrue(_is_sensitive("llm_api_key"))
        self.assertTrue(_is_sensitive("api_key"))
        self.assertTrue(_is_sensitive("openai_api_key"))
        self.assertFalse(_is_sensitive("character_name"))

    def test_mask_config(self):
        data = {
            "character_config": {"character_name": "小月"},
            "agent_config": {"llm_configs": {"openai_compatible_llm": {"llm_api_key": "sk-secret-123"}}},
        }
        masked = _mask_config(data)
        key = masked["agent_config"]["llm_configs"]["openai_compatible_llm"]["llm_api_key"]
        self.assertNotIn("secret", key)
        self.assertIn("****", key)
        self.assertEqual(masked["character_config"]["character_name"], "小月")

    def test_config_schema_validates_current_conf(self):
        # 当前 conf.yaml 必须通过 pydantic 校验（配置 API 的读写前提）。
        from src.open_llm_vtuber.config_manager.utils import read_yaml
        import os
        if not os.path.exists("conf.yaml"):
            self.skipTest("conf.yaml not present")
        validated = validate_config(read_yaml("conf.yaml"))
        self.assertIsInstance(validated, Config)
        # Phase 1 卖点默认翻转后的取值断言。
        cc = validated.character_config
        self.assertTrue(cc.long_term_memory_enabled)
        self.assertTrue(cc.fts_memory_enabled)
        # vector_memory_enabled 不在 pydantic 模型内（extra 忽略），靠 getattr 兜底为 False。
        self.assertFalse(getattr(cc, "vector_memory_enabled", False))
        # Phase 2：SystemConfig.ui_prefs 已落位（前端设置单一事实源）。
        prefs = validated.system_config.ui_prefs
        self.assertTrue(prefs.screen_aware_enabled)
        self.assertTrue(prefs.proactive_enabled)
        self.assertEqual(prefs.proactive_idle_sec, 60)
        schema = Config.model_json_schema()
        self.assertIn("UiPrefs", schema.get("$defs", {}))


class TestReadiness(unittest.TestCase):
    def test_check_all_pass(self):
        results = check({
            "asr_model": lambda: (True, "ok"),
            "llm": lambda: (True, "ok"),
        })
        self.assertTrue(all_passed(results))

    def test_check_failure_isolated(self):
        results = check({
            "llm": lambda: (_ for _ in ()).throw(RuntimeError("boom")),  # 抛异常
            "mic": lambda: (False, "需要权限"),
        })
        self.assertFalse(all_passed(results))
        self.assertEqual(results[0]["passed"], False)
        self.assertIn("执行失败", results[0]["hint"])


# ------------------------------------------------------------------ //
# TTS 文本预处理（utils/tts_preprocessor.py）
# ------------------------------------------------------------------ //

class TestTtsFilter(unittest.TestCase):
    """验证送进 TTS 引擎的文本是干净的：
    - 剥离动作/表情描述（括号/星号/尖括号包裹）
    - 中文文本标点恢复全角（NFKC 把 ，。？！…… 转成半角，中文 TTS 读半角标点会出杂音）
    - emoji / 特殊符号被剔除
    """

    def _filter(self, text: str) -> str:
        return tts_filter(
            text,
            remove_special_char=True,
            ignore_brackets=True,
            ignore_parentheses=True,
            ignore_asterisks=True,
            ignore_angle_brackets=True,
        )

    def test_strips_action_descriptions(self):
        # 括号/星号/尖括号内的动作描述必须从语音里消失（但字幕保留，见 display）
        self.assertEqual(
            self._filter("（托腮看着你）怎么突然安静啦？"),
            "怎么突然安静啦？",
        )
        self.assertEqual(
            self._filter("*眨眨眼* 我在听呢！"),
            "我在听呢！",
        )
        self.assertEqual(
            self._filter("嗯<思考中>你说得对"),
            "嗯你说得对",
        )

    def test_cjk_punctuation_restored_fullwidth(self):
        # NFKC 会把全角标点半角化（？→? ，→, ……→...），必须恢复全角避免中文 TTS 杂音
        self.assertEqual(self._filter("你吃饭了吗？"), "你吃饭了吗？")
        self.assertEqual(self._filter("好呀，那我们走吧！"), "好呀，那我们走吧！")
        self.assertEqual(self._filter("还是说……你在想我呀？"), "还是说……你在想我呀？")
        self.assertEqual(self._filter("他说：没问题；好的"), "他说：没问题；好的")

    def test_emoji_removed(self):
        out = self._filter("今天也要开心哦～（比心）✨")
        self.assertNotIn("✨", out)
        self.assertNotIn("（比心）", out)

    def test_english_text_kept_halfwidth(self):
        # 纯英文/无中文字符的文本不强制转全角（避免破坏英文标点）
        self.assertEqual(self._filter("Hello, world!?"), "Hello, world!?")
        self.assertEqual(self._filter("Let's go."), "Let's go.")

    def test_url_and_decimal_not_mangled(self):
        # 中文语境里夹带的 URL / 小数 / 版本号不能误伤（冒号、点保持半角）
        self.assertEqual(self._filter("参考 https://example.com 看看"), "参考 https://example.com 看看")
        self.assertEqual(self._filter("圆周率是 3.14 哦"), "圆周率是 3.14 哦")
        self.assertEqual(self._filter("现在到 v2.0 啦"), "现在到 v2.0 啦")
        self.assertEqual(self._filter("时间 12:30 见"), "时间 12:30 见")

    def test_no_junk_punct_in_cjk(self):
        # 中文语境下不应该残留 ? , ! . 等半角标点（这是杂音来源）
        out = self._filter("是呀,你说得对! 今晚?")
        for bad in ("?", ",", "!"):
            self.assertNotIn(bad, out)


if __name__ == "__main__":
    unittest.main()
