"""聊天 agent 内置工具循环测试（2026-08-09 第二次修复 / 2026-08-10 结构化）。

覆盖 _simple_chat_with_builtin_tool 的关键行为：
1. **List[ToolCallObject] 事件能被捕获**（此前被静默丢弃 → 工具永不执行）；
2. 工具轮次的 content（模型思考）**不外泄**，只有最终回复被 yield；
3. 无工具调用时正常输出文本；
4. **2026-08-10**：`_call_delegate_task` 返回结构化 dict——
   成功 → yield `task_result` 事件；失败 → 不 yield task_result（错误不再
   以「任务结果」暴露给用户），工具消息改为指导 LLM 口语转述。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_chat_builtin_tool.py -q
"""
import asyncio
import unittest
from typing import AsyncIterator

from src.open_llm_vtuber.agent.agents.basic_memory_agent import (
    BasicMemoryAgent,
    DELEGATE_TASK_TOOL_NAME,
)
from src.open_llm_vtuber.mcpp.types import ToolCallObject


class FakeLLM:
    """可编排的假 LLM：按顺序消费 events，产出 str / List[ToolCallObject] / dict。"""

    def __init__(self, rounds):
        # rounds: list of list[event]；每轮一个流
        self._rounds = list(rounds)

    def chat_completion(self, messages, system=None, tools=None):
        # 与真实 openai_compatible_llm 一致：返回 async generator（不是 coroutine）
        async def _gen():
            for ev in self._rounds.pop(0):
                yield ev

        return _gen()


