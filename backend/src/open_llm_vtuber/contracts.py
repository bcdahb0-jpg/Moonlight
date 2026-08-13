"""WebSocket 通信契约层（Phase 0）。

单一事实源：contracts/ws-protocol.md + contracts/error-codes.json。
本模块为后端出站/入站消息提供 pydantic schema 校验与统一发送入口：

- :data:`ErrorCode`：结构化错误码枚举（须与 contracts/error-codes.json 一致）。
- 出站消息模型：ServerMessage 联合的各个 pydantic 模型。
- :func:`send_message`：所有出站消息的唯一入口 —— schema 校验 + 序列化；
  构造失败时降级发送结构化 ``error``（code=PROTOCOL_ERROR），绝不静默丢帧。
- :func:`send_error`：结构化错误发送入口。
- :func:`validate_client_message`：入站消息白名单校验，非法消息返回原因。

用法（服务端任意发送点）：

    from .contracts import send_message, send_error, ErrorCode
    await send_message(websocket.send_text, {"type": "full-text", "text": "hi"})
    await send_error(websocket.send_text, ErrorCode.LLM_UNREACHABLE, "无法连接模型")
"""
from __future__ import annotations

import json
import logging
import typing
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


def _literal_default(field: Any) -> Optional[str]:
    """提取 ``type: Literal["xxx"]`` 字段的字面量值（pydantic v2）。

    ``Literal["full-text"]`` 这类无默认值字段的 ``default`` 是 PydanticUndefined，
    不能直接用作 key，必须从注解里取字面量。
    """
    ann = field.annotation
    if typing.get_origin(ann) is Literal:
        return typing.get_args(ann)[0]
    return None

# 与 contracts/error-codes.json 保持一致的错误码枚举。
class ErrorCode(str, Enum):
    UNKNOWN = "UNKNOWN"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    ASR_MODEL_MISSING = "ASR_MODEL_MISSING"
    ASR_LOAD_FAILED = "ASR_LOAD_FAILED"
    ASR_TRANSCRIBE_FAILED = "ASR_TRANSCRIBE_FAILED"
    LLM_UNREACHABLE = "LLM_UNREACHABLE"
    LLM_INVALID_KEY = "LLM_INVALID_KEY"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    TTS_FAILED = "TTS_FAILED"
    VAD_FAILED = "VAD_FAILED"
    MIC_PERMISSION_DENIED = "MIC_PERMISSION_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CONFIG_INVALID = "CONFIG_INVALID"
    MCP_SERVER_FAILED = "MCP_SERVER_FAILED"
    MEMORY_FAILED = "MEMORY_FAILED"
    HISTORY_FAILED = "HISTORY_FAILED"
    TRANSLATE_FAILED = "TRANSLATE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


SendText = Callable[[str], Awaitable[None]]
"""服务端向客户端发送 JSON 文本帧的回调（websocket.send_text / 群聊转发等）。"""


# --------------------------------------------------------------------------- #
# 出站消息模型（Server → Client）
# 序列化统一 `exclude_none=True`：可选字段缺省即省略，前端用 ?? 兜底。
# --------------------------------------------------------------------------- #


class SetModelAndConfMessage(BaseModel):
    type: Literal["set-model-and-conf"]
    model_info: dict[str, Any]
    conf_name: str
    character_name: Optional[str] = None
    conf_uid: str
    client_uid: str


class FullTextMessage(BaseModel):
    type: Literal["full-text"]
    text: str
    quote: bool = False


class AudioMessage(BaseModel):
    type: Literal["audio"]
    audio: Optional[str] = None
    volumes: list[float] = Field(default_factory=list)
    slice_length: int = 20
    # 音素级口型数据：每 slice 一个 [a,i,u,e,o] 概率向量（可选增强，
    # 由 utils/viseme.py 的 F1/F2 共振峰分析产出；前端无此字段时回退 RMS 口型）。
    visemes: list[list[float]] = Field(default_factory=list)
    # 情绪强度/时长元数据（Phase 1 面部表情）：{intensity, duration_ms, source}
    # 缺省时前端用 emotion 默认强度与固定时长。
    emotion_meta: Optional[dict[str, Any]] = None
    display_text: Optional[dict[str, Any]] = None
    subtitle_text: Optional[str] = None
    actions: Optional[dict[str, Any]] = None
    forwarded: bool = False
    emotion: Optional[str] = None


