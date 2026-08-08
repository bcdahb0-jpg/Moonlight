"""
記憶類型化 + 睡眠合併（memory v2）模組。

在既有「核心記憶 + FTS 深度回憶（+ 可選向量）」之上，新增一層結構化記憶庫
（``chat_history/<conf_uid>/memory_v2.db``），實現完整記憶生命週期：

- 事實提取（post-turn，N.E.K.O 簡化版）：每輪對話後背景抽 0~3 條 facts。
- 證據雙時鐘（N.E.K.O evidence 思路）：reinforcement / disputation 各自帶時間戳，
  半衰期 30 天衰減；``evidence_score = eff_rein - eff_disp``。
- 反思合成：facts 攢夠 K 條 → LLM 歸納成 reflection（pending → confirmed → promoted）。
- Dreaming 睡眠合併（Kokoro 簡化版）：canonical_hash 確定性合併 → LLM 批量判定 →
  自動應用/提案 → 高置信 reflections 精選寫入 core_memory.md（閉環）。
- 檢索：facts/reflections 納入 FTS5 trigram 檢索池，供 prompt 注入。

設計原則（沿用本模組家族 house style）：
- 完全 fail-soft：任何錯誤都只記 warning，絕不讓記憶問題阻塞對話。
- 每角色一庫、路徑安全（safe_join 隔離，同 memory_core）。
- LLM 呼叫全部 fire-and-forget / 背景執行（同 memory_core.consolidate_core_memory）。
- 純函數（衰減/狀態機）與 IO 分離，便於單測。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from .memory_fts import _build_match_query
from .utils.path_safety import safe_join

CHAT_HISTORY_DIR = "chat_history"
V2_DB_FILE = "memory_v2.db"

# --------------------------------------------------------------------------- #
# 常數（計劃書 §3.1 / §3.3 / §3.4）
# --------------------------------------------------------------------------- #

HALF_LIFE_DAYS = 30.0                 # 半衰期（天），證據衰減與事實重要性衰減共用
CONFIRM_SCORE = 1.5                   # reflection pending → confirmed 的 evidence_score 門檻
PROMOTE_SCORE = 2.5                   # confirmed → promoted（固化進 core）門檻
PROMOTE_MIN_AGE_DAYS = 1.0            # 固化前至少 confirmed 多久（run_dream_pass 可覆寫）
ARCHIVE_SCORE = -0.5                  # reflection eff_score 低於此 → archived
FACT_ARCHIVE_DECAY = 0.05             # importance * 0.5^(age/30) 低於此 → fact archived
REFLECTION_K = 5                      # 攢夠幾條新 facts 合成一條 reflection
DREAM_MIN_INTERVAL_SECONDS = 30 * 60  # 距上次 dream 至少多久才再自動跑
DREAM_MAX_PAIRS = 20                  # 一次 dream LLM 判定的候選對上限
DREAM_AUTO_CONFIDENCE = 0.88          # merge 置信 ≥ 此值才自動應用，否則寫提案
FACT_MAX_DEFAULT = 500                # 每角色 facts 上限（可被 conf memory_v2_max_facts 覆寫）
SIGNAL_REINFORCE_DELTA = 1.0          # 一次 reinforce 信號的加值
SIGNAL_REBUT_DELTA = 1.0              # 一次 rebut 信號的加值
FACT_IMPORTANCE_BOOST_ON_REPEAT = 0.2  # 重複事實（hash 撞庫）的輕微強化
FACTS_LABEL = "## 關於你的事實"
REFLECTIONS_LABEL = "## 過去的反思"

# 允許的 fact 狀態集合（fail-soft 校驗用）
_FACT_STATUSES = {"active", "archived"}
# 允許的 reflection 狀態集合
_REFL_STATUSES = {"pending", "confirmed", "promoted", "archived"}
# 允許的 proposal kind / status
_PROPOSAL_KINDS = {"merge", "conflict"}
_PROPOSAL_STATUSES = {"pending", "applied", "rejected"}

# 合併判定用的正規化：去所有空白 + 小寫（中文語境下比只 strip 更穩）
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# 路徑 / schema
# --------------------------------------------------------------------------- #

def _db_path(conf_uid: str) -> str:
    """chat_history/<sanitized conf_uid>/memory_v2.db（safe_join 隔離，防路徑穿越）。"""
    return safe_join(CHAT_HISTORY_DIR, conf_uid, V2_DB_FILE)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          importance REAL DEFAULT 5,
          entity TEXT DEFAULT 'user',
          source TEXT DEFAULT 'llm_extract',
          canonical_hash TEXT,
          created_at REAL, last_seen_at REAL,
          reinforcement REAL DEFAULT 0,
          disputation REAL DEFAULT 0,
          rein_last_at REAL, disp_last_at REAL,
          status TEXT DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_facts_hash ON facts(canonical_hash);
        CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);

        CREATE TABLE IF NOT EXISTS reflections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          entity TEXT DEFAULT 'user',
          status TEXT DEFAULT 'pending',
          source_fact_ids TEXT,
          reinforcement REAL DEFAULT 0,
          disputation REAL DEFAULT 0,
          created_at REAL, last_signal_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_reflections_status ON reflections(status);

        CREATE TABLE IF NOT EXISTS dream_proposals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          source_ids TEXT,
          proposed_text TEXT,
          confidence REAL DEFAULT 0,
          status TEXT DEFAULT 'pending',
          created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_proposals_status ON dream_proposals(status);

        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        -- facts 的 FTS5 trigram 索引（content 表 + 觸發器同步，Kokoro 方式）
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
          text, content='facts', content_rowid='id', tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
          INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
          INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
        CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF text ON facts BEGIN
          INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
          INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); END;

        -- reflections 的 FTS5 trigram 索引
        CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts USING fts5(
          text, content='reflections', content_rowid='id', tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS reflections_ai AFTER INSERT ON reflections BEGIN
          INSERT INTO reflections_fts(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS reflections_ad AFTER DELETE ON reflections BEGIN
          INSERT INTO reflections_fts(reflections_fts, rowid, text) VALUES
            ('delete', old.id, old.text); END;
        CREATE TRIGGER IF NOT EXISTS reflections_au AFTER UPDATE OF text ON reflections BEGIN
          INSERT INTO reflections_fts(reflections_fts, rowid, text) VALUES
            ('delete', old.id, old.text);
          INSERT INTO reflections_fts(rowid, text) VALUES (new.id, new.text); END;
        """
    )


