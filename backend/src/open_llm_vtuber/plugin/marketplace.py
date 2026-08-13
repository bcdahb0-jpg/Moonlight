"""plugin/marketplace.py — 技能市场（P5 插件生态，my-neuro 思路）。

- 目录册：优先拉远程 JSON（URL 可配置，conf `system_config.plugin.marketplace_url`），
  失败降级内置 `marketplace_catalog.json`（随包分发，离线可用）。
- 安装：后台线程下载 zip（download_url 或 repo 动态解析 main.zip）→
  解压去顶层壳 + 防 zip-slip → 校验 plugin.json → 原子 rename 到
  `plugins/community/<name>/` → 加入列表。
- 进度：内存 dict `install_tasks[plugin_id] = {status, progress, error}`，
  前端轮询 `/api/plugin/marketplace/install-status/<id>`。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .registry import PLUGINS_ROOT, scan_plugins

#: 内置目录册（随包分发，离线兜底）。
_CATALOG_FILE = Path(__file__).parent / "marketplace_catalog.json"


def _load_builtin_catalog() -> list[dict]:
    try:
        data = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
        items = data.get("plugins") if isinstance(data, dict) else data
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"plugin: 内置目录册不可用: {e}")
        return []


def _fetch_catalog(url: str, timeout: float = 5.0) -> Optional[list[dict]]:
    """拉远程目录册；失败 → None。"""
    try:
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(url, headers={"User-Agent": "moonlight/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("plugins") if isinstance(data, dict) else data
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"plugin: 远程目录册 {url} 不可达（{e}），降级内置")
        return None


def catalog(remote_url: str = "") -> list[dict]:
    """目录册 = 远程（可配置）优先，失败/空 URL → 内置。"""
    if remote_url:
        remote = _fetch_catalog(remote_url)
        if remote:
            return remote
    return _load_builtin_catalog()


# --------------------------------------------------------------------------- #
# 安装（后台线程）
# --------------------------------------------------------------------------- #

_install_tasks: dict[str, dict[str, Any]] = {}
_install_lock = threading.Lock()


def install_status(plugin_id: str) -> dict:
    with _install_lock:
        return dict(_install_tasks.get(plugin_id, {"status": "idle", "progress": 0}))


def install(plugin_id: str, item: dict) -> dict:
    """发起后台安装；已在安装/已安装 → 拒绝。"""
    existing = {p.plugin_id for p in scan_plugins()}
    if f"community/{plugin_id}" in existing:
        return {"ok": False, "error": f"已安装: {plugin_id}"}
    with _install_lock:
        if _install_tasks.get(plugin_id, {}).get("status") == "installing":
            return {"ok": False, "error": "安装进行中"}
        _install_tasks[plugin_id] = {"status": "installing", "progress": 0}
    threading.Thread(
        target=_install_worker, args=(plugin_id, item), daemon=True
    ).start()
    return {"ok": True, "plugin_id": plugin_id}


def _install_worker(plugin_id: str, item: dict) -> None:
    """后台安装线程：下载 → 解压 → 校验 → 原子落盘。"""
    try:
        _set_progress(plugin_id, 10, "下载中")
        archive_path, tmp_dir = _download_and_extract(item)
        if archive_path is None or tmp_dir is None:
            _fail(plugin_id, "下载/解压失败（离线或 URL 无效）")
            return

        meta_file = tmp_dir / "plugin.json"
        if not meta_file.is_file():
            _fail(plugin_id, "压缩包内缺少 plugin.json")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        _set_progress(plugin_id, 70, "校验中")
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _fail(plugin_id, "plugin.json 非法")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        name = str(meta.get("name") or plugin_id)
        dest = PLUGINS_ROOT / "community" / name
        if dest.exists():
            _fail(plugin_id, f"目标目录已存在: {name}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
        PLUGINS_ROOT.joinpath("community").mkdir(parents=True, exist_ok=True)
        _set_progress(plugin_id, 90, "落盘中")
        shutil.move(str(tmp_dir), str(dest))  # 原子 rename
        _set_progress(plugin_id, 100, "完成")
    except Exception as e:  # noqa: BLE001
        logger.error(f"plugin: 安装 {plugin_id} 异常: {e}")
        _fail(plugin_id, f"安装异常: {e}")


def _set_progress(plugin_id: str, progress: int, status: str) -> None:
    with _install_lock:
        _install_tasks[plugin_id] = {
            "status": status,
            "progress": progress,
            "ts": int(time.time()),
        }


def _fail(plugin_id: str, error: str) -> None:
    with _install_lock:
        _install_tasks[plugin_id] = {
            "status": "error",
            "progress": 0,
            "error": error,
            "ts": int(time.time()),
        }


def _download_and_extract(item: dict) -> tuple[Optional[Path], Optional[Path]]:
    """下载 zip → 解压到临时目录（去顶层壳 + 防 zip-slip）。

    返回 (archive_path 或 None, tmp_dir 或 None)。tmp_dir 内应含 plugin.json。
    """
    import tempfile  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    download_url = str(item.get("download_url") or "")
    repo = str(item.get("repo") or "")
    if not download_url and repo:
        repo = repo.rstrip("/")
        download_url = f"{repo}/archive/refs/heads/main.zip"

    tmp_root = Path(tempfile.mkdtemp(prefix="moonlight_plugin_"))
    archive_path = tmp_root / "plugin.zip"
    try:
        req = urllib.request.Request(
            download_url, headers={"User-Agent": "moonlight/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(
            archive_path, "wb"
        ) as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"plugin: 下载 {download_url} 失败: {e}")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return None, None

    extract_dir = tmp_root / "extract"
    extract_dir.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as zf:
            for member in zf.namelist():
                target = (extract_dir / member).resolve()
                # zip-slip 防护：解压目标必须仍在 extract_dir 内
                if not str(target).startswith(str(extract_dir.resolve())):
                    raise ValueError(f"非法路径: {member}")
            zf.extractall(extract_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"plugin: 解压失败: {e}")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return None, None

    # 去掉 GitHub archive 顶层目录壳（repo-name-commit-hash/）
    inner = extract_dir
    entries = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(entries) == 1 and not (extract_dir / "plugin.json").exists():
        inner = entries[0]
    return archive_path, inner


__all__: list[str] = ["catalog", "install", "install_status"]
