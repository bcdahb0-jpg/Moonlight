"""pet-ptt-workflow Phase 1：workspace 透传链路测试。

覆盖：
- contracts.validate_client_message：text-input / mic-audio-end 可选 workspace 类型校验；
- single_conversation._auto_create_history：metadata.workspace 透传给 create_new_history，
  无 workspace / 主动陪聊 / 已有会话时不重复创建；
- chat_history_manager.create_new_history：workspace 落 metadata（真实落盘）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_pet_ptt_workspace.py -q --basetemp=.pytest-tmp
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


class TestContractWorkspaceValidation(unittest.TestCase):
    def test_text_input_with_workspace_ok(self):
        from src.open_llm_vtuber.contracts import validate_client_message

        self.assertIsNone(
            validate_client_message(
                {"type": "text-input", "text": "你好", "workspace": "C:\\work"}
            )
        )

    def test_text_input_workspace_without_it_ok(self):
        from src.open_llm_vtuber.contracts import validate_client_message

        self.assertIsNone(validate_client_message({"type": "text-input", "text": "你好"}))

    def test_text_input_workspace_wrong_type_rejected(self):
        from src.open_llm_vtuber.contracts import validate_client_message

        err = validate_client_message(
            {"type": "text-input", "text": "你好", "workspace": 123}
        )
        self.assertIsNotNone(err)
        self.assertIn("workspace", err)

    def test_mic_audio_end_with_workspace_ok(self):
        from src.open_llm_vtuber.contracts import validate_client_message

        self.assertIsNone(
            validate_client_message({"type": "mic-audio-end", "workspace": "C:\\work"})
        )

    def test_mic_audio_end_workspace_null_ok(self):
        from src.open_llm_vtuber.contracts import validate_client_message

        # workspace 可选且显式 null 视为未传（兼容旧客户端）。
        self.assertIsNone(
            validate_client_message({"type": "mic-audio-end", "workspace": None})
        )


class TestAutoCreateHistory(unittest.TestCase):
    """_auto_create_history：workspace 透传 + 不重复创建 + 主动陪聊跳过。"""

    def _context(self):
        class _Cfg:
            conf_uid = "test-conf"
            conf_name = "test"
            character_name = "小月"
            avatar = ""

        class _Agent:
            def set_memory_from_history(self, **kw):
                pass

        class _Ctx:
            def __init__(self):
                self.character_config = _Cfg()
                self.agent_engine = _Agent()
                self.history_uid = None

        return _Ctx()

    def test_workspace_passed_to_create(self):
        from src.open_llm_vtuber.conversations import single_conversation as sc

        ctx = self._context()
        sent = []

        async def _fake_send(send_fn, payload):
            sent.append(payload)

        with (
            patch(
                "src.open_llm_vtuber.chat_history_manager.create_new_history",
                return_value="hid-1",
            ) as m_create,
            patch.object(sc, "send_message", new=_fake_send),
        ):
            hid = asyncio.run(
                sc._auto_create_history(
                    ctx, {"workspace": "D:\\projects\\demo"}, _noop_send,
                    input_text="你好",
                )
            )
        self.assertEqual(hid, "hid-1")
        m_create.assert_called_once_with("test-conf", workspace="D:\\projects\\demo")
        self.assertEqual(ctx.history_uid, "hid-1")
        self.assertEqual(sent[0]["type"], "new-history-created")
        self.assertTrue(sent[0]["auto"])

    def test_no_workspace_keeps_empty(self):
        from src.open_llm_vtuber.conversations import single_conversation as sc

        ctx = self._context()
        with patch(
            "src.open_llm_vtuber.chat_history_manager.create_new_history",
            return_value="hid-2",
        ) as m_create:
            asyncio.run(
                sc._auto_create_history(ctx, None, _noop_send, input_text="你好")
            )
        m_create.assert_called_once_with("test-conf", workspace="")

    def test_existing_history_skips_create(self):
        from src.open_llm_vtuber.conversations import single_conversation as sc

        ctx = self._context()
        ctx.history_uid = "existing"
        with patch("src.open_llm_vtuber.chat_history_manager.create_new_history") as m_create:
            hid = asyncio.run(
                sc._auto_create_history(ctx, {"workspace": "W"}, _noop_send, input_text="hi")
            )
        self.assertIsNone(hid)
        m_create.assert_not_called()

    def test_proactive_turn_skips_create(self):
        from src.open_llm_vtuber.conversations import single_conversation as sc

        ctx = self._context()
        with patch("src.open_llm_vtuber.chat_history_manager.create_new_history") as m_create:
            hid = asyncio.run(
                sc._auto_create_history(
                    ctx,
                    {"proactive_speak": True, "workspace": "W"},
                    _noop_send,
                    input_text="hi",
                )
            )
        self.assertIsNone(hid)
        m_create.assert_not_called()

    def test_empty_input_skips_create(self):
        from src.open_llm_vtuber.conversations import single_conversation as sc

        ctx = self._context()
        with patch("src.open_llm_vtuber.chat_history_manager.create_new_history") as m_create:
            hid = asyncio.run(
                sc._auto_create_history(ctx, {"workspace": "W"}, _noop_send, input_text="  ")
            )
        self.assertIsNone(hid)
        m_create.assert_not_called()


class TestHistoryWorkspacePersist(unittest.TestCase):
    """create_new_history(conf_uid, workspace) → metadata.workspace 真实落盘。"""

    def test_workspace_written_to_metadata(self):
        from src.open_llm_vtuber import chat_history_manager as chm

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                hid = chm.create_new_history("ws-test-conf", workspace="C:\\my\\dir")
                self.assertTrue(hid)
                # 找到会话文件读取 metadata。
                conf_dir = os.path.join("chat_history", "ws-test-conf")
                files = [f for f in os.listdir(conf_dir) if f.endswith(".json")]
                self.assertTrue(files)
                with open(
                    os.path.join(conf_dir, f"{hid}.json"), encoding="utf-8"
                ) as f:
                    data = json.load(f)
                self.assertEqual(data[0].get("workspace"), "C:\\my\\dir")
            finally:
                os.chdir(old)

    def test_empty_workspace_no_metadata_key(self):
        from src.open_llm_vtuber import chat_history_manager as chm

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                hid = chm.create_new_history("ws-test-conf-2")
                self.assertTrue(hid)
                with open(
                    os.path.join("chat_history", "ws-test-conf-2", f"{hid}.json"),
                    encoding="utf-8",
                ) as f:
                    data = json.load(f)
                self.assertNotIn("workspace", data[0])
            finally:
                os.chdir(old)


def _noop_send(send_fn, payload):
    """占位 websocket send（测试不真正收发）。"""
    raise AssertionError("should not send: %s" % payload)


if __name__ == "__main__":
    unittest.main()
