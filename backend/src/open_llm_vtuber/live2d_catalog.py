"""Live2D 模型目录扫描 / 热切换（P1 多模型与专属 Prompt）。

- GET  /api/live2d/models              → {models:[{name, model_url, custom_prompt, has_prompt}], current}
- POST /api/live2d/models/{name}/load  → 写 conf character_config.live2d_model_name（surgical）
- GET  /api/live2d/models/{name}/prompt → 读取模型目录 model_prompt.txt
- POST /api/live2d/models/{name}/prompt → 写回 model_prompt.txt（UTF-8）

扫描规则：`live2d-models/**/*.model3.json`；模型名 = model3 所在目录名；
专属 Prompt = 同目录 `model_prompt.txt`（可选，缺失时 custom_prompt 为空）。
模型 URL = `/live2d-models/<相对路径>`（正斜杠，与 server.py 静态挂载一致）。

参考：reference/SoulLink_Live2D/src/models/scanner.py
（递归扫描 + model_prompt.txt 读取 + 相对路径前缀技巧）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from loguru import logger

from .llm_config_route import _is_local_request, _forbidden
from .translator_route import _find_block_extent, _backup_once, _atomic_write, _quote_yaml_scalar, CONF_PATH
from .memory_route import _character_config_extent

# 模型根目录（相对后端 cwd，与 server.py 静态挂载一致）。
MODELS_DIR = Path("live2d-models")


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #

# 玩家可放入模型顶层目录的缩略图文件名（按顺序尝试；<name>.png 也尝试）。
# 与 character_route._THUMB_NAMES 保持同一定义，保证两个入口看到同一张图。
_THUMB_NAMES = (
    "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg", "thumbnail.webp",
    "preview.png", "preview.jpg", "icon.png",
)


def _detect_thumbnail(name: str, base_dir: Path = MODELS_DIR) -> Optional[str]:
    """模型顶层目录内的缩略图 Web URL，或 None（与角色卡缩略图约定一致）。"""
    folder = base_dir / name
    if not folder.is_dir():
        return None
    for fn in (*_THUMB_NAMES, f"{name}.png", f"{name}.jpg"):
        if (folder / fn).is_file():
            return f"/live2d-models/{name}/{fn}"
    return None


def scan_models(base_dir: Path = MODELS_DIR) -> list[dict]:
    """递归扫描 *.model3.json，返回模型清单（按目录名排序）。

    每项含 name / model_url / custom_prompt / has_prompt / thumbnail；
    这是全站唯一模型清单扫描器（character_route 的皮肤注册只做 model_dict 补注册）。
    """
    models: list[dict] = []
    if not base_dir.exists():
        return models
    for model_file in sorted(base_dir.rglob("*.model3.json")):
        rel = model_file.relative_to(base_dir).as_posix()
        # 模型名 = 相对根目录的第一级目录（mao_pro/runtime/xx.model3.json → mao_pro）
        name = rel.split("/")[0]
        model_url = f"/live2d-models/{rel}"
        prompt_path = model_file.parent / "model_prompt.txt"
        custom_prompt = ""
        if prompt_path.exists():
            try:
                custom_prompt = prompt_path.read_text(encoding="utf-8").strip()
            except Exception:
                custom_prompt = ""
        models.append(
            {
                "name": name,
                "model_url": model_url,
                "custom_prompt": custom_prompt,
                "has_prompt": bool(custom_prompt),
                "thumbnail": _detect_thumbnail(name, base_dir),
            }
        )
    return models


def _current_model_from_conf() -> str:
    try:
        from .config_manager.utils import read_yaml  # noqa: PLC0415

        data = read_yaml(CONF_PATH) or {}
        cc = data.get("character_config", {}) or {}
        return str(cc.get("live2d_model_name") or "hiyori")
    except Exception:
        return "hiyori"


def _write_model_name(name: str) -> bool:
    """surgical 写 character_config.live2d_model_name（leaf 已存在）。"""
    with open(CONF_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cc_start, cc_end = _character_config_extent(lines)
    for j in range(cc_start, cc_end):
        stripped = lines[j].lstrip()
        if stripped.startswith("live2d_model_name:"):
            indent_ws = lines[j][: len(lines[j]) - len(stripped)]
            comment = ""
            m_comment = re.search(r"(\s+#.*?)\s*$", lines[j].rstrip("\n"))
            if m_comment:
                comment = m_comment.group(1)
            lines[j] = f"{indent_ws}live2d_model_name: {_quote_yaml_scalar(name)}{comment}\n"
            _backup_once()
            _atomic_write(lines)
            return True
    return False


def _resolve_model(name: str) -> Optional[Path]:
    """按模型名定位 model3 文件；防路径穿越（仅允许扫描结果内）。"""
    for m in scan_models():
        if m["name"] == name:
            return MODELS_DIR / Path(m["model_url"]).parent.relative_to("/live2d-models")
    return None


# --------------------------------------------------------------------------- #
# Route factory
# --------------------------------------------------------------------------- #

def init_live2d_catalog_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/live2d/models")
    async def list_models(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return JSONResponse(
            {
                "ok": True,
                "models": scan_models(),
                "current": _current_model_from_conf(),
            }
        )

    @router.post("/api/live2d/models/{name}/load")
    async def load_model(request: Request, name: str):
        if not _is_local_request(request):
            return _forbidden()
        if "/" in name or "\\" in name or ".." in name:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid model name"})
        model = next((m for m in scan_models() if m["name"] == name), None)
        if model is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": f"model '{name}' not found"})
        try:
            written = _write_model_name(name)
        except Exception as e:
            logger.error(f"[live2d-catalog] write model failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "conf write failed"})
        if not written:
            return JSONResponse(status_code=500, content={"ok": False, "error": "live2d_model_name leaf not found"})
        logger.info(f"[live2d-catalog] model switched -> {name}")
        return JSONResponse(
            {
                "ok": True,
                "name": name,
                "model_url": model["model_url"],
                "custom_prompt": model["custom_prompt"],
                # 前端可直接用 model_url 热切换渲染层，无需重启后端。
                "hot_switch": True,
            }
        )

    @router.get("/api/live2d/models/{name}/prompt")
    async def get_prompt(request: Request, name: str):
        if not _is_local_request(request):
            return _forbidden()
        model_dir = _resolve_model(name)
        if model_dir is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "model not found"})
        prompt_path = model_dir / "model_prompt.txt"
        content = ""
        if prompt_path.exists():
            try:
                content = prompt_path.read_text(encoding="utf-8").strip()
            except Exception:
                content = ""
        return JSONResponse({"ok": True, "name": name, "custom_prompt": content})

    @router.post("/api/live2d/models/{name}/prompt")
    async def set_prompt(request: Request, name: str):
        if not _is_local_request(request):
            return _forbidden()
        model_dir = _resolve_model(name)
        if model_dir is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "model not found"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON"})
        content = str((body or {}).get("custom_prompt") or "").strip()[:4000]
        try:
            prompt_path = model_dir / "model_prompt.txt"
            prompt_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error(f"[live2d-catalog] prompt write failed: {type(e).__name__}: {e}")
            return JSONResponse(status_code=500, content={"ok": False, "error": "prompt write failed"})
        return JSONResponse({"ok": True, "name": name, "custom_prompt": content})

    return router
