"""bash 命令审计分级（v3 Phase 5，参考 deer-flow sandbox_audit_middleware）。

高危命令自动拦截，warn 级命令执行但追加审计警告。两级分类：
- **block**：`_HIGH_RISK_PATTERNS`（rm -rf 根目录 / 管道下载执行 / fork 炸弹 / 命令注入等），
  直接拒绝执行，返回 error 文案（工具不抛异常铁律）。
- **warn**：`pip install` / `npm install -g` / `sudo` / `chmod 777` 等高风险操作，
  执行但结果尾部追加 `[审计警告]`。

复合命令拆分：先整条命令跑高危正则，再用 `shlex.split` + 子句拆分（`&&`/`;`/`|`）
逐子句分类——`echo hi && rm -rf /` 也能拦到（deer-flow `_split_compound_command` 思想）。

单向依赖：bash_audit.py ← graph.py；仅 import stdlib。
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from loguru import logger

#: block 级高危正则（适配 Windows cmd + bash 双语义，选取 deer-flow 17 条核心 10 条）。
#: 命中任意一条 → 整条命令拒绝执行。
_HIGH_RISK_PATTERNS: list[re.Pattern] = [
    # 删除根/家目录（bash + Windows 双语义）
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*[~/]\s*$", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+[~/]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/[a-zA-Z]*\s*$", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/[a-zA-Z]+\s*$", re.IGNORECASE),
    re.compile(r"\bdel\s+(/[a-zA-Z]*\s+)*[a-zA-Z]:[\\/](Windows|Program Files|Users)", re.IGNORECASE),
    re.compile(r"\brmdir\s+/s\s+/q\s+[a-zA-Z]:[\\/]", re.IGNORECASE),
    # 管道下载并执行（curl|sh / wget|sh / bash -c "$(curl ...)"）
    re.compile(r"(curl|wget|Invoke-WebRequest)[^|;&]*\|\s*(sh|bash|zsh)\b", re.IGNORECASE),
    re.compile(r"\b(bash|sh|zsh)\s+-c\s*[\"']?\$?\(?\s*(curl|wget)", re.IGNORECASE),
    # 命令注入（eval $(...) / $(curl ...)）
    re.compile(r"\beval\s+[^;]*(\(|\$\(|\$\[)", re.IGNORECASE),
    re.compile(r"\$\(\s*(curl|wget)\s", re.IGNORECASE),
    # fork 炸弹（:(){ :|:& };: 及其变体）
    re.compile(r":\(\)\s*\{|fork\s+bomb|\}\s*;\s*:\s*$", re.IGNORECASE),
    # 权限与系统级破坏
    re.compile(r"\bchmod\s+-R\s+777\s+/", re.IGNORECASE),
    re.compile(r"\bmkfs\.|format\s+[a-zA-Z]:", re.IGNORECASE),
]

#: warn 级高风险操作（执行 + 追加警告）。
_WARN_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bpip\s+install", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\s+-g", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),  # 非根目录的 rm -rf（工作区内可接受但警告）
]

#: rm -rf 高危目标（根/家/通配全局）——token 级兜底判定（防正则漏网）。
_RM_DANGEROUS_ARGS = ("/", "~", "/*", "~/*", "/.*", "~.*")


def _tokens_high_risk(command: str) -> bool:
    """token 级兜底：`rm -rf <arg>` 且 arg 命中危险目标 → block（不依赖正则锚定）。"""
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        return False
    for i, t in enumerate(toks):
        if t in ("rm", "del", "rmdir"):
            if t == "rm":
                force = any(a == "-rf" or (a.startswith("-") and "r" in a and "f" in a) for a in toks[i + 1 :])
                if force:
                    for a in toks[i + 1 :]:
                        if a.startswith("-"):
                            continue
                        if a in _RM_DANGEROUS_ARGS or a.startswith(("/", "~/", "~\\")):
                            return True
            elif t in ("del", "rmdir"):
                for a in toks[i + 1 :]:
                    if a.startswith(("C:\\", "D:\\", "c:\\", "d:\\")) or a in ("/s", "/S"):
                        if any(x in a for x in ("\\Windows", "\\Program Files")):
                            return True
    return False


def _split_compound(command: str) -> list[str]:
    """把复合命令拆成子句（&& / ; / | / || 分隔），引号感知。

    实现：单/双引号内的分隔符不切（deer-flow `_split_compound_command` 同思想）。
    返回的子句会再分别跑高危正则。
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in command:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in ("&", ";", "|"):
            if ch == "&" and buf and buf[-1] == "&":  # 处理 &&（前一个 & 已 append）
                buf.pop()
                if buf:
                    parts.append("".join(buf).strip())
                buf = []
                continue
            if buf:
                parts.append("".join(buf).strip())
                buf = []
            if ch == "|":
                parts.append("|")  # 管道符自身作为标记（正则需要）
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p.strip()] or [command]


