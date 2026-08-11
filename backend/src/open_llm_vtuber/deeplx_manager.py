"""
DeepLXManager — DeepLX 本地翻译服务的启动 / 停止 / 状态管理。

DeepLX（OwO-Network/DeepLX）是免费、毫秒级的 DeepL 非官方本地翻译服务，
二进制 backend/vendor/deeplx/deeplx.exe，运行于 127.0.0.1:1188
（与 conf.yaml translator_config.deeplx.deeplx_api_endpoint 默认值一致）。

与 VOICEVOX 一致的管理模式：
- 启动：subprocess 拉起 deeplx.exe，日志写 backend/vendor/deeplx/deeplx.log，
  轮询 1188 端口确认就绪（冷启动通常 <2s）。
- 停止：按 PID 终止（taskkill /F /PID，不带 /T —— deeplx 无子进程）。
- 状态：TCP 探测 1188 + 读 PID 文件。

所有方法 fail-soft：任何异常都不影响主服务。
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

from loguru import logger

# deeplx_manager.py 位于 backend/src/open_llm_vtuber/ —— 上溯 3 层才是 backend/
BACKEND_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DEEPLX_DIR = os.path.join(BACKEND_DIR, "vendor", "deeplx")
PID_FILE = os.path.join(DEEPLX_DIR, ".deeplx.pid")
LOG_FILE = os.path.join(DEEPLX_DIR, "deeplx.log")

HOST = "127.0.0.1"
PORT = 1188

_process_lock = threading.Lock()

# DeepL 官方限流状态（被动检测：由 translate/deeplx.py 在真实请求后写入，
# 不额外发探测请求——避免探测本身加剧限流）。线程安全读写。
_health_lock = threading.Lock()
_health: dict = {
    "rate_limited": False,      # 最近一次请求是否被 DeepL 429 拒绝
    "last_error": "",           # 最近一次错误摘要（429 / 连接拒绝 / ...）
    "last_error_at": None,      # ISO 时间戳
    "last_success_at": None,    # ISO 时间戳（最近一次翻译成功）
}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_exe() -> Optional[str]:
    """定位 deeplx.exe（不存在时返回 None）。"""
    for name in ("deeplx.exe", "deeplx"):
        p = os.path.join(DEEPLX_DIR, name)
        if os.path.isfile(p):
            return p
    return None


# --------------------------------------------------------------------------- #
# 限流/健康状态记录（被动检测，由 translate/deeplx.py 调用）
# --------------------------------------------------------------------------- #
def record_error(kind: str, message: str) -> None:
    """记录一次翻译失败。kind='rate_limit'（DeepL 429）| 'unreachable' | 'other'。"""
    with _health_lock:
        _health["rate_limited"] = kind == "rate_limit"
        _health["last_error"] = f"{kind}: {message}"[:200]
        _health["last_error_at"] = datetime.now().isoformat(timespec="seconds")


def record_success() -> None:
    """记录一次翻译成功（清除限流标记——DeepL 解封后第一次成功即自动恢复）。"""
    with _health_lock:
        _health["rate_limited"] = False
        _health["last_error"] = ""
        _health["last_success_at"] = datetime.now().isoformat(timespec="seconds")


def _read_pid() -> Optional[int]:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(pid))
    except OSError:
        logger.warning(f"[deeplx] could not write pid file: {PID_FILE}")


# --------------------------------------------------------------------------- #
# 管理接口
# --------------------------------------------------------------------------- #
class DeepLXManager:
    @staticmethod
    def status() -> dict:
        """返回 DeepLX 状态：{running, pid, exe_exists, port, msg, rate_limited, ...}。

        rate_limited 为被动检测结果（最近一次真实翻译请求是否被 DeepL 429 拒绝），
        不额外发探测请求，避免探测本身加剧限流。
        """
        running = _port_open(HOST, PORT)
        pid = _read_pid()
        exe = _find_exe()
        with _health_lock:
            health = dict(_health)
        base = {
            "pid": pid,
            "exe_exists": exe is not None,
            "port": PORT,
            **health,
        }
        if running:
            return {
                "running": True,
                "msg": "DeepLX 正在运行",
                **base,
            }
        if exe:
            return {
                "running": False,
                "msg": "已就绪 · 未启动",
                **base,
            }
        return {
            "running": False,
            "msg": "deeplx.exe 未找到，请先下载",
            **base,
        }

    @staticmethod
    def start() -> dict:
        """启动 DeepLX（已在运行则直接返回 ok）。"""
        with _process_lock:
            if _port_open(HOST, PORT):
                return {"ok": True, "running": True, "msg": "DeepLX 已在运行"}
            exe = _find_exe()
            if not exe:
                return {"ok": False, "running": False,
                        "msg": "deeplx.exe 未找到（backend/vendor/deeplx/）"}
            os.makedirs(DEEPLX_DIR, exist_ok=True)
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    proc = subprocess.Popen(
                        [exe],
                        stdout=f, stderr=subprocess.STDOUT,
                        cwd=DEEPLX_DIR,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            except OSError as e:
                logger.error(f"[deeplx] spawn failed: {e}")
                return {"ok": False, "running": False,
                        "msg": f"启动失败：{e}（沙箱/权限限制时请手动运行 start_deeplx.bat）"}
            _write_pid(proc.pid)
            # 等待健康检查（DeepLX 冷启动很快，<2s）
            for _ in range(15):
                if _port_open(HOST, PORT):
                    logger.info(f"[deeplx] started (pid={proc.pid})")
                    return {"ok": True, "running": True, "msg": "DeepLX 已启动"}
                if proc.poll() is not None:
                    return {"ok": False, "running": False,
                            "msg": f"进程提前退出（exit={proc.returncode}），"
                                   f"详情见 {LOG_FILE}；沙箱/权限限制时可手动运行 "
                                   f"backend/vendor/deeplx/start_deeplx.bat"}
                time.sleep(1)
            return {"ok": False, "running": False, "msg": "启动超时（15s 未就绪）"}

    @staticmethod
    def stop() -> dict:
        """停止 DeepLX（按 PID 文件终止，未运行则返回 ok）。"""
        with _process_lock:
            if not _port_open(HOST, PORT):
                return {"ok": True, "running": False, "msg": "DeepLX 未在运行"}
            pid = _read_pid()
            if pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, text=True, timeout=5, check=False,
                    )
                except Exception as e:
                    logger.warning(f"[deeplx] taskkill failed: {e}")
            # 兜底：按端口占用找 PID 强杀（不带 /T，deeplx 无子进程）
            if _port_open(HOST, PORT):
                try:
                    out = subprocess.run(
                        ["netstat", "-ano"], capture_output=True, text=True,
                        timeout=10, check=False,
                    ).stdout
                    for line in out.splitlines():
                        if f":{PORT}" in line and "LISTENING" in line:
                            pid2 = line.split()[-1]
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid2],
                                capture_output=True, timeout=5, check=False,
                            )
                except Exception as e:
                    logger.warning(f"[deeplx] port sweep failed: {e}")
            return {"ok": True, "running": False,
                    "msg": "DeepLX 已停止" if not _port_open(HOST, PORT)
                    else "停止失败，请手动结束占用 1188 端口的进程"}


# 单例（与 engine_route 的 VoiceVoxManager 一致）
mgr = DeepLXManager()


def init_deeplx_manager() -> None:
    """模块导入时无副作用；预留钩子（与 voicevox_manager 风格一致）。"""
    logger.debug(f"[deeplx] manager ready (dir={DEEPLX_DIR}, port={PORT})")
