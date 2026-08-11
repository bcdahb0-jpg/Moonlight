"""任务平台配置 REST 路由（设置界面全面改造：任务平台配置 + MCP 服务器管理）。

把 Phase 0-6 新增的可配置项全部暴露给设置 UI，替代旧 MCP 的 `use_mcpp` 开关。

- GET    /api/task-platform/config    → 扁平化配置（全部可编辑字段 + mcp_servers）
- PUT    /api/task-platform/config    → JSON Merge Patch 写 conf.yaml 的 task_platform 块
                                        （仅标量叶；mcp_servers 走专用 POST 端点）
- POST   /api/task-platform/mcp/servers → 整体替换 mcp.servers 列表
- POST   /api/task-platform/mcp/probe   → 探测单个 MCP 服务器工具列表（测试连接）

设计说明（镜像 translator_route / perf_route 约定）：
- localhost 守卫 `_is_local_request` / `_forbidden` 复用，不外扩。
- conf.yaml 注释密集，写盘用 **外科手术式行编辑**（_find_block_extent / _rewrite_leaf /
  _rewrite_bool_leaf / _rewrite_int_leaf），**不是** ruamel 全量重 dump（会 churn 全文件
  True->true / null）。嵌套叶（skills.root 等）限定到各自子块范围，避免 root 重名误写。
- 原子写（temp + os.replace）+ 一次性 .bak，与 llm_config_route 一致。
- 所有写入重启后端才生效（task_platform 配置启动时读取一次，见 conf_bridge），响应如实
  返回 restart_required=True；GET 用 force_reload 始终读磁盘最新值，让用户立刻看到保存结果。
- fail-soft：probe 探测单个服务器，失败返回 status=error 而非 500。
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Optional

from fastapi import APIRouter, Request
from loguru import logger
from starlette.responses import JSONResponse

from ..llm_config_route import _is_local_request, _forbidden
from ..translator_route import (
    CONF_PATH as _TRANSLATOR_CONF_PATH,
    _find_block_extent,
    _rewrite_leaf,
)
from ..memory_route import _rewrite_int_leaf
from . import mcp_client
from .conf_bridge import CONF_PATH, McpServerConfig, TaskPlatformConfig, task_config
from ..translator_route import _rewrite_bool_leaf

#: 本路由的写盘路径（默认与 translator_route 同；测试可 monkeypatch 到临时文件）。
_CONF_PATH = _TRANSLATOR_CONF_PATH

#: task_platform 顶层块锚点（列 0，无缩进）。
_TP_RE = re.compile(r"^(\s*)task_platform:\s*(#.*)?$")

#: MCP transport 白名单（与 mcp_client.server_connection 支持集一致）。
_MCP_TRANSPORTS = {"stdio", "http", "streamable_http", "streamable-http", "sse", "websocket"}

#: 扁平配置 → conf.yaml 叶映射：(flat_key, yaml_key, 子块正则或 None, kind)
# kind ∈ {"str", "int", "float", "bool"}。子块正则用 `^(\s+)`（至少一个空格 → 只匹配缩进块，
# 天然限定在 task_platform 内部，不会误中顶层同名 key）。
_WRITE_MAP: list[tuple[str, str, Optional[re.Pattern], str]] = [
    ("enabled", "enabled", None, "bool"),
    ("tasks_root", "tasks_root", None, "str"),
    ("max_no_progress", "max_no_progress", None, "int"),
    ("tool_timeout_sec", "tool_timeout_sec", None, "int"),
    ("bash_output_limit", "bash_output_limit", None, "int"),
    ("write_limit_bytes", "write_limit_bytes", None, "int"),
    ("read_limit_bytes", "read_limit_bytes", None, "int"),
    ("allow_network", "allow_network", None, "bool"),
    ("skills_root", "root", re.compile(r"^(\s+)skills:\s*(#.*)?$"), "str"),
    ("embedding_enabled", "embedding_enabled", re.compile(r"^(\s+)skills:\s*(#.*)?$"), "bool"),
    ("embedding_top_k", "embedding_top_k", re.compile(r"^(\s+)skills:\s*(#.*)?$"), "int"),
    ("agents_root", "root", re.compile(r"^(\s+)agents:\s*(#.*)?$"), "str"),
    ("plugins_root", "root", re.compile(r"^(\s+)plugins:\s*(#.*)?$"), "str"),
    # ---- v3 升级（借鉴 deer-flow / pi-agent）----
    ("llm_context_window", "llm_context_window", None, "int"),
    ("read_before_write", "read_before_write", None, "bool"),
    ("web_search_enabled", "web_search_enabled", None, "bool"),
    ("web_search_provider", "web_search_provider", None, "str"),
    ("web_search_max_results", "web_search_max_results", None, "int"),
    ("web_fetch_max_bytes", "web_fetch_max_bytes", None, "int"),
    ("tavily_api_key", "tavily_api_key", None, "str"),
    ("jina_api_key", "jina_api_key", None, "str"),
    ("bash_audit", "bash_audit", None, "bool"),
    ("token_budget_warn_ratio", "token_budget_warn_ratio", None, "float"),
    ("token_budget_hard_ratio", "token_budget_hard_ratio", None, "float"),
    ("memory_max_injection_tokens", "max_injection_tokens",
     re.compile(r"^(\s+)memory:\s*(#.*)?$"), "int"),
]

#: 扁平配置 GET 允许返回/接受的全部 key（用于 PUT 未知 key 校验）。
_FLAT_KEYS = {entry[0] for entry in _WRITE_MAP}

#: 数值字段边界（校验上限，避免误写把运行参数设成荒谬值）。
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "max_no_progress": (1, 100),
    "tool_timeout_sec": (1, 3600),
    "bash_output_limit": (1024, 10_000_000),
    "write_limit_bytes": (1024, 100_000_000),
    "read_limit_bytes": (1024, 100_000_000),
    "embedding_top_k": (1, 100),
    # v3
    "llm_context_window": (0, 2_000_000),
    "web_search_max_results": (1, 20),
    "web_fetch_max_bytes": (1024, 10_000_000),
    "memory_max_injection_tokens": (100, 100_000),
}

#: float 字段边界（token 预算比例：0~1）。
_FLOAT_BOUNDS: dict[str, tuple[float, float]] = {
    "token_budget_warn_ratio": (0.0, 1.0),
    "token_budget_hard_ratio": (0.0, 1.0),
}

#: 字符串枚举白名单（web 搜索源）。
_WEB_PROVIDERS = {"auto", "ddg", "tavily"}

# --------------------------------------------------------------------------- #
# 写盘辅助（原子写 + 一次性 .bak，路径可注入便于测试）
# --------------------------------------------------------------------------- #

def _backup_conf_once() -> None:
    if not os.path.exists(_CONF_PATH + ".bak"):
        try:
            shutil.copy2(_CONF_PATH, _CONF_PATH + ".bak")
        except Exception as e:  # noqa: BLE001 备份失败不阻断写盘
            logger.warning(f"Could not create conf.yaml.bak: {type(e).__name__}")


def _atomic_write_conf(lines: list[str]) -> None:
    conf_dir = os.path.dirname(os.path.abspath(_CONF_PATH)) or "."
    os.makedirs(conf_dir, exist_ok=True)
    tmp_path = os.path.join(conf_dir, ".conf.yaml.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp_path, _CONF_PATH)


def _yaml_scalar(v: Any) -> str:
    """YAML 标量渲染：纯安全字符不引号（贴合 conf.yaml 风格），否则单引号转义。"""
    s = str(v)
    if re.fullmatch(r"[A-Za-z0-9_./\-]+", s):
        return s
    return "'" + s.replace("'", "''") + "'"


def _rewrite_float_leaf(lines: list, start: int, end: int, key: str, value: float) -> bool:
    """Rewrite a FLOAT leaf 'key: 0.8' (bare literal) preserving indent + comment.

    与 _rewrite_int_leaf 同构（translator_route._rewrite_leaf 会单引号标量 → YAML
    存成字符串，Pydantic float 解析失败），float 需要专用 writer。
    """
    for j in range(start, end):
        line = lines[j]
        stripped = line.lstrip()
        if stripped.startswith(key + ":"):
            indent_ws = line[: len(line) - len(stripped)]
            comment = ""
            m = re.search(r"(\s+#.*?)\s*$", line.rstrip("\n"))
            if m:
                comment = m.group(1)
            # 0.8 保留两位内精度；整数值补 .0（如 1 → 1.0，YAML float）
            rendered = f"{value:.6g}"
            if "." not in rendered and "e" not in rendered:
                rendered += ".0"
            lines[j] = f"{indent_ws}{key}: {rendered}{comment}\n"
            return True
    return False


def _render_mcp_server(s: McpServerConfig, indent: int) -> list[str]:
    """把一个 MCP 服务器渲染成 conf.yaml 风格的行（- name: ... 块）。"""
    ws = " " * indent
    sub = " " * (indent + 2)
    out = [f"{ws}- name: {_yaml_scalar(s.name)}"]
    out.append(f"{sub}transport: {_yaml_scalar(s.transport)}")
    if s.command:
        out.append(f"{sub}command: {_yaml_scalar(s.command)}")
    if s.args:
        out.append(f"{sub}args: [{', '.join(_yaml_scalar(a) for a in s.args)}]")
    if s.url:
        out.append(f"{sub}url: {_yaml_scalar(s.url)}")
    if s.headers:
        items = ", ".join(f"{_yaml_scalar(k)}: {_yaml_scalar(v)}" for k, v in s.headers.items())
        out.append(f"{sub}headers: {{{items}}}")
    out.append(f"{sub}enabled: {str(bool(s.enabled))}")
    return out


def _rewrite_mcp_servers(lines: list[str], servers: list[McpServerConfig]) -> bool:
    """整体替换 conf.yaml `task_platform.mcp.servers` 列表（保留服务器块头注释）。

    找到 servers: 行，用新列表重渲染替换其整个子块；`servers:` 与首个 item 之间的
    注释/空行（如「启用前先本机预热」提示）原样保留。
    """
    tp_start, _, tp_end = _find_block_extent(lines, _TP_RE)
    if tp_start is None:
        return False
    mcp_start, _, mcp_end = _find_block_extent(
        lines, re.compile(r"^(\s+)mcp:\s*(#.*)?$"), tp_start + 1
    )
    if mcp_start is None:
        return False
    servers_idx = None
    for j in range(mcp_start, mcp_end):
        if lines[j].lstrip().startswith("servers:"):
            servers_idx = j
            break
    if servers_idx is None:
        return False
    servers_indent = len(lines[servers_idx]) - len(lines[servers_idx].lstrip())
    end = mcp_end
    for j in range(servers_idx + 1, mcp_end):
        raw = lines[j]
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= servers_indent:
            end = j
            break
    # 保留 servers: 与首个 item 之间的注释/空行
    keep = []
    for j in range(servers_idx + 1, end):
        s = lines[j].strip()
        if s.startswith("#") or s == "":
            keep.append(lines[j])
        else:
            break
    new_lines = [lines[servers_idx]] + keep
    for s in servers:
        new_lines.extend(_render_mcp_server(s, servers_indent + 2))
    lines[servers_idx:end] = new_lines
    return True


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

def _validate_patch(patch: dict) -> None:
    """校验扁平配置 patch：未知 key、类型、数值边界。非法抛 ValueError。"""
    unknown = set(patch) - _FLAT_KEYS
    if unknown:
        raise ValueError(f"未知配置字段：{', '.join(sorted(unknown))}")
    for key in _FLAT_KEYS:
        if key not in patch:
            continue
        value = patch[key]
        if key in ("enabled", "allow_network", "embedding_enabled",
                   "read_before_write", "web_search_enabled", "bash_audit"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须是布尔值")
        elif key in _INT_BOUNDS:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须是整数")
            lo, hi = _INT_BOUNDS[key]
            if not (lo <= value <= hi):
                raise ValueError(f"{key} 必须在 {lo}~{hi} 之间")
        elif key in _FLOAT_BOUNDS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} 必须是数字")
            lo, hi = _FLOAT_BOUNDS[key]
            if not (lo <= float(value) <= hi):
                raise ValueError(f"{key} 必须在 {lo}~{hi} 之间")
        elif key == "web_search_provider":
            if value not in _WEB_PROVIDERS:
                raise ValueError(f"web_search_provider 必须是 {'/'.join(_WEB_PROVIDERS)}")
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是字符串")


def _parse_server(raw: Any, *, index: int) -> McpServerConfig:
    """解析单个 MCP 服务器配置；非法抛 ValueError（带序号便于定位）。"""
    if not isinstance(raw, dict):
        raise ValueError(f"第 {index + 1} 个 MCP 服务器必须是对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"第 {index + 1} 个 MCP 服务器缺少 name")
    if len(name) > 64:
        raise ValueError(f"MCP 服务器 name 过长（>64 字符）：{name[:20]}…")
    transport = str(raw.get("transport") or "stdio").strip() or "stdio"
    if transport not in _MCP_TRANSPORTS:
        raise ValueError(f"第 {index + 1} 个服务器 transport={transport!r} 不支持")
    command = str(raw.get("command") or "").strip()
    if transport == "stdio" and not command:
        raise ValueError(f"stdio 服务器 {name} 需要 command")
    if transport != "stdio" and not raw.get("url"):
        raise ValueError(f"{transport} 服务器 {name} 需要 url")
    args_raw = raw.get("args") or []
    if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
        raise ValueError(f"服务器 {name} 的 args 必须是字符串数组")
    headers_raw = raw.get("headers") or {}
    if not isinstance(headers_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers_raw.items()
    ):
        raise ValueError(f"服务器 {name} 的 headers 必须是字符串映射")
    url = raw.get("url")
    return McpServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=list(args_raw),
        url=str(url) if url else None,
        headers={str(k): str(v) for k, v in headers_raw.items()},
        enabled=bool(raw.get("enabled", True)),
    )


# --------------------------------------------------------------------------- #
# 读/写编排
# --------------------------------------------------------------------------- #

def _public_config(cfg: TaskPlatformConfig) -> dict[str, Any]:
    """TaskPlatformConfig → 扁平化公开配置（不暴露 db_path / LLM 密钥 / max_iterations）。

    v3：API key（tavily/jina）暴露给本地设置界面（localhost 守卫），空字符串 = 未配置。
    """
    return {
        "enabled": cfg.enabled,
        "tasks_root": cfg.tasks_root,
        "tool_timeout_sec": cfg.tool_timeout_sec,
        "bash_output_limit": cfg.bash_output_limit,
        "write_limit_bytes": cfg.write_limit_bytes,
        "read_limit_bytes": cfg.read_limit_bytes,
        "allow_network": cfg.allow_network,
        "max_no_progress": cfg.max_no_progress,
        "skills_root": cfg.skills_root,
        "agents_root": cfg.agents_root,
        "plugins_root": cfg.plugins_root,
        "embedding_enabled": cfg.embedding_enabled,
        "embedding_top_k": cfg.embedding_top_k,
        # v3
        "llm_context_window": cfg.llm_context_window,
        "read_before_write": cfg.read_before_write,
        "web_search_enabled": cfg.web_search_enabled,
        "web_search_provider": cfg.web_search_provider,
        "web_search_max_results": cfg.web_search_max_results,
        "web_fetch_max_bytes": cfg.web_fetch_max_bytes,
        "tavily_api_key": cfg.tavily_api_key or "",
        "jina_api_key": cfg.jina_api_key or "",
        "bash_audit": cfg.bash_audit,
        "token_budget_warn_ratio": cfg.token_budget_warn_ratio,
        "token_budget_hard_ratio": cfg.token_budget_hard_ratio,
        "memory_max_injection_tokens": cfg.memory_max_injection_tokens,
        "mcp_servers": [
            {
                "name": s.name,
                "transport": s.transport,
                "command": s.command,
                "args": list(s.args),
                "url": s.url,
                "headers": dict(s.headers),
                "enabled": s.enabled,
            }
            for s in cfg.mcp_servers
        ],
    }


def _apply_task_platform_patch(patch: dict) -> list[str]:
    """把扁平配置 patch 外科手术式写入 conf.yaml 的 task_platform 块。

    仅重写 patch 中出现的叶（未涉及的叶保持原样，注释/布尔大小写零 churn）。
    返回实际写入的 flat key 列表。结构缺失 → KeyError。
    """
    _validate_patch(patch)
    with open(_CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tp_start, _, tp_end = _find_block_extent(lines, _TP_RE)
    if tp_start is None:
        raise KeyError("conf.yaml 缺少 task_platform 块")
    written: list[str] = []
    for flat_key, yaml_key, sub_re, kind in _WRITE_MAP:
        if flat_key not in patch:
            continue
        start, end = tp_start, tp_end
        if sub_re is not None:
            s, _, e = _find_block_extent(lines, sub_re, tp_start + 1)
            if s is None or e > tp_end:
                raise KeyError(f"task_platform 块缺少 {sub_re.pattern!r} 子块")
            start, end = s, e
        value = patch[flat_key]
        if kind == "bool":
            ok = _rewrite_bool_leaf(lines, start, end, yaml_key, bool(value))
        elif kind == "int":
            ok = _rewrite_int_leaf(lines, start, end, yaml_key, int(value))
        elif kind == "float":
            ok = _rewrite_float_leaf(lines, start, end, yaml_key, float(value))
        else:
            ok = _rewrite_leaf(lines, start, end, yaml_key, str(value))
        if not ok:
            raise KeyError(f"task_platform 块缺少叶 {yaml_key}")
        written.append(flat_key)
    if not written:
        raise ValueError("没有可保存的字段")
    _backup_conf_once()
    _atomic_write_conf(lines)
    return written


def _apply_mcp_servers(servers: list[McpServerConfig]) -> None:
    """整体替换 conf.yaml 的 mcp.servers 列表。"""
    with open(_CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if not _rewrite_mcp_servers(lines, servers):
        raise KeyError("conf.yaml 缺少 task_platform.mcp.servers 块")
    _backup_conf_once()
    _atomic_write_conf(lines)


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_task_config_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/task-platform/config")
    async def get_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        # force_reload：始终读磁盘最新值，写入后无需重启即可看到保存结果。
        cfg = task_config(force_reload=True)
        return JSONResponse({"ok": True, "config": _public_config(cfg)})

    @router.put("/api/task-platform/config")
    async def put_config(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if "mcp_servers" in body:
            return JSONResponse(
                {"ok": False, "error": "请使用 POST /api/task-platform/mcp/servers 保存 MCP 服务器"},
                status_code=400,
            )
        try:
            written = _apply_task_platform_patch(body)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except (KeyError, OSError) as e:
            logger.error(f"task-platform config write failed: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        logger.info(f"task-platform config updated: {written}")
        return JSONResponse({"ok": True, "updated": written, "restart_required": True})

    @router.post("/api/task-platform/mcp/servers")
    async def put_mcp_servers(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        if not isinstance(body, list):
            return JSONResponse({"ok": False, "error": "Body 必须是 MCP 服务器数组。"}, status_code=400)
        servers: list[McpServerConfig] = []
        for i, raw in enumerate(body):
            try:
                servers.append(_parse_server(raw, index=i))
            except ValueError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        try:
            _apply_mcp_servers(servers)
        except (KeyError, OSError) as e:
            logger.error(f"task-platform mcp.servers write failed: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        logger.info(f"task-platform mcp.servers saved: {len(servers)} servers")
        return JSONResponse({"ok": True, "count": len(servers), "restart_required": True})

    @router.post("/api/task-platform/mcp/probe")
    async def probe_mcp_server(request: Request):
        """探测单个 MCP 服务器：连接并列出工具。fail-soft：失败返回 status=error 而非 500。"""
        if not _is_local_request(request):
            return _forbidden()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON body."}, status_code=400)
        try:
            server = _parse_server(body, index=0)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        try:
            cfg = TaskPlatformConfig(mcp_servers=[server])
            results = await mcp_client.probe_servers(cfg)
        except Exception as e:  # noqa: BLE001 探测失败不炸请求
            logger.warning(f"mcp probe {server.name} failed: {e}")
            results = []
        result = results[0] if results else {
            "name": server.name, "transport": server.transport,
            "enabled": server.enabled, "status": "error", "error": "探测失败",
        }
        return JSONResponse({"ok": True, "result": result})

    return router
