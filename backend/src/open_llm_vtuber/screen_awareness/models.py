"""结构化数据模型（pydantic v2）。

与计划 §5 核心数据协议一一对应；前端 `frontend/src/types/ws.ts` 同步同名字段。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Scene = Literal["coding", "reading", "video", "game", "chat", "design", "unknown"]
ProactiveKind = Literal["interrupt", "light_chat", "silence"]


class ScreenWindowInfo(BaseModel):
    """前台窗口身份（与前端 Electron active-window 探测对齐）。"""

    title: str = ""
    app: str = ""
    pid: int = 0
    # [x, y, width, height] 物理像素（窗口工作区）。
    bounds: list[int] = Field(default_factory=list)


class ScreenFrame(BaseModel):
    """前端上传的单帧（独立协议，不混入普通聊天消息）。"""

    type: Literal["screen-frame"] = "screen-frame"
    frame_id: str
    captured_at: float
    window: ScreenWindowInfo
    # data:image/webp;base64,... 或 data:image/jpeg;base64,...
    image: str = ""
    image_hash: str = ""
    reason: Literal["window_changed", "content_changed", "user_requested"] = (
        "content_changed"
    )


class ScreenSnapshot(BaseModel):
    """视觉模型输出的严格结构化摘要。"""

    scene: Scene = "unknown"
    summary: str = ""
    salient_text: list[str] = Field(default_factory=list)
    possible_topic: str = ""
    sensitive: bool = False
    worth_interrupting: bool = False
    confidence: float = 0.0


class ScreenStatus(BaseModel):
    """屏幕感知运行时状态（设置页 / 状态灯数据源）。"""

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
    # 累计指标（供设置页「识别反馈」与性能面板）
    frames_captured: int = 0
    frames_deduped: int = 0
    analyze_count: int = 0
    last_error: str = ""


class PolicyDecision(BaseModel):
    """主动陪聊决策结果。"""

    kind: ProactiveKind = "silence"
    reason: str = ""
    # 命中时给角色的一句话提示（prompt 注入用），空串 = 不注入。
    hint: str = ""


class ScreenConfig(BaseModel):
    """system_config.screen_awareness 配置块。"""

    enabled: bool = False
    # 视觉 provider（OpenAI 兼容 /chat/completions）。空 = 不分析，只保留窗口元数据。
    provider: str = "openai_compatible_llm"  # openai_compatible_llm | custom | local
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    # 图像预处理
    max_side: int = 1280
    quality: int = 78
    # 调度
    poll_interval_sec: float = 3.0
    content_change_threshold: float = 0.08  # pHash 归一化差异阈值（0-1，越小越敏感）
    # 主动陪聊
    proactive_enabled: bool = True
    proactive_cooldown_sec: float = 180.0
    proactive_min_confidence: float = 0.6
    # 2026-08-11：游戏场景是否允许主动轻聊（桌宠陪伴场景：打游戏时 AI 陪聊）。
    # 默认 True；video/reading 仍沉浸静默（看剧/看书时不打扰）。
    proactive_allow_game: bool = True
    # 2026-08-11：用户明确问屏幕时，是否把原始截图附带进对话 LLM。
    # 默认 False：只注入视觉摘要（Qwen3-VL 分析产出），避免无视觉能力的 LLM
    # （如 DeepSeek）收到图像后 chat 接口报错崩溃。换用视觉 LLM（gpt-4o/
    # qwen-vl 等）后置 True 即可让 LLM 直接看图。
    attach_screen_image_to_llm: bool = False
    # 对话模型是否明确支持图像输入。屏幕视觉摘要模型与聊天模型可以不同，
    # 因此不能仅凭 attach_screen_image_to_llm 推断聊天模型支持多模态。
    chat_model_supports_vision: bool = False
    # 隐私
    blocked_apps: list[str] = Field(default_factory=list)
    blocked_title_keywords: list[str] = Field(default_factory=list)
    allowlist_apps: list[str] = Field(default_factory=list)
    # 摘要 TTL（秒）：2026-08-11 从 45s 放宽到 120s —— 视觉分析单次约 12s，
    # 45s 的 TTL 意味着「说话间隔超过 ~33s 就无屏幕上下文」（AI 只能猜/说不知道）。
    # 120s 内说话都能复用；画面变化时前端会刷新帧 → 新摘要覆盖。
    summary_ttl_sec: float = 120.0
    # 仅本地模型模式：禁止任何非 localhost 视觉请求
    local_only: bool = False
    # Phase 5：成本估算（USD/百万 token；0 = 不估算）。默认按 Qwen3-VL-8B-Instruct
    # 硅基流动公开定价（$0.18 输入 / $0.68 输出）。
    price_input_per_mtok: float = 0.18
    price_output_per_mtok: float = 0.68


def screen_config_from(conf_obj: Any) -> ScreenConfig:
    """从 conf.yaml system_config.screen_awareness 读取（缺省安全）。"""
    raw: dict = {}
    try:
        raw = dict(getattr(conf_obj, "screen_awareness", {}) or {})
    except Exception:
        raw = {}
    known = ScreenConfig.model_fields.keys()
    return ScreenConfig(**{k: v for k, v in raw.items() if k in known})
