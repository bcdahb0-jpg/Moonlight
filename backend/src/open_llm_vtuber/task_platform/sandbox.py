"""白名单沙箱工具（plan §5.3，G4 本地直执行 + 路径白名单）。

- `resolve_local`：`os.path.realpath`（解析 symlink）后 `os.path.commonpath`
  前缀校验 == workspace；`../`、绝对路径逃逸、symlink 越界一律 `SandboxPathError`。
- 工具集（system prompt 硬约束 §5.3）：`ls/glob/grep/read_file/write_file`
  优先，`bash` 仅作最后手段（Windows cmd 语义，强制 timeout + 输出截断）。
- 双向路径映射：输出反写 `to_container`（宿主绝对路径掩码成 `/workspace/...`），
  输入反掩码 `from_container`（`/workspace/...` 还原为 workspace 相对路径，与前者对称）。

单向依赖：sandbox.py ← graph.py / middleware.py（唯一副作用出口）；不 import 其他 task_platform 模块。
"""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import re
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from .conf_bridge import task_config

#: 输出里宿主 workspace 路径的掩码前缀（deer-flow path_patterns 思想）
_CONTAINER_ROOT = "/workspace"


class SandboxPathError(ValueError):
    """路径越界（`../`、绝对路径逃逸、symlink 指向 workspace 外）被拒。"""


class PathMapping:
    """container_path ↔ local_path 双向映射（plan §5.3）。"""

    def __init__(self, workspace: str):
        self.workspace = os.path.realpath(workspace)

    def to_container(self, text: str) -> str:
        """把输出文本里的宿主绝对路径替换成 `/workspace/...`（段边界掩码）。

        注意：Windows 下 `os.path.realpath`/`glob`/`os.walk` 返回 `\\` 分隔路径，
        调用方需先对**路径**做正斜杠归一化（`p.replace("\\", "/")`）再传入，否则掩码失配。
        """
        ws = self.workspace.replace("\\", "/")
        # 段边界 lookahead：只替换独立路径段，防误匹配（deer-flow path_patterns）
        return re.sub(
            re.escape(ws) + r"(?=[/\\]|$)",
            _CONTAINER_ROOT,
            text,
        )

    def from_container(self, container_path: str) -> str:
        """`/workspace/...` 虚拟路径 → workspace 相对路径（`to_container` 的逆）。

        2026-08-10 修复：模型被 system prompt 约束用 `/workspace/...` 虚拟路径，
        若直接交给 resolve_local，Windows 下 pathlib 视其为绝对路径，解析到
        `C:\\workspace\\...` 越界被拒。这里还原为相对路径，与 to_container 闭环。

        - `/workspace/foo` → `foo`；`/workspace` / `/workspace/` → `.`（根）；
        - 其余（相对路径 / 宿主绝对路径）原样返回。
        """
        raw = str(container_path).replace("\\", "/")
        if raw == _CONTAINER_ROOT or raw == _CONTAINER_ROOT + "/":
            return "."
        if raw.startswith(_CONTAINER_ROOT + "/"):
            return raw[len(_CONTAINER_ROOT) + 1:]
        return str(container_path)


