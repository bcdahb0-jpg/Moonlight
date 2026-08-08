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