class TranscriptMessage(BaseModel):
    type: Literal["transcript"]
    text: str


class UserInputTranscriptionMessage(BaseModel):
    type: Literal["user-input-transcription"]
    text: str


class AffectionUpdateMessage(BaseModel):
    type: Literal["affection-update"]
    affection: dict[str, Any]
    milestone: Optional[str] = None


class ControlMessage(BaseModel):
    type: Literal["control"]
    text: Literal[
        "start-mic",
        "mic-audio-end",
        "interrupt",
        "conversation-chain-start",
        "conversation-chain-end",
    ]


class BackendSynthCompleteMessage(BaseModel):
    type: Literal["backend-synth-complete"]


class IntentEventMessage(BaseModel):
    """P5.1 意图副模型出站：勿扰/闲聊/任务 + 情绪（前端状态条实时显示）。"""

    type: Literal["intent-event"]
    intent: str  # silence | chat | task
    emotion: str
    source: str  # rule | llm | fallback | disabled
    text: Optional[str] = None


class ToolCallStatusMessage(BaseModel):
    """聊天 agent 工具执行状态（basic_memory_agent 的 tool_call_status 事件）。

    text 非空 = 工具执行中（前端显示状态条）；空串 = 结束/清除。
    """

    type: Literal["tool_call_status"]
    text: str
    name: Optional[str] = None


class TaskResultMessage(BaseModel):
    """delegate 任务完整结果直达聊天区（single_conversation 的 task_result 事件）。"""

    type: Literal["task-result"]
    text: str
    name: Optional[str] = None
    avatar: Optional[str] = None
    task_id: Optional[str] = None


class ForceNewMessage(BaseModel):
    type: Literal["force-new-message"]


class ErrorMessage(BaseModel):
    type: Literal["error"]
    code: ErrorCode
    message: str
    recover: Optional[dict[str, Any]] = None


class HistoryListMessage(BaseModel):
    type: Literal["history-list"]
    histories: list[Any] = Field(default_factory=list)


class NewHistoryCreatedMessage(BaseModel):
    type: Literal["new-history-created"]
    history_uid: str
    # v5：会话绑定的工作目录（绝对路径）。空串 = 未绑定（旧链路兼容）。
    # 后端 websocket_handler._handle_create_history 始终携带；前端据此立即
    # 解析 currentWorkspace，避免「刚新建的会话因空会话不进 history-list
    # 而一直提示选择工作目录」。
    workspace: str = ""
    # True = 后端在对话中途自动创建的会话（首次真人消息）；False/缺省 = 用户手动新建。
    # 前端据此决定是否清空当前聊天区：自动创建时聊天区里已有刚回显的用户消息，不能清。
    auto: bool = False


class HistoryDeletedMessage(BaseModel):
    type: Literal["history-deleted"]
    success: bool
    history_uid: Optional[str] = None


class HistoryTitleUpdatedMessage(BaseModel):
    type: Literal["history-title-updated"]
    success: bool
    history_uid: Optional[str] = None
    title: str = ""


class HistoryDataMessage(BaseModel):
    type: Literal["history-data"]
    # Moonlight（2026-08-10 修复）：点击旧会话加载后前端需要同步当前会话
    # uid，否则 state.currentHistoryUid 保持 null，输入会误弹"选择工作目录"。
    history_uid: Optional[str] = None
    messages: list[Any] = Field(default_factory=list)


class HistoryWorkspaceUpdatedMessage(BaseModel):
    type: Literal["history-workspace-updated"]
    success: bool
    history_uid: str
    workspace: str


class HistoriesClearedMessage(BaseModel):
    type: Literal["histories-cleared"]
    removed: int


class ConfigFilesMessage(BaseModel):
    type: Literal["config-files"]
    configs: list[str] = Field(default_factory=list)


class BackgroundFilesMessage(BaseModel):
    type: Literal["background-files"]
    files: list[str] = Field(default_factory=list)


