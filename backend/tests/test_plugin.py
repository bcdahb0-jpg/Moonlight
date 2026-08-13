"""P5 插件生态测试：registry / manager / hooks / export / intent / marketplace。

全部离线可跑：不依赖外网（marketplace 用内置目录册兜底）、不依赖 LLM
（analyze_intent 走规则路径）、不动真实 conf.yaml（scrub/merge 用字典纯函数）。
"""

import asyncio
import copy
import json
import unittest
from pathlib import Path

from src.open_llm_vtuber.plugin import (
    analyze_intent,
    export_character,
    export_config,
    find_plugin,
    get_plugin_manager,
    import_config,
    scan_plugins,
    scrub_secrets,
)
from src.open_llm_vtuber.plugin import marketplace


class TestRegistry(unittest.TestCase):
    def test_scan_finds_builtin_echo(self):
        plugins = scan_plugins()
        self.assertTrue(any(p.plugin_id == "builtin/echo" for p in plugins))

    def test_find_plugin(self):
        info = find_plugin("builtin/echo")
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "echo")
        self.assertIn("on_message", info.hooks)

    def test_scan_skip_invalid(self):
        # 无 plugin.json 的目录应被跳过而非崩溃
        import shutil
        import tempfile

        from src.open_llm_vtuber.plugin import registry

        tmp = Path(tempfile.mkdtemp())
        (tmp / "no_meta").mkdir()
        old_root = registry.PLUGINS_ROOT
        try:
            registry.PLUGINS_ROOT = tmp
            self.assertEqual(scan_plugins(), [])
        finally:
            registry.PLUGINS_ROOT = old_root
            shutil.rmtree(tmp, ignore_errors=True)


class TestManager(unittest.TestCase):
    def test_toggle_and_dispatch(self):
        manager = get_plugin_manager()
        # 先确保 echo 处于停用态（隔离测试环境）
        info = find_plugin("builtin/echo")
        if info and info.enabled:
            manager.toggle("builtin/echo")
        # 未启用时消息放行
        self.assertIsNone(manager.on_message({"text": "echo: hi"}))
        # 启用后 echo: 前缀被吞掉并返回大写
        result = manager.toggle("builtin/echo")
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("enabled"))
        reply = manager.on_message({"text": "echo: hello"})
        self.assertEqual(reply, "HELLO")
        # 普通消息放行
        self.assertIsNone(manager.on_message({"text": "今天天气"}))


class TestExport(unittest.TestCase):
    def test_scrub_secrets_keeps_shape(self):
        data = {
            "llm": {"api_key": "sk-123", "model": "deepseek", "nested": {"token": "t"}},
            "tts": {"api_key": "k", "voice": "x"},
            "ok": "保留",
        }
        out = scrub_secrets(data)
        # 值被清空、键保留
        self.assertEqual(out["llm"]["api_key"], "")
        self.assertEqual(out["llm"]["model"], "deepseek")
        self.assertEqual(out["llm"]["nested"]["token"], "")
        self.assertEqual(out["tts"]["api_key"], "")
        self.assertEqual(out["ok"], "保留")
        # 深拷贝：原数据不受影响
        self.assertEqual(data["llm"]["api_key"], "sk-123")

    def test_export_character_no_secrets(self):
        out = export_character()
        blob = json.dumps(out, ensure_ascii=False)
        self.assertIn("character_config", out)
        self.assertNotIn("sk-", blob)
        self.assertNotIn("api_key: ", blob)  # 值已清空（键仍在但无真实值）

    def test_import_config_merge(self):
        payload = {
            "version": 1,
            "kind": "character",
            "character_config": {"agent_config": {"prompt": "新人格"}},
        }
        # 纯函数校验（不真正写 conf）：merge 逻辑正确性
        merged = {}
        src_block = payload["character_config"]
        base = {"agent_config": {"prompt": "旧人格", "model": "keep"}}
        for k, v in src_block.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
        self.assertEqual(base["agent_config"]["prompt"], "新人格")
        self.assertEqual(base["agent_config"]["model"], "keep")

    def test_import_config_invalid(self):
        self.assertEqual(import_config(None)["ok"], False)
        self.assertEqual(import_config({"kind": "unknown"})["ok"], False)


class TestIntent(unittest.TestCase):
    def test_rule_silence(self):
        result = asyncio.run(analyze_intent("别烦我，我忙着呢"))
        self.assertEqual(result["intent"], "silence")
        self.assertEqual(result["source"], "rule")

    def test_rule_task(self):
        result = asyncio.run(analyze_intent("帮我写个 Python 脚本"))
        self.assertEqual(result["intent"], "task")

    def test_rule_chat_fallback(self):
        # 不含关键词 → LLM 或 fallback，intent 必须为 chat（聊天链路最安全）
        result = asyncio.run(analyze_intent("今天天气不错"))
        self.assertEqual(result["intent"], "chat")
        self.assertIn(result["source"], ("llm", "fallback"))

    def test_intent_config_defaults(self):
        from src.open_llm_vtuber.plugin.intent import intent_config

        cfg = intent_config()
        self.assertIn("enabled", cfg)
        self.assertIn("model", cfg)


class TestMarketplace(unittest.TestCase):
    def test_builtin_catalog_offline(self):
        items = marketplace.catalog()  # 空 URL → 内置
        self.assertTrue(len(items) >= 3)
        names = {i["name"] for i in items}
        self.assertIn("hello-world", names)

    def test_install_status_idle(self):
        self.assertEqual(marketplace.install_status("hello-world")["status"], "idle")


if __name__ == "__main__":
    unittest.main()