class Sandbox:
    """绑定单个 workspace 的本地执行沙箱。"""

    def __init__(
        self,
        workspace: str,
        *,
        timeout_sec: Optional[int] = None,
        bash_output_limit: Optional[int] = None,
        write_limit_bytes: Optional[int] = None,
        read_limit_bytes: Optional[int] = None,
        allow_network: bool = True,
    ):
        cfg = task_config()
        self.mapping = PathMapping(workspace)
        self.workspace = self.mapping.workspace
        self.timeout_sec = timeout_sec or cfg.tool_timeout_sec
        self.bash_output_limit = bash_output_limit or cfg.bash_output_limit
        self.write_limit_bytes = write_limit_bytes or cfg.write_limit_bytes
        self.read_limit_bytes = read_limit_bytes or cfg.read_limit_bytes
        self.allow_network = allow_network and cfg.allow_network

    # ------------------------------------------------------------------ //
    # 路径白名单
    # ------------------------------------------------------------------ //
    def resolve_local(self, container_path: str) -> str:
        """容器路径 → 宿主绝对路径（realpath 后校验在 workspace 内）。

        接受三种输入：`/workspace/...` 虚拟路径（先反掩码）、workspace 相对路径、
        宿主绝对路径。

        Raises:
            SandboxPathError: 越界路径（`..` / 绝对路径逃逸 / symlink 越界）。
        """
        p = Path(self.mapping.from_container(container_path)).expanduser()
        if not p.is_absolute():
            p = Path(self.workspace) / p
        real = os.path.realpath(str(p))
        try:
            common = os.path.commonpath([real, self.workspace])
        except ValueError as e:  # 不同盘符（Windows）
            raise SandboxPathError(
                f"路径越界被拒：{container_path!r}（不在工作目录 {self.workspace} 内）"
            ) from e
        if common != self.workspace:
            raise SandboxPathError(
                f"路径越界被拒：{container_path!r}（解析到 {real}，工作目录 {self.workspace}）"
            )
        return real

    # ------------------------------------------------------------------ //
    # 纯 Python 文件工具（跨平台，plan §5.3）
    # ------------------------------------------------------------------ //
    def ls(self, path: str = ".") -> str:
        try:
            real = self.resolve_local(path)
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        if not os.path.isdir(real):
            return f"[沙箱] 不是目录：{path}"
        try:
            entries = sorted(os.listdir(real))
        except OSError as e:
            return f"[沙箱错误] {e}"
        return self.mapping.to_container("\n".join(entries) if entries else "(空目录)")

    def glob(self, pattern: str) -> str:
        pattern = self.mapping.from_container(pattern)  # 反掩码后再校验/拼接
        try:
            self.resolve_local(pattern)  # 校验 pattern 是否越界（anchor 必须在 workspace 内）
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        p = Path(pattern).expanduser()
        full = str(Path(self.workspace) / p) if not p.is_absolute() else str(p)
        matches = sorted(_glob.glob(full, recursive=True))
        # Windows glob 返回 os.sep='\' 路径，归一化正斜杠后再掩码（见 to_container docstring）
        return self.mapping.to_container(
            "\n".join(m.replace("\\", "/") for m in matches) if matches else "(无匹配)"
        )

    def grep(self, pattern: str, path: str = ".") -> str:
        try:
            real = self.resolve_local(path)
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[沙箱] 正则错误：{e}"
        hits: list[str] = []
        total = 0
        max_hits = 200  # 防止输出爆炸
        for dp, _dns, fns in os.walk(real):
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if rx.search(line):
                                hits.append(
                                    f"{self.mapping.to_container(fp.replace('\\', '/'))}:{lineno}: {line.rstrip()[:200]}"
                                )
                                total += 1
                                if total >= max_hits:
                                    return "\n".join(hits) + "\n...(结果过多，截断)"
                except OSError:
                    continue
        return "\n".join(hits) if hits else "(无匹配)"

    def read_file(self, path: str) -> str:
        try:
            real = self.resolve_local(path)
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        if not os.path.isfile(real):
            return f"[沙箱] 不是文件：{path}"
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(self.read_limit_bytes + 1)
        except OSError as e:
            return f"[沙箱错误] {e}"
        truncated = len(content) > self.read_limit_bytes
        if truncated:
            content = content[: self.read_limit_bytes]
        return content + ("\n...(读取截断，超出上限)" if truncated else "")

    def write_file(self, path: str, content: str) -> str:
        try:
            real = self.resolve_local(path)
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        if len(content.encode("utf-8")) > self.write_limit_bytes:
            return f"[沙箱] 内容超上限（>{self.write_limit_bytes} 字节），拒绝写入"
        try:
            Path(real).parent.mkdir(parents=True, exist_ok=True)
            with open(real, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return f"[沙箱错误] {e}"
        return f"已写入 {self.mapping.to_container(real.replace('\\', '/'))}（{len(content.encode('utf-8'))} 字节）"

    # ------------------------------------------------------------------ //
    # str_replace 精确编辑（v3 Phase 1，参考 deer-flow str_replace_tool）
    # ------------------------------------------------------------------ //
    @staticmethod
    def _detect_line_ending(content: str) -> str:
        """检测文件行尾（\r\n 优先），写回保持原风格（pi edit 思想，Windows 项目刚需）。"""
        if "\r\n" in content:
            return "\r\n"
        return "\n"

    def str_replace(
        self,
        path: str,
        old_str: str,
        new_str: str,
        replace_all: bool = False,
    ) -> str:
        """在文件中做精确文本替换（**不重写整个文件**，token 高效、改动最小化）。

        - `old_str` 精确匹配；未找到 → 返回错误提示（附文件大小 + 建议先 read_file 定位），
          永不抛异常（deer-flow 铁律：工具失败返回 Error 字符串）。
        - `replace_all=False`：仅替换首次出现（默认，防误伤多处相同文本）；
          `replace_all=True`：替换全部。
        - 写回保持原文件行尾（\r\n / \n）。
        """
        try:
            real = self.resolve_local(path)
        except SandboxPathError as e:
            return f"[沙箱] {e}"
        if not os.path.isfile(real):
            return f"[沙箱] 不是文件：{path}"
        if not old_str:
            return "[沙箱] old_str 不能为空"
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return f"[沙箱错误] {e}"
        if len(content.encode("utf-8")) > self.write_limit_bytes:
            return f"[沙箱] 文件超上限（>{self.write_limit_bytes} 字节），请用 write_file 或拆分处理"

        count = content.count(old_str)
        if count == 0:
            return (
                f"[沙箱] 未找到要替换的文本（文件 {self.mapping.to_container(real.replace('\\', '/'))} "
                f"共 {len(content)} 字符）。请先 read_file 查看实际内容后重试。"
            )
        if replace_all:
            new_content = content.replace(old_str, new_str)
            replaced = count
        else:
            new_content = content.replace(old_str, new_str, 1)
            replaced = 1
        eol = self._detect_line_ending(content)
        if eol == "\r\n":
            new_content = new_content.replace("\n", "\r\n")
        try:
            with open(real, "w", encoding="utf-8") as f:
                f.write(new_content)
        except OSError as e:
            return f"[沙箱错误] {e}"
        return (
            f"已替换 {replaced} 处（文件 {self.mapping.to_container(real.replace('\\', '/'))}，"
            f"共 {count} 处匹配{'，仅替换首次' if replaced < count else ''}）"
        )

    # ------------------------------------------------------------------ //
    # bash（最后手段；Windows cmd 语义 + 强制 timeout + 输出截断）
    # ------------------------------------------------------------------ //
    async def bash(self, command: str) -> str:
        if len(command) > 4096:
            return "[沙箱] bash 命令过长（>4096 字符），拒绝执行"
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                raw, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_sec)
            except asyncio.TimeoutError:
                proc.kill()
                return f"[沙箱] bash 超时（>{self.timeout_sec}s），已终止"
            text = raw.decode("utf-8", errors="replace")
            # v6.6：成功退出但 0 输出 → 环境诊断提示（服务若由沙箱拉起，cmd.exe 输出可能被吞，
            # 模型看到空会反复探测浪费轮次；显式提示避免）。退出码非 0 时附加，不吞 stderr。
            if not text.strip() and proc.returncode == 0:
                text = (
                    "[沙箱] 命令执行成功但无输出（exit 0）。"
                    "若预期有输出，可能是运行环境拦截了子进程 stdout——"
                    "请改用 ls/glob/grep/read_file 等文件工具获取结果，或换一种命令写法重试。"
                )
            elif proc.returncode not in (0, None):
                text = f"(exit code {proc.returncode})\n{text}" if text else f"(exit code {proc.returncode})"
        except OSError as e:
            return f"[沙箱错误] {e}"
        truncated = len(text) > self.bash_output_limit
        if truncated:
            text = text[: self.bash_output_limit]
        return text + ("\n...(输出截断)" if truncated else "")


