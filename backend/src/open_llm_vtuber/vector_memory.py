"""
向量记忆（语义检索）＋ RRF 混合融合。

參考 Kokoro-Engine 的「embedding + FTS5 BM25 + RRF」混合记忆（MIT，思路移植、原创实现）：
- 语义层：每轮对话把「使用者說的話」嵌入并存进 SQLite（chat_history/<conf_uid>/vector_memory.db），
  检索时用查询向量做余弦 top-K。
- 融合层：向量结果与既有 FTS5（memory_fts.search）结果做 RRF（Reciprocal Rank Fusion）合并，
  两边各带独立标签注入 prompt。
- 生命周期：高相似去重（>SIM_THRESHOLD 不重复存）、重要度衰减、数量上限裁剪。

嵌入走 OpenAI 兼容 ``/embeddings`` 端点（base_url/model/api_key 可独立配置；未配置时沿用
对话 LLM 的 base_url + model + api_key，适配大多数提供方）。完全 fail-soft：
任何错误都视为「未命中」，绝不阻塞对话。

纯 stdlib + httpx + numpy（均已依赖）。
"""

from __future__ import annotations

import os
import time
import sqlite3
import struct
from typing import List, Optional, Tuple

import httpx
import numpy as np
from loguru import logger

from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"
VECTOR_DB_FILE = "vector_memory.db"
RETRIEVAL_LABEL = "## 可能相關的過去回憶（語義）"

# 余弦相似度高于此值视为重复（不重复存储）
SIM_THRESHOLD = 0.85
# 每条记忆的重要性衰减基数（天数）
IMPORTANCE_DECAY_DAYS = 30.0
# 每个角色最多保留的记忆条数（超限裁掉最旧/最不重要）
MAX_MEMORIES = 500
# 检索默认 top-k
DEFAULT_K = 3


# --------------------------------------------------------------------------- #
# 路径 / schema
# --------------------------------------------------------------------------- #

def _db_path(conf_uid: str) -> str:
    return safe_join(CHAT_HISTORY_DIR, conf_uid, VECTOR_DB_FILE)


