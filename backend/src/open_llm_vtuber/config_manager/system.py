# config_manager/system.py
from pydantic import BaseModel, Field, model_validator
from typing import Dict, ClassVar
from .i18n import I18nMixin, Description


class UiPrefs(BaseModel):
    """前端 UI 行为偏好（Phase 2：配置单一事实源收口，替代浏览器存储）。

    与 frontend/src/state/types.ts 的 LocalSettings（不含 theme）保持一致。
    值默认与 Phase 1 卖点默认翻转后的前端默认值一致。
    """

    screen_aware_enabled: bool = Field(True, alias="screen_aware_enabled")
    screen_poll_interval_sec: int = Field(5, alias="screen_poll_interval_sec")
    proactive_enabled: bool = Field(True, alias="proactive_enabled")
    proactive_idle_sec: int = Field(60, alias="proactive_idle_sec")
    auto_speak_on_idle: bool = Field(True, alias="auto_speak_on_idle")
    # Moonlight（2026-08-10 UX 修复）：主动找话题只应在「桌宠模式」触发，
    # 窗口模式（用户等待任务/思考输入）不得打扰。默认 True（仅桌宠模式）。
    proactive_pet_mode_only: bool = Field(True, alias="proactive_pet_mode_only")
    # Moonlight（2026-08-10 UX 修复）：双语气泡（字幕翻译）开关的前端渲染
    # 闸门。默认 False（关闭，用户确认默认关是正确的）。
    subtitle_enabled: bool = Field(False, alias="subtitle_enabled")
    # Phase 2（pet-ptt-workflow）：定时屏幕巡检间隔（秒）。0=关闭（默认）；
    # 300~3600 可调。与 proactive_idle_sec（空闲主动）相互独立：按固定周期
    # 触发检查，且只允许「上次主动对话之后产生的新快照」通过（静态画面去重）。
    screen_proactive_interval_sec: int = Field(0, alias="screen_proactive_interval_sec")


class SystemConfig(I18nMixin):
    """System configuration settings."""

    conf_version: str = Field(..., alias="conf_version")
    host: str = Field(..., alias="host")
    port: int = Field(..., alias="port")
    config_alts_dir: str = Field(..., alias="config_alts_dir")
    tool_prompts: Dict[str, str] = Field(..., alias="tool_prompts")
    enable_proxy: bool = Field(False, alias="enable_proxy")
    player_language: str = Field("", alias="player_language")
    player_prompt: str = Field("", alias="player_prompt")
    default_background: str = Field("", alias="default_background")
    # Moonlight：MCP 服务端总开关。开启后外部 Agent（Claude/OpenClaw 等）可经
    # Streamable HTTP（127.0.0.1:12394，Bearer token 鉴权）反向控制桌宠。
    mcp_server_enabled: bool = Field(True, alias="mcp_server_enabled")
    # Moonlight：前端 UI 行为偏好（屏幕感知/主动话题等）。带默认值，conf.yaml
    # 未声明时用默认，向后兼容。
    ui_prefs: UiPrefs = Field(default_factory=UiPrefs, alias="ui_prefs")
    # Moonlight（screen_awareness Phase 0）：屏幕理解与陪聊配置块。
    # 原样透传 dict（未声明为 None），由 screen_awareness.screen_config_from 兜底。
    screen_awareness: Dict[str, object] | None = Field(
        default=None, alias="screen_awareness"
    )

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "player_language": Description(
            en="Player/reading language. When set, ALL characters are instructed to always reply in this language regardless of input language (system-level; not written into any persona). Empty = use each persona's own language.",
            zh="玩家/閱讀語言。設定後，所有角色都會被要求一律用此語言回覆（系統層級，不寫進任何角色人設）。留空＝沿用各角色自身語言。",
        ),
        "player_prompt": Description(
            en="Global directive injected into EVERY character's system prompt describing the player and how to address them (the player is always the same person). Empty = none.",
            zh="注入到每個角色系統提示詞的全域指令，描述玩家是誰、該怎麼稱呼他（玩家永遠是同一個人）。留空＝不注入。",
        ),
        "conf_version": Description(en="Configuration version", zh="配置文件版本"),
        "host": Description(en="Server host address", zh="服务器主机地址"),
        "port": Description(en="Server port number", zh="服务器端口号"),
        "config_alts_dir": Description(
            en="Directory for alternative configurations", zh="备用配置目录"
        ),
        "tool_prompts": Description(
            en="Tool prompts to be inserted into persona prompt",
            zh="要插入到角色提示词中的工具提示词",
        ),
        "enable_proxy": Description(
            en="Enable proxy mode for multiple clients",
            zh="启用代理模式以支持多个客户端使用一个 ws 连接",
        ),
        "default_background": Description(
            en="Default background image filename (in backgrounds/) applied to a browser the first time it connects, e.g. 'cityscape.jpeg'. Empty = use the built-in default. Lets the background be set server-side (works in Safari).",
            zh="預設背景圖檔名(放在 backgrounds/),會在瀏覽器第一次連線時套用,例如 'cityscape.jpeg'。留空＝用內建預設。讓背景可由伺服器端設定(Safari 也適用)。",
        ),
    }

    @model_validator(mode="after")
    def check_port(cls, values):
        port = values.port
        if port < 0 or port > 65535:
            raise ValueError("Port must be between 0 and 65535")
        return values