# --------------------------------------------------------------------------- #
# 工具注册：每个工具绑定一个 Sandbox 实例
# --------------------------------------------------------------------------- #
def sandbox_tools(workspace: str) -> list:
    """构建绑定 workspace 的 6 个白名单工具（供 create_agent 注册）。"""
    sb = Sandbox(workspace)

    @tool
    def ls(path: str = ".") -> str:
        """列出目录内容（文件名一行一个）。默认当前目录。"""
        return sb.ls(path)

    @tool
    def glob(pattern: str) -> str:
        """按通配符查找文件路径（支持 ** 递归）。pattern 相对 workspace。"""
        return sb.glob(pattern)

    @tool
    def grep(pattern: str, path: str = ".") -> str:
        """在 path（目录）下递归搜索正则 pattern，返回 文件:行号: 内容 列表。"""
        return sb.grep(pattern, path)

    @tool
    def read_file(path: str) -> str:
        """读取文本文件内容（UTF-8，上限 512KB）。"""
        return sb.read_file(path)

    @tool
    def write_file(path: str, content: str) -> str:
        """写入文本文件（UTF-8，上限 1MB）。父目录自动创建。整文件重写/新建用。"""
        return sb.write_file(path, content)

    @tool
    def str_replace(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
        """精确替换文件中已存在文本（不重写整文件，token 高效）。

        - 修改已有文件的局部内容**优先用本工具**（先 read_file 定位 old_str）；
        - old_str 必须与文件内容精确匹配（含缩进/换行）；
        - replace_all=False 只替换首次出现；同文本多处时用 replace_all=True；
        - 未找到 old_str 会返回错误提示，请 read_file 确认实际内容。
        """
        return sb.str_replace(path, old_str, new_str, replace_all)

    @tool
    async def bash(command: str) -> str:
        """执行 shell 命令（Windows cmd 语义，cwd=workspace，超时 120s，输出截断 64KB）。

        仅在其他工具无法完成时使用：优先 ls/glob/grep/read_file/write_file/str_replace。
        """
        return await sb.bash(command)

    return [ls, glob, grep, read_file, write_file, str_replace, bash]
