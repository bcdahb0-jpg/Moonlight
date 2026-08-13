"""聊天回合并发与 TTS 交付时序回归测试。"""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


class _GroupManager:
    def get_client_group(self, _client_uid):
        return None


class _Context:
    class _Character:
        conf_uid = "test-conf"
        human_name = "Human"

    class _System:
        tool_prompts = {}

    character_config = _Character()
    system_config = _System()


class TestConversationTriggerConcurrency(unittest.TestCase):
    def test_running_individual_task_rejects_second_trigger(self):
        from src.open_llm_vtuber.conversations.conversation_handler import (
            handle_conversation_trigger,
        )

        async def scenario():
            client_uid = "client-1"
            running = asyncio.create_task(asyncio.sleep(60))
            current_tasks = {client_uid: running}
            ws = type("WS", (), {"send_text": AsyncMock()})()
            try:
                with patch(
                    "src.open_llm_vtuber.conversations.conversation_handler.process_single_conversation"
                ) as process:
                    await handle_conversation_trigger(
                        msg_type="text-input",
                        data={"type": "text-input", "text": "hello"},
                        client_uid=client_uid,
                        context=_Context(),
                        websocket=ws,
                        client_contexts={client_uid: _Context()},
                        client_connections={client_uid: ws},
                        chat_group_manager=_GroupManager(),
                        received_data_buffers={client_uid: []},
                        current_conversation_tasks=current_tasks,
                        broadcast_to_group=AsyncMock(),
                    )
                    process.assert_not_called()
                    self.assertIs(current_tasks[client_uid], running)
            finally:
                running.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await running

        asyncio.run(scenario())


class TestTtsDelivery(unittest.TestCase):
    def test_wait_for_delivery_waits_until_audio_frame_is_sent(self):
        from src.open_llm_vtuber.conversations.tts_manager import TTSTaskManager

        async def scenario():
            manager = TTSTaskManager()
            gate = asyncio.Event()
            frames = []

            async def send(text):
                await gate.wait()
                frames.append(json.loads(text))

            manager._sender_task = asyncio.create_task(
                manager._process_payload_queue(send)
            )
            await manager._payload_queue.put(
                ({
                    "type": "audio",
                    "audio": None,
                    "volumes": [],
                    "slice_length": 20,
                }, 0)
            )
            delivery = asyncio.create_task(manager.wait_for_delivery())
            await asyncio.sleep(0)
            self.assertFalse(delivery.done())
            self.assertEqual(frames, [])
            gate.set()
            self.assertTrue(await delivery)
            self.assertEqual(frames[0]["type"], "audio")
            manager.clear()

        asyncio.run(scenario())


class TestHistoryWriteConsistency(unittest.TestCase):
    def test_concurrent_messages_and_brief_are_all_preserved(self):
        from src.open_llm_vtuber import chat_history_manager as history

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                uid = history.create_new_history("regression-conf")

                async def scenario():
                    jobs = [
                        asyncio.to_thread(
                            history.store_message,
                            "regression-conf", uid, "ai", f"reply-{i}"
                        )
                        for i in range(12)
                    ]
                    jobs.append(
                        asyncio.to_thread(
                            history.upsert_task_brief,
                            "regression-conf", uid, "task-1", "brief"
                        )
                    )
                    await asyncio.gather(*jobs)

                asyncio.run(scenario())
                messages = history.get_history("regression-conf", uid)
                self.assertEqual(
                    {m["content"] for m in messages if m.get("kind") != "task_brief"},
                    {f"reply-{i}" for i in range(12)},
                )
                briefs = [m for m in messages if m.get("kind") == "task_brief"]
                self.assertEqual(len(briefs), 1)
                self.assertEqual(briefs[0]["content"], "brief")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