class GroupUpdateMessage(BaseModel):
    type: Literal["group-update"]
    members: list[str] = Field(default_factory=list)
    is_owner: bool = False


class HeartbeatAckMessage(BaseModel):
    type: Literal["heartbeat-ack"]


class ConfigUpdatedMessage(BaseModel):
    type: Literal["config-updated"]


class ConfigSwitchedMessage(BaseModel):
    type: Literal["config-switched"]
    message: str


class ScreenStatusMessage(BaseModel):
    """屏幕感知状态推送（screen_awareness，Phase 0）。"""

    type: Literal["screen-status"]
    enabled: bool = False
    capturing: bool = False
    last_capture_at: Optional[float] = None
    last_analyze_at: Optional[float] = None
    last_window_title: str = ""
    last_window_app: str = ""
    last_scene: str = ""
    last_summary: str = ""
    pause_reason: str = ""
    pending_frames: int = 0
    frames_captured: int = 0
    frames_deduped: int = 0
    analyze_count: int = 0
    last_error: str = ""


class ScreenContextMessage(BaseModel):
    """屏幕上下文就绪通知（前端可据此显示「正在看屏幕」状态）。"""

    type: Literal["screen-context"]
    snapshot: Optional[dict[str, Any]] = None
    frame_available: bool = False
    reason: str = ""


ServerMessage = Union[
    SetModelAndConfMessage,
    FullTextMessage,
    AudioMessage,
    TranscriptMessage,
    UserInputTranscriptionMessage,
    AffectionUpdateMessage,
    ControlMessage,
    BackendSynthCompleteMessage,
    ForceNewMessage,
    ToolCallStatusMessage,
    TaskResultMessage,
    ErrorMessage,
    HistoryListMessage,
    NewHistoryCreatedMessage,
    HistoryDeletedMessage,
    HistoryTitleUpdatedMessage,
    HistoryDataMessage,
    HistoryWorkspaceUpdatedMessage,
    HistoriesClearedMessage,
    ConfigFilesMessage,
    BackgroundFilesMessage,
    GroupUpdateMessage,
    HeartbeatAckMessage,
    ConfigUpdatedMessage,
    ConfigSwitchedMessage,
    ScreenStatusMessage,
    ScreenContextMessage,
]

# type 字符串 -> 对应 pydantic 模型（用于动态分发校验）。
_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    _literal_default(m.model_fields["type"]): m
    for m in ServerMessage.__args__  # type: ignore[attr-defined]
}


def build_server_message(payload: dict[str, Any]) -> BaseModel:
    """按 ``type`` 构造并校验出站消息模型。

    Raises:
        ValidationError: 消息与对应 schema 不符。
        ValueError: 未知的消息类型。
    """
    msg_type = payload.get("type")
    model_cls = _MESSAGE_MODELS.get(msg_type)
    if model_cls is None:
        raise ValueError(f"unknown server message type: {msg_type!r}")
    return model_cls.model_validate(payload)


def _dump(msg: BaseModel) -> str:
    # 保留 None 字段：与旧协议逐字节兼容（如 audio: null 表示「本条无音频」），
    # 前端统一用 ?? 兜底，不会因字段缺失而误判。
    return json.dumps(msg.model_dump(mode="json", exclude_none=False))


async def _safe_send_error(send_text: SendText, code: ErrorCode, message: str) -> None:
    """尽力发送结构化错误；若发送本身也失败则仅记日志，避免递归抛错。"""
    try:
        await send_text(json.dumps({"type": "error", "code": code.value, "message": message}))
    except Exception:  # pragma: no cover - 发送通道已不可用
        logger.exception("[contracts] failed to send error frame")