class FakeToolCall(ToolCallObject):
    @classmethod
    def make(cls, name: str, arguments: str, call_id: str = "call_1"):
        return cls.from_dict(
            {
                "id": call_id,
                "index": 0,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )


def _collect(agent, messages):
    async def _run():
        out = []
        async for item in agent._simple_chat_with_builtin_tool(messages):
            out.append(item)
        return out

    return asyncio.run(_run())


def _ok_result(summary="[任务结果] ok"):
    """2026-08-10：成功结果的结构化 dict（与真实 _call_delegate_task 契约一致）。"""
    return {"ok": True, "status": "completed", "summary": summary, "error": ""}


def _fail_result(error="HTTP 502"):
    return {"ok": False, "status": "error", "summary": "", "error": error}


class TestBuiltinToolLoop(unittest.TestCase):
    def _agent(self, llm):
        # 最小化 agent：只挂假 LLM，跳过 MCP 装配
        agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
        agent._llm = llm
        agent._formatted_tools_openai = [
            {"type": "function", "function": {"name": DELEGATE_TASK_TOOL_NAME, "parameters": {}}}
        ]
        agent._memory = []
        agent._add_message = BasicMemoryAgent._add_message.__get__(agent)
        agent._system = "你是小月。"
        return agent

    def test_tool_call_list_is_captured(self):
        """修复①：List[ToolCallObject] 事件必须被捕获并执行工具（不再静默丢弃）。

        编排：round1 只发 tool_call（content 空）；round2 发最终回复。
        """
        agent = self._agent(FakeLLM(
            [
                [
                    [FakeToolCall.make(DELEGATE_TASK_TOOL_NAME, '{"goal": "列出目录"}')],
                ],
                ["目录里有 a.txt 和 b.txt。"],
            ]
        ))
        # 工具执行走 HTTP → 不可用；直接替换执行器
        async def fake_call(goal):
            return _ok_result(f"[任务结果]\n{goal} 已完成")

        agent._call_delegate_task = fake_call

        out = _collect(agent, [{"role": "user", "content": "本目录有什么文件"}])
        texts = [o for o in out if isinstance(o, str)]
        self.assertTrue(any("a.txt" in t for t in texts), f"工具结果应进入最终回复: {texts}")
        # 无工具调用时才会吐文本 → 只应有 round2 一条文本
        self.assertEqual(len(texts), 1, texts)

    def test_thinking_text_not_leaked(self):
        """修复②：工具轮次的 content（模型思考）不得 yield 给用户。"""
        agent = self._agent(FakeLLM(
            [
                # round1：模型先写英文思考，再调工具（修复前的泄漏场景）
                [
                    "The user asks what files are in the current directory. "
                    "I should use the delegate_to_task tool to list files.",
                    [FakeToolCall.make(DELEGATE_TASK_TOOL_NAME, '{"goal": "list files"}')],
                ],
                ["好的主人，目录下有这些文件：a.txt、b.txt。"],
            ]
        ))

        async def fake_call(goal):
            return _ok_result("[任务结果]\na.txt\nb.txt")

        agent._call_delegate_task = fake_call

        out = _collect(agent, [{"role": "user", "content": "本目录有什么文件"}])
        joined = "".join(o for o in out if isinstance(o, str))
        # 思考文本不得出现
        self.assertNotIn("The user asks", joined)
        self.assertNotIn("I should use", joined)
        # 最终回复要包含工具结果
        self.assertIn("a.txt", joined)
        self.assertIn("b.txt", joined)

    def test_plain_chat_no_tool(self):
        """无工具调用：文本原样输出（不丢内容）。"""
        agent = self._agent(FakeLLM([["今天天气不错呢。"]]))
        out = _collect(agent, [{"role": "user", "content": "今天天气怎么样"}])
        texts = [o for o in out if isinstance(o, str)]
        self.assertEqual(texts, ["今天天气不错呢。"])

    def test_status_events_emitted(self):
        """工具执行前后应发出 tool_call_status 事件（前端显示执行状态）。"""
        agent = self._agent(FakeLLM(
            [
                [[FakeToolCall.make(DELEGATE_TASK_TOOL_NAME, '{"goal": "查资料"}')]],
                ["查到了。"],
            ]
        ))

        async def fake_call(goal):
            return _ok_result("[任务结果] ok")

        agent._call_delegate_task = fake_call

        out = _collect(agent, [{"role": "user", "content": "帮我查一下"}])
        statuses = [o for o in out if isinstance(o, dict) and o.get("type") == "tool_call_status"]
        self.assertEqual(len(statuses), 2, statuses)
        self.assertTrue(statuses[0]["text"])
        self.assertEqual(statuses[1]["text"], "")

    def test_success_yields_task_result_event(self):
        """2026-08-10：成功路径 yield task_result（直达聊天区），且不含裸错误。"""
        agent = self._agent(FakeLLM(
            [
                [[FakeToolCall.make(DELEGATE_TASK_TOOL_NAME, '{"goal": "生成番茄钟"}')]],
                ["生成好啦，在下面。"],
            ]
        ))

        async def fake_call(goal):
            return _ok_result("已生成 pomodoro-timer.html")

        agent._call_delegate_task = fake_call

        out = _collect(agent, [{"role": "user", "content": "帮我做一个番茄钟"}])
        results = [o for o in out if isinstance(o, dict) and o.get("type") == "task_result"]
        self.assertEqual(len(results), 1, out)
        self.assertEqual(results[0]["status"], "completed")
        self.assertIn("pomodoro-timer.html", str(results[0]["content"]))
        # 成功结果不含错误字段
        self.assertNotIn("error", results[0])

    def test_failure_does_not_yield_task_result(self):
        """2026-08-10：失败路径**不** yield task_result —— HTTP 502 等原始错误
        不再以「任务结果」形式暴露给用户；错误只注入 tool message 由 LLM 转述。"""
        agent = self._agent(FakeLLM(
            [
                [[FakeToolCall.make(DELEGATE_TASK_TOOL_NAME, '{"goal": "查文件位置"}')]],
                ["抱歉主人，刚才那个操作没成功，你要不再试试？"],
            ]
        ))

        async def fake_call(goal):
            return _fail_result("HTTP 502")

        agent._call_delegate_task = fake_call

        out = _collect(agent, [{"role": "user", "content": "文件在哪"}])
        results = [o for o in out if isinstance(o, dict) and o.get("type") == "task_result"]
        self.assertEqual(len(results), 0, f"失败不得 yield task_result: {out}")
        texts = [o for o in out if isinstance(o, str)]
        self.assertTrue(any("抱歉" in t for t in texts), f"LLM 应口语道歉: {texts}")

    def test_tool_guidance_idempotent(self):
        """工具指引注入幂等：已有 delegate_to_task 段落不再重复拼接。"""
        agent = self._agent(FakeLLM([]))
        s1 = agent._with_tool_guidance("base")
        s2 = agent._with_tool_guidance(s1)
        self.assertIn("delegate_to_task", s1)
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
