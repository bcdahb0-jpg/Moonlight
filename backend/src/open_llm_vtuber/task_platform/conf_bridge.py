"""conf_bridge — 从 conf.yaml 读 task_platform 配置（plan §5.6）。

- 只读 conf.yaml 的 `task_platform` 顶层块 + `openai_compatible_llm` 块快照。
- 不 import 现有 agent / mcpp / memory 实现（单向依赖铁律）。
- 配置缺省回退默认值；conf.yaml 缺失或解析失败时静默用默认（不阻塞启动）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import yaml

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # backend/
CONF_PATH = _BACKEND_ROOT / "conf.yaml"


@dataclass
class McpServerConfig:
    """单个 MCP 服务器配置（plan §5.9）。"""

    name: str
    transport: str = "stdio"  # stdio | http
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class AcpAgentConfig:
    """外部 ACP 编码 agent 配置（v4 Phase C2，参考 deer-flow acp_agents）。

    - command/args：启动外部 agent 的命令（codex / claude / codex-acp 适配器等）。
    - env：额外环境变量；值支持 `$ENV_NAME` 语法（从进程环境展开）。
    - timeout_seconds：单次 prompt 超时（秒），超时终止子进程。
    - auto_approve_permissions：true 时自动批准权限请求（allow_once 优先），否则全部拒绝。
    - model：可选，传给 ACP new_session 的模型名。
    """

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 300
    auto_approve_permissions: bool = False
    model: str = ""


@dataclass
class TaskPlatformConfig:
    """task_platform 配置（plan §5.6 默认值）。"""

    enabled: bool = True
    db_path: str = "task_platform.db"  # 相对 backend/，可改绝对路径
    tasks_root: str = "tasks/"  # 默认任务根（相对 backend/）
    tool_timeout_sec: int = 120  # bash 超时
    bash_output_limit: int = 65536  # bash 输出截断 64KB
    write_limit_bytes: int = 1048576  # write_file 上限 1MB
    read_limit_bytes: int = 524288  # read_file 上限 512KB
    allow_network: bool = True
    max_no_progress: int = 5  # 连续无进展（同 AI 文本）轮数上限，达则提示澄清（plan §5.2 D）
    max_iterations: int = 20  # 目标循环总轮数硬上限（与无进展检测解耦，review MEDIUM）
    goal_evaluator_model: str = "main"  # main=复用主模型
    skills_root: str = "skills/"  # 技能库根（相对 backend/）
    agents_root: str = "agents/"  # sub-agent 定义根（相对 backend/，plan §8 6b）
    plugins_root: str = "plugins/"  # extensions 插件根（相对 backend/，plan §8 6c）
    embedding_enabled: bool = False
    embedding_top_k: int = 5
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    acp_agents: list[AcpAgentConfig] = field(default_factory=list)  # v4 Phase C2
    # ---- LLM 配置快照（openai_compatible_llm 块）----
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_context_window: int = 0  # 0=按模型自动探测（见 llm_adapter.context_window）；>0 显式覆盖
    # ---- 文件编辑（v3 Phase 1）----
    read_before_write: bool = True  # 版本门：写前必须读过该文件（fail-open 白名单）
    # ---- 网页搜索（v3 Phase 2）----
    web_search_enabled: bool = True
    web_search_provider: str = "auto"  # auto | ddg | tavily
    web_search_max_results: int = 5
    web_fetch_max_bytes: int = 524288
    web_verify_tls: bool = True  # v4 Phase A2：false 时 httpx 跳过 TLS 证书校验（Clash 拦截场景）
    tavily_api_key: str = ""
    jina_api_key: str = ""
    # ---- bash 审计（v3 Phase 5）----
    bash_audit: bool = True
    # ---- token 预算（v3 Phase 6）----
    token_budget_warn_ratio: float = 0.8
    token_budget_hard_ratio: float = 0.95
    # ---- 项目级记忆（v3 Phase 7）----
    memory_root: str = ""  # 空 = tasks_root 下按 workspace 分目录（默认跟随工作目录）
    memory_max_injection_tokens: int = 1500
    # ---- 工具输出预算（v4 Phase B1，参考 deer-flow ToolOutputBudget）----
    tool_output_enabled: bool = True  # 总开关
    tool_output_externalize_min_chars: int = 12000  # 超限 → 落盘换预览
    tool_output_preview_head_chars: int = 2000  # 预览开头节选
    tool_output_preview_tail_chars: int = 1000  # 预览结尾节选
    tool_output_fallback_max_chars: int = 30000  # 落盘失败 → head+tail 截断硬上限
    # ---- 委派（v4 Phase B2/C1）----
    delegate_result_brief_chars: int = 2000  # 委派结果入账本的长度上限
    delegate_parallel_max: int = 3  # delegate_parallel 并发上限
    delegate_ledger_max_entries: int = 8  # system prompt 注入的委派账本条数上限
    # ---- 浏览器自动化（v4 Phase A3）----
    browser_enabled: bool = False  # 总开关（依赖 playwright + 系统 Edge/Chrome）
    browser_headless: bool = True
    browser_use_system_edge: bool = True  # true=指向系统 Edge/Chrome executable，免下载 Chromium
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 720

    def resolve(self, rel: str) -> Path:
        """把相对路径解析到 backend/ 根（绝对路径原样返回）。"""
        p = Path(rel)
        return p if p.is_absolute() else _BACKEND_ROOT / p

    @property
    def db_file(self) -> Path:
        return self.resolve(self.db_path)

    @property
    def tasks_root_dir(self) -> Path:
        return self.resolve(self.tasks_root)

    @property
    def skills_root_dir(self) -> Path:
        return self.resolve(self.skills_root)

    @property
    def agents_root_dir(self) -> Path:
        return self.resolve(self.agents_root)

    @property
    def plugins_root_dir(self) -> Path:
        return self.resolve(self.plugins_root)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logger.debug(f"task_platform: conf.yaml not found at {path}, using defaults")
        return {}
    except Exception as e:  # 解析失败不阻塞启动
        logger.warning(f"task_platform: conf.yaml parse failed ({e}), using defaults")
        return {}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _parse_mcp_servers(raw: Any) -> list[McpServerConfig]:
    if not isinstance(raw, list):
        return []
    out: list[McpServerConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            McpServerConfig(
                name=str(item.get("name", "mcp")),
                transport=str(item.get("transport", "stdio")),
                command=str(item.get("command", "")),
                args=[str(a) for a in (item.get("args") or [])],
                url=item.get("url"),
                headers=dict(item.get("headers") or {}),
                enabled=_as_bool(item.get("enabled", True), True),
            )
        )
    return out


def _parse_acp_agents(raw: Any) -> list[AcpAgentConfig]:
    """解析 task_platform.acp_agents（dict：name → 配置 或 list）。

    支持两种形态：
    - dict 形态：`codex: {command: ..., args: [...]}`（key 即 name）；
    - list 形态：`- name: codex\n  command: ...`。
    """
    if isinstance(raw, dict):
        items = [{"name": str(name), **(val if isinstance(val, dict) else {})}
                 for name, val in raw.items()]
    elif isinstance(raw, list):
        items = [item for item in raw if isinstance(item, dict)]
    else:
        return []
    out: list[AcpAgentConfig] = []
    for item in items:
        command = str(item.get("command", ""))
        if not command:
            continue  # 缺 command → 跳过（fail-soft）
        out.append(
            AcpAgentConfig(
                name=str(item.get("name", "acp")),
                command=command,
                args=[str(a) for a in (item.get("args") or [])],
                env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                timeout_seconds=float(item.get("timeout_seconds", 300) or 300),
                auto_approve_permissions=_as_bool(
                    item.get("auto_approve_permissions", False), False
                ),
                model=str(item.get("model", "")),
            )
        )
    return out


_cached: Optional[TaskPlatformConfig] = None


def task_config(force_reload: bool = False) -> TaskPlatformConfig:
    """读取（并缓存）task_platform 配置。conf.yaml 变更需重启后端生效。"""
    global _cached
    if _cached is not None and not force_reload:
        return _cached

    data = _load_yaml(CONF_PATH)
    tp = data.get("task_platform") or {}
    # LLM 配置实际嵌套在 character_config.agent_config.llm_configs（非顶层 agent_config）。
    llm_cfg = (
        ((data.get("character_config") or {}).get("agent_config") or {}).get("llm_configs")
        or {}
    )
    oai = llm_cfg.get("openai_compatible_llm") or {}

    cfg = TaskPlatformConfig(
        enabled=_as_bool(tp.get("enabled", True), True),
        db_path=str(tp.get("db_path", "task_platform.db")),
        tasks_root=str(tp.get("tasks_root", "tasks/")),
        tool_timeout_sec=_as_int(tp.get("tool_timeout_sec", 120), 120),
        bash_output_limit=_as_int(tp.get("bash_output_limit", 65536), 65536),
        write_limit_bytes=_as_int(tp.get("write_limit_bytes", 1048576), 1048576),
        read_limit_bytes=_as_int(tp.get("read_limit_bytes", 524288), 524288),
        allow_network=_as_bool(tp.get("allow_network", True), True),
        max_no_progress=_as_int(tp.get("max_no_progress", 5), 5),
        max_iterations=_as_int(tp.get("max_iterations", 20), 20),
        goal_evaluator_model=str(tp.get("goal_evaluator_model", "main")),
        skills_root=str(tp.get("skills", {}).get("root", "skills/")),
        agents_root=str((tp.get("agents") or {}).get("root", "agents/")),
        plugins_root=str((tp.get("plugins") or {}).get("root", "plugins/")),
        embedding_enabled=_as_bool(
            (tp.get("skills") or {}).get("embedding_enabled", False), False
        ),
        embedding_top_k=_as_int(
            (tp.get("skills") or {}).get("embedding_top_k", 5), 5
        ),
        mcp_servers=_parse_mcp_servers(tp.get("mcp", {}).get("servers")),
        acp_agents=_parse_acp_agents(tp.get("acp_agents")),
        llm_base_url=str(oai.get("base_url", "")),
        llm_api_key=str(oai.get("llm_api_key", "")),
        llm_model=str(oai.get("model", "")),
        llm_context_window=_as_int(tp.get("llm_context_window", 0), 0),
        read_before_write=_as_bool(tp.get("read_before_write", True), True),
        web_search_enabled=_as_bool(tp.get("web_search_enabled", True), True),
        web_search_provider=str(tp.get("web_search_provider", "auto")),
        web_search_max_results=_as_int(tp.get("web_search_max_results", 5), 5),
        web_fetch_max_bytes=_as_int(tp.get("web_fetch_max_bytes", 524288), 524288),
        web_verify_tls=_as_bool(tp.get("web_verify_tls", True), True),
        tavily_api_key=str(tp.get("tavily_api_key", "") or os.getenv("TAVILY_API_KEY", "")),
        jina_api_key=str(tp.get("jina_api_key", "") or os.getenv("JINA_API_KEY", "")),
        bash_audit=_as_bool(tp.get("bash_audit", True), True),
        token_budget_warn_ratio=float(tp.get("token_budget_warn_ratio", 0.8) or 0.8),
        token_budget_hard_ratio=float(tp.get("token_budget_hard_ratio", 0.95) or 0.95),
        memory_root=str(tp.get("memory", {}).get("root", "")),
        memory_max_injection_tokens=_as_int(
            (tp.get("memory") or {}).get("max_injection_tokens", 1500), 1500
        ),
        tool_output_enabled=_as_bool(tp.get("tool_output_enabled", True), True),
        tool_output_externalize_min_chars=_as_int(
            tp.get("tool_output_externalize_min_chars", 12000), 12000
        ),
        tool_output_preview_head_chars=_as_int(
            tp.get("tool_output_preview_head_chars", 2000), 2000
        ),
        tool_output_preview_tail_chars=_as_int(
            tp.get("tool_output_preview_tail_chars", 1000), 1000
        ),
        tool_output_fallback_max_chars=_as_int(
            tp.get("tool_output_fallback_max_chars", 30000), 30000
        ),
        delegate_result_brief_chars=_as_int(
            tp.get("delegate_result_brief_chars", 2000), 2000
        ),
        delegate_parallel_max=_as_int(tp.get("delegate_parallel_max", 3), 3),
        delegate_ledger_max_entries=_as_int(
            tp.get("delegate_ledger_max_entries", 8), 8
        ),
        browser_enabled=_as_bool(tp.get("browser_enabled", False), False),
        browser_headless=_as_bool(tp.get("browser_headless", True), True),
        browser_use_system_edge=_as_bool(
            tp.get("browser_use_system_edge", True), True
        ),
        browser_viewport_width=_as_int(tp.get("browser_viewport_width", 1280), 1280),
        browser_viewport_height=_as_int(tp.get("browser_viewport_height", 720), 720),
    )
    _cached = cfg
    return cfg
