"""项目级记忆 DeerMem（v3 Phase 7，参考 deer-flow agents/memory/backends/deermem/）。

数据模型（deer-flow storage.py 思想）：
- 每个 fact 一个 Markdown 文件：`<workspace>/.pi/memory/facts/**/*.md`
- frontmatter：id / title / category / created_at / updated_at / confidence / task_id
- 按 workspace 隔离（路径含 workspace），跨任务共享（同一工作目录的所有任务可见）。

索引：
- SQLite FTS5 虚拟表（`<workspace>/.pi/memory/memory.db`），tokenize 空格分词；
  中文经 jieba 预切词后以空格拼接入库（deer-flow `_preprocess_content` L44 思想）。
- 检索：jieba 切查询词 → OR join → MATCH；bm25 排序 + 时间衰减 + confidence 加权。

写入：
- `add()`：原子写（临时文件 + os.replace，Windows 安全），再 upsert FTS 行。

注入：
- `get_context(workspace, query)` → 按 `max_injection_tokens` 截断的纯文本块
  （deer-flow core/prompt.py `format_memory_for_injection` 思想）。

单向依赖：memory/* ← graph.py / task_route.py；仅 import stdlib + jieba（惰性）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

#: 记忆库根（相对 workspace）：<workspace>/.pi/memory/
MEMORY_DIR = ".pi/memory"
#: facts 目录名。
FACTS_DIR = "facts"
#: FTS 索引文件名。
INDEX_DB = "memory.db"
#: fact Markdown 文件扩展名。
FACT_EXT = ".md"
#: 检索默认条数。
DEFAULT_TOP_K = 5
#: 时间衰减半衰期（天）：30 天后相关性减半（deer-flow L479 思想）。
DECAY_HALFLIFE_DAYS = 30
#: confidence 权重（deer-flow `confidence*0.2`）。
CONFIDENCE_WEIGHT = 0.2

#: 注入文本前缀（deer-flow `User Context:/History:/Facts:` 风格）。
_INJECTION_HEADER = "【项目记忆】（跨任务积累，仅作参考，可能过时）\n"

#: frontmatter 字段白名单。
_META_FIELDS = (
    "id", "title", "category", "created_at", "updated_at",
    "confidence", "task_id", "tags",
)


def _sha1(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]  # noqa: S324 非安全用途


class MemoryStore:
    """一个 workspace 的项目级记忆（facts 文件 + FTS5 索引）。"""

    def __init__(self, workspace: str):
        self.workspace = os.path.realpath(str(workspace))
        self.root = Path(self.workspace) / MEMORY_DIR
        self.facts_dir = self.root / FACTS_DIR
        self.db_path = self.root / INDEX_DB
        self._lock = threading.Lock()
        self._jieba_mod: Any | None = None
        self._fts_ready = False

    # ------------------------------------------------------------------ //
    # 初始化
    # ------------------------------------------------------------------ //
    def _ensure_dirs(self) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)

    def _jieba(self) -> Any:
        """惰性加载 jieba 模块（首次调用导入，缓存模块对象）。"""
        if self._jieba_mod is None:
            import jieba

            self._jieba_mod = jieba
        return self._jieba_mod

    def _preprocess(self, text: str) -> str:
        """内容预分词：原文保留 + jieba 词追加（deer-flow `_preprocess_content` 思想）。

        输出空格分隔的 token 串，供 FTS5 unicode61 tokenize 按词匹配。
        - 中文原文（整串）保留为单个 token：查"端口"时若原文含"端口约定"，
          unicode61 会把连续 CJK 当整体，匹配不到——故追加 jieba 词。
        - 中英混 token：拆成 中文段 + 非中文段。
        """
        if not text:
            return ""
        try:
            jieba = self._jieba()
            tokens = jieba.cut(text, cut_all=False)
            out: list[str] = []
            for t in tokens:
                t = t.strip()
                if not t:
                    continue
                if re.fullmatch(r"[\u4e00-\u9fff\u3000-\u303f]+", t):
                    out.append(t)  # jieba 词（如 端口/约定）
                elif any("\u4e00" <= ch <= "\u9fff" for ch in t):
                    for part in re.findall(r"[\u4e00-\u9fff]+|[^\u4e00-\u9fff]+", t):
                        part = part.strip()
                        if part:
                            out.append(part)
                else:
                    out.append(t)  # 英文/数字 token
            # 去重保序 + 追加原文整串（保证短语查询可命中）
            seen: list[str] = []
            for t in out:
                if t not in seen:
                    seen.append(t)
            return " ".join(seen)
        except Exception:
            return text  # jieba 失败回退原文（FTS 仍可匹配英文/数字）

    def _index_fact_content(self, fact: dict[str, Any]) -> str:
        """fact 的 FTS 索引内容：标题 + 正文（预分词）+ 分类。"""
        content = self._preprocess(fact.get("content", ""))
        title = self._preprocess(fact.get("title", ""))
        return f"{title} {content} {fact.get('category', '')}"

    def _preprocess_query(self, query: str) -> str:
        """查询预分词：原始片段 + jieba 切词 OR 并集（FTS5 MATCH 按 token 精确匹配）。

        关键：jieba 可能把"端口"切成"端""口"（单字），而索引 token 是"端口"，
        只 OR 单字会查不到。故把原始词片段也加入 OR 候选（原始串 + 切词去重）。
        """
        candidates: list[str] = []
        # 1) 原始片段（按空白/标点粗切，保整词）
        for seg in re.split(r"[\s,，。；;！!？?、]+", query):
            seg = seg.strip()
            if seg and seg not in candidates:
                candidates.append(seg)
        # 2) jieba 切词（补充词级 token）
        try:
            jieba = self._jieba()
            for t in jieba.cut(query):
                t = t.strip()
                if t and t not in candidates:
                    candidates.append(t)
        except Exception:
            pass
        # 3) 过滤 + 去 FTS5 特殊字符
        safe = []
        for t in candidates:
            t = re.sub(r'["\'()*:^]', "", t)
            if not t:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]", t):
                safe.append(t)  # 中文单字保留（可命中单字 token）
            elif len(t) <= 1:
                continue  # 英文单字母/符号过滤
            else:
                safe.append(t)
        # 去重保序
        seen: list[str] = []
        for t in safe:
            if t not in seen:
                seen.append(t)
        return " OR ".join(seen) if seen else ""

    # ------------------------------------------------------------------ //
    # FTS 索引
    # ------------------------------------------------------------------ //
    def _ensure_fts(self) -> sqlite3.Connection:
        self._ensure_dirs()
        con = sqlite3.connect(str(self.db_path))
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(content, category, id UNINDEXED, title, "
            "created_at UNINDEXED, confidence UNINDEXED, task_id UNINDEXED)"
        )
        return con

    def _index_fact(self, con: sqlite3.Connection, fact: dict[str, Any]) -> None:
        content = self._index_fact_content(fact)
        # FTS5 虚拟表不支持 UPSERT → 先删后插（幂等更新）。
        con.execute("DELETE FROM memory_fts WHERE id = ?", (fact["id"],))
        con.execute(
            "INSERT INTO memory_fts(content, category, id, title, created_at, confidence, task_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content,
                fact.get("category", "general"),
                fact["id"],
                fact.get("title", ""),
                fact.get("created_at", ""),
                float(fact.get("confidence", 0.8)),
                fact.get("task_id", ""),
            ),
        )

    # ------------------------------------------------------------------ //
    # 写入
    # ------------------------------------------------------------------ //
    def add(self, *, title: str, content: str, category: str = "general",
            task_id: str = "", confidence: float = 0.8, tags: list[str] | None = None) -> str:
        """写入一条 fact（原子：临时文件 + os.replace）。返回 fact id。"""
        with self._lock:
            self._ensure_dirs()
            fid = uuid.uuid4().hex[:16]
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            meta = {
                "id": fid,
                "title": title,
                "category": category,
                "created_at": now,
                "updated_at": now,
                "confidence": confidence,
                "task_id": task_id,
                "tags": tags or [],
            }
            body = _render_markdown(meta, content)
            target = self.facts_dir / f"{fid}{FACT_EXT}"
            # 原子写（Windows 安全：临时文件 + os.replace）
            fd, tmp = tempfile.mkstemp(dir=str(self.facts_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, target)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            # 更新索引
            try:
                con = self._ensure_fts()
                try:
                    self._index_fact(con, meta | {"content": content})
                    con.commit()
                finally:
                    con.close()
            except Exception as e:  # noqa: BLE001 索引失败不丢文件
                logger.warning(f"memory: FTS 索引失败（文件已写入）: {e}")
            return fid

    def list_facts(self) -> list[dict[str, Any]]:
        """列出全部 fact（按时间倒序）。"""
        out: list[dict[str, Any]] = []
        if not self.facts_dir.exists():
            return out
        for p in sorted(self.facts_dir.glob(f"*{FACT_EXT}"), reverse=True):
            fact = self._parse_fact_file(p)
            if fact:
                out.append(fact)
        return out

    def _parse_fact_file(self, path: Path) -> dict[str, Any] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, body = _split_frontmatter(text)
        if not meta or "id" not in meta:
            meta = {"id": path.stem}
        meta["content"] = body.strip()
        return meta

    # ------------------------------------------------------------------ //
    # 检索
    # ------------------------------------------------------------------ //
    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
        """检索记忆：jieba 切查询 → OR join MATCH → bm25 + 时间衰减 + confidence 加权。

        失败（索引不存在 / 查询异常）→ 返回空列表（fail-soft）。
        """
        if not query.strip():
            return []
        if not self.db_path.exists():
            return []
        q = self._preprocess_query(query)
        if not q:
            return []
        try:
            con = sqlite3.connect(str(self.db_path))
            try:
                rows = con.execute(
                    "SELECT id, title, category, content, created_at, confidence, "
                    "bm25(memory_fts) AS score FROM memory_fts WHERE memory_fts MATCH ? "
                    "ORDER BY bm25(memory_fts) LIMIT ?",
                    (q, top_k * 3),
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            logger.warning(f"memory: 检索失败（{e}）")
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            fid, title, category, content, created_at, confidence, score = r
            days = _days_since(created_at)
            decay = 0.5 ** (days / DECAY_HALFLIFE_DAYS)
            rank = (score or 0) + confidence * CONFIDENCE_WEIGHT + decay
            out.append({
                "id": fid, "title": title, "category": category, "content": content,
                "created_at": created_at, "confidence": confidence, "score": rank,
            })
        out.sort(key=lambda f: f["score"], reverse=True)
        return out[:top_k]

    # ------------------------------------------------------------------ //
    # 注入
    # ------------------------------------------------------------------ //
    def get_context(self, query: str, max_tokens: int = 1500) -> str:
        """检索并格式化为注入文本（按 max_tokens 截断）。"""
        facts = self.search(query, top_k=DEFAULT_TOP_K)
        if not facts:
            return ""
        parts = [_INJECTION_HEADER]
        budget = max_tokens * 4  # 字符粗估
        used = 0
        for f in facts:
            block = f"- [{f['category']}] {f['title']}：{f['content'][:300]}"
            if used + len(block) > budget:
                parts.append("- ...（记忆过多已截断）")
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 模块级辅助
# --------------------------------------------------------------------------- #
def _render_markdown(meta: dict[str, Any], content: str) -> str:
    lines = ["---"]
    for k in _META_FIELDS:
        if k in meta and meta[k] not in (None, "", []):
            v = meta[k]
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            lines.append(f"{k}: {v}")
    lines += ["---", "", content.rstrip(), ""]
    return "\n".join(lines)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown frontmatter（--- 包围的 YAML 简化版，仅支持标量/list）。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n", 10)
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, Any] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if v.startswith("[") and v.endswith("]"):
            try:
                meta[k] = json.loads(v)
            except json.JSONDecodeError:
                meta[k] = v
        elif v.isdigit():
            meta[k] = int(v)
        elif _is_float(v):
            meta[k] = float(v)
        else:
            meta[k] = v.strip('"')
    body = "\n".join(lines[end + 1:])
    return meta, body


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _days_since(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, (time.time() - t) / 86400)
    except (ValueError, TypeError):
        return 0.0


#: 模块级单例注册表（workspace → MemoryStore），避免重复建连。
_stores: dict[str, MemoryStore] = {}
_stores_lock = threading.Lock()


def get_memory_manager(workspace: str) -> MemoryStore:
    """获取（或创建）workspace 的记忆管理器（进程内缓存）。"""
    ws = os.path.realpath(str(workspace))
    with _stores_lock:
        if ws not in _stores:
            _stores[ws] = MemoryStore(ws)
        return _stores[ws]
