"""plugin_route.py — 插件生态路由（P5）。

- `GET  /api/plugin/list`                   插件列表（含 enabled）
- `POST /api/plugin/toggle`                 {plugin_id} 启用/停用
- `GET  /api/plugin/marketplace`            技能市场目录册（远程/内置兜底）
- `POST /api/plugin/marketplace/install`    {plugin_id} 后台安装
- `GET  /api/plugin/marketplace/install-status/{plugin_id}` 安装进度
- `GET  /api/export/character|config`      角色卡/配置导出（脱敏）
- `POST /api/import`                        配置导入（merge 白名单）
- `GET  /api/intent/config` / `POST /api/intent/config`  意图配置
- `POST /api/intent/classify`               {text} → {intent, emotion}
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Request, UploadFile
from loguru import logger
from starlette.responses import JSONResponse

from .plugin import (
    analyze_intent,
    catalog,
    export_character,
    export_config,
    find_plugin,
    get_plugin_manager,
    import_config,
    install,
    install_status,
    intent_config,
)

# --------------------------------------------------------------------------- #
# 本地请求守卫（同 emotion_route/live_route：仅 localhost 可访问）
# --------------------------------------------------------------------------- #

def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _forbidden() -> JSONResponse:
    return JSONResponse({"error": "仅本机可访问"}, status_code=403)


# --------------------------------------------------------------------------- #
# conf system_config.plugin / intent 块（surgical upsert）
# --------------------------------------------------------------------------- #

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _find_block_extent(
    lines: list[str], block_re: re.Pattern, start_from: int = 0
) -> tuple[Optional[int], Optional[int], str]:
    """返回 (块起始行, 块结束行, 缩进)。块结束 = 下一个缩进更小/相等的非空行。"""
    for i in range(start_from, len(lines)):
        m = block_re.match(lines[i])
        if not m:
            continue
        indent = m.group(1)
        end = len(lines)
        for j in range(i + 1, len(lines)):
            line = lines[j]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            mj = _CHILD_RE.match(line)
            if not mj or len(mj.group(1)) <= len(indent):
                end = j
                break
        return i, end, indent
    return None, None, ""


def _quote_yaml_scalar(value: str) -> str:
    if re.search(r"[:#\[\]{}&*!|>'\"%@`,]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _upsert_plugin_block(fields: dict) -> bool:
    """surgical 写入 conf system_config.plugin 块（缺失则插入）。"""
    try:
        from .translator_route import CONF_PATH  # noqa: PLC0415
    except Exception:
        return False
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False

    sys_start, sys_end, _indent = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False
    block_re = re.compile(r"^(\s*)plugin:\s*(#.*)?$")
    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = block_re.match(lines[i])
        if m:
            s, e, indent = _find_block_extent(lines, block_re, start_from=i)
            found = (s, min(e, sys_end), indent)
            break
    if found is None:
        indent = "  "
        block_lines = [f"{indent}plugin:  # 插件生态（plugin_route，P5）\n"]
        for key, value in fields.items():
            block_lines.append(f"{indent}  {key}: {_yaml_render(value)}\n")
        insert_at = sys_start + 1
        for i in range(sys_start + 1, min(sys_end, len(lines))):
            if _CHILD_RE.match(lines[i]):
                insert_at = i
                break
        lines[insert_at:insert_at] = block_lines
    else:
        start, end, _indent = found
        for key, value in fields.items():
            replaced = False
            for j in range(start, end):
                stripped = lines[j].lstrip()
                if stripped.startswith(key + ":"):
                    lines[j] = f"{_indent}  {key}: {_yaml_render(value)}\n"
                    replaced = True
                    break
            if not replaced:
                lines.insert(end, f"{_indent}  {key}: {_yaml_render(value)}\n")
                end += 1
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except OSError:
        return False


def _upsert_intent_block(fields: dict) -> bool:
    try:
        from .translator_route import CONF_PATH  # noqa: PLC0415
    except Exception:
        return False
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    sys_start, sys_end, _indent = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False
    block_re = re.compile(r"^(\s*)intent:\s*(#.*)?$")
    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = block_re.match(lines[i])
        if m:
            s, e, indent = _find_block_extent(lines, block_re, start_from=i)
            found = (s, min(e, sys_end), indent)
            break
    if found is None:
        indent = "  "
        block_lines = [f"{indent}intent:  # 意图识别（plugin_route，P5）\n"]
        for key, value in fields.items():
            block_lines.append(f"{indent}  {key}: {_yaml_render(value)}\n")
        insert_at = sys_start + 1
        for i in range(sys_start + 1, min(sys_end, len(lines))):
            if _CHILD_RE.match(lines[i]):
                insert_at = i
                break
        lines[insert_at:insert_at] = block_lines
    else:
        start, end, _indent = found
        for key, value in fields.items():
            replaced = False
            for j in range(start, end):
                stripped = lines[j].lstrip()
                if stripped.startswith(key + ":"):
                    lines[j] = f"{_indent}  {key}: {_yaml_render(value)}\n"
                    replaced = True
                    break
            if not replaced:
                lines.insert(end, f"{_indent}  {key}: {_yaml_render(value)}\n")
                end += 1
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except OSError:
        return False


def _plugin_config() -> dict:
    try:
        from .config_manager.utils import read_yaml  # noqa: PLC0415

        conf = read_yaml("conf.yaml") or {}
        block = (conf.get("system_config") or {}).get("plugin") or {}
        return {
            "marketplace_url": str(block.get("marketplace_url") or ""),
        }
    except Exception:
        return {"marketplace_url": ""}


def _install_zip_bytes(data: bytes, filename: str) -> dict:
    """解压上传的 zip（防 zip-slip + 去顶层壳）→ 校验 plugin.json → 原子落盘。

    返回 {ok, plugin_id?, name?, error?}。
    """
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import zipfile  # noqa: PLC0415

    from .plugin import registry  # noqa: PLC0415

    tmp_root = Path(tempfile.mkdtemp(prefix="moonlight_zip_"))
    extract_dir = tmp_root / "extract"
    extract_dir.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                target = (extract_dir / member).resolve()
                if not str(target).startswith(str(extract_dir.resolve())):
                    raise ValueError(f"非法路径: {member}")
            zf.extractall(extract_dir)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": f"解压失败: {e}"}

    # 去 GitHub archive 顶层目录壳
    inner = extract_dir
    entries = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(entries) == 1 and not (extract_dir / "plugin.json").exists():
        inner = entries[0]
    meta_file = inner / "plugin.json"
    if not meta_file.is_file():
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": "压缩包内缺少 plugin.json"}

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": "plugin.json 非法"}

    name = str(meta.get("name") or "")
    if not name:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": "plugin.json 缺 name 字段"}
    dest = registry.PLUGINS_ROOT / "community" / name
    if dest.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": f"目标目录已存在: {name}"}
    registry.PLUGINS_ROOT.joinpath("community").mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(inner), str(dest))  # 原子 rename
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(tmp_root, ignore_errors=True)
        return {"ok": False, "error": f"落盘失败: {e}"}
    shutil.rmtree(tmp_root, ignore_errors=True)
    return {"ok": True, "plugin_id": f"community/{name}", "name": name, "filename": filename}


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

def init_plugin_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/plugin/list")
    async def plugin_list(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        manager = get_plugin_manager()
        return {
            "ok": True,
            "plugins": [p.to_dict() for p in manager.list()],
            "enabled": manager.enabled_ids(),
        }

    @router.post("/api/plugin/toggle")
    async def plugin_toggle(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        plugin_id = str(body.get("plugin_id") or "")
        if not plugin_id or "/" not in plugin_id:
            return JSONResponse({"ok": False, "error": "plugin_id 格式应为 category/name"}, 400)
        result = get_plugin_manager().toggle(plugin_id)
        return result

    @router.get("/api/plugin/marketplace")
    async def marketplace(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        cfg = _plugin_config()
        items = catalog(cfg.get("marketplace_url") or "")
        return {"ok": True, "plugins": items, "source": "remote" if cfg.get("marketplace_url") else "builtin"}

    @router.post("/api/plugin/marketplace/install")
    async def marketplace_install(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        plugin_id = str(body.get("plugin_id") or "")
        items = catalog(_plugin_config().get("marketplace_url") or "")
        item = next((i for i in items if str(i.get("name")) == plugin_id), None)
        if item is None:
            return JSONResponse({"ok": False, "error": f"目录册无此插件: {plugin_id}"}, 404)
        return install(plugin_id, item)

    @router.get("/api/plugin/marketplace/install-status/{plugin_id}")
    async def marketplace_install_status(plugin_id: str, request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return install_status(plugin_id)

    # ---- 市场配置（P6：远程目录册 URL） ----
    @router.get("/api/plugin/config")
    async def plugin_config_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return _plugin_config()

    @router.post("/api/plugin/config")
    async def plugin_config_save(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        url = str(body.get("marketplace_url") or "").strip()
        ok = _upsert_plugin_block({"marketplace_url": url})
        return {"ok": ok, "marketplace_url": url}

    # ---- 本地 zip 安装（P6：上传安装） ----
    @router.post("/api/plugin/install-zip")
    async def plugin_install_zip(request: Request, file: UploadFile = File(...)):
        """multipart zip → 解压（防 zip-slip + 去顶层壳）→ 校验 plugin.json → 落盘。"""
        if not _is_local_request(request):
            return _forbidden()
        filename = str(file.filename or "plugin.zip")
        try:
            data = await file.read()
        except Exception:
            return JSONResponse({"ok": False, "error": "读取文件失败"}, 400)
        if not data:
            return JSONResponse({"ok": False, "error": "空文件"}, 400)
        if len(data) > 100 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "zip 超过 100MB 上限"}, 413)
        result = _install_zip_bytes(data, filename)
        return result

    # ---- 导出 / 导入 ----
    @router.get("/api/export/character")
    async def export_character_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return export_character()

    @router.get("/api/export/config")
    async def export_config_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return export_config()

    @router.post("/api/import")
    async def import_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "请求体非法 JSON"}, 400)
        return import_config(payload)

    # ---- 意图识别 ----
    @router.get("/api/intent/config")
    async def intent_config_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return intent_config()

    @router.post("/api/intent/config")
    async def intent_config_save(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        fields: dict[str, Any] = {}
        if "enabled" in body:
            fields["enabled"] = bool(body["enabled"])
        if "model" in body:
            fields["model"] = str(body["model"])
        if not fields:
            return JSONResponse({"ok": False, "error": "无可写字段"}, 400)
        ok = _upsert_intent_block(fields)
        return {"ok": ok, **intent_config()}

    @router.post("/api/intent/classify")
    async def intent_classify(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        text = str(body.get("text") or "")
        if not text.strip():
            return JSONResponse({"ok": False, "error": "text 为空"}, 400)
        if not intent_config().get("enabled", True):
            return {"ok": True, "intent": "chat", "emotion": "neutral", "source": "disabled"}
        result = await analyze_intent(text)
        result["ok"] = True
        return result

    # ---- 模型能力（多模态输入卡用） ----
    @router.get("/api/llm/capabilities")
    async def llm_capabilities(request: Request):
        """当前 LLM 的模态能力（text/image/audio/video/pdf）。

        按 conf openai_compatible_llm.model 名规则映射（静态表，不做探测）。
        DeepSeek 系 → 纯文本；含 vl/vision（qwen-vl / 本地 ollama）→ 图；
        gpt-4o/4.1 → 图+音频；gemini 系 → 图+音频+视频+PDF。
        """
        if not _is_local_request(request):
            return _forbidden()
        try:
            from .config_manager.utils import read_yaml  # noqa: PLC0415

            conf = read_yaml("conf.yaml") or {}
            block = (conf.get("character_config") or {}).get("agent_config") or {}
            llm = (block.get("llm_configs") or {}).get("openai_compatible_llm") or {}
            model = str(llm.get("model") or "")
        except Exception:
            model = ""
        m = model.lower()
        caps = {"text": True, "image": False, "audio": False, "video": False, "pdf": False}
        if any(k in m for k in ("vl", "vision")):
            caps["image"] = True  # qwen-vl / 本地 vision 模型
        if "gpt-4o" in m or "gpt-4.1" in m:
            caps["image"] = True
            caps["audio"] = True
        if m.startswith("gemini"):
            caps.update({"image": True, "audio": True, "video": True, "pdf": True})
        return {"ok": True, "model": model, "capabilities": caps}

    return router