def _open(conf_uid: str) -> sqlite3.Connection:
    p = _db_path(conf_uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memories("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "text TEXT NOT NULL, embedding BLOB NOT NULL, "
        "ts REAL NOT NULL, importance REAL NOT NULL DEFAULT 1.0)"
    )
    return conn


def _pack(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> List[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cosine(a: List[float], b: List[float]) -> float:
    try:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0:
            return 0.0
        return float(np.dot(va, vb) / denom)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# 嵌入（OpenAI 兼容 /embeddings）
# --------------------------------------------------------------------------- #

async def embed_texts(
    texts: List[str],
    base_url: str,
    model: str,
    api_key: str = "",
) -> List[Optional[List[float]]]:
    """异步嵌入一批文本。返回与输入等长的向量列表；单个失败置 None。

    OpenAI 兼容端点：POST {base_url}/embeddings，body {"model", "input"}。
    api_key 非空时带 Authorization: Bearer header（ollama 本地可留空）。
    """
    if not texts or not base_url or not model:
        return [None] * len(texts)
    payload = {"model": model, "input": [t for t in texts]}
    headers = None
    if api_key and api_key != "ollama":
        headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/embeddings",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            out: List[Optional[List[float]]] = [None] * len(texts)
            for item in data:
                idx = item.get("index")
                if isinstance(idx, int) and 0 <= idx < len(out):
                    emb = item.get("embedding")
                    if isinstance(emb, list) and emb:
                        out[idx] = emb
            return out
    except Exception as e:
        logger.warning(f"[vector_memory] embed failed ({model}): {e}")
        return [None] * len(texts)


# --------------------------------------------------------------------------- #
# 写入 / 检索
# --------------------------------------------------------------------------- #

def store_memory(
    conf_uid: str, text: str, embedding: List[float], ts: float, importance: float = 1.0
) -> bool:
    """存储一条记忆。与现有高相似记忆去重；超上限裁剪最旧/最不重要。"""
    if not text or not text.strip() or not embedding:
        return False
    try:
        conn = _open(conf_uid)
        try:
            # 去重：与现有记忆余弦 > SIM_THRESHOLD 就跳过（保留旧的那条）
            rows = conn.execute("SELECT id, embedding, importance FROM memories").fetchall()
            for rid, blob, imp in rows:
                try:
                    if _cosine(embedding, _unpack(blob)) > SIM_THRESHOLD:
                        # 轻微提升重要度（被想起/被相似内容强化）
                        conn.execute(
                            "UPDATE memories SET importance = MIN(importance + 0.1, 5.0) WHERE id = ?",
                            (rid,),
                        )
                        conn.commit()
                        return False
                except Exception:
                    continue
            conn.execute(
                "INSERT INTO memories(text, embedding, ts, importance) VALUES (?, ?, ?, ?)",
                (text.strip(), _pack(embedding), ts, importance),
            )
            # 裁剪：超上限按 importance 升序删最不重要的（并列时删最旧）
            n = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
            if n > MAX_MEMORIES:
                conn.execute(
                    "DELETE FROM memories WHERE id IN ("
                    "SELECT id FROM memories ORDER BY importance ASC, ts ASC LIMIT ?)",
                    (n - MAX_MEMORIES,),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[vector_memory] store failed: {e}")
        return False


def search(
    conf_uid: str,
    query_embedding: List[float],
    k: int = DEFAULT_K,
    max_age_days: Optional[float] = None,
) -> List[str]:
    """按查询向量做余弦 top-K，回传「已格式化」的回忆片段（含角色标签）。

    max_age_days 提供时做时间衰减：超过该天数的重要度打折再排序。完全 fail-soft。
    """
    try:
        k = max(1, min(int(k), 50))
        conn = _open(conf_uid)
        try:
            rows = conn.execute(
                "SELECT text, embedding, ts, importance FROM memories"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        scored: List[Tuple[float, str]] = []
        now = time.time()
        for text, blob, ts, importance in rows:
            try:
                emb = np.asarray(_unpack(blob), dtype=np.float32)
                denom = float(np.linalg.norm(emb)) * qn
                if denom == 0:
                    continue
                sim = float(np.dot(q, emb) / denom)
                age_days = max(0.0, (now - ts) / 86400.0)
                decay = 1.0
                if max_age_days:
                    decay = max(0.2, 1.0 - age_days / max(IMPORTANCE_DECAY_DAYS, 1))
                scored.append((sim * decay * importance, text))
            except Exception:
                continue
        scored.sort(key=lambda x: -x[0])
        out: List[str] = []
        for _sim, text in scored[:k]:
            t = str(text).strip().replace("\n", " ")
            if len(t) > 200:
                t = t[:200].rstrip() + "…"
            out.append(f"「{t}」")
        return out
    except Exception as e:
        logger.warning(f"[vector_memory] search failed: {e}")
        return []


def count(conf_uid: str) -> int:
    try:
        conn = _open(conf_uid)
        try:
            return int(conn.execute("SELECT count(*) FROM memories").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# RRF 融合
# --------------------------------------------------------------------------- #

def rrf_fuse(
    fts_snippets: List[str],
    vec_snippets: List[str],
    k: int = DEFAULT_K,
) -> List[str]:
    """Reciprocal Rank Fusion：合并两个已排序片段列表（各取前 k）。

    对重复文本去重（保留分数更高者），返回融合后的前 k 条。纯函数，fail-soft。
    """
    k = max(1, min(int(k), 50))
    scores: dict = {}
    for lst in (fts_snippets, vec_snippets):
        for rank, snippet in enumerate(lst):
            key = snippet
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank + 1)
    ordered = sorted(scores.items(), key=lambda x: -x[1])
    return [s for s, _ in ordered[:k]]