def _open(conf_uid: str) -> sqlite3.Connection:
    """Open (creating the dir + schema) the per-character memory_v2 DB."""
    p = _db_path(conf_uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)
    _ensure_schema(conn)
    return conn


def db_exists(conf_uid: str) -> bool:
    """True if a memory_v2 DB exists (best-effort, for the UI)."""
    try:
        return os.path.isfile(_db_path(conf_uid))
    except Exception:
        return False


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# meta 讀寫（last_dream_at / synth_consumed 等）
# --------------------------------------------------------------------------- #

def _meta_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else default


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def last_dream_at(conf_uid: str) -> float:
    """距上次 dream 的 epoch 秒；從未 dream 過 → 0（fail-soft）。"""
    try:
        conn = _open(conf_uid)
        try:
            v = _meta_get(conn, "last_dream_at")
            return float(v) if v else 0.0
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] last_dream_at failed for {conf_uid}: {e}")
        return 0.0


# --------------------------------------------------------------------------- #
# 純函數：衰減 / 證據分數 / 狀態機（可單測，無 IO）
# --------------------------------------------------------------------------- #

def effective_reinforcement(
    reinforcement: float, rein_last_at: Optional[float], now: float
) -> float:
    """強化值按半衰期衰減：score(t) = score * 0.5^(age / HALF_LIFE_DAYS)。

    無最後信號時間 → 視為剛發生（不衰減），fail-soft 保守取值。
    """
    if not rein_last_at:
        return float(reinforcement)
    age_days = max(0.0, (now - rein_last_at) / 86400.0)
    return float(reinforcement) * (0.5 ** (age_days / HALF_LIFE_DAYS))


def effective_disputation(
    disputation: float, disp_last_at: Optional[float], now: float
) -> float:
    """反駁值按同一半衰期衰減（rein/disp 雙時鐘互相獨立）。"""
    if not disp_last_at:
        return float(disputation)
    age_days = max(0.0, (now - disp_last_at) / 86400.0)
    return float(disputation) * (0.5 ** (age_days / HALF_LIFE_DAYS))


def evidence_score(
    reinforcement: float,
    disputation: float,
    rein_last_at: Optional[float],
    disp_last_at: Optional[float],
    now: float,
) -> float:
    """evidence_score = eff_rein - eff_disp（N.E.K.O 模型）。"""
    return effective_reinforcement(reinforcement, rein_last_at, now) - effective_disputation(
        disputation, disp_last_at, now
    )


def decayed_importance(importance: float, created_at: float, now: float) -> float:
    """事實重要性衰減：importance * 0.5^(age/30)。"""
    age_days = max(0.0, (now - created_at) / 86400.0)
    return float(importance) * (0.5 ** (age_days / HALF_LIFE_DAYS))


def _derive_reflection_status(
    status: str, score: float, age_days: float
) -> str:
    """狀態機：pending → confirmed（score 達標）→ archived（score 太低）。

    promoted 由 dreaming 固化時顯式設定，這裡不往回退。
    """
    if status in ("promoted", "archived"):
        return status
    if score < ARCHIVE_SCORE:
        return "archived"
    if status == "pending" and score >= CONFIRM_SCORE:
        return "confirmed"
    return status


def canonical_hash(text: str) -> str:
    """正規化去重 hash：去空白 + 小寫 → sha256。中文「我 喜歡 貓」==「我喜歡貓」。"""
    norm = _WS_RE.sub("", text or "").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# facts 讀寫
# --------------------------------------------------------------------------- #

def _clamp_fact_text(text: str, cap: int = 300) -> str:
    """事實單條長度上限（防 LLM 塞一大段進單條 fact）。"""
    t = (text or "").strip()
    return t if len(t) <= cap else t[:cap].rstrip() + "…"


def upsert_fact(
    conf_uid: str,
    text: str,
    importance: float = 5.0,
    entity: str = "user",
    source: str = "llm_extract",
    max_facts: int = FACT_MAX_DEFAULT,
) -> Optional[int]:
    """寫入一條 fact，與現有 active fact 做 canonical_hash 撞庫去重。

    - 撞庫命中：更新 last_seen_at、importance 取 max、輕微強化（重複出現 = 被再次確認）。
    - 未命中：插入；若超過 max_facts 上限，按「importance 升序、時間最舊」裁掉超出的
      archived（只裁 archived，不裁 active 之外的必要資料？——不，超限時直接裁最舊/最不重要
      的 active，行為與 vector_memory 一致，但優先裁 archived 再裁 active）。
    - 回傳 fact id；失敗回 None。完全 fail-soft。
    """
    t = _clamp_fact_text(text)
    if not t:
        return None
    try:
        h = canonical_hash(t)
        now = _now()
        conn = _open(conf_uid)
        try:
            row = conn.execute(
                "SELECT id, importance, status FROM facts WHERE canonical_hash = ? "
                "ORDER BY id LIMIT 1",
                (h,),
            ).fetchone()
            if row:
                fid, old_imp, old_status = row
                new_imp = max(float(old_imp), float(importance))
                if old_status == "archived":
                    # 重新激活（事實再次出現 → 從墳場撈回來）
                    conn.execute(
                        "UPDATE facts SET status='active', importance=?, last_seen_at=?, "
                        "reinforcement=reinforcement+?, rein_last_at=?, text=? WHERE id=?",
                        (new_imp, now, FACT_IMPORTANCE_BOOST_ON_REPEAT, now, t, fid),
                    )
                else:
                    conn.execute(
                        "UPDATE facts SET importance=?, last_seen_at=?, "
                        "reinforcement=reinforcement+?, rein_last_at=?, text=? WHERE id=?",
                        (new_imp, now, FACT_IMPORTANCE_BOOST_ON_REPEAT, now, t, fid),
                    )
                conn.commit()
                return int(fid)
            conn.execute(
                "INSERT INTO facts(text, importance, entity, source, canonical_hash, "
                "created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (t, float(importance), entity, source, h, now, now),
            )
            fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 超上限裁剪：優先 archived → 再最不重要/最舊的 active
            n = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
            if n > max_facts:
                conn.execute(
                    "DELETE FROM facts WHERE id IN ("
                    "SELECT id FROM facts WHERE status='archived' "
                    "ORDER BY importance ASC, created_at ASC LIMIT ?)",
                    (max(1, n - max_facts),),
                )
                n = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
            if n > max_facts:
                conn.execute(
                    "DELETE FROM facts WHERE id IN ("
                    "SELECT id FROM facts WHERE status='active' "
                    "ORDER BY importance ASC, created_at ASC LIMIT ?)",
                    (n - max_facts,),
                )
            conn.commit()
            return int(fid)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] upsert_fact failed for {conf_uid}: {e}")
        return None


