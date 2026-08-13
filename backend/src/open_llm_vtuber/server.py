"""
Open-LLM-VTuber Server
========================
This module contains the WebSocket server for Open-LLM-VTuber, which handles
the WebSocket connections, serves static files, and manages the web tool.
It uses FastAPI for the server and Starlette for static file serving.
"""

import os
import shutil

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles as StarletteStaticFiles

from .routes import init_client_ws_route, init_webtool_routes, init_proxy_route
from .config_route import init_config_route
from .readiness_route import init_readiness_route
from .health_route import init_health_route
from .llm_config_route import init_llm_config_route
from .character_route import init_character_route
from .quotes_route import init_quotes_route
from .translator_route import init_translator_route
from .memory_route import init_memory_route
from .perf_route import init_perf_route
from .engine_route import init_engine_route
from .console_route import init_console_route
from .expression_route import init_expression_route
from .live2d_catalog import init_live2d_catalog_route
from .singing.routes import init_singing_route
from .live.live_route import init_live_route
from .playmate.route import init_playmate_route
from .plugin_route import init_plugin_route
from .attachment_route import init_attachment_route
from .occlusion_route import init_occlusion_route
from .social_route import init_social_route
from .emotion_route import init_emotion_route
from .topics_route import (
    init_topics_route,
    start_news_refresh_task,
    stop_news_refresh_task,
)
from .task_platform.task_route import init_task_route
from .task_platform.task_config_route import init_task_config_route
from .task_platform.intent_route import init_intent_route
from .screen_awareness.route import init_screen_route
from .service_context import ServiceContext
from .config_manager.utils import Config