def classify_command(command: str) -> tuple[str, list[str]]:
    """分类命令：返回 (level, 命中模式)。

    level ∈ {"block", "warn", "pass"}；命中多个模式时 block 优先。
    - 高危正则跑**整条命令**（管道模式跨段，如 `curl x | sh` 拆段会漏）；
    - warn 判定拆子句（`rm -rf ./tmp` 在工作区内 warn，不影响整条 pass）。
    """
    block_hits: list[str] = []
    warn_hits: list[str] = []
    # token 级兜底（rm -rf 危险目标）先判
    if _tokens_high_risk(command):
        return "block", ["rm/del 高危目标"]
    # 整条命令跑高危正则（管道/注入模式依赖跨段匹配）
    for pat in _HIGH_RISK_PATTERNS:
        if pat.search(command):
            block_hits.append(f"{command!r} → {pat.pattern}")
    # warn 拆子句判定（避免整条命令里一个 rm -rf ./tmp 拖累 pass 段）
    for clause in _split_compound(command):
        for pat in _WARN_PATTERNS:
            if pat.search(clause):
                warn_hits.append(f"{clause!r} → {pat.pattern}")
    if block_hits:
        return "block", block_hits
    if warn_hits:
        return "warn", warn_hits
    return "pass", []


class BashAuditMiddleware(AgentMiddleware):
    """bash 命令审计：wrap_tool_call 只拦 `bash` 工具。

    block → 返回 error ToolMessage（不执行）；warn → 执行后结果尾部追加 `[审计警告]`。
    同步/异步双实现（hooks.py 惯例）。
    """

    def __init__(self, enabled: bool = True):
        super().__init__()
        self.enabled = enabled

    def wrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        if not self.enabled:
            return execute(request)
        call = request.tool_call
        if call.get("name") != "bash":
            return execute(request)
        command = str((call.get("args") or {}).get("command", ""))
        if not command:
            return execute(request)
        level, hits = classify_command(command)
        if level == "block":
            logger.warning(f"bash_audit: BLOCK {command!r} → {hits}")
            return ToolMessage(
                content=(
                    f"[沙箱] 高危命令被拒绝执行：{hits[0]}\n"
                    f"（命令：{command}）请改用安全的替代命令或拆分操作。"
                ),
                status="error",
                tool_call_id=str(call.get("id", "") or ""),
            )
        if level == "warn":
            return self._append_warning(execute(request), hits)
        return execute(request)

    async def awrap_tool_call(self, request: ToolCallRequest, execute: Callable) -> Any:
        if not self.enabled:
            return await execute(request)
        call = request.tool_call
        if call.get("name") != "bash":
            return await execute(request)
        command = str((call.get("args") or {}).get("command", ""))
        if not command:
            return await execute(request)
        level, hits = classify_command(command)
        if level == "block":
            logger.warning(f"bash_audit: BLOCK {command!r} → {hits}")
            return ToolMessage(
                content=(
                    f"[沙箱] 高危命令被拒绝执行：{hits[0]}\n"
                    f"（命令：{command}）请改用安全的替代命令或拆分操作。"
                ),
                status="error",
                tool_call_id=str(call.get("id", "") or ""),
            )
        if level == "warn":
            return self._append_warning(await execute(request), hits)
        return await execute(request)

    @staticmethod
    def _append_warning(result: Any, hits: list[str]) -> Any:
        if isinstance(result, ToolMessage):
            result = ToolMessage(
                content=f"{result.content}\n[审计警告] 命令含高风险操作：{hits[0]}",
                tool_call_id=result.tool_call_id,
                status=result.status or "success",
            )
        else:
            result = f"{result}\n[审计警告] 命令含高风险操作：{hits[0]}"
        return result
