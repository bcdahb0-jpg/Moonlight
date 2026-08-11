"""Phase 2a：TaskState schema 与 llm_adapter（TDD）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_agent_state.py -q
"""
import asyncio
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import llm_adapter
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.state import TaskState


class _StubModel(BaseChatModel, BaseModel):
    responses: list[BaseMessage] = Field(default_factory=list)
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


class TestTaskStateSchema:
    def test_create_agent_accepts_taskstate(self):
        """create_agent(state_schema=TaskState) 可编译，额外字段（goal/workspace）作为输入透传。"""
        agent = create_agent(
            model=_StubModel(responses=[AIMessage(content="ok")]),
            state_schema=TaskState,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "t-state-1"}}

        async def run():
            await agent.ainvoke(
                {
                    "messages": [HumanMessage(content="整理目录")],
                    "goal": "按类型整理",
                    "workspace": "/tmp/ws",
                },
                config,
            )
            return await agent.aget_state(config)

        state = asyncio.run(run())
        assert state.values["goal"] == "按类型整理"
        assert state.values["workspace"] == "/tmp/ws"
        contents = [str(m.content) for m in state.values["messages"]]
        assert "ok" in contents

    def test_taskstate_defaults_empty(self):
        """未传入的字段缺省为空（total=False），不阻塞建 agent。"""
        agent = create_agent(
            model=_StubModel(responses=[AIMessage(content="ok")]),
            state_schema=TaskState,
            checkpointer=InMemorySaver(),
        )

        async def run():
            await agent.ainvoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "t-state-2"}})
            return await agent.aget_state({"configurable": {"thread_id": "t-state-2"}})

        state = asyncio.run(run())
        assert state.values.get("goal", "") == ""
        assert state.values.get("workspace", "") == ""
        assert state.values.get("summary", "") == ""
        assert state.values.get("skill_context", "") == ""


class TestLlmAdapter:
    def test_missing_config_raises(self):
        cfg = TaskPlatformConfig(llm_base_url="", llm_api_key="", llm_model="")
        try:
            llm_adapter.build_chat_model(cfg)
        except llm_adapter.TaskLLMConfigError as e:
            assert "未配置" in str(e)
        else:
            raise AssertionError("应抛出 TaskLLMConfigError")

    def test_partial_config_raises(self):
        cfg = TaskPlatformConfig(llm_base_url="http://x", llm_api_key="", llm_model="m")
        try:
            llm_adapter.build_chat_model(cfg)
        except llm_adapter.TaskLLMConfigError:
            pass
        else:
            raise AssertionError("部分配置也应抛出 TaskLLMConfigError")

    def test_complete_config_builds_chatopenai(self):
        cfg = TaskPlatformConfig(
            llm_base_url="http://127.0.0.1:8000/v1",
            llm_api_key="sk-test",
            llm_model="deepseek-chat",
        )
        model = llm_adapter.build_chat_model(cfg)
        assert model.model_name == "deepseek-chat"
        assert model.temperature == 0.3
        assert "127.0.0.1:8000" in str(model.openai_api_base)


if __name__ == "__main__":
    import unittest

    unittest.main()
