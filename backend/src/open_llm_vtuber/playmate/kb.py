"""攻略知识库（P4）：每游戏独立向量库 + 融合检索。

- `import_text(game_id, text)`：分块（按段落/300 字）→ embed_texts（复用
  conf 的 vector_embedding_* 配置）→ 存 sqlite（data/playmate_kb.db，
  game_id 命名空间）。
- `query(game_id, question, top_k)`：向量检索（cosine）top-k。
  语义检索为主；FTS+RRF 融合随 sense-vault（P4.1）升级。

参考：super-agent-party py/know_base.py（embedding + BM25 集成 RAG）、
N.E.K.O hybrid_recall（RRF 融合思路，本版本先向量）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .. import vector_memory

# 知识库数据库（相对后端 cwd）。
_KB_DB = Path("data") / "playmate_kb.db"
_CHUNK_SIZE = 300  # 每块约 300 字符


def _conn() -> sqlite3.Connection:
    _KB_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_KB_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB,
            source TEXT DEFAULT '',
            created_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_game ON chunks(game_id)")
    return conn


def _embedding_config() -> dict:
    """复用 conf 的 vector_embedding_* 配置（与记忆同一端点/模型）。"""
    try:
        from .memory_route import _embedding_from_conf  # noqa: PLC0415

        cfg = _embedding_from_conf() or {}
        return {
            "base_url": str(cfg.get("base_url") or ""),
            "model": str(cfg.get("model") or ""),
            "api_key": str(cfg.get("api_key") or ""),
        }
    except Exception:
        return {"base_url": "", "model": "", "api_key": ""}


def _chunk(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """按段落 + 长度切块。"""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 1 > size and buf:
            chunks.append(buf)
            buf = ""
        # 超长段落内部硬切
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size:]
        buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


async def import_text(game_id: str, text: str, source: str = "") -> dict:
    """导入攻略文本 → 分块 → 向量化 → 存库。"""
    chunks = _chunk(text)
    if not chunks:
        return {"ok": False, "error": "空文本"}
    cfg = _embedding_config()
    if not cfg["base_url"] or not cfg["model"]:
        return {"ok": False, "error": "向量 embedding 未配置（需先配置 vector_embedding_*）"}
    embeddings = await vector_memory.embed_texts(chunks, cfg["base_url"], cfg["model"], cfg["api_key"])
    now = time.time()
    conn = _conn()
    inserted = 0
    with conn:
        for chunk, emb in zip(chunks, embeddings):
            if not emb:
                continue
            conn.execute(
                "INSERT INTO chunks(game_id, text, embedding, source, created_at) VALUES(?,?,?,?,?)",
                (game_id, chunk, vector_memory._pack(emb), source, now),
            )
            inserted += 1
    conn.close()
    logger.info(f"[playmate-kb] imported {inserted}/{len(chunks)} chunks for {game_id}")
    return {"ok": True, "game_id": game_id, "imported": inserted, "chunks": len(chunks)}


async def query(game_id: str, question: str, top_k: int = 3) -> dict:
    """向量检索 top-k（cosine）。"""
    cfg = _embedding_config()
    top_k = max(1, min(10, int(top_k)))
    if not cfg["base_url"] or not cfg["model"]:
        return {"ok": False, "error": "向量 embedding 未配置", "hits": []}
    emb = (await vector_memory.embed_texts([question], cfg["base_url"], cfg["model"], cfg["api_key"]))[0]
    if not emb:
        return {"ok": False, "error": "问题向量化失败", "hits": []}
    conn = _conn()
    rows = conn.execute(
        "SELECT text, embedding FROM chunks WHERE game_id=? AND embedding IS NOT NULL",
        (game_id,),
    ).fetchall()
    conn.close()
    scored: list[tuple[float, str]] = []
    for text, blob in rows:
        try:
            vec = vector_memory._unpack(blob)
        except Exception:
            continue
        score = _cosine(emb, vec)
        scored.append((score, str(text)))
    scored.sort(key=lambda x: -x[0])
    hits = [{"text": text, "score": round(score, 4)} for score, text in scored[:top_k]]
    return {"ok": True, "game_id": game_id, "hits": hits}


def stats(game_id: str) -> dict:
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*), COUNT(embedding) FROM chunks WHERE game_id=?",
        (game_id,),
    ).fetchone()
    conn.close()
    return {"game_id": game_id, "chunks": row[0] if row else 0, "indexed": row[1] if row else 0}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
