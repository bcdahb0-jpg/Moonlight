"""social_route.py — QQ 社交连接器路由（P6）。

- `GET  /api/qq/config` / `POST /api/qq/config` — conf `system_config.qq`
  （enabled / ws_url / auto_reply）surgical upsert + 运行时热更新。
- `GET  /api/qq/status` — 连接状态 / 消息计数 / 最近错误。
- `POST /api/qq/connect` / `POST /api/qq/disconnect` — 手动启停。
- `POST /api/qq/send` — 手动回发测试（{message_type, target, text}）。

全部 localhost-only。NapCat 需用户自行安装运行（风控自担）。
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from starlette.responses import JSONResponse

from .llm_config_route import _is_local_request, _forbidden
from .translator_route import _find_block_extent, _atomic_write, _quote_yaml_scalar, CONF_PATH
from .social.qq_client import get_qq_client

_SYSTEM_BLOCK_RE = re.compile(r"^(\s*)system_config:\s*(#.*)?$")
_QQ_BLOCK_RE = re.compile(r"^(\s*)qq:\s*(#.*)?$")
_CHILD_RE = re.compile(r"^(\s{2,})\S")


def _upsert_qq_block(fields: dict) -> bool:
    """surgical 写入 conf system_config.qq 块（缺失则插入）。"""
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    sys_start, _, sys_end = _find_block_extent(lines, _SYSTEM_BLOCK_RE)
    if sys_start is None:
        return False
    found = None
    for i in range(sys_start, min(sys_end, len(lines))):
        m = _QQ_BLOCK_RE.match(lines[i])
        if m:
            s, e, indent = _find_block_extent(lines, _QQ_BLOCK_RE, start_from=i)
            found = (s, min(e, sys_end), indent)
            break
    if found is None:
        indent = "  "
        block_lines = [f"{indent}qq:  # QQ 社交连接器（social_route，P6）\n"]
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
        _atomic_write(CONF_PATH, "".join(lines))
        return True
    except OSError:
        return False


def _yaml_render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml_scalar(str(value))


def _qq_config() -> dict:
    from .config_manager.utils import read_yaml  # noqa: PLC0415

    try:
        conf = read_yaml(CONF_PATH) or {}
        block = (conf.get("system_config") or {}).get("qq") or {}
        return {
            "enabled": bool(block.get("enabled", False)),
            "ws_url": str(block.get("ws_url") or "ws://127.0.0.1:3001"),
            "auto_reply": bool(block.get("auto_reply", True)),
        }
    except Exception:
        return {"enabled": False, "ws_url": "ws://127.0.0.1:3001", "auto_reply": True}


def init_social_route() -> APIRouter:
    router = APIRouter()

    @router.get("/api/qq/config")
    async def qq_config_ep(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return _qq_config()

    @router.post("/api/qq/config")
    async def qq_config_save(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        fields: dict[str, Any] = {}
        if "enabled" in body:
            fields["enabled"] = bool(body["enabled"])
        if "ws_url" in body:
            fields["ws_url"] = str(body["ws_url"])
        if "auto_reply" in body:
            fields["auto_reply"] = bool(body["auto_reply"])
        if not fields:
            return JSONResponse({"ok": False, "error": "无可写字段"}, 400)
        ok = _upsert_qq_block(fields)
        # 运行时热更新（enabled=False 会触发 stop）
        client = get_qq_client()
        cfg = {**_qq_config(), **fields}
        client.configure(cfg["enabled"], cfg["ws_url"], cfg["auto_reply"])
        return {"ok": ok, **_qq_config()}

    @router.get("/api/qq/status")
    async def qq_status(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        return {"ok": True, **get_qq_client().status()}

    @router.post("/api/qq/connect")
    async def qq_connect(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        client = get_qq_client()
        cfg = _qq_config()
        client.configure(True, cfg["ws_url"], cfg["auto_reply"])
        _upsert_qq_block({"enabled": True})
        return {"ok": True, **client.status()}

    @router.post("/api/qq/disconnect")
    async def qq_disconnect(request: Request):
        if not _is_local_request(request):
            return _forbidden()
        client = get_qq_client()
        await client.stop()
        _upsert_qq_block({"enabled": False})
        return {"ok": True, **client.status()}

    @router.post("/api/qq/send")
    async def qq_send(request: Request):
        """手动回发测试（NapCat 在线时）。body: {message_type, target, text}"""
        if not _is_local_request(request):
            return _forbidden()
        body = await request.json()
        text = str(body.get("text") or "")
        if not text:
            return JSONResponse({"ok": False, "error": "text 为空"}, 400)
        ok = await get_qq_client().send_text(
            text,
            message_type=str(body.get("message_type") or "group"),
            target=body.get("target"),
        )
        return {"ok": ok, "error": None if ok else "未连接（NapCat 未运行？）"}

    return router


__all__: list[str] = ["init_social_route"]