# Create a custom StaticFiles class that adds CORS headers
class CORSStaticFiles(StarletteStaticFiles):
    """
    Static files handler that adds CORS headers to all responses.
    Needed because Starlette StaticFiles might bypass standard middleware.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)

        # Add CORS headers to all responses
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"

        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"

        # Don't cache HTML: avoids edits getting stuck behind a stale browser cache (especially
        # for remote devices). Decided by content-type because a request for "/" has an empty
        # path (StaticFiles serves index.html internally). Hash-named .js/.css can still cache long.
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"

        return response


class AvatarStaticFiles(CORSStaticFiles):
    """
    Avatar files handler with security restrictions and CORS headers
    """

    async def get_response(self, path: str, scope):
        allowed_extensions = (".jpg", ".jpeg", ".png", ".gif", ".svg")
        if not any(path.lower().endswith(ext) for ext in allowed_extensions):
            return Response("Forbidden file type", status_code=403)
        response = await super().get_response(path, scope)
        return response


class WebSocketServer:
    """
    API server for Open-LLM-VTuber. This contains the websocket endpoint for the client, hosts the web tool, and serves static files.

    Creates and configures a FastAPI app, registers all routes
    (WebSocket, web tools, proxy) and mounts static assets with CORS.

    Args:
        config (Config): Application configuration containing system settings.
        default_context_cache (ServiceContext, optional):
            Pre‑initialized service context for sessions' service context to reference to.
            **If omitted, `initialize()` method needs to be called to load service context.**

    Notes:
        - If default_context_cache is omitted, call `await initialize()` to load service context cache.
        - Use `clean_cache()` to clear and recreate the local cache directory.
    """

    def __init__(self, config: Config, default_context_cache: ServiceContext = None):
        self.app = FastAPI(title="Open-LLM-VTuber Server")  # Added title for clarity
        self.config = config
        self.default_context_cache = (
            default_context_cache or ServiceContext()
        )  # Use provided context or initialize a new empty one waiting to be loaded
        # It will be populated during the initialize method call

        # 进程级探针必须在静态前端挂载前注册，避免被 catch-all 路由吞掉。
        self.app.include_router(init_health_route(self.default_context_cache))

        # Add CORS middleware for the local renderer and the backend-served UI.
        # The previous wildcard + credentials combination was unnecessarily broad
        # for a localhost-only desktop app and is rejected by some browsers.
        configured_host = str(getattr(config.system_config, "host", "127.0.0.1"))
        configured_port = int(getattr(config.system_config, "port", 12393))
        local_origins = {
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            f"http://{configured_host}:{configured_port}",
            "null",  # Electron loadFile() renderer origin.
        }
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(local_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Include routes, passing the context instance
        # The context will be populated during the initialize step
        self.app.include_router(
            init_client_ws_route(default_context_cache=self.default_context_cache),
        )
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache),
        )

        # 统一配置 API（Phase 1）：GET/PUT /api/config —— 单一事实源收口。
        # localhost-only；写盘后热重载 default_context_cache 并广播 config-updated。
        self.app.include_router(
            init_config_route(default_context_cache=self.default_context_cache),
        )

        # 就绪度检查（Phase 3）：GET /api/readiness —— Onboarding 第 2 步数据源。
        self.app.include_router(
            init_readiness_route(default_context_cache=self.default_context_cache),
        )

        # First-run BYO-LLM setup endpoints (localhost-only). Reads/writes conf.yaml
        # directly, so no ServiceContext is needed. Registered BEFORE the static
        # mounts below so the /api/* routes resolve ahead of the "/" catch-all.
        self.app.include_router(init_llm_config_route())

        # Character Manager endpoints (localhost-only). Create/edit/switch/delete
        # companion characters (override YAMLs in characters/) + scan/auto-register
        # Live2D skins + list edge-tts voices. Same /api/* placement (before "/").
        self.app.include_router(init_character_route())

        # Character quotes (台词集) management endpoints (localhost-only). Read/update/
        # reset per-character quote libraries (scenario + keyword-triggered lines).
        # Same /api/* placement (before "/").
        self.app.include_router(init_quotes_route())

        # Cross-language voice + translated-subtitle toggle (localhost-only). Reads/
        # writes tts_preprocessor_config.translator_config + base edge_tts voice in
        # conf.yaml. Same /api/* placement (before "/").
        self.app.include_router(init_translator_route())

        # Long-term (core) memory settings endpoints (localhost-only). Read/clear the
        # active character's core memory + toggle long_term_memory_enabled in
        # conf.yaml. Same /api/* placement (before "/").
        self.app.include_router(init_memory_route())

        # Performance / hardware settings endpoints (localhost-only). ASR/TTS engine
        # selector + cloud creds, ollama keep_alive, memory consolidation interval, and
        # one-click performance presets. Reads/writes conf.yaml surgically. Same /api/*
        # placement (before "/").
        self.app.include_router(init_perf_route())

        # Engine management endpoints (localhost-only). Per-engine config status,
        # surgical per-block writes, and VOICEVOX local-engine download/start/stop.
        self.app.include_router(init_engine_route())

        # 控制台聚合 API（P0 接线工程）：GET /api/console/overview —— 顶栏 + 概览
        # 分区 + 系统性能的实时数据，聚合现有 service 内存态（fail-soft）。
        self.app.include_router(init_console_route())

        # 表情 / 口型动作（P1 表情与动作域）：motion-plan / generate / config。
        self.app.include_router(init_expression_route())

        # Live2D 模型目录扫描 / 热切换（P1 多模型与专属 Prompt）。
        self.app.include_router(init_live2d_catalog_route())

        # 唱歌与音乐（P2 唱歌 MVP）：点歌学唱 / 队列 / 翻唱引擎。
        self.app.include_router(init_singing_route())

        # 直播与互动（P3）：B站接入 / 弹幕玩法 / 叠加层 / OBS+VTS。
        self.app.include_router(init_live_route())

        # 游戏陪玩（P4）：目标游戏 / 画面事件 / 攻略知识库 / 高光喝彩。
        self.app.include_router(init_playmate_route())

        # 插件生态（P5）：插件管理器 / 技能市场 / 导出分享 / 意图识别。
        self.app.include_router(init_plugin_route())

        # 聊天附件（P5.1）：PDF 抽取 / 图片预描述（multipart 上传）。
        self.app.include_router(init_attachment_route())

        # 遮罩 AI 前景（P6）：rembg 抠图 → 轮廓点。
        self.app.include_router(init_occlusion_route())

        # QQ 社交连接器（P6）：OneBot v11（NapCat）。
        self.app.include_router(init_social_route())

        # Proactive topic-pool endpoints (localhost-only). Manage the manual topic
        # pool + optional Google-News auto-topics that compose into
        # proactive_speak_prompt.txt. Same /api/* placement (before "/").
        self.app.include_router(init_topics_route())

        # Moonlight 集成：情绪系统端点（localhost-only）。查询/覆盖当前情绪。
        self.app.include_router(init_emotion_route())

        # 任务制智能体平台端点（localhost-only，plan §6）。任务 CRUD + 工作目录扫描
        # （Phase 1）；Phase 2 起追加 runs/stream SSE + interrupt，Phase 3 追加 skills。
        # 装配外壳播报（G7）：广播函数注入 task_route，TTS 引擎注入 task_platform.shell
        # ——外壳汇报复用主对话链路（WS audio 消息），前端零改动。
        self.app.include_router(init_task_route())

        # 意图路由（P1）：POST /api/intent/classify —— 聊天/任务自动分类。
        self.app.include_router(init_intent_route())

        # 屏幕理解与陪聊（Phase 0）：状态/指标/分析/清除/配置端点（localhost-only）。
        # 读取 system_config.screen_awareness 并注入 screen_awareness store；
        # 视觉 provider 未显式配置时继承对话 LLM（inherit 语义）。
        try:
            from .screen_awareness.route import apply_screen_config, fill_llm_defaults
            from .screen_awareness.models import screen_config_from

            _sc_cfg = fill_llm_defaults(
                screen_config_from(config.system_config), config.character_config
            )
            apply_screen_config(_sc_cfg)
        except Exception as _sc_e:
            from loguru import logger as _logger

            _logger.warning(f"[screen_awareness] startup config inject failed: {_sc_e}")
        self.app.include_router(init_screen_route())

        # 任务平台配置端点（localhost-only）：设置 UI 读取/保存 task_platform 配置 +
        # MCP 服务器管理（增删改/整体替换/测试连接）。写盘外科手术式，需重启生效。
        self.app.include_router(init_task_config_route())

        # Start the in-app periodic news-refresh task on server startup, and cancel
        # it on shutdown. This replaces an OS cron: when news auto-topics are enabled
        # it re-fetches every interval_hours and recomposes the proactive prompt.
        # Cancel-safe + exception-swallowing (a fetch error never kills the loop).
        @self.app.on_event("startup")
        async def _start_topics_refresh():  # noqa: D401
            try:
                start_news_refresh_task()
            except Exception as e:
                # Never let the background task break server startup.
                from loguru import logger as _logger

                _logger.warning(
                    f"could not start news-refresh task: {type(e).__name__}: {e}"
                )

        @self.app.on_event("shutdown")
        async def _stop_topics_refresh():  # noqa: D401
            try:
                await stop_news_refresh_task()
            except Exception:
                pass

        # Moonlight 集成：MCP 服务端优雅停止（与启动对称，幂等）。
        @self.app.on_event("shutdown")
        async def _stop_mcp_server():  # noqa: D401
            try:
                from .mcp_server.launcher import stop_mcp_server

                stop_mcp_server()
            except Exception:
                pass

        # Initialize and include proxy routes if proxy is enabled
        system_config = config.system_config
        if hasattr(system_config, "enable_proxy") and system_config.enable_proxy:
            # Construct the server URL for the proxy
            host = system_config.host
            port = system_config.port
            server_url = f"ws://{host}:{port}/client-ws"
            self.app.include_router(
                init_proxy_route(server_url=server_url),
            )

        # Mount cache directory first (to ensure audio file access)
        if not os.path.exists("cache"):
            os.makedirs("cache")
        self.app.mount(
            "/cache",
            CORSStaticFiles(directory="cache"),
            name="cache",
        )

        # P2 唱歌产物（学歌完成的 vocal/accompany wav）静态挂载。
        os.makedirs(os.path.join("output", "singing"), exist_ok=True)
        self.app.mount(
            "/singing-output",
            CORSStaticFiles(directory=os.path.join("output", "singing")),
            name="singing-output",
        )

        # Ensure static dirs exist before mounting. Empty dirs (notably avatars/)
        # are not shipped in a fresh download/clone, and mounting a missing
        # directory raises at startup — which would crash every first launch.
        for _static_dir in ("live2d-models", "backgrounds", "avatars"):
            os.makedirs(_static_dir, exist_ok=True)

        # Mount static files with CORS-enabled handlers
        self.app.mount(
            "/live2d-models",
            CORSStaticFiles(directory="live2d-models"),
            name="live2d-models",
        )
        self.app.mount(
            "/bg",
            CORSStaticFiles(directory="backgrounds"),
            name="backgrounds",
        )
        self.app.mount(
            "/avatars",
            AvatarStaticFiles(directory="avatars"),
            name="avatars",
        )

        # Mount web tool directory separately from frontend
        self.app.mount(
            "/web-tool",
            CORSStaticFiles(directory="web_tool", html=True),
            name="web_tool",
        )

        # Mount main frontend last (as catch-all)
        self.app.mount(
            "/",
            CORSStaticFiles(directory="frontend", html=True),
            name="frontend",
        )

    def start_mcp_service(self) -> None:
        """启动 MCP 服务端（延后到模型加载完成后调用，见 run_server.py）。

        不能放在构造函数里：MCP 的后台 uvicorn 线程与 sherpa_onnx 的 ONNX
        模型加载并发时会在 C++ 层 segfault（2026-08-12 实测，100% 复现），
        导致整个后端启动崩溃。模型全部加载完再拉起 MCP 线程则稳定。
        启动失败只记警告，绝不让 MCP 问题拖垮主服务。
        """
        system_config = self.config.system_config
        if not getattr(system_config, "mcp_server_enabled", True):
            return
        try:
            from .mcp_server.launcher import start_mcp_server

            start_mcp_server()
        except Exception as e:
            from loguru import logger as _logger

            _logger.warning(f"Failed to start MCP server: {e}")

    async def initialize(self):
        """Asynchronously load the service context from config.
        Calling this function is needed if default_context_cache was not provided to the constructor."""
        await self.default_context_cache.load_from_config(self.config)

    @staticmethod
    def clean_cache():
        """Clean the cache directory by removing and recreating it."""
        cache_dir = "cache"
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)
