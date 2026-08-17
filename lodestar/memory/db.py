"""SQLite schema + FTS5（全文检索）。对应 PRD §10 Knowledge State / §11 Memory。

V0 明确不做向量库：结构化 JSON + SQLite + FTS5 全文检索，接口化以便以后换 embeddings。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'unknown',      -- known | partial | unknown | needs_review
  confidence TEXT NOT NULL DEFAULT 'low',       -- low | medium | high
  notes TEXT NOT NULL DEFAULT '[]',             -- json list[str]
  related TEXT NOT NULL DEFAULT '[]',           -- json list[str] 概念名
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts USING fts5(
  name, content='concepts', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS concepts_ai AFTER INSERT ON concepts BEGIN
  INSERT INTO concepts_fts(rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS concepts_ad AFTER DELETE ON concepts BEGIN
  INSERT INTO concepts_fts(concepts_fts, rowid, name) VALUES('delete', old.id, old.name);
END;
CREATE TRIGGER IF NOT EXISTS concepts_au AFTER UPDATE ON concepts BEGIN
  INSERT INTO concepts_fts(concepts_fts, rowid, name) VALUES('delete', old.id, old.name);
  INSERT INTO concepts_fts(rowid, name) VALUES (new.id, new.name);
END;

CREATE TABLE IF NOT EXISTS research_tasks(
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  plan TEXT,                        -- json
  queries TEXT,                     -- json
  status TEXT NOT NULL DEFAULT 'running',   -- running | finished | error
  llm_mode TEXT NOT NULL DEFAULT 'live',
  metrics TEXT,                     -- json：基础指标（来源数/搜索次数等）
  brief_md TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  source_type TEXT NOT NULL,        -- paper | web
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  authors TEXT DEFAULT '[]',        -- json list[str]
  date TEXT,
  snippet TEXT,
  query TEXT,
  dedup_key TEXT,                   -- 归一化标题 或 arXiv id（强 key）
  rank INTEGER,
  reason TEXT,
  read_depth TEXT NOT NULL DEFAULT 'none',  -- abstract | full | none
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_sources_task ON sources(task_id);

CREATE TABLE IF NOT EXISTS knowledge_updates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  concept TEXT NOT NULL,
  action TEXT NOT NULL,             -- create | update
  proposal TEXT NOT NULL,           -- json {old, new, evidence, reasoning, claims}
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | applied | rejected
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);

CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  usefulness INTEGER,               -- 0..5（NULL=未填）
  best_item TEXT,
  repeated_item TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT,                        -- json
  FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_trace_task ON trace_events(task_id, seq);

CREATE TABLE IF NOT EXISTS eval_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  task_id TEXT,
  llm_mode TEXT NOT NULL,
  scores TEXT,                      -- json
  verdict TEXT NOT NULL,            -- pass | fail | warn
  created_at TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移：老库补充新列（V1-R1 为 sources 增加 venue 元数据字段）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    for col, ddl in (("venue", "TEXT"), ("is_published", "INTEGER"), ("external_ids", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE sources ADD COLUMN {col} {ddl}")
    conn.commit()


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn
