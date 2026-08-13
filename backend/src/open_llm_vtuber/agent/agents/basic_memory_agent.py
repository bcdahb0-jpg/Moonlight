from typing import (
    AsyncIterator,
    List,
    Dict,
    Any,
    Callable,
    Literal,
    Union,
    Optional,
)
from loguru import logger
from .agent_interface import AgentInterface
from ..output_types import SentenceOutput, DisplayText
from ..stateless_llm.stateless_llm_interface import StatelessLLMInterface
from ..stateless_llm.claude_llm import AsyncLLM as ClaudeAsyncLLM
from ..stateless_llm.openai_compatible_llm import AsyncLLM as OpenAICompatibleAsyncLLM
from ...chat_history_manager import get_history
from ..transformers import (
    sentence_divider,
    actions_extractor,
    tts_filter,
    display_processor,
)
from ...config_manager import TTSPreprocessorConfig
from ..input_types import BatchInput, TextSource
from prompts import prompt_loader
from ...mcpp.tool_manager import ToolManager
from ...mcpp.json_detector import StreamJSONDetector
from ...mcpp.types import ToolCallObject
from ...mcpp.tool_executor import ToolExecutor
from loguru import logger as _logger

#: 内置工具：委托任务内核执行（sub-agent 委派范式，2026-08-09）
#: 解决"聊天 AI 不能用工具"——basic_memory_agent 默认无 MCP 服务器，
#: 通过此内置工具在需要查证/搜索/操作文件时调用任务内核（有 bash + skill +
#: delegate agents + MCP 全能力），结果作为 tool_message 注入聊天上下文。
#: OpenAI function calling 格式（Claude 暂不支持内置工具，仅 OpenAI）。
DELEGATE_TASK_TOOL_NAME = "delegate_to_task"
DELEGATE_TASK_TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": DELEGATE_TASK_TOOL_NAME,
            "description": (
                "委派任务内核执行轻量子任务：用于查证事实/搜索网络/检索资料/操作本地文件/执行命令。"
                "任务内核拥有 bash / skill / delegate agents / MCP 等完整能力，会在工作目录里真去执行，"
                "返回真实结果（不是编造）。当用户问题需要客观查证、具体搜索或文件操作时必须调用此工具；"
                "纯闲聊、问候、情感、观点讨论等不需要调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "明确、可执行的执行目标，例："
                            "'查询 DeepSeek V4 Flash 是否已正式发布'"
                            "'读取当前目录下 README.md 前 50 行并总结'"
                            "'搜索 github 上 Moonlight 项目的最新 release 版本'"
                        ),
                    },
                },
                "required": ["goal"],
            },
        },
    }
]


class BasicMemoryAgent(AgentInterface):
    """Agent with basic chat memory and tool calling support."""

    _system: str = "You are a helpful assistant."

    def __init__(
        self,
        llm: StatelessLLMInterface,
        system: str,
        live2d_model,
        tts_preprocessor_config: TTSPreprocessorConfig = None,
        faster_first_response: bool = True,
        segment_method: str = "pysbd",
        use_mcpp: bool = False,
        interrupt_method: Literal["system", "user"] = "user",
        tool_prompts: Dict[str, str] = None,
        tool_manager: Optional[ToolManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        mcp_prompt_string: str = "",
    ):
        """Initialize agent with LLM and configuration."""
        super().__init__()
        self._memory = []
        self._live2d_model = live2d_model
        self._tts_preprocessor_config = tts_preprocessor_config
        self._faster_first_response = faster_first_response
        self._segment_method = segment_method
        self._use_mcpp = use_mcpp
        self.interrupt_method = interrupt_method
        self._tool_prompts = tool_prompts or {}
        self._interrupt_handled = False
        self.prompt_mode_flag = False

        self._tool_manager = tool_manager
        self._tool_executor = tool_executor
        self._mcp_prompt_string = mcp_prompt_string
        self._json_detector = StreamJSONDetector()

        self._formatted_tools_openai = []
        self._formatted_tools_claude = []

        self._set_llm(llm)
        self.set_system(system if system else self._system)

        # 2026-08-09：内置委托任务工具（始终启用，不依赖 MCP 服务器）——
        # 解决"聊天 AI 不能用工具"。OpenAI 路径注入 delegate_to_task schema；
        # Claude 路径暂不支持内置工具（保留 MCP 路线）。需在 _set_llm 之后装配
        # （_llm 已赋值才能判类型）。
        if isinstance(self._llm, OpenAICompatibleAsyncLLM):
            self._formatted_tools_openai = list(DELEGATE_TASK_TOOL_SCHEMA)
        if self._tool_manager:
            tm_openai = self._tool_manager.get_formatted_tools("OpenAI")
            tm_claude = self._tool_manager.get_formatted_tools("Claude")
            # 合并：MCP 工具 + 内置委托任务工具（去重）
            existing_names = {t.get("function", {}).get("name") for t in self._formatted_tools_openai}
            self._formatted_tools_openai = self._formatted_tools_openai + [
                t for t in tm_openai if t.get("function", {}).get("name") not in existing_names
            ]
            self._formatted_tools_claude = tm_claude
            logger.debug(
                f"Agent received pre-formatted tools - OpenAI: {len(self._formatted_tools_openai)}, Claude: {len(self._formatted_tools_claude)}"
            )
        elif self._formatted_tools_openai:
            logger.debug(
                f"Built-in delegate_to_task enabled (OpenAI). Claude path: unsupported."
            )
        else:
            logger.debug(
                "ToolManager not provided, agent will not have pre-formatted tools."
            )

        if self._use_mcpp and not all(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is True, but some MCP components are missing in the agent. Tool calling might not work as expected."
            )
        elif not self._use_mcpp and any(
            [
                self._tool_manager,
                self._tool_executor,
                self._json_detector,
            ]
        ):
            logger.warning(
                "use_mcpp is False, but some MCP components were passed to the agent."
            )

        logger.info("BasicMemoryAgent initialized.")

    def _set_llm(self, llm: StatelessLLMInterface):
        """Set the LLM for chat completion."""
        self._llm = llm
        self.chat = self._chat_function_factory()

    def set_system(self, system: str):
        """Set the system prompt."""
        logger.debug(f"Memory Agent: Setting system prompt: '''{system}'''")

        if self.interrupt_method == "user":
            system = f"{system}\n\nIf you received `[interrupted by user]` signal, you were interrupted."

        self._system = system

    async def _call_delegate_task(self, goal: str) -> dict:
        """内置工具 delegate_to_task 的执行器（2026-08-09 / 2026-08-10 结构化）。

        异步 HTTP POST 调用 task_platform 的 /api/chat/delegate-task 端点，
        在当前会话工作目录创建临时任务、start_run、轮询直到完成，返回最后 AI 文本。

        2026-08-10 改造：
        - **返回结构化 dict**（不再拼裸字符串）：成功/失败可区分，调用方据此决定
          结果直达聊天区还是让 LLM 口语转述（避免 HTTP 502 等原始错误暴露给用户）。
        - **瞬时故障重试**：5xx / 网络异常最多重试 2 次（指数退避 1s/2s），
          减少一次抖动就让用户看到失败（截图场景：追问"文件在哪"撞上 502）。

        返回：
        {
          "ok": bool,              # 任务是否成功完成
          "status": str,           # completed / error / timeout / ...
          "summary": str,          # 成功时的最后 AI 文本（仅 ok=True 非空）
          "error": str,            # 失败原因（给 LLM 的技术描述，含可操作信息）
        }
        """
        import asyncio
        import json as _json
        try:
            import httpx  # 异步 HTTP 客户端（项目已在用）
        except ImportError:
            return {"ok": False, "status": "error", "summary": "", "error": "后端缺 httpx，无法委派任务。"}

        # 时间是一个需要“现在这一刻”语义的特例。不要把它交给任务
        # agent 再经网络 API/LLM 摘要一遍：公共 GET 可能命中旧缓存，
        # LLM 也可能从上下文复述上一次时间。
        try:
            from ...task_platform.time_tool import accurate_time_result, is_current_time_goal

            if is_current_time_goal(goal):
                return await accurate_time_result()
        except Exception as exc:
            _logger.warning(f"[chat-delegate] direct time result unavailable: {exc}")

        conf_uid = ""
        history_uid = ""
        try:
            if getattr(self, "_character_config", None):
                conf_uid = str(self._character_config.conf_uid or "")
            history_uid = str(getattr(self, "_history_uid", "") or "")
        except Exception:
            pass
        if not conf_uid or not history_uid:
            return {"ok": False, "status": "error", "summary": "", "error": "缺少 conf_uid / history_uid 上下文，无法确定工作目录。"}

        # 瞬时故障重试：5xx / 连接异常最多重试 2 次（指数退避 1s/2s）。
        # 4xx（参数/鉴权问题）与明确的任务失败不重试，直接返回。
        last_err = ""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        "http://127.0.0.1:12393/api/chat/delegate-task",
                        json={
                            "goal": goal,
                            "conversation_uid": history_uid,
                            "conf_uid": conf_uid,
                            "timeout_sec": 90,
                        },
                    )
                if resp.status_code >= 500 and attempt < 2:
                    last_err = f"HTTP {resp.status_code}"
                    await asyncio.sleep(1 + attempt)
                    continue
                if resp.status_code != 200:
                    body = ""
                    try:
                        body = str(resp.json().get("error") or "") or resp.text[:200]
                    except Exception:
                        body = resp.text[:200]
                    return {
                        "ok": False,
                        "status": "error",
                        "summary": "",
                        "error": f"任务服务返回异常（HTTP {resp.status_code}）：{body}".strip(),
                    }
                data = resp.json()
                if not data.get("ok"):
                    return {
                        "ok": False,
                        "status": str(data.get("status") or "error"),
                        "summary": "",
                        "error": f"任务执行失败：{data.get('error') or data.get('status') or 'unknown'}",
                    }
                summary = str(data.get("summary") or "").strip()
                status = str(data.get("status") or "")
                if not summary:
                    summary = f"任务执行完成（{status}），但无输出摘要。"
                return {
                    "ok": True,
                    "status": status or "completed",
                    "summary": summary[:4000],
                    "task_id": str(data.get("task_id") or ""),
                    "error": "",
                }
            except Exception as e:
                _logger.warning(f"[chat-delegate] HTTP call failed (attempt {attempt + 1}/3): {e}")
                last_err = f"{type(e).__name__}: {e}"
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
        return {"ok": False, "status": "error", "summary": "", "error": f"任务服务连接失败：{last_err}"}

    def _add_message(
        self,
        message: Union[str, List[Dict[str, Any]]],
        role: str,
        display_text: DisplayText | None = None,
        skip_memory: bool = False,
    ):
        """Add message to memory."""
        if skip_memory:
            return

        text_content = ""
        if isinstance(message, list):
            for item in message:
                if item.get("type") == "text":
                    text_content += item["text"] + " "
            text_content = text_content.strip()
        elif isinstance(message, str):
            text_content = message
        else:
            logger.warning(
                f"_add_message received unexpected message type: {type(message)}"
            )
            text_content = str(message)

        if not text_content and role == "assistant":
            return

        message_data = {
            "role": role,
            "content": text_content,
        }

        if display_text:
            if display_text.name:
                message_data["name"] = display_text.name
            if display_text.avatar:
                message_data["avatar"] = display_text.avatar

        if (
            self._memory
            and self._memory[-1]["role"] == role
            and self._memory[-1]["content"] == text_content
        ):
            return

        self._memory.append(message_data)

    def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
        """Load memory from chat history."""
        # 2026-08-09 修复：同步会话上下文（delegate_to_task 靠它定位工作目录）。
        # 会话切换/新建/自动创建都走此方法（websocket_handler.fetch_history /
        # create_history / single_conversation 首消息自动建会话），在此统一同步，
        # 否则 _history_uid 停留在 init_agent 时的旧值 → 委托任务拿不到工作目录。
        self._history_uid = history_uid

        messages = get_history(conf_uid, history_uid)

        self._memory = []
        for msg in messages:
            role = "user" if msg["role"] == "human" else "assistant"
            content = msg["content"]
            if isinstance(content, str) and content:
                self._memory.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )
            else:
                logger.warning(f"Skipping invalid message from history: {msg}")
        logger.info(f"Loaded {len(self._memory)} messages from history.")

    def handle_interrupt(self, heard_response: str) -> None:
        """Handle user interruption."""
        if self._interrupt_handled:
            return

        self._interrupt_handled = True

        # 只在真的聽到內容時才補 "..."（表示話被打斷）；heard_response 為空/全空白時
        # 不附 "..."，避免把無意義的 "..." 寫進記憶，僅留下 [Interrupted by user] 標記。
        partial = (heard_response + "...") if heard_response.strip() else heard_response

        if self._memory and self._memory[-1]["role"] == "assistant":
            self._memory[-1]["content"] = partial
        elif heard_response:
            self._memory.append(
                {
                    "role": "assistant",
                    "content": partial,
                }
            )

        interrupt_role = "system" if self.interrupt_method == "system" else "user"
        self._memory.append(
            {
                "role": interrupt_role,
                "content": "[Interrupted by user]",
            }
        )
        logger.info(f"Handled interrupt with role '{interrupt_role}'.")

    def _to_text_prompt(self, input_data: BatchInput) -> str:
        """Format input data to text prompt."""
        message_parts = []

        for text_data in input_data.texts:
            if text_data.source == TextSource.INPUT:
                message_parts.append(text_data.content)
            elif text_data.source == TextSource.CLIPBOARD:
                message_parts.append(
                    f"[User shared content from clipboard: {text_data.content}]"
                )

        if input_data.images:
            message_parts.append("\n[User has also provided images]")

        return "\n".join(message_parts).strip()

    def _to_messages(self, input_data: BatchInput) -> List[Dict[str, Any]]:
        """Prepare messages for LLM API call."""
        messages = self._memory.copy()
        user_content = []
        text_prompt = self._to_text_prompt(input_data)
        if text_prompt:
            user_content.append({"type": "text", "text": text_prompt})

        if input_data.images:
            image_added = False
            for img_data in input_data.images:
                if isinstance(img_data.data, str) and img_data.data.startswith(
                    "data:image"
                ):
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": img_data.data, "detail": "auto"},
                        }
                    )
                    image_added = True
                else:
                    logger.error(
                        f"Invalid image data format: {type(img_data.data)}. Skipping image."
                    )

            if not image_added and not text_prompt:
                logger.warning(
                    "User input contains images but none could be processed."
                )

        if user_content:
            user_message = {"role": "user", "content": user_content}
            messages.append(user_message)

            skip_memory = False
            if input_data.metadata and input_data.metadata.get("skip_memory", False):
                skip_memory = True

            if not skip_memory:
                self._add_message(
                    text_prompt if text_prompt else "[User provided image(s)]", "user"
                )
        else:
            logger.warning("No content generated for user message.")

        return messages

    async def _claude_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle Claude interaction loop with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls = []
        current_assistant_message_content = []

        while True:
            stream = self._llm.chat_completion(messages, self._system, tools=tools)
            pending_tool_calls.clear()
            current_assistant_message_content.clear()

            async for event in stream:
                if event["type"] == "text_delta":
                    text = event["text"]
                    current_turn_text += text
                    yield text
                    if (
                        not current_assistant_message_content
                        or current_assistant_message_content[-1]["type"] != "text"
                    ):
                        current_assistant_message_content.append(
                            {"type": "text", "text": text}
                        )
                    else:
                        current_assistant_message_content[-1]["text"] += text
                elif event["type"] == "tool_use_complete":
                    tool_call_data = event["data"]
                    logger.info(
                        f"Tool request: {tool_call_data['name']} (ID: {tool_call_data['id']})"
                    )
                    pending_tool_calls.append(tool_call_data)
                    current_assistant_message_content.append(
                        {
                            "type": "tool_use",
                            "id": tool_call_data["id"],
                            "name": tool_call_data["name"],
                            "input": tool_call_data["input"],
                        }
                    )
                # elif event["type"] == "message_delta":
                #     if event["data"]["delta"].get("stop_reason"):
                #         stop_reason = event["data"]["delta"].get("stop_reason")
                elif event["type"] == "message_stop":
                    break
                elif event["type"] == "error":
                    logger.error(f"LLM API Error: {event['message']}")
                    yield f"[Error from LLM: {event['message']}]"
                    return

            if pending_tool_calls:
                filtered_assistant_content = [
                    block
                    for block in current_assistant_message_content
                    if not (
                        block.get("type") == "text"
                        and not block.get("text", "").strip()
                    )
                ]

                if filtered_assistant_content:
                    messages.append(
                        {"role": "assistant", "content": filtered_assistant_content}
                    )
                    assistant_text_for_memory = "".join(
                        [
                            c["text"]
                            for c in filtered_assistant_content
                            if c["type"] == "text"
                        ]
                    ).strip()
                    if assistant_text_for_memory:
                        self._add_message(assistant_text_for_memory, "assistant")

                tool_results_for_llm = []
                if not self._tool_executor:
                    logger.error(
                        "Claude Tool interaction requested but ToolExecutor is not available."
                    )
                    yield "[Error: ToolExecutor not configured]"
                    return

                tool_executor_iterator = self._tool_executor.execute_tools(
                    tool_calls=pending_tool_calls,
                    caller_mode="Claude",
                )
                try:
                    while True:
                        update = await anext(tool_executor_iterator)
                        if update.get("type") == "final_tool_results":
                            tool_results_for_llm = update.get("results", [])
                            break
                        else:
                            yield update
                except StopAsyncIteration:
                    logger.warning(
                        "Tool executor finished without final results marker."
                    )

                if tool_results_for_llm:
                    messages.append({"role": "user", "content": tool_results_for_llm})

                # stop_reason = None
                continue
            else:
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")
                return

    async def _openai_tool_interaction_loop(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
        """Handle OpenAI interaction with tool support."""
        messages = initial_messages.copy()
        current_turn_text = ""
        pending_tool_calls: Union[List[ToolCallObject], List[Dict[str, Any]]] = []
        current_system_prompt = self._system

        while True:
            if self.prompt_mode_flag:
                if self._mcp_prompt_string:
                    current_system_prompt = (
                        f"{self._system}\n\n{self._mcp_prompt_string}"
                    )
                else:
                    logger.warning("Prompt mode active but mcp_prompt_string is empty!")
                    current_system_prompt = self._system
                tools_for_api = None
            else:
                current_system_prompt = self._system
                tools_for_api = tools

            stream = self._llm.chat_completion(
                messages, current_system_prompt, tools=tools_for_api
            )
            pending_tool_calls.clear()
            current_turn_text = ""
            assistant_message_for_api = None
            detected_prompt_json = None
            goto_next_while_iteration = False

            async for event in stream:
                if self.prompt_mode_flag:
                    if isinstance(event, str):
                        current_turn_text += event
                        if self._json_detector:
                            potential_json = self._json_detector.process_chunk(event)
                            if potential_json:
                                try:
                                    if isinstance(potential_json, list):
                                        detected_prompt_json = potential_json
                                    elif isinstance(potential_json, dict):
                                        detected_prompt_json = [potential_json]

                                    if detected_prompt_json:
                                        break
                                except Exception as e:
                                    logger.error(f"Error parsing detected JSON: {e}")
                                    if self._json_detector:
                                        self._json_detector.reset()
                                    yield f"[Error parsing tool JSON: {e}]"
                                    goto_next_while_iteration = True
                                    break
                        yield event
                else:
                    if isinstance(event, str):
                        current_turn_text += event
                        yield event
                    elif isinstance(event, list) and all(
                        isinstance(tc, ToolCallObject) for tc in event
                    ):
                        pending_tool_calls = event
                        assistant_message_for_api = {
                            "role": "assistant",
                            "content": current_turn_text if current_turn_text else None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": tc.type,
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in pending_tool_calls
                            ],
                        }
                        break
                    elif event == "__API_NOT_SUPPORT_TOOLS__":
                        logger.warning(
                            f"LLM {getattr(self._llm, 'model', '')} has no native tool support. Switching to prompt mode."
                        )
                        self.prompt_mode_flag = True
                        if self._tool_manager:
                            self._tool_manager.disable()
                        if self._json_detector:
                            self._json_detector.reset()
                        goto_next_while_iteration = True
                        break
            if goto_next_while_iteration:
                continue

            if detected_prompt_json:
                logger.info("Processing tools detected via prompt mode JSON.")
                self._add_message(current_turn_text, "assistant")

                parsed_tools = self._tool_executor.process_tool_from_prompt_json(
                    detected_prompt_json
                )
                if parsed_tools:
                    tool_results_for_llm = []
                    if not self._tool_executor:
                        logger.error(
                            "Prompt Tool interaction requested but ToolExecutor/MCPClient is not available."
                        )
                        yield "[Error: ToolExecutor/MCPClient not configured for prompt mode]"
                        continue

                    tool_executor_iterator = self._tool_executor.execute_tools(
                        tool_calls=parsed_tools,
                        caller_mode="Prompt",
                    )
                    try:
                        while True:
                            update = await anext(tool_executor_iterator)
                            if update.get("type") == "final_tool_results":
                                tool_results_for_llm = update.get("results", [])
                                break
                            else:
                                yield update
                    except StopAsyncIteration:
                        logger.warning(
                            "Prompt mode tool executor finished without final results marker."
                        )

                    if tool_results_for_llm:
                        result_strings = [
                            res.get("content", "Error: Malformed result")
                            for res in tool_results_for_llm
                        ]
                        combined_results_str = "\n".join(result_strings)
                        messages.append(
                            {"role": "user", "content": combined_results_str}
                        )
                continue

            elif pending_tool_calls and assistant_message_for_api:
                messages.append(assistant_message_for_api)
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")

                tool_results_for_llm = []
                if not self._tool_executor:
                    logger.error(
                        "OpenAI Tool interaction requested but ToolExecutor/MCPClient is not available."
                    )
                    yield "[Error: ToolExecutor/MCPClient not configured for OpenAI mode]"
                    continue

                tool_executor_iterator = self._tool_executor.execute_tools(
                    tool_calls=pending_tool_calls,
                    caller_mode="OpenAI",
                )
                try:
                    while True:
                        update = await anext(tool_executor_iterator)
                        if update.get("type") == "final_tool_results":
                            tool_results_for_llm = update.get("results", [])
                            break
                        else:
                            yield update
                except StopAsyncIteration:
                    logger.warning(
                        "OpenAI tool executor finished without final results marker."
                    )

                if tool_results_for_llm:
                    messages.extend(tool_results_for_llm)
                continue

            else:
                if current_turn_text:
                    self._add_message(current_turn_text, "assistant")
                return

    def _chat_function_factory(
        self,
    ) -> Callable[[BatchInput], AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]]:
        """Create the chat pipeline function."""

        @tts_filter(self._tts_preprocessor_config)
        @display_processor()
        @actions_extractor(self._live2d_model)
        @sentence_divider(
            faster_first_response=self._faster_first_response,
            segment_method=self._segment_method,
            valid_tags=["think"],
        )
        async def chat_with_memory(
            input_data: BatchInput,
        ) -> AsyncIterator[Union[str, Dict[str, Any]]]:
            """Process chat with memory and tools."""
            self.reset_interrupt()
            self.prompt_mode_flag = False

            messages = self._to_messages(input_data)
            tools = None
            tool_mode = None
            llm_supports_native_tools = False

            if self._use_mcpp and self._tool_manager:
                tools = None
                if isinstance(self._llm, ClaudeAsyncLLM):
                    tool_mode = "Claude"
                    tools = self._formatted_tools_claude
                    llm_supports_native_tools = True
                elif isinstance(self._llm, OpenAICompatibleAsyncLLM):
                    tool_mode = "OpenAI"
                    tools = self._formatted_tools_openai
                    llm_supports_native_tools = True
                else:
                    logger.warning(
                        f"LLM type {type(self._llm)} not explicitly handled for tool mode determination."
                    )

                if llm_supports_native_tools and not tools:
                    logger.warning(
                        f"No tools available/formatted for '{tool_mode}' mode, despite MCP being enabled."
                    )

            if self._use_mcpp and tool_mode == "Claude":
                logger.debug(
                    f"Starting Claude tool interaction loop with {len(tools)} tools."
                )
                async for output in self._claude_tool_interaction_loop(
                    messages, tools if tools else []
                ):
                    yield output
                return
            elif self._use_mcpp and tool_mode == "OpenAI":
                logger.debug(
                    f"Starting OpenAI tool interaction loop with {len(tools)} tools."
                )
                async for output in self._openai_tool_interaction_loop(
                    messages, tools if tools else []
                ):
                    yield output
                return
            else:
                # 2026-08-09：内置 delegate_to_task 工具循环（OpenAI 路径）。
                # 即使没启用 MCP，也支持 LLM 自主委派任务内核查证/搜索/操作文件，
                # 解决"聊天 AI 不能用工具"。
                if (
                    self._formatted_tools_openai
                    and isinstance(self._llm, OpenAICompatibleAsyncLLM)
                ):
                    async for output in self._simple_chat_with_builtin_tool(messages):
                        yield output
                else:
                    logger.info("Starting simple chat completion (no built-in tools).")
                    token_stream = self._llm.chat_completion(messages, self._system)
                    complete_response = ""
                    async for event in token_stream:
                        text_chunk = ""
                        if isinstance(event, dict) and event.get("type") == "text_delta":
                            text_chunk = event.get("text", "")
                        elif isinstance(event, str):
                            text_chunk = event
                        else:
                            continue
                        if text_chunk:
                            yield text_chunk
                            complete_response += text_chunk
                    if complete_response:
                        self._add_message(complete_response, "assistant")

        return chat_with_memory

    async def _simple_chat_with_builtin_tool(
        self, messages: List[Dict[str, Any]]
    ) -> AsyncIterator[str]:
        """简单 chat 路径下支持内置工具 delegate_to_task 的循环（2026-08-09）。

        2026-08-09 第二次修复（用户截图：AI 只输出英文思考、工具不执行）：
        - **补 `List[ToolCallObject]` 事件分支**：LLM 层（openai_compatible_llm）把
          工具调用以列表形式 yield，此前既不是 str 也不是 dict → 被静默丢弃，
          工具永远不会真正执行，只剩模型写在 content 里的"我该用工具"思考文本。
        - **工具轮次的 content 只缓冲、不 yield**：模型调用工具前常输出英文思考/
          计划，直接透出即"内心戏泄漏"。轮末确有工具调用 → 丢弃缓冲；无工具 →
          才把缓冲文本整体 yield（这才是用户可见的最终回复）。
        - **工具执行期间 yield `{"type": "tool_call_status", ...}`**：single_conversation
          会把它转发给前端显示执行状态（"执行过程显示"）。
        """
        max_rounds = 3  # 防止工具循环死循环
        tools = list(self._formatted_tools_openai)
        current_messages = list(messages)
        system = self._with_tool_guidance(self._system)

        for round_no in range(1, max_rounds + 1):
            logger.info(
                f"[chat-builtin-tool] round={round_no}, "
                f"tools={[t.get('function', {}).get('name') for t in tools]}"
            )
            complete_response = ""
            pending_tool_calls: list[dict] = []

            token_stream = self._llm.chat_completion(
                current_messages, system, tools=tools
            )
            async for event in token_stream:
                if isinstance(event, str):
                    # content 文本：缓冲（不立即 yield，防止工具轮次思考外泄）
                    complete_response += event
                elif isinstance(event, dict):
                    etype = event.get("type")
                    if etype == "text_delta":
                        complete_response += event.get("text", "")
                    # 其他 dict（error 等）忽略
                elif isinstance(event, list):
                    # List[ToolCallObject]：工具调用事件（含流中完成与流末两种 yield）
                    for tc in event:
                        if hasattr(tc, "function"):  # ToolCallObject
                            pending_tool_calls.append(
                                {
                                    "id": getattr(tc, "id", "") or "",
                                    "name": getattr(tc.function, "name", "") or "",
                                    "arguments": getattr(tc.function, "arguments", "")
                                    or "",
                                }
                            )
                        elif isinstance(tc, dict):
                            fn = tc.get("function") or {}
                            pending_tool_calls.append(
                                {
                                    "id": tc.get("id", "") or "",
                                    "name": fn.get("name", "") or "",
                                    "arguments": fn.get("arguments", "") or "",
                                }
                            )
                else:
                    logger.debug(
                        f"[chat-builtin-tool] skip unknown event: {type(event)}"
                    )

            if not pending_tool_calls:
                # 无工具调用：缓冲文本即最终回复，整体输出
                if complete_response.strip():
                    yield complete_response
                    self._add_message(complete_response.strip(), "assistant")
                return

            # 有工具调用：本轮 content（模型思考/计划）丢弃不外泄，
            # 仅把 assistant(tool_calls) + tool 结果注入上下文，继续下一轮。
            current_messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in pending_tool_calls
                    ],
                }
            )

            # 执行内置工具并注入 tool result
            import json as _json

            for tc in pending_tool_calls:
                name = tc["name"]
                tool_call_id = tc["id"]
                raw_args = tc["arguments"]
                if name == DELEGATE_TASK_TOOL_NAME:
                    try:
                        # OpenAI 兼容层可能给 dict 也可能给 JSON 字符串，两者都要吃
                        args = raw_args if isinstance(raw_args, dict) else (_json.loads(raw_args) if raw_args else {})
                    except Exception:
                        args = {}
                    goal = str(args.get("goal") or "").strip()
                    if not goal:
                        result = {"ok": False, "status": "error", "summary": "", "error": "工具调用失败：缺少 goal 参数。"}
                    else:
                        logger.info(
                            f"[chat-builtin-tool] delegate_to_task: {goal[:80]}"
                        )
                        yield {
                            "type": "tool_call_status",
                            "text": f"🔧 正在执行：{goal[:40]}",
                        }
                        result = await self._call_delegate_task(goal)
                        yield {"type": "tool_call_status", "text": ""}
                        # 2026-08-10 协议拆分：
                        # - 成功 → task_result 直达聊天区（完整清单/表格立即可见），
                        #   并提示 LLM 只总结要点、不要逐字复述；
                        # - 失败 → **不**推 task_result（此前把 HTTP 502 原样展示给用户，
                        #   体验断裂）。技术细节只进 tool message，由 LLM 用口语向用户
                        #   说明"刚才的操作没完成 + 可操作建议"，不暴露状态码/堆栈。
                        if result.get("ok"):
                            summary = str(result.get("summary") or "").strip()
                            if summary:
                                yield {
                                    "type": "task_result",
                                    "status": result.get("status") or "completed",
                                    "content": summary,
                                    "task_id": result.get("task_id") or None,
                                }
                            if result.get("direct"):
                                # 结构化事实直接成为最终气泡，避免下一轮
                                # LLM 又把准确时间改写成旧时间。
                                if summary:
                                    yield summary
                                    self._add_message(summary, "assistant")
                                return
                            tool_content = (
                                f"{summary[:4000]}\n\n"
                                "请先直接回答用户原问题，第一句必须是结论，不要以‘我来查一下’或‘搜索结果是’开头；"
                                "如果结果没有确认该事实，第一句明确说‘目前无法确认/没有查到’，再简短说明原因。"
                                "完整任务结果已由系统展示在任务卡中，回复只总结与问题直接相关的要点，"
                                "不要逐字复述完整清单或表格。"
                            )
                        else:
                            err = str(result.get("error") or "任务执行失败").strip()
                            tool_content = (
                                f"任务委派执行失败：{err}\n\n"
                                "请用简体中文、自然的语气先直接回答用户问题，第一句必须是结论；若没有查到答案，"
                                "第一句明确说‘目前无法确认/没有查到’，再告诉用户刚才的操作没有完成，"
                                "并给出可操作的建议（如：稍后重试、提供更明确的目标、"
                                "或说明结果在哪个工作目录），不要展示 HTTP 状态码/异常类名/"
                                "堆栈等技术细节，不要编造任务结果。"
                                "若用户问的是**之前已完成任务**的文件位置/结果，"
                                "请优先依据对话历史中的【任务简报】（含工作目录与生成文件）回答，"
                                "不要再调用 delegate_to_task。"
                            )
                    current_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": tool_content,
                        }
                    )
                else:
                    # 不支持的内置工具名（不该出现）
                    current_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": f"内置工具 {name} 未实现",
                        }
                    )
            # 继续下一轮：让 LLM 用工具结果生成回复
            continue

        # 达到 max_rounds 仍未收敛——避免无限循环
        logger.warning("[chat-builtin-tool] max_rounds reached, ending.")
        fallback = "（多次工具调用未收敛，本次回复省略）"
        yield fallback
        self._add_message(fallback, "assistant")

    #: 工具使用指引（2026-08-09 第二次修复）：引导 LLM 直接调用工具而非"描述调用"，
    #: 且不得输出思考过程/英文说明；已有同名段落时跳过，避免重复注入。
    _TOOL_GUIDANCE = (
        "\n\n## Tool usage (delegate_to_task)\n"
        "当用户问题需要查证事实、搜索网络、读取或操作本地文件、执行命令时，"
        "直接调用 delegate_to_task 工具（把目标写清楚即可），"
        "不要先输出思考过程、计划或英文说明，也不要在回复正文里描述"
        "\"我打算调用工具/我应该用工具\"——直接调用。"
        "工具返回结果后，用简体中文、自然的语气把真实结果总结给用户；"
        "若工具失败，如实告知失败原因。"
        "注意：工具返回的完整结果（清单/表格/长文本）会由系统自动显示在聊天区，"
        "你只需要用一两句话总结要点（如\"共 18 个文件，含计划文档、原型页等\"），"
        "绝对不要逐字逐句复述完整清单或表格内容——那样会让回复变得极其冗长。"
        "纯闲聊、问候、情感陪伴等无需调用任何工具，直接回复。"
        "\n"
        "【追问优先用历史】用户问\"刚才那个任务的结果/生成的文件在哪/做到哪一步了\"时，"
        "如果对话历史里已有【任务简报】（含工作目录与生成文件），直接依据简报回答，"
        "不要再次调用 delegate_to_task——重复委派会重新执行整个任务，浪费且可能失败。"
        "只有简报里没有的信息（如具体文件内容）才值得再委派一次。"
    )

    def _with_tool_guidance(self, system: str) -> str:
        """在 system prompt 末尾附加工具使用规则（幂等：已有则跳过）。"""
        if not system or "delegate_to_task" in system:
            return system
        return f"{system}\n\n{self._TOOL_GUIDANCE}"

    async def chat(
        self,
        input_data: BatchInput,
    ) -> AsyncIterator[Union[SentenceOutput, Dict[str, Any]]]:
        """Run chat pipeline."""
        chat_func_decorated = self._chat_function_factory()
        async for output in chat_func_decorated(input_data):
            yield output

    def reset_interrupt(self) -> None:
        """Reset interrupt flag."""
        self._interrupt_handled = False

    def start_group_conversation(
        self, human_name: str, ai_participants: List[str]
    ) -> None:
        """Start a group conversation."""
        if not self._tool_prompts:
            logger.warning("Tool prompts dictionary is not set.")
            return

        other_ais = ", ".join(name for name in ai_participants)
        prompt_name = self._tool_prompts.get("group_conversation_prompt", "")

        if not prompt_name:
            logger.warning("No group conversation prompt name found.")
            return

        try:
            group_context = prompt_loader.load_util(prompt_name).format(
                human_name=human_name, other_ais=other_ais
            )
            self._memory.append({"role": "user", "content": group_context})
        except FileNotFoundError:
            logger.error(f"Group conversation prompt file not found: {prompt_name}")
        except KeyError as e:
            logger.error(f"Missing formatting key in group conversation prompt: {e}")
        except Exception as e:
            logger.error(f"Failed to load group conversation prompt: {e}")
