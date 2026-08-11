"""
VoiceVoxManager — VOICEVOX 本地引擎的下载 / 启动 / 停止管理。

引擎（仅 HTTP 服务的单体，无 GUI）下载到 backend/vendor/voicevox_engine/，
运行于 127.0.0.1:50021（与 conf.yaml voicevox_tts.base_url 一致）。
- 下载：GitHub Releases（VOICEVOX/voicevox_engine），自动找 windows-x64 zip；
  支持 ghproxy 镜像前缀（网络慢时）。后台线程下载 + 进度文件。
- 启动：subprocess 拉起引擎 exe，日志写 backend/logs/voicevox_engine.log。
- 停止：按 PID 终止（Windows 用 taskkill /T 杀进程树）。

所有方法 fail-soft：任何异常都不影响主服务。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from typing import Optional

from loguru import logger

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

# voicevox_manager.py 位于 backend/src/open_llm_vtuber/ —— 上溯 3 层才是 backend/
BACKEND_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
VENDOR_DIR = os.path.join(BACKEND_DIR, "vendor", "voicevox_engine")
LOGS_DIR = os.path.join(BACKEND_DIR, "logs")
PROGRESS_FILE = os.path.join(VENDOR_DIR, ".download_progress.json")
PID_FILE = os.path.join(VENDOR_DIR, ".engine.pid")

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 50021
HEALTH_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}/version"

# GitHub Releases（VOICEVOX/voicevox_engine）—— 引擎单体，Windows x64 zip。
RELEASE_API = "https://api.github.com/repos/VOICEVOX/voicevox_engine/releases/latest"
MIRROR_PREFIXES = ("https://ghproxy.net/", "https://ghproxy.com/", "https://mirror.ghproxy.com/")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_download_lock = threading.Lock()
_process_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get_json(url: str, timeout: float = 15.0):
    if requests is None:
        raise RuntimeError("requests 未安装")
    resp = requests.get(url, timeout=timeout, verify=False, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _find_engine_exe(directory: str) -> Optional[str]:
    """在解压目录中找引擎可执行文件（run.exe / voicevox_engine.exe / *.exe）。"""
    if not os.path.isdir(directory):
        return None
    for root, _dirs, files in os.walk(directory):
        # 按目录层级限制（根=0，直接子目录=1…），避免走进 python/ 等运行库子目录
        rel = os.path.relpath(root, directory)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 3:
            break
        for name in files:
            if name.lower().endswith(".exe"):
                low = name.lower()
                if "voicevox" in low or "run" in low or "engine" in low:
                    p = os.path.join(root, name)
                    # 排除 python.exe 等运行库自带 exe
                    if os.path.basename(p).lower() in ("python.exe", "pythonw.exe"):
                        continue
                    return p
    return None


def _extract_archive(archive_path: str, dest: str) -> None:
    """解压引擎包：zip 用 zipfile；7z 依次尝试 tar(libarchive) / 7-Zip / py7zr。"""
    low = archive_path.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest)
        return

    # 1) Windows 10+ 自带 tar.exe（libarchive），支持 7z 单卷。
    #    Git Bash 的 GNU tar 不支持 7z，因此优先用 System32 绝对路径。
    for tar_exe in (
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe"),
        "tar",
    ):
        try:
            r = subprocess.run(
                [tar_exe, "-xf", archive_path, "-C", dest],
                capture_output=True, timeout=900,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass

    # 2) 7-Zip
    for seven in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        "7z",
    ):
        try:
            r = subprocess.run(
                [seven, "x", archive_path, f"-o{dest}", "-y"],
                capture_output=True, timeout=900,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass

    # 3) py7zr（纯 Python）
    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(archive_path, "r") as z:
            z.extractall(dest)
        return
    except Exception:
        pass

    raise RuntimeError(
        "解压失败：需要安装 7-Zip（或 pip install py7zr）后再试"
    )


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def _read_progress() -> dict:
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_progress(data: dict) -> None:
    try:
        os.makedirs(VENDOR_DIR, exist_ok=True)
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _read_pid() -> Optional[int]:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class VoiceVoxManager:
    def status(self) -> dict:
        """返回引擎状态：missing / downloading(progress) / downloaded / failed / running。"""
        prog = _read_progress()
        # 失败是终态：保留失败原因供前端展示（避免"进度条悄悄消失"）
        if not prog.get("active") and str(prog.get("phase", "")).startswith("failed"):
            return {
                "state": "failed",
                "running": False,
                "progress": 0,
                "phase": prog.get("phase", "failed"),
                "size_mb": 0,
            }
        if prog.get("active") is True:
            return {
                "state": "downloading",
                "running": False,
                "progress": prog.get("progress", 0),
                "phase": prog.get("phase", "downloading"),
                "size_mb": prog.get("size_mb", 0),
            }
        has_engine = _find_engine_exe(VENDOR_DIR) is not None
        running = _port_open(ENGINE_HOST, ENGINE_PORT)
        if running:
            return {"state": "downloaded", "running": True, "progress": 100}
        if has_engine:
            return {"state": "downloaded", "running": False, "progress": 100}
        return {"state": "missing", "running": False, "progress": 0}

    # ------------------------------------------------------------------ //
    # 下载
    # ------------------------------------------------------------------ //
    def resolve_download_url(self) -> str:
        """查询最新 release，返回 Windows 引擎包的下载地址。

        注意：VOICEVOX ENGINE 0.25+ 不再发布 .zip，改为 7z 分卷（.7z.001，
        单卷，1.7GB）或 .vvpp。这里按「windows + cpu 优先 + 7z.001 优先」
        评分挑选，确保总能拿到可解压的包。
        """
        data = _http_get_json(RELEASE_API)
        assets = data.get("assets", [])

        def score(a: dict) -> int:
            name = (a.get("name") or "").lower()
            s = 0
            if "windows" in name:
                s += 100
            if "cpu" in name:
                s += 50
            elif "directml" in name:
                s += 30
            elif "nvidia" in name:
                s += 10
            if name.endswith(".7z.001"):
                s += 10  # 单卷 7z，Windows 自带 tar(libarchive) 可解
            elif name.endswith(".zip"):
                s += 8
            elif name.endswith(".vvpp"):
                s += 4
            return s

        candidates = [a for a in assets if score(a) > 100]
        if not candidates:
            raise RuntimeError("未在最新 release 中找到 Windows 引擎包")
        best = max(candidates, key=score)
        return best["browser_download_url"]

    def download(self, use_mirror: bool = False) -> None:
        """后台下载引擎 zip 并解压到 VENDOR_DIR。"""
        with _download_lock:
            if _read_progress().get("active"):
                logger.info("[voicevox] download already in progress")
                return
            _write_progress({"active": True, "phase": "resolving", "progress": 0})
        try:
            url = self.resolve_download_url()
            if use_mirror:
                for pre in MIRROR_PREFIXES:
                    if not url.startswith("https://github.com"):
                        break
                    url = pre + url
                    logger.info(f"[voicevox] using mirror: {pre}")
                    break
            if requests is None:
                raise RuntimeError("requests 未安装")
            logger.info(f"[voicevox] downloading {url}")

            os.makedirs(VENDOR_DIR, exist_ok=True)
            archive_path = os.path.join(VENDOR_DIR, "engine" + self._archive_ext(url))
            with requests.get(url, stream=True, timeout=(20, 300), verify=False,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done = 0
                _write_progress({"active": True, "phase": "downloading",
                                 "progress": 0, "size_mb": round(total / 1048576, 1)})
                with open(archive_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            _write_progress({
                                "active": True, "phase": "downloading",
                                "progress": round(done * 100 / total, 1),
                                "size_mb": round(total / 1048576, 1),
                            })

            _write_progress({"active": True, "phase": "extracting", "progress": 99})
            _extract_archive(archive_path, VENDOR_DIR)
            try:
                os.remove(archive_path)
            except Exception:
                pass
            _write_progress({"active": False, "phase": "done", "progress": 100})
            logger.info("[voicevox] engine downloaded & extracted")
        except Exception as e:
            logger.error(f"[voicevox] download failed: {type(e).__name__}: {e}")
            _write_progress({"active": False, "phase": f"failed: {e}", "progress": 0})
            raise

    @staticmethod
    def _archive_ext(url: str) -> str:
        name = url.rsplit("/", 1)[-1].lower()
        if name.endswith(".7z.001"):
            return ".7z.001"
        if name.endswith(".zip"):
            return ".zip"
        return ".7z"

    def download_async(self, use_mirror: bool = False) -> bool:
        """线程内启动下载；返回是否已开始（False=已有任务进行中）。"""
        with _download_lock:
            if _read_progress().get("active"):
                return False
        threading.Thread(target=self.download, args=(use_mirror,), daemon=True).start()
        return True

    # ------------------------------------------------------------------ //
    # 启动 / 停止
    # ------------------------------------------------------------------ //
    def start(self) -> dict:
        with _process_lock:
            if _port_open(ENGINE_HOST, ENGINE_PORT):
                return {"ok": True, "running": True, "msg": "引擎已在运行"}
            exe = _find_engine_exe(VENDOR_DIR)
            if not exe:
                return {"ok": False, "running": False, "msg": "引擎未下载，请先下载"}
            os.makedirs(LOGS_DIR, exist_ok=True)
            log_path = os.path.join(LOGS_DIR, "voicevox_engine.log")
            with open(log_path, "a", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    [exe, "--port", str(ENGINE_PORT), "--host", ENGINE_HOST],
                    stdout=f, stderr=subprocess.STDOUT,
                    cwd=os.path.dirname(exe),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            try:
                with open(PID_FILE, "w", encoding="utf-8") as f:
                    f.write(str(proc.pid))
            except Exception:
                pass
            # 等待健康检查（引擎冷启动可能要几秒）
            for _ in range(30):
                if _port_open(ENGINE_HOST, ENGINE_PORT):
                    logger.info(f"[voicevox] engine started (pid={proc.pid})")
                    return {"ok": True, "running": True, "msg": "引擎已启动"}
                if proc.poll() is not None:
                    return {"ok": False, "running": False,
                            "msg": self._start_failure_hint(proc.returncode, log_path)}
                time.sleep(1)
            return {"ok": False, "running": False, "msg": "引擎启动超时（30s 未就绪）"}

    @staticmethod
    def _start_failure_hint(returncode: int, log_path: str) -> str:
        """从引擎日志判断失败原因，给出可操作的提示（尤其沙箱/权限限制）。"""
        hint = f"引擎进程提前退出（exit={returncode}）"
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-30:]
            tail_text = "".join(tail)
            if "PermissionError" in tail_text or "Permission denied" in tail_text:
                hint = (
                    "引擎写用户数据目录（%LOCALAPPDATA%\\voicevox-engine）被当前运行环境限制。"
                    "请在 PowerShell/CMD 中手动启动引擎（cmd 中不要加 ./ 前缀）："
                    "cd backend/vendor/voicevox_engine/windows-cpu && run.exe，"
                    "等 10-15 秒后 curl http://127.0.0.1:50021/version 返回 JSON 即就绪，"
                    "回到设置页点「检测状态」刷新即可。"
                )
            elif "見つかりません" in tail_text or "not found" in tail_text.lower():
                hint = f"引擎组件缺失（{tail_text.strip().splitlines()[-1][:80]}）。建议重新下载引擎。"
            else:
                hint += f"，查看 logs/voicevox_engine.log"
        except Exception:
            pass
        return hint

    def stop(self) -> dict:
        with _process_lock:
            pid = _read_pid()
            stopped = False
            if pid and _process_alive(pid):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, timeout=10,
                    )
                    stopped = True
                except Exception:
                    pass
            # 兜底：按端口占用进程终止
            if _port_open(ENGINE_HOST, ENGINE_PORT):
                try:
                    out = subprocess.run(
                        ["netstat", "-ano"], capture_output=True, text=True, timeout=10
                    ).stdout
                    m = re.search(rf"{ENGINE_HOST}:{ENGINE_PORT}\s+\S+\s+LISTENING\s+(\d+)", out)
                    if m:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", m.group(1)],
                            capture_output=True, timeout=10,
                        )
                        stopped = True
                except Exception:
                    pass
            try:
                if os.path.exists(PID_FILE):
                    os.remove(PID_FILE)
            except Exception:
                pass
            if stopped:
                logger.info("[voicevox] engine stopped")
                return {"ok": True, "running": False, "msg": "引擎已停止"}
            return {"ok": False, "running": False, "msg": "未发现运行中的引擎"}
