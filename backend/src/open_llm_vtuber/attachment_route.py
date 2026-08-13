"""attachment_route.py — 聊天附件处理（P5.1 多模态输入真实生效）。

`POST /api/conversation/attachments`（multipart: file）→ 按类型降级处理：
- PDF（application/pdf）→ pypdf 文本抽取（最多 20 页 / 截断 6000 字），
  文本作为上下文注入（PetGPT getFileFallbackText 思路）。
- 图片（image/*）→ 复用 screen_awareness VisionAnalyzer 预描述
  （无视觉 provider 时 fail-soft 返回提示，不阻塞）。
- 音频（audio/*）→ 引导提示（ASR 已通过 F2 语音链路覆盖，文件转写留 P6）。
- 其他 → 不支持提示。

返回 {ok, kind, name, size, summary}：summary 为可直接拼入消息文本的降级块。
前端把 summary 拼进 user_input 发送 → conversations 零改动（优雅降级闭环）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any, Optional

from fastapi import APIRouter, File, Request, UploadFile
from loguru import logger
from starlette.responses import JSONResponse

_PDF_MAX_PAGES = 20
_PDF_MAX_CHARS = 6000


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _forbidden() -> JSONResponse:
    return JSONResponse({"error": "仅本机可访问"}, status_code=403)


async def _transcribe_audio(data: bytes, mime: str, name: str) -> str:
    """音频附件转写（P6）：wav → wave 解码 → 复用全局 ASR 引擎。

    - 仅支持 wav（环境无 ffmpeg，mp3/m4a 提示转换）；
    - ASR 引擎复用 websocket_handler.default_context_cache.asr_engine
      （桌面运行时后端已加载，零重复初始化）；
    - 任何一步失败 → 明确提示（fail-soft，不阻塞）。
    """
    import wave  # noqa: PLC0415

    if mime not in ("audio/wav", "audio/x-wav", "audio/wave") and not name.lower().endswith(".wav"):
        return (
            f"【音频附件「{name}」】暂仅支持 wav（环境无 ffmpeg）；"
            "请先转换为 wav，或直接按住 F2 说话（ASR 链路已就绪）"
        )
    try:
        import numpy as np  # noqa: PLC0415

        with wave.open(io.BytesIO(data), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sampwidth = wf.getsampwidth()
            ch = wf.getnchannels()
        if sampwidth == 2:
            arr = np.frombuffer(frames, dtype=np.int16)
        elif sampwidth == 4:
            arr = np.frombuffer(frames, dtype=np.int32)
        else:
            return f"【音频附件「{name}」】不支持的采样宽度 {sampwidth * 8}bit（支持 16/32bit wav）"
        if ch > 1:
            arr = arr.reshape(-1, ch)[:, 0]  # 取单声道
        audio = (arr.astype(np.float32)) / 32768.0

        from .bridge import get_ws_handler  # noqa: PLC0415

        handler = get_ws_handler()
        ctx = getattr(handler, "default_context_cache", None)
        asr = getattr(ctx, "asr_engine", None) if ctx is not None else None
        if asr is None:
            return (
                f"【音频附件「{name}」】ASR 引擎未加载（需先在设置配置语音识别模型，"
                "或按住 F2 直接说话）"
            )
        text = (await asr.async_transcribe_np(audio) or "").strip()
        if not text:
            return f"【音频附件「{name}」】未识别到语音内容"
        return f"【音频附件「{name}」语音转写】{text}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"attachment: 音频转写失败: {e}")
        return f"【音频附件「{name}」】转写失败（{e}）"


async def _extract_pdf(data: bytes, name: str) -> str:
    """pypdf 文本抽取；失败/无文本 → 明确提示（不抛）。"""
    try:
        import pypdf  # noqa: PLC0415

        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for i, page in enumerate(reader.pages[: _PDF_MAX_PAGES]):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            if text.strip():
                parts.append(text.strip())
        if not parts:
            return f"【PDF 附件「{name}」】未提取到文本（可能是扫描件/图片型 PDF）"
        joined = "\n".join(parts)
        if len(joined) > _PDF_MAX_CHARS:
            joined = joined[: _PDF_MAX_CHARS] + "\n…（已截断）"
        return f"【PDF 附件「{name}」】\n{joined}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"attachment: PDF 抽取失败: {e}")
        return f"【PDF 附件「{name}」】解析失败（{e}）"


def _data_url_from_bytes(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _describe_image(data: bytes, mime: str, name: str) -> str:
    """VisionAnalyzer 预描述（复用屏幕感知视觉链路，inherit 对话 LLM）。"""
    try:
        from .screen_awareness.analyzer import VisionAnalyzer  # noqa: PLC0415
        from .screen_awareness.models import (  # noqa: PLC0415
            ScreenConfig,
            ScreenFrame,
            ScreenWindowInfo,
        )
        from .screen_awareness.route import fill_llm_defaults  # noqa: PLC0415
        from .config_manager.utils import read_yaml  # noqa: PLC0415
        from .character_config import CharacterConfig  # noqa: PLC0415

        conf = read_yaml("conf.yaml") or {}
        block = (conf.get("system_config") or {}).get("screen_awareness") or {}
        cfg = ScreenConfig(**{k: v for k, v in block.items() if k in ScreenConfig.model_fields})
        # 继承对话 LLM（用户把对话模型指向视觉端点即生效）
        cc_data = conf.get("character_config") or {}
        try:
            cc = CharacterConfig(**cc_data)
            cfg = fill_llm_defaults(cfg, cc)
        except Exception:
            pass  # 结构异常则用纯 ScreenConfig

        frame = ScreenFrame(
            frame_id=f"attachment-{int(time.time() * 1000)}",
            captured_at=time.time(),
            window=ScreenWindowInfo(title=f"用户上传图片: {name}", app="chat-attachment"),
            image=_data_url_from_bytes(data, mime),
            reason="user_requested",
        )
        snapshot = await VisionAnalyzer(cfg).analyze(frame, api_key=cfg.api_key, timeout_sec=25.0)
        if snapshot and snapshot.summary:
            return f"【图片附件「{name}」画面描述】{snapshot.summary}"
        return f"【图片附件「{name}」】视觉模型未返回描述（provider 不可用或内容不清晰）"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"attachment: 图片描述失败: {e}")
        return f"【图片附件「{name}」】无法分析（视觉模型未配置，可在 屏幕感知 设置）"


def init_attachment_route() -> APIRouter:
    router = APIRouter()

    @router.post("/api/conversation/attachments")
    async def upload_attachment(request: Request, file: UploadFile = File(...)):
        if not _is_local_request(request):
            return _forbidden()
        name = str(file.filename or "attachment")
        mime = str(file.content_type or "").lower()
        try:
            data = await file.read()
        except Exception:
            return JSONResponse({"ok": False, "error": "读取文件失败"}, 400)
        if not data:
            return JSONResponse({"ok": False, "error": "空文件"}, 400)
        if len(data) > 25 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "文件超过 25MB 上限"}, 413)

        try:
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                summary = await _extract_pdf(data, name)
                return {"ok": True, "kind": "pdf", "name": name, "size": len(data), "summary": summary}
            if mime.startswith("image/"):
                summary = await _describe_image(data, mime, name)
                return {"ok": True, "kind": "image", "name": name, "size": len(data), "summary": summary}
            if mime.startswith("audio/"):
                summary = await _transcribe_audio(data, mime, name)
                return {"ok": True, "kind": "audio", "name": name, "size": len(data), "summary": summary}
            return {
                "ok": True,
                "kind": "unsupported",
                "name": name,
                "size": len(data),
                "summary": f"【附件「{name}」】暂不支持 {mime or '未知'} 类型（支持 PDF / 图片）",
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"attachment: 处理 {name} 异常: {e}")
            return JSONResponse({"ok": False, "error": f"处理失败: {e}"}, 500)

    return router


__all__: list[str] = ["init_attachment_route"]