def list_facts(conf_uid: str, status: str = "active", limit: int = 100) -> List[dict]:
    """列出 facts（供 UI 分頁 / 檢視）。fail-soft → []。"""
    try:
        conn = _open(conf_uid)
        try:
            rows = conn.execute(
                "SELECT id, text, importance, entity, source, reinforcement, disputation, "
                "status, created_at, last_seen_at FROM facts "
                "WHERE status = ? ORDER BY importance DESC, id DESC LIMIT ?",
                (status, max(1, min(int(limit), 500))),
            ).fetchall()
            return [
                {
                    "id": r[0], "text": r[1], "importance": r[2], "entity": r[3],
                    "source": r[4], "reinforcement": r[5], "disputation": r[6],
                    "status": r[7], "created_at": r[8], "last_seen_at": r[9],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] list_facts failed for {conf_uid}: {e}")
        return []


def count_facts(conf_uid: str) -> int:
    try:
        conn = _open(conf_uid)
        try:
            return int(conn.execute("SELECT count(*) FROM facts").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# 證據信號（reinforcement / disputation，雙時鐘）
# --------------------------------------------------------------------------- #

def apply_signal(conf_uid: str, fact_id: int, action: str) -> bool:
    """對一條 fact 施加 reinforce / rebut 信號（更新各自的時鐘）。

    純計數 + 時間戳：reinforcement += 1 且 rein_last_at=now（disp 時鐘不受影響）。
    """
    if action not in ("reinforce", "rebut"):
        return False
    try:
        conn = _open(conf_uid)
        try:
            now = _now()
            if action == "reinforce":
                conn.execute(
                    "UPDATE facts SET reinforcement=reinforcement+?, rein_last_at=? WHERE id=?",
                    (SIGNAL_REINFORCE_DELTA, now, int(fact_id)),
                )
            else:
                conn.execute(
                    "UPDATE facts SET disputation=disputation+?, disp_last_at=? WHERE id=?",
                    (SIGNAL_REBUT_DELTA, now, int(fact_id)),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] apply_signal failed for {conf_uid}: {e}")
        return False


# --------------------------------------------------------------------------- #
# reflections 讀寫
# --------------------------------------------------------------------------- #

def add_reflection(
    conf_uid: str, text: str, source_fact_ids: List[int], entity: str = "user"
) -> Optional[int]:
    """新建一條 pending reflection。text 為空（LLM 歸納不出）→ 不建，回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        conn = _open(conf_uid)
        try:
            now = _now()
            conn.execute(
                "INSERT INTO reflections(text, entity, status, source_fact_ids, "
                "reinforcement, disputation, created_at, last_signal_at) "
                "VALUES (?, ?, 'pending', ?, 0, 0, ?, ?)",
                (t, entity, json.dumps([int(i) for i in source_fact_ids], ensure_ascii=False),
                 now, now),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] add_reflection failed for {conf_uid}: {e}")
        return None


def list_reflections(conf_uid: str, status: str = "", limit: int = 100) -> List[dict]:
    try:
        conn = _open(conf_uid)
        try:
            sql = (
                "SELECT id, text, entity, status, source_fact_ids, reinforcement, "
                "disputation, created_at, last_signal_at FROM reflections"
            )
            params: tuple = ()
            if status:
                sql += " WHERE status = ?"
                params = (status,)
            sql += " ORDER BY id DESC LIMIT ?"
            rows = conn.execute(sql, params + (max(1, min(int(limit), 500)),)).fetchall()
            return [
                {
                    "id": r[0], "text": r[1], "entity": r[2], "status": r[3],
                    "source_fact_ids": r[4], "reinforcement": r[5], "disputation": r[6],
                    "created_at": r[7], "last_signal_at": r[8],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] list_reflections failed for {conf_uid}: {e}")
        return []


def count_reflections(conf_uid: str) -> int:
    try:
        conn = _open(conf_uid)
        try:
            return int(conn.execute("SELECT count(*) FROM reflections").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def _reflection_score(
    conn: sqlite3.Connection, source_fact_ids_json: str, now: float
) -> float:
    """reflection 的證據分數 = 其支撐 facts 的 eff_score 加總（證據由事實層傳導）。

    reflection 自身不直接收訊號（初版無 UI 反饋），分數完全來自支撐它的 facts：
    score = Σ_fact (eff_rein - eff_disp)。缺 fact（已歸檔/刪除）只計剩餘部分。
    """
    try:
        ids = json.loads(source_fact_ids_json or "[]")
    except Exception:
        return 0.0
    total = 0.0
    for fid in ids:
        row = conn.execute(
            "SELECT reinforcement, disputation, rein_last_at, disp_last_at "
            "FROM facts WHERE id = ?",
            (int(fid),),
        ).fetchone()
        if row:
            total += evidence_score(row[0], row[1], row[2], row[3], now)
    return total


def refresh_statuses(conf_uid: str) -> int:
    """對所有非終態 reflection 重算衰減後的分數並推進狀態機。回傳改動行數。

    分數由支撐 facts 的證據加總而來（_reflection_score）；pending → confirmed
    （score ≥ CONFIRM_SCORE）；score < ARCHIVE_SCORE → archived。promoted 不往回退。
    純規則、無 LLM。
    """
    changed = 0
    try:
        conn = _open(conf_uid)
        try:
            now = _now()
            rows = conn.execute(
                "SELECT id, status, source_fact_ids, created_at FROM reflections"
            ).fetchall()
            for rid, status, src_ids, created in rows:
                if status in ("promoted", "archived"):
                    continue
                score = _reflection_score(conn, src_ids, now)
                age_days = max(0.0, (now - created) / 86400.0)
                new_status = _derive_reflection_status(str(status), score, age_days)
                if new_status != status:
                    conn.execute(
                        "UPDATE reflections SET status=? WHERE id=?", (new_status, rid)
                    )
                    changed += 1
            conn.commit()
        finally:
            conn.close()
        return changed
    except Exception as e:
        logger.warning(f"[memory_v2] refresh_statuses failed for {conf_uid}: {e}")
        return 0


# --------------------------------------------------------------------------- #
# LLM 呼叫（OpenAI 兼容 /chat/completions，同 memory_core 風格）
# --------------------------------------------------------------------------- #

def _llm_headers(api_key: str) -> Optional[dict]:
    if api_key and api_key != "ollama":
        return {"Authorization": f"Bearer {api_key}"}
    return None


async def _llm_json(
    base_url: str,
    model: str,
    prompt: str,
    api_key: str = "",
    timeout: float = 60.0,
) -> Optional[dict]:
    """呼叫 LLM 並解析 JSON 輸出。任何失敗回 None（fail-soft）。

    回應可能包 ```json ``` 圍欄或夾雜說明文字，這裡做容錯剝離。
    """
    if not base_url or not model:
        return None
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=_llm_headers(api_key),
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"[memory_v2] llm call failed ({model}): {e}")
        return None
    # 剝離 ```json ... ``` 圍欄 / 前後廢話。dream 判定輸出 JSON **數組**，
    # 提取 / 合成輸出 JSON **對象**，這裡兩種都容錯（貪婪匹配到最外層結尾）。
    text = (content or "").strip().strip("`")
    for pattern in (r"\[.*\]", r"\{.*\}"):
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
            if isinstance(data, (dict, list)):
                return data
        except Exception:
            continue
    logger.warning(f"[memory_v2] llm returned no JSON block: {text[:120]!r}")
    return None


# --------------------------------------------------------------------------- #
# post-turn 事實提取（階段 1：N.E.K.O 簡化版）
# --------------------------------------------------------------------------- #

_EXTRACT_PROMPT = """你是桌寵的記憶管理員。根據這輪對話，抽取值得長期記住的使用者事實。

規則（嚴格遵守）：
- 只記：身分／職業／正在做的事、偏好與習慣、稱呼、重要事件或對話結論。
- 絕不記：寒暄、一次性閒聊、沒有新資訊的內容、AI 自己說的話。
- 抽取 0~3 條；每條一句話、具體、不含修飾語；繁體中文。
- importance 1~10：越穩定、越重要的分數越高。
- 另外檢查下列「現有事實」是否有被這輪對話再次確認（reinforce）或被否認／推翻（rebut），
  最多各 2 條；沒有就空陣列。

輸出 JSON（不要任何其他文字或解釋）：
{"facts":[{"text":"...","importance":6}],"signals":[{"fact_id":1,"action":"reinforce"}]}"""


async def extract_facts_from_turn(
    conf_uid: str,
    user_input: str,
    ai_response: str,
    base_url: str,
    model: str,
    api_key: str = "",
) -> Tuple[int, int]:
    """一輪對話後抽取 facts 並施加證據信號（LLM 一次呼叫完成）。

    回傳 (新 facts 數, 信號數)。完全 fail-soft：任何錯誤回 (0, 0)。
    去重：canonical_hash 撞庫（upsert_fact 內部處理）。
    """
    try:
        if not user_input or not user_input.strip():
            return 0, 0
        # 帶上現有高 importance facts，讓 LLM 判斷 reinforce/rebut
        existing = list_facts(conf_uid, status="active", limit=20)
        existing_block = ""
        if existing:
            existing_block = "\n現有事實：\n" + "\n".join(
                f"- id={f['id']}: {f['text']}" for f in existing[:12]
            )
        prompt = _EXTRACT_PROMPT + existing_block + (
            f"\n\n這輪對話：\n使用者說：{user_input}\nAI 回：{ai_response[:800]}"
        )
        data = await _llm_json(base_url, model, prompt, api_key)
        if not isinstance(data, dict):
            return 0, 0
        added = 0
        signals = 0
        for f in data.get("facts") or []:
            if not isinstance(f, dict):
                continue
            txt = str(f.get("text") or "").strip()
            if not txt:
                continue
            try:
                imp = float(f.get("importance") or 5)
            except (TypeError, ValueError):
                imp = 5.0
            fid = upsert_fact(
                conf_uid, txt, importance=max(1.0, min(10.0, imp)), source="llm_extract"
            )
            if fid is not None:
                added += 1
        for s in data.get("signals") or []:
            if not isinstance(s, dict):
                continue
            try:
                fid = int(s.get("fact_id"))
            except (TypeError, ValueError):
                continue
            act = str(s.get("action") or "")
            if act in ("reinforce", "rebut") and apply_signal(conf_uid, fid, act):
                signals += 1
        if added or signals:
            logger.info(
                f"[memory_v2] post-turn extract for {conf_uid}: "
                f"+{added} facts, {signals} signals"
            )
        return added, signals
    except Exception as e:
        logger.warning(f"[memory_v2] extract_facts_from_turn failed for {conf_uid}: {e}")
        return 0, 0


# --------------------------------------------------------------------------- #
# 反思合成（階段 2）
# --------------------------------------------------------------------------- #

_SYNTH_PROMPT = """根據以下幾條關於使用者的事實，歸納成一條「高層反思」——穩定的人格特質、
長期偏好或行為模式，而不是逐條重複事實。

規則：
- 一句話（不超過 60 字）；繁體中文。
- 若這幾條事實彼此無關、無法歸納出任何穩定結論，就輸出 null。
- 輸出 JSON（不要任何其他文字）：{"reflection": "..."} 或 {"reflection": null}"""


def _synth_consumed(conn: sqlite3.Connection) -> List[int]:
    try:
        v = _meta_get(conn, "synth_consumed")
        return [int(i) for i in json.loads(v)] if v else []
    except Exception:
        return []


def _mark_synth_consumed(conn: sqlite3.Connection, ids: List[int]) -> None:
    cur = _synth_consumed(conn) + [int(i) for i in ids]
    _meta_set(conn, "synth_consumed", json.dumps(list(dict.fromkeys(cur))))


async def synthesize_reflections(
    conf_uid: str, base_url: str, model: str, api_key: str = "", k: int = REFLECTION_K
) -> int:
    """把「未被任何 reflection 引用過」的 active facts 湊 K 條 → LLM 歸納一條 reflection。

    無論成功與否，該批 fact ids 都會被標記為已嘗試（避免下輪無限重試同一批）。
    回傳新建的 reflection 數（0 或 1）。完全 fail-soft。
    """
    try:
        k = max(2, min(int(k), 20))
        conn = _open(conf_uid)
        try:
            consumed = set(_synth_consumed(conn))
            rows = conn.execute(
                "SELECT id, text, importance FROM facts "
                "WHERE status='active' ORDER BY importance DESC, id ASC"
            ).fetchall()
        finally:
            conn.close()
        cands = [r for r in rows if r[0] not in consumed][:k]
        if len(cands) < k:
            return 0
        block = "\n".join(f"- {r[1]}" for r in cands)
        data = await _llm_json(
            base_url, model, _SYNTH_PROMPT + "\n\n事實：\n" + block, api_key
        )
        ids = [int(r[0]) for r in cands]
        # 無論結果如何都標記已嘗試
        conn = _open(conf_uid)
        try:
            _mark_synth_consumed(conn, ids)
            conn.commit()
        finally:
            conn.close()
        ref_text = ""
        if isinstance(data, dict):
            v = data.get("reflection")
            if isinstance(v, str):
                ref_text = v.strip()
        if not ref_text:
            logger.info(
                f"[memory_v2] synth for {conf_uid}: {len(ids)} facts, no reflection "
                "(LLM judged unrelated)"
            )
            return 0
        rid = add_reflection(conf_uid, ref_text, ids)
        if rid is not None:
            logger.info(f"[memory_v2] synthesized reflection #{rid} for {conf_uid}")
            return 1
        return 0
    except Exception as e:
        logger.warning(f"[memory_v2] synthesize_reflections failed for {conf_uid}: {e}")
        return 0


# --------------------------------------------------------------------------- #
# Dreaming 睡眠合併（階段 3）
# --------------------------------------------------------------------------- #

_DREAM_PROMPT = """你是記憶合併員。以下每一對「關於使用者的記憶」，判斷應如何處理：

規則：
- keep：兩條並存（無矛盾、無重複），confidence 代表你有多確定。
- merge：兩條意思重疊／重複，合併成一句更好；給出 target_text（繁體中文、一句話）。
- conflict：兩條互相矛盾，無法自動合併。
- confidence 0~1。

輸出 JSON 數組（不要任何其他文字），每個元素對應一對：
[{"pair_index":0,"decision":"keep","confidence":0.9},
 {"pair_index":1,"decision":"merge","target_text":"...","confidence":0.95}]"""


def _build_pairs(facts: List[dict], max_pairs: int = DREAM_MAX_PAIRS) -> List[Tuple[int, int]]:
    """挑選 dream 候選對：同 entity 的 active facts 兩兩組合。

    上限 max_pairs 對；優先挑 importance 高的。回傳 [(id_a, id_b), ...]。
    """
    groups: Dict[str, List[dict]] = {}
    for f in facts:
        groups.setdefault(str(f["entity"]), []).append(f)
    pairs: List[Tuple[int, int]] = []
    for g in groups.values():
        g_sorted = sorted(g, key=lambda x: -float(x["importance"]))
        for i in range(len(g_sorted)):
            for j in range(i + 1, len(g_sorted)):
                pairs.append((g_sorted[i]["id"], g_sorted[j]["id"]))
                if len(pairs) >= max_pairs:
                    return pairs
    return pairs


def _create_proposal(
    conf_uid: str,
    kind: str,
    source_ids: List[int],
    proposed_text: str,
    confidence: float,
    status: str = "pending",
) -> Optional[int]:
    try:
        conn = _open(conf_uid)
        try:
            conn.execute(
                "INSERT INTO dream_proposals(kind, source_ids, proposed_text, "
                "confidence, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, json.dumps(source_ids), proposed_text or "", float(confidence),
                 status, _now()),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] create_proposal failed for {conf_uid}: {e}")
        return None


def _merge_facts(conf_uid: str, ids: List[int], merged_text: str, confidence: float) -> bool:
    """自動應用一次 merge：插入合併後的新 fact，把來源 facts 標 archived。

    新 fact 的 importance = 來源最大 importance（+0.1 微獎），entity 沿用第一個。
    全程一條事務；失敗回 False（不動任何資料）。
    """
    try:
        conn = _open(conf_uid)
        try:
            now = _now()
            rows = conn.execute(
                f"SELECT id, importance, entity, canonical_hash FROM facts "
                f"WHERE id IN ({','.join('?' * len(ids))}) AND status='active'",
                tuple(ids),
            ).fetchall()
            if not rows or len(rows) != len(ids):
                return False
            imp = max(float(r[1]) for r in rows) + 0.1
            entity = str(rows[0][2] or "user")
            # 合併後保留來源的強化證據（加總），反駁歸零
            rein_sum = sum(
                float(conn.execute(
                    "SELECT reinforcement FROM facts WHERE id=?", (rid,)
                ).fetchone()[0])
                for rid in ids
            )
            conn.execute(
                "INSERT INTO facts(text, importance, entity, source, canonical_hash, "
                "created_at, last_seen_at, reinforcement, disputation, rein_last_at, "
                "disp_last_at, status) VALUES (?, ?, ?, 'llm_merge', ?, ?, ?, ?, 0, ?, "
                "NULL, 'active')",
                (_clamp_fact_text(merged_text), imp, entity, canonical_hash(merged_text),
                 now, now, rein_sum, now),
            )
            new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                f"UPDATE facts SET status='archived' WHERE id IN ({','.join('?' * len(ids))})",
                tuple(ids),
            )
            # 審計：寫 applied 提案（不刪除歷史）
            conn.execute(
                "INSERT INTO dream_proposals(kind, source_ids, proposed_text, confidence, "
                "status, created_at) VALUES ('merge', ?, ?, ?, 'applied', ?)",
                (json.dumps(ids), merged_text, confidence, now),
            )
            conn.commit()
            logger.info(
                f"[dream] {conf_uid}: merged facts {ids} -> new fact #{new_id} "
                f"(conf={confidence:.2f})"
            )
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] merge_facts failed for {conf_uid}: {e}")
        return False


def _dedup_by_hash(conf_uid: str) -> int:
    """確定性合併：canonical_hash 完全相同的 active facts → 只留最新那條。

    保留：importance 最高、時間最新的那條（更新其 last_seen_at / reinforcement 加總），
    其餘標 archived。回傳歸併掉的條數。無 LLM。
    """
    merged = 0
    try:
        conn = _open(conf_uid)
        try:
            rows = conn.execute(
                "SELECT id, canonical_hash, importance, created_at FROM facts "
                "WHERE status='active' AND canonical_hash IS NOT NULL "
                "ORDER BY canonical_hash, importance DESC, created_at DESC"
            ).fetchall()
            by_hash: Dict[str, List[Tuple[int, float, float]]] = {}
            for rid, h, imp, created in rows:
                if h:
                    by_hash.setdefault(h, []).append((rid, imp, created))
            for h, group in by_hash.items():
                if len(group) < 2:
                    continue
                keeper = group[0]  # 已按 importance DESC, created_at DESC 排序
                others = [g[0] for g in group[1:]]
                conn.execute(
                    "UPDATE facts SET last_seen_at=?, reinforcement=reinforcement+0.1, "
                    "rein_last_at=? WHERE id=?",
                    (_now(), _now(), keeper[0]),
                )
                conn.execute(
                    f"UPDATE facts SET status='archived' "
                    f"WHERE id IN ({','.join('?' * len(others))})",
                    tuple(others),
                )
                merged += len(others)
                logger.info(f"[dream] {conf_uid}: hash-dedup {len(others)} facts -> #{keeper[0]}")
            conn.commit()
        finally:
            conn.close()
        return merged
    except Exception as e:
        logger.warning(f"[memory_v2] dedup_by_hash failed for {conf_uid}: {e}")
        return 0


def _archive_decayed(conf_uid: str) -> int:
    """衰減清理：importance * 0.5^(age/30) < FACT_ARCHIVE_DECAY → fact archived。

    回傳歸檔條數。reflection 的衰減歸檔由 refresh_statuses 的 ARCHIVE_SCORE 處理。
    """
    n = 0
    try:
        now = _now()
        conn = _open(conf_uid)
        try:
            rows = conn.execute(
                "SELECT id, importance, created_at FROM facts WHERE status='active'"
            ).fetchall()
            doomed = [
                rid for rid, imp, created in rows
                if decayed_importance(imp, created, now) < FACT_ARCHIVE_DECAY
            ]
            for rid in doomed:
                conn.execute("UPDATE facts SET status='archived' WHERE id=?", (rid,))
            conn.commit()
            n = len(doomed)
            if n:
                logger.info(f"[dream] {conf_uid}: archived {n} decayed facts")
        finally:
            conn.close()
        return n
    except Exception as e:
        logger.warning(f"[memory_v2] archive_decayed failed for {conf_uid}: {e}")
        return 0


def _fuse_into_core_memory(
    conf_uid: str, reflections: List[dict], cap: int = 1500
) -> int:
    """把高置信 reflections 精選寫入 core_memory.md（閉環，不動現有格式）。

    - 逐條追加為一行「- {text}」（與現有條列式 core 記憶格式一致）。
    - 已存在同文本 → 跳過（防重複固化）；只處理一次（status → promoted）。
    - 超 cap 不截斷（記 warning，由現有 consolidation 提煉合併），
      避免把使用者手動編輯的內容弄壞。
    """
    written = 0
    try:
        from .memory_core import load_core_memory, core_memory_path

        current = load_core_memory(conf_uid)
        lines = [ln.strip() for ln in current.splitlines() if ln.strip()] if current else []
        merged = list(lines)
        for r in reflections:
            text = str(r.get("text") or "").strip()
            if not text:
                continue
            if any(text in ln for ln in merged):
                continue
            merged.append(f"- {text}")
            written += 1
        if not written:
            return 0
        content = "\n".join(merged)
        if len(content) > cap:
            logger.warning(
                f"[memory_v2] fused memory for {conf_uid} exceeds cap "
                f"({len(content)} > {cap}); stored as-is, consolidation will compact"
            )
        p = core_memory_path(conf_uid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        # 已固化的 reflection 標 promoted（只固化一次）
        conn = _open(conf_uid)
        try:
            ids = [int(r["id"]) for r in reflections if str(r.get("text") or "").strip()]
            conn.execute(
                f"UPDATE reflections SET status='promoted' WHERE id IN ({','.join('?' * len(ids))})",
                tuple(ids),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(f"[dream] {conf_uid}: fused {written} reflection(s) into core_memory.md")
        return written
    except Exception as e:
        logger.warning(f"[memory_v2] fuse_into_core_memory failed for {conf_uid}: {e}")
        return 0


def _promotable_reflections(
    conf_uid: str, max_items: int = 3, min_age_days: float = PROMOTE_MIN_AGE_DAYS
) -> List[dict]:
    """選出可固化的 reflections：confirmed 且 eff_score ≥ PROMOTE_SCORE 且已確認夠久。

    回傳 top max_items 條（含衰減後 score）。純讀取，不改狀態。
    """
    out: List[dict] = []
    try:
        conn = _open(conf_uid)
        try:
            now = _now()
            rows = conn.execute(
                "SELECT id, text, source_fact_ids, created_at "
                "FROM reflections WHERE status='confirmed'"
            ).fetchall()
            for rid, text, src_ids, created in rows:
                score = _reflection_score(conn, src_ids, now)
                age_days = max(0.0, (now - created) / 86400.0)
                if score >= PROMOTE_SCORE and age_days >= max(0.0, min_age_days):
                    out.append({"id": rid, "text": text, "score": score})
        finally:
            conn.close()
        out.sort(key=lambda x: -float(x["score"]))
        return out[:max_items]
    except Exception as e:
        logger.warning(f"[memory_v2] promotable_reflections failed for {conf_uid}: {e}")
        return []


async def run_dream_pass(
    conf_uid: str,
    base_url: str,
    model: str,
    api_key: str = "",
    promote_min_age_days: Optional[float] = None,
    cap: int = 1500,
) -> dict:
    """執行一次完整的 dreaming 睡眠合併（Kokoro 簡化版）。

    ① 確定性合併（canonical_hash）→ ② 衰減歸檔 → ③ LLM 批量判定候選對
       （merge 且置信 ≥ DREAM_AUTO_CONFIDENCE 自動應用；conflict/低置信 → 提案）
    → ④ 狀態機推進（refresh_statuses）→ ⑤ 高置信 reflections 固化進 core_memory.md。
    回傳摘要 dict；完全 fail-soft（任何一步失敗都不影響其他步驟）。
    """
    summary = {
        "hash_dedup": 0, "archived": 0, "pairs": 0, "auto_merged": 0,
        "proposals": 0, "reflections_fused": 0, "confirmed": 0,
    }
    try:
        # ① 確定性合併
        summary["hash_dedup"] = _dedup_by_hash(conf_uid)
        # ② 衰減歸檔
        summary["archived"] = _archive_decayed(conf_uid)
        # ④ 先推進狀態機（讓該 confirmed 的 reflection 先 confirmed，再判定是否固化）
        summary["confirmed"] = refresh_statuses(conf_uid)

        # ③ LLM 批量判定（初版純 LLM，不依賴 embedding）
        if base_url and model:
            facts = list_facts(conf_uid, status="active", limit=200)
            pairs = _build_pairs(facts, DREAM_MAX_PAIRS)
            summary["pairs"] = len(pairs)
            if pairs:
                pair_block = json.dumps(
                    [
                        {"pair_index": i, "pair": [
                            {"id": a, "text": next(
                                (f["text"] for f in facts if f["id"] == a), "")},
                            {"id": b, "text": next(
                                (f["text"] for f in facts if f["id"] == b), "")},
                        ]}
                        for i, (a, b) in enumerate(pairs)
                    ],
                    ensure_ascii=False,
                )
                data = await _llm_json(
                    base_url, model, _DREAM_PROMPT + "\n\n" + pair_block, api_key,
                    timeout=90.0,
                )
                if isinstance(data, list):
                    id_by_index = {i: p for i, p in enumerate(pairs)}
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        try:
                            idx = int(item.get("pair_index"))
                        except (TypeError, ValueError):
                            continue
                        if idx not in id_by_index:
                            continue
                        a, b = id_by_index[idx]
                        decision = str(item.get("decision") or "keep")
                        try:
                            conf = float(item.get("confidence") or 0)
                        except (TypeError, ValueError):
                            conf = 0.0
                        conf = max(0.0, min(1.0, conf))
                        if decision == "merge":
                            target = str(item.get("target_text") or "").strip()
                            if conf >= DREAM_AUTO_CONFIDENCE and target:
                                if _merge_facts(conf_uid, [a, b], target, conf):
                                    summary["auto_merged"] += 1
                            else:
                                # 低置信 merge → 審查提案
                                if _create_proposal(
                                    conf_uid, "merge", [a, b], target, conf
                                ):
                                    summary["proposals"] += 1
                                    logger.info(
                                        f"[dream] {conf_uid}: proposal #{summary['proposals']} "
                                        f"merge (conf={conf:.2f})"
                                    )
                        elif decision == "conflict":
                            if _create_proposal(
                                conf_uid, "conflict", [a, b], "", conf
                            ):
                                summary["proposals"] += 1
                                logger.info(
                                    f"[dream] {conf_uid}: proposal conflict "
                                    f"(facts {a},{b})"
                                )
        # ⑤ 固化：confirmed 且 score 靠前的 reflections → core_memory.md
        age_days = promote_min_age_days
        if age_days is None:
            age_days = PROMOTE_MIN_AGE_DAYS
        promotable = _promotable_reflections(conf_uid, max_items=3, min_age_days=age_days)
        if promotable:
            summary["reflections_fused"] = _fuse_into_core_memory(
                conf_uid, promotable, cap=cap
            )

        # 記錄本次 dream 時間（meta 持久化，重啟後仍生效）
        conn = _open(conf_uid)
        try:
            _meta_set(conn, "last_dream_at", str(_now()))
            conn.commit()
        finally:
            conn.close()
        logger.info(f"[dream] {conf_uid} pass done: {summary}")
        return summary
    except Exception as e:
        logger.warning(f"[memory_v2] run_dream_pass failed for {conf_uid}: {e}")
        return summary


def should_dream(conf_uid: str, min_interval: float = DREAM_MIN_INTERVAL_SECONDS) -> bool:
    """距上次 dream 是否已超過 min_interval 秒（自動觸發閘）。fail-soft → False。"""
    try:
        last = last_dream_at(conf_uid)
        return (now := _now()) - last >= max(60.0, float(min_interval))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# post-turn 編排（階段 1+2+3 的單一入口，single_conversation 呼叫）
# --------------------------------------------------------------------------- #

async def post_turn(
    conf_uid: str,
    user_input: str,
    ai_response: str,
    base_url: str,
    model: str,
    api_key: str = "",
    max_facts: int = FACT_MAX_DEFAULT,
    auto_dream: bool = True,
) -> dict:
    """一輪對話結束後的記憶後處理（fire-and-forget 用）：

    1. 事實提取 + 證據信號（LLM 一次呼叫）
    2. 反思合成（facts 攢夠 K 條才跑）
    3. 若距上次 dream 已超時 → 順帶跑一次 dreaming（對話已結束，不搶資源）
    回傳摘要 dict。整段 fail-soft，絕不影響對話。
    """
    out = {"facts": 0, "signals": 0, "reflections": 0, "dreamed": False}
    try:
        if not conf_uid or not user_input or not user_input.strip():
            return out
        f_added, f_sig = await extract_facts_from_turn(
            conf_uid, user_input, ai_response, base_url, model, api_key
        )
        out["facts"], out["signals"] = f_added, f_sig
        # 反思合成：有 LLM 可用且庫裡有足夠未嘗試 facts 才跑（函數內部自行判斷）
        if base_url and model:
            out["reflections"] = await synthesize_reflections(
                conf_uid, base_url, model, api_key
            )
        # 自動 dreaming（閘：距上次 ≥ DREAM_MIN_INTERVAL）
        if auto_dream and should_dream(conf_uid):
            await run_dream_pass(
                conf_uid, base_url, model, api_key, cap=max(500, int(max_facts))
            )
            out["dreamed"] = True
    except Exception as e:
        logger.warning(f"[memory_v2] post_turn failed for {conf_uid}: {e}")
    return out


# --------------------------------------------------------------------------- #
# 檢索（階段 4：facts/reflections 納入 prompt 注入池）
# --------------------------------------------------------------------------- #

def _fmt_mem(text: str, who: str) -> str:
    t = str(text).strip().replace("\n", " ")
    if len(t) > 200:
        t = t[:200].rstrip() + "…"
    return f"「{who}：{t}」"


def _fts_search_table(
    conf_uid: str, table: str, match_expr: str, k: int
) -> List[str]:
    """對 facts_fts / reflections_fts 做 trigram MATCH 檢索，回傳原文列表。"""
    try:
        conn = _open(conf_uid)
        try:
            rows = conn.execute(
                f"SELECT text FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
                (match_expr, k),
            ).fetchall()
            return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] fts search failed ({table}): {e}")
        return []


def _like_search_table(conf_uid: str, table: str, query: str, k: int) -> List[str]:
    """LIKE 子串兜底（原始表）：trigram 需要 ≥3 字符窗口，2 字關鍵詞（如「跑步」）
    會被 FTS 漏掉；facts 又是短句，LIKE 精確子串更可靠。只匹配 status='active'。

    查詢拆詞後再滑動 2 字符窗口（「跑步習慣」→ 跑步/步習/習慣），任一窗口命中即
    命中（OR），策略與 memory_fts 的 trigram OR 一致。
    """
    terms: List[str] = []
    for chunk in re.split(r"\s+", (query or "").strip()):
        if len(chunk) < 2:
            continue
        terms.append(chunk)
        for i in range(len(chunk) - 1):
            win = chunk[i : i + 2]
            if len(win) >= 2 and win not in terms:
                terms.append(win)
    terms = terms[:6]
    if not terms:
        return []
    try:
        conn = _open(conf_uid)
        try:
            conds = " OR ".join(["text LIKE ?"] * len(terms))
            rows = conn.execute(
                f"SELECT text FROM {table} WHERE status='active' AND ({conds}) LIMIT ?",
                tuple(f"%{t}%" for t in terms) + (k,),
            ).fetchall()
            return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] like search failed ({table}): {e}")
        return []


def search(conf_uid: str, query: str, k: int = 3) -> List[str]:
    """聯合檢索 facts + reflections，回傳格式化注入塊（含標籤）。

    FTS5 trigram（≥3 字符長查詢）+ LIKE 子串兜底（2 字符短詞）雙通道，結果去重。
    完全 fail-soft：任何錯誤 → []。k 界在 [1, 10]。
    """
    try:
        try:
            k = max(1, min(10, int(k)))
        except (TypeError, ValueError):
            k = 3
        if not isinstance(query, str) or not query.strip():
            return []
        fk = max(1, k // 2 + 1)
        facts: List[str] = []
        refs: List[str] = []
        match = _build_match_query(query)
        if match:
            facts = _fts_search_table(conf_uid, "facts_fts", match, fk)
            refs = _fts_search_table(conf_uid, "reflections_fts", match, fk)
        # LIKE 兜底（2 字詞也能命中；與 FTS 結果去重）
        for t in _like_search_table(conf_uid, "facts", query, fk):
            if t not in facts:
                facts.append(t)
        for t in _like_search_table(conf_uid, "reflections", query, fk):
            if t not in refs:
                refs.append(t)
        out: List[str] = []
        if facts:
            out.append(FACTS_LABEL + "（僅供參考，未必準確；若與當下無關就忽略）\n")
            out.append("\n".join(_fmt_mem(t, "關於你") for t in facts[:k]))
        if refs:
            out.append(REFLECTIONS_LABEL + "（僅供參考，未必準確；若與當下無關就忽略）\n")
            out.append("\n".join(_fmt_mem(t, "過去反思") for t in refs[:k]))
        return out
    except Exception as e:
        logger.warning(f"[memory_v2] search failed for {conf_uid}: {e}")
        return []


# --------------------------------------------------------------------------- #
# 提案審計（階段 4：UI 人工批准/拒絕）
# --------------------------------------------------------------------------- #

def list_proposals(conf_uid: str, status: str = "pending", limit: int = 100) -> List[dict]:
    try:
        conn = _open(conf_uid)
        try:
            sql = (
                "SELECT id, kind, source_ids, proposed_text, confidence, status, "
                "created_at FROM dream_proposals"
            )
            params: tuple = ()
            if status:
                sql += " WHERE status = ?"
                params = (status,)
            sql += " ORDER BY id DESC LIMIT ?"
            rows = conn.execute(sql, params + (max(1, min(int(limit), 500)),)).fetchall()
            return [
                {
                    "id": r[0], "kind": r[1], "source_ids": r[2],
                    "proposed_text": r[3], "confidence": r[4], "status": r[5],
                    "created_at": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] list_proposals failed for {conf_uid}: {e}")
        return []


def count_proposals(conf_uid: str, status: str = "pending") -> int:
    """status 為空字串 → 統計全部提案。"""
    try:
        conn = _open(conf_uid)
        try:
            if status:
                return int(
                    conn.execute(
                        "SELECT count(*) FROM dream_proposals WHERE status = ?", (status,)
                    ).fetchone()[0]
                )
            return int(conn.execute("SELECT count(*) FROM dream_proposals").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def decide_proposal(conf_uid: str, proposal_id: int, approve: bool) -> Tuple[bool, str]:
    """人工審查提案：approve=True → 對 merge 提案自動應用；conflict 提案批准視為已處理。

    approve=False → 標 rejected。回傳 (ok, 錯誤訊息)。fail-soft。
    """
    try:
        conn = _open(conf_uid)
        try:
            row = conn.execute(
                "SELECT id, kind, source_ids, proposed_text, confidence, status "
                "FROM dream_proposals WHERE id = ?",
                (int(proposal_id),),
            ).fetchone()
            if not row:
                return False, "Proposal not found."
            _id, kind, source_ids_json, proposed_text, confidence, status = row
            if status != "pending":
                return False, "Proposal already decided."
            if approve:
                if kind == "merge" and proposed_text:
                    ids = [int(i) for i in json.loads(source_ids_json or "[]")]
                    # 批准 → 先嘗試應用合併；成功才標 applied（人工決定為準，不再看置信度）
                    ok = _merge_facts(conf_uid, ids, str(proposed_text), float(confidence or 0))
                    if not ok:
                        return False, "Could not apply merge (source facts may be gone)."
                    conn.execute(
                        "UPDATE dream_proposals SET status='applied' WHERE id=?",
                        (_id,),
                    )
                    conn.commit()
                    return True, ""
                # conflict 提案批准：僅標記已處理（不自動改資料，矛盾留待後續對話澄清）
                conn.execute(
                    "UPDATE dream_proposals SET status='applied' WHERE id=?", (_id,)
                )
                conn.commit()
                return True, ""
            conn.execute(
                "UPDATE dream_proposals SET status='rejected' WHERE id=?", (_id,)
            )
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[memory_v2] decide_proposal failed for {conf_uid}: {e}")
        return False, f"Internal error: {type(e).__name__}"


def stats(conf_uid: str) -> dict:
    """庫概況（給記憶分頁 UI）。fail-soft → 全 0。"""
    try:
        return {
            "exists": db_exists(conf_uid),
            "facts": count_facts(conf_uid),
            "reflections": count_reflections(conf_uid),
            "proposals_pending": count_proposals(conf_uid, "pending"),
            "proposals_total": count_proposals(conf_uid, ""),
            "last_dream_at": last_dream_at(conf_uid),
        }
    except Exception as e:
        logger.warning(f"[memory_v2] stats failed for {conf_uid}: {e}")
        return {
            "exists": False, "facts": 0, "reflections": 0,
            "proposals_pending": 0, "proposals_total": 0, "last_dream_at": 0.0,
        }
