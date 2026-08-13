"""Regressions for delegated-task history leakage and current-time answers."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.open_llm_vtuber import chat_history_manager as history
from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.task_platform.time_tool import current_time_result


class TestTaskHistoryTimeRegressions(unittest.TestCase):
    def test_legacy_delegate_goal_is_hidden_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                uid = history.create_new_history("regression-conf")
                history.store_message("regression-conf", uid, "human", "现在几点？")
                history.store_message(
                    "regression-conf",
                    uid,
                    "ai",
                    "好的，我来查一下。",
                    kind="task_shell",
                    task_id="task-1",
                )
                history.store_message(
                    "regression-conf",
                    uid,
                    "human",
                    "获取当前准确时间，优先用网络时间 API",
                )
                history.store_message("regression-conf", uid, "ai", "现在是 17 点。")
                messages = history.get_history("regression-conf", uid)
                self.assertEqual(
                    [m["content"] for m in messages],
                    ["现在几点？", "现在是 17 点。"],
                )
            finally:
                os.chdir(old_cwd)

    def test_current_time_result_is_backend_now_and_structured(self):
        result = current_time_result()
        self.assertTrue(result["ok"])
        self.assertTrue(result["direct"])
        self.assertIn("后端系统时钟", result["source"])
        self.assertIn("Asia/Shanghai（UTC+8）", result["summary"])
        self.assertRegex(result["summary"], r"\d{4}年\d{1,2}月\d{1,2}日.*\d{2}:\d{2}:\d{2}")

    def test_delegate_time_bypasses_task_and_network(self):
        agent = BasicMemoryAgent.__new__(BasicMemoryAgent)

        async def run():
            return await agent._call_delegate_task("获取当前准确北京时间，包括日期和具体时间")

        with patch(
            "src.open_llm_vtuber.task_platform.time_tool.accurate_time_result",
            new=AsyncMock(return_value=current_time_result()),
        ):
            result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertTrue(result["direct"])
        self.assertNotIn("timeapi.io", result["summary"])


if __name__ == "__main__":
    unittest.main()
