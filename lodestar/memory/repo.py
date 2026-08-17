"""Memory 仓储：Knowledge State / Research Memory / Feedback / Eval 的 CRUD。

所有 JSON 字段落库前序列化；读取时反序列化。时间统一 UTC ISO。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


# ----------------------------------------------------------------------
# Knowledge State
# ----------------------------------------------------------------------
CONCEPT_STATUS = {"known", "partial", "unknown", "needs_review"}
CONCEPT_CONFIDENCE = {"low", "medium", "high"}


def upsert_concept(conn: sqlite3.Connection, name: str, status: str = "unknown",
                   confidence: str = "low", notes: Optional[list] = None,
                   related: Optional[list] = None, append_note: Optional[str] = None) -> dict:
    if status not in CONCEPT_STATUS:
        raise ValueError(f"非法 status={status!r}")
    if confidence not in CONCEPT_CONFIDENCE:
        raise ValueError(f"非法 confidence={confidence!r}")
    now = _now()
    row = conn.execute("SELECT * FROM concepts WHERE name=?", (name,)).fetchone()
    if row is None:
        notes = list(notes or [])
        if append_note:
            notes.append(append_note)
        conn.execute(
            "INSERT INTO concepts(name,status,confidence,notes,related,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (name, status, confidence, _dumps(notes), _dumps(related or []), now, now),
        )
        conn.commit()
        return get_concept(conn, name)
    # 更新
    old = dict(row)
    final_notes = list(_loads(row["notes"], []) or [])
    if append_note:
        final_notes.append(append_note)
    elif notes is not None:
        final_notes = list(notes)
    final_related = list(related if related is not None else _loads(row["related"], []) or [])
    conn.execute(
        "UPDATE concepts SET status=?, confidence=?, notes=?, related=?, updated_at=? WHERE name=?",
        (status, confidence, _dumps(final_notes), _dumps(final_related), now, name),
    )
    conn.commit()
    new = get_concept(conn, name)
    return {"old": old, "new": new}


def get_concept(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM concepts WHERE name=?", (name,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["notes"] = _loads(d.get("notes"), [])
    d["related"] = _loads(d.get("related"), [])
    return d


def list_concepts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM concepts ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["notes"] = _loads(d.get("notes"), [])
        d["related"] = _loads(d.get("related"), [])
        out.append(d)
    return out


def search_concepts(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """FTS5 语义检索（V0 用全文检索近似，后续可换 embeddings）。"""
    if not query.strip():
        return []
    q = query.strip().replace('"', " ").replace("'", " ")
    tokens = [t for t in q.split() if t][:6]
    fts_q = " OR ".join(f'"{t}"' for t in tokens)
    try:
        rows = conn.execute(
            "SELECT c.* FROM concepts_fts f JOIN concepts c ON c.id=f.rowid "
            "WHERE concepts_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS 语法/空 token 等异常降级为 LIKE 兜底
        rows = conn.execute(
            "SELECT * FROM concepts WHERE name LIKE ? OR notes LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["notes"] = _loads(d.get("notes"), [])
        d["related"] = _loads(d.get("related"), [])
        out.append(d)
    return out


def seed_concepts(conn: sqlite3.Connection, items: list[dict]) -> int:
    """批量种子概念（用户声明已懂 / golden case 预置）。items: [{name,status,confidence}]。"""
    n = 0
    for it in items:
        upsert_concept(conn, it["name"], it.get("status", "unknown"), it.get("confidence", "low"),
                       notes=it.get("notes"))
        n += 1
    return n


# ----------------------------------------------------------------------
# Research Memory
# ----------------------------------------------------------------------
def create_task(conn: sqlite3.Connection, task_id: str, goal: str, plan: dict,
                queries: Optional[list] = None, llm_mode: str = "live") -> None:
    conn.execute(
        "INSERT INTO research_tasks(id,goal,plan,queries,status,llm_mode,created_at) VALUES(?,?,?,?,?,?,?)",
        (task_id, goal, _dumps(plan), _dumps(queries or []), "running", llm_mode, _now()),
    )
    conn.commit()


def finish_task(conn: sqlite3.Connection, task_id: str, brief_md: str,
                status: str = "finished", metrics: Optional[dict] = None) -> None:
    conn.execute(
        "UPDATE research_tasks SET brief_md=?, status=?, metrics=?, finished_at=? WHERE id=?",
        (brief_md, status, _dumps(metrics or {}), _now(), task_id),
    )
    conn.commit()


def get_task(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["plan"] = _loads(d.get("plan"), {})
    d["queries"] = _loads(d.get("queries"), [])
    d["metrics"] = _loads(d.get("metrics"), {})
    return d


def add_source(conn: sqlite3.Connection, task_id: str, source: dict) -> int:
    cur = conn.execute(
        "INSERT INTO sources(task_id,source_type,title,url,authors,date,snippet,query,dedup_key,"
        "venue,is_published,external_ids,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, source.get("source_type", "web"), source["title"], source["url"],
         _dumps(source.get("authors", []) or []), source.get("date"), source.get("snippet"),
         source.get("query"), source.get("dedup_key"),
         source.get("venue"), 1 if source.get("is_published") else 0,
         _dumps(source.get("external_ids", {}) or {}), _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_source(conn: sqlite3.Connection, source_id: int, **fields) -> None:
    keys = {k for k in fields if k in {"rank", "reason", "read_depth"}}
    if not keys:
        return
    sets = ", ".join(f"{k}=?" for k in keys)
    conn.execute(f"UPDATE sources SET {sets} WHERE id=?", (*[fields[k] for k in keys], source_id))
    conn.commit()


def list_sources(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM sources WHERE task_id=? ORDER BY rank IS NULL, rank ASC, id ASC", (task_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = _loads(d.get("authors"), [])
        d["external_ids"] = _loads(d.get("external_ids"), {})
        d["is_published"] = bool(d.get("is_published"))
        out.append(d)
    return out


# ----------------------------------------------------------------------
# Knowledge Update Proposal（PRD §19：修改前确认）
# ----------------------------------------------------------------------
def add_knowledge_update(conn: sqlite3.Connection, task_id: str, concept: str, action: str,
                         proposal: dict) -> int:
    cur = conn.execute(
        "INSERT INTO knowledge_updates(task_id,concept,action,proposal,status,created_at) VALUES(?,?,?,?,?,?)",
        (task_id, concept, action, _dumps(proposal), "pending", _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_knowledge_updates(conn: sqlite3.Connection, task_id: Optional[str] = None,
                           status: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM knowledge_updates WHERE 1=1"
    args: list = []
    if task_id:
        sql += " AND task_id=?"
        args.append(task_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id"
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["proposal"] = _loads(d.get("proposal"), {})
        out.append(d)
    return out


def set_update_status(conn: sqlite3.Connection, update_id: int, status: str) -> None:
    conn.execute(
        "UPDATE knowledge_updates SET status=?, confirmed_at=? WHERE id=?",
        (status, _now(), update_id),
    )
    conn.commit()


# ----------------------------------------------------------------------
# Feedback（PRD 缺口 B2：反馈信号采集）
# ----------------------------------------------------------------------
def add_feedback(conn: sqlite3.Connection, task_id: str, usefulness: Optional[int] = None,
                 best_item: Optional[str] = None, repeated_item: Optional[str] = None,
                 note: Optional[str] = None) -> int:
    cur = conn.execute(
        "INSERT INTO feedback(task_id,usefulness,best_item,repeated_item,note,created_at) VALUES(?,?,?,?,?,?)",
        (task_id, usefulness, best_item, repeated_item, note, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_feedback(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY id DESC").fetchall()]


# ----------------------------------------------------------------------
# Experiments（V3：Research → Experiment → Build）
# ----------------------------------------------------------------------
def add_experiment(conn: sqlite3.Connection, hypothesis: str, task_id: str | None = None,
                   description: str | None = None, source_claim: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO experiments(task_id,hypothesis,description,source_claim,build_status,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (task_id, hypothesis, description, source_claim, "draft", _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_experiment(conn: sqlite3.Connection, exp_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM experiments WHERE id=?", (exp_id,)).fetchone()
    return dict(row) if row else None


def list_experiments(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM experiments ORDER BY id DESC").fetchall()]


def set_experiment_build(conn: sqlite3.Connection, exp_id: int, status: str, output_dir: str | None = None) -> None:
    conn.execute(
        "UPDATE experiments SET build_status=?, output_dir=?, built_at=? WHERE id=?",
        (status, output_dir, _now() if status in {"built", "failed"} else None, exp_id),
    )
    conn.commit()


# ----------------------------------------------------------------------
# Eval
# ----------------------------------------------------------------------
def save_eval_run(conn: sqlite3.Connection, case_id: str, task_id: str, llm_mode: str,
                  scores: dict, verdict: str) -> int:
    cur = conn.execute(
        "INSERT INTO eval_runs(case_id,task_id,llm_mode,scores,verdict,created_at) VALUES(?,?,?,?,?,?)",
        (case_id, task_id, llm_mode, _dumps(scores), verdict, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_eval_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute("SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scores"] = _loads(d.get("scores"), {})
        out.append(d)
    return out


# ----------------------------------------------------------------------
# Trace
# ----------------------------------------------------------------------
def add_trace_event(conn: sqlite3.Connection, task_id: str, seq: int, kind: str, data: Any) -> None:
    conn.execute(
        "INSERT INTO trace_events(task_id,seq,ts,kind,data) VALUES(?,?,?,?,?)",
        (task_id, seq, _now(), kind, _dumps(data)),
    )
    conn.commit()


def list_trace_events(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM trace_events WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = _loads(d.get("data"), {})
        out.append(d)
    return out