async def send_message(send_text: SendText, payload: dict[str, Any] | BaseModel) -> None:
    """统一出站入口：schema 校验 + 序列化后发送。

    - ``payload`` 为 dict：按 type 构造对应模型校验；
    - ``payload`` 为 BaseModel：直接序列化（跳过重复校验，供调用方复用构造好的模型）；
    - 校验失败：记日志并向客户端发送结构化 ``error``（PROTOCOL_ERROR），不中断调用方。
    """
    if isinstance(payload, BaseModel):
        try:
            await send_text(_dump(payload))
        except Exception:  # pragma: no cover - 发送通道不可用
            logger.exception("[contracts] failed to send message")
        return

    try:
        msg = build_server_message(payload)
    except (ValidationError, ValueError) as e:
        logger.error(
            "[contracts] outbound message rejected: %s (payload=%s)",
            e,
            json.dumps(payload, ensure_ascii=False)[:300],
        )
        await _safe_send_error(
            send_text,
            ErrorCode.PROTOCOL_ERROR,
            "服务器内部消息构造失败，请查看后端日志。",
        )
        return

    try:
        await send_text(_dump(msg))
    except Exception:  # pragma: no cover - 发送通道不可用
        logger.exception("[contracts] failed to send message")


async def send_error(
    send_text: SendText,
    code: ErrorCode,
    message: str,
    recover: Optional[dict[str, Any]] = None,
) -> None:
    """发送结构化错误消息。message 建议用用户可读的中文。"""
    await send_message(
        send_text,
        {"type": "error", "code": code.value, "message": message, "recover": recover},
    )


# --------------------------------------------------------------------------- #
# 入站消息校验（Client → Server）
# --------------------------------------------------------------------------- #

# 已知入站 type 白名单（websocket_handler._init_message_handlers 同源）。
_CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "add-client-to-group",
        "remove-client-from-group",
        "request-group-info",
        "fetch-history-list",
        "fetch-and-set-history",
        "create-new-history",
        "delete-history",
        "set-history-title",
        "set-history-workspace",
        "clear-all-histories",
        "interrupt-signal",
        "mic-audio-data",
        "mic-audio-end",
        "raw-audio-data",
        "text-input",
        "ai-speak-signal",
        "interact",
        "fetch-configs",
        "switch-config",
        "fetch-backgrounds",
        "audio-play-start",
        "request-init-config",
        "heartbeat",
        "frontend-playback-complete",
        "tts-play-start",
        "refresh-model-conf",
        # screen_awareness（Phase 0）：独立协议，不混入普通聊天消息。
        "screen-frame",
        "screen-enable",
        "screen-clear",
    }
)

# 需要携带指定字段的入站类型。
_CLIENT_MESSAGE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "text-input": ("text",),
    "mic-audio-data": ("audio",),
    "raw-audio-data": ("audio",),
    "fetch-and-set-history": ("history_uid",),
    "delete-history": ("history_uid",),
    "set-history-title": ("history_uid", "title"),
    # v5：会话必须归属工作目录；移动会话同样需要目标目录。
    "create-new-history": ("workspace",),
    "set-history-workspace": ("history_uid", "workspace"),
    "switch-config": ("file",),
    # screen_awareness：screen-frame 必须携带帧标识与窗口身份。
    "screen-frame": ("frame_id", "captured_at", "window"),
}

# 可选字段的类型约束（Phase 1 pet-ptt-workflow：workspace 可选但必须是字符串）。
_CLIENT_MESSAGE_OPTIONAL_FIELD_TYPES: dict[str, dict[str, type]] = {
    "text-input": {"workspace": str},
    "mic-audio-end": {"workspace": str},
    "interrupt-signal": {"text": str},
}


def validate_client_message(data: dict[str, Any]) -> Optional[str]:
    """校验入站消息。合法返回 None，非法返回用户可读的中文原因。"""
    if not isinstance(data, dict):
        return "消息必须是 JSON 对象"
    msg_type = data.get("type")
    if not isinstance(msg_type, str) or not msg_type:
        return "消息缺少 type 字段"
    if msg_type not in _CLIENT_MESSAGE_TYPES:
        return f"未知消息类型：{msg_type}"
    for field in _CLIENT_MESSAGE_REQUIRED_FIELDS.get(msg_type, ()):
        if field not in data:
            return f"消息 {msg_type} 缺少必需字段 {field}"
    for field, expected in _CLIENT_MESSAGE_OPTIONAL_FIELD_TYPES.get(msg_type, {}).items():
        if field in data and data[field] is not None and not isinstance(data[field], expected):
            return f"消息 {msg_type} 字段 {field} 类型错误（应为 {expected.__name__}）"
    return None
