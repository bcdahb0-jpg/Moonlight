"""occlusion_route.py — 遮罩 AI 前景提取（P6，SoulLink occlusion-mask 思路）。

`POST /api/occlusion/extract`（multipart 图）→ 背景分离 → 归一化轮廓点
列表（0-100 坐标，前端 SVG 编辑器直接填入）。

主方案：rembg（U2Net，CPU onnxruntime，首次运行需联网下载模型 ~170MB，
缓存于用户目录）；模型不可用/下载失败 → Pillow 色差阈值兜底（四角背景色
采样 + 网格边界提取）。任何失败返回 400 + 明确文案（fail-soft）。
"""

from __future__ import annotations

import io
from typing import Any, Optional

from fastapi import APIRouter, File, Request, UploadFile
from loguru import logger
from starlette.responses import JSONResponse

from .screen_awareness.route import _is_local_request, _forbidden  # noqa: PLC0415

#: 输出轮廓最大点数（前端 SVG 多边形不宜过密）。
_MAX_POINTS = 96
#: Pillow 兜底的采样网格（16x16 → 最多 256 候选点，再抽稀）。
_GRID = 24


def _to_rgba(data: bytes) -> Optional[Any]:
    """解码图片 → RGBA PIL Image；失败 → None。"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        return img.convert("RGBA")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"occlusion: 图片解码失败: {e}")
        return None


# --------------------------------------------------------------------------- #
# rembg 主方案（懒加载 + 模型缓存）
# --------------------------------------------------------------------------- #

_rembg_ok: Optional[bool] = None


def _rembg_available() -> bool:
    global _rembg_ok
    if _rembg_ok is not None:
        return _rembg_ok
    try:
        import rembg  # noqa: PLC0415

        _rembg_ok = True
    except Exception:
        _rembg_ok = False
    return _rembg_ok


def _extract_with_rembg(img: Any, max_points: int = _MAX_POINTS) -> Optional[list[dict]]:
    """rembg 抠图 → 轮廓采样。首次运行会下载 U2Net 模型（需联网）。

    采样策略：mask 边缘按角度均匀抽稀到 max_points（轮廓走向大致均匀）。
    任何失败（模型下载/推理）→ None（调用方降级 Pillow）。
    """
    try:
        import numpy as np  # noqa: PLC0415
        import rembg  # noqa: PLC0415

        mask = rembg.remove(img, only_mask=True)  # L 模式 mask
        arr = np.array(mask)
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        h, w = arr.shape
        alpha = arr > 128
        if not alpha.any():
            return None
        # 边缘点集合（mask 边界 = 上下左右任一方向发生 0↔1 变化）
        pad = np.zeros(alpha.shape, dtype=bool)
        pad[:-1, :] |= alpha[:-1, :] != alpha[1:, :]  # 垂直边缘
        pad[:, :-1] |= alpha[:, :-1] != alpha[:, 1:]  # 水平边缘
        ys, xs = np.nonzero(pad)
        if len(xs) == 0:
            return None
        # 按角度均匀抽稀（重心角排序）
        cx, cy = float(xs.mean()), float(ys.mean())
        ang = np.arctan2(ys - cy, xs - cx)
        order = np.argsort(ang)
        idxs = order[:: max(1, len(order) // max_points)][:max_points]
        pts = [
            {
                "x": round(float(xs[i]) / w * 100, 1),
                "y": round(float(ys[i]) / h * 100, 1),
            }
            for i in idxs
        ]
        return pts
    except Exception as e:  # noqa: BLE001
        logger.warning(f"occlusion: rembg 失败（降级 Pillow）: {e}")
        return None


# --------------------------------------------------------------------------- #
# Pillow 色差兜底（零依赖，复杂背景效果有限）
# --------------------------------------------------------------------------- #

def _extract_with_pillow(img: Any, max_points: int = _MAX_POINTS) -> Optional[list[dict]]:
    """四角背景色采样 → 色差阈值分割 → 网格边界采样。"""
    try:
        import numpy as np  # noqa: PLC0415

        small = img.resize((_GRID * 8, _GRID * 8))
        arr = np.asarray(small, dtype=np.float32)[:, :, :3]
        h, w = arr.shape[:2]
        corners = np.stack(
            [
                arr[2, 2], arr[2, w - 3], arr[h - 3, 2], arr[h - 3, w - 3],
            ]
        )
        bg = corners.mean(axis=0)
        dist = np.linalg.norm(arr - bg, axis=2)
        thr = max(30.0, float(dist.max()) * 0.35)
        alpha = dist > thr  # 前景 mask
        if not alpha.any():
            return None
        # 边缘点集合（mask 边界 = 上下左右任一方向发生 0↔1 变化）
        pad = np.zeros(alpha.shape, dtype=bool)
        pad[:-1, :] |= alpha[:-1, :] != alpha[1:, :]  # 垂直边缘
        pad[:, :-1] |= alpha[:, :-1] != alpha[:, 1:]  # 水平边缘
        ys, xs = np.nonzero(pad)
        if len(xs) == 0:
            return None
        cx, cy = float(xs.mean()), float(ys.mean())
        ang = np.arctan2(ys - cy, xs - cx)
        order = np.argsort(ang)
        idxs = order[:: max(1, len(order) // max_points)][:max_points]
        return [
            {"x": round(float(xs[i]) / w * 100, 1), "y": round(float(ys[i]) / h * 100, 1)}
            for i in idxs
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"occlusion: Pillow 兜底失败: {e}")
        return None


def init_occlusion_route() -> APIRouter:
    router = APIRouter()

    @router.post("/api/occlusion/extract")
    async def occlusion_extract(request: Request, file: UploadFile = File(...)):
        if not _is_local_request(request):
            return _forbidden()
        try:
            data = await file.read()
        except Exception:
            return JSONResponse({"ok": False, "error": "读取文件失败"}, 400)
        if not data or len(data) > 25 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "文件为空或超过 25MB"}, 400)
        img = _to_rgba(data)
        if img is None:
            return JSONResponse({"ok": False, "error": "图片解码失败（支持 jpg/png/webp）"}, 400)

        engine = "none"
        points: Optional[list[dict]] = None
        if _rembg_available():
            points = _extract_with_rembg(img)
            engine = "rembg" if points else "rembg-failed"
        if points is None:
            points = _extract_with_pillow(img)
            if points:
                engine = "pillow-fallback"
        if points is None:
            return JSONResponse(
                {"ok": False, "error": "前景提取失败（背景过于复杂？rembg 模型未下载可重试联网）"},
                422,
            )
        return {"ok": True, "engine": engine, "points": points, "name": file.filename or "image"}

    return router


__all__: list[str] = ["init_occlusion_route"]
