"""Durable paper-watch subscriptions and per-project recommendation inbox."""
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_watches (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 query TEXT NOT NULL, terms TEXT NOT NULL, interval_hours INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, next_run TEXT NOT NULL, lease_until TEXT,
 UNIQUE(project_id, query)
);
CREATE TABLE IF NOT EXISTS paper_watch_runs (
 id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL, started_at TEXT NOT NULL,
 status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_recommendations (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, paper_key TEXT NOT NULL,
 source TEXT NOT NULL, matches TEXT NOT NULL, first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL, channels TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'unread',
 UNIQUE(project_id, paper_key)
);
CREATE TABLE IF NOT EXISTS paper_candidate_assessments (
 id INTEGER PRIMARY KEY, recommendation_id INTEGER NOT NULL, read_id INTEGER NOT NULL,
 status TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_candidate_reads (
 id INTEGER PRIMARY KEY, recommendation_id INTEGER NOT NULL REFERENCES paper_recommendations(id),
 status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def subscribe(conn, project_id, query, terms, interval_hours, now):
    if not query.strip() or not terms or any(not t.strip() for t in terms):
        raise ValueError('A public search query and at least one matching term are required')
    if len(query) > 300 or len(terms) > 12 or any(len(t) > 80 for t in terms):
        raise ValueError('Query or matching terms exceed the watch budget')
    if not 1 <= interval_hours <= 720:
        raise ValueError('interval_hours must be between 1 and 720')
    conn.execute("""INSERT INTO paper_watches(project_id,query,terms,interval_hours,next_run)
        VALUES(?,?,?,?,?) ON CONFLICT(project_id,query) DO UPDATE SET
        terms=excluded.terms,interval_hours=excluded.interval_hours,enabled=1,next_run=excluded.next_run""",
        (project_id, query.strip(), json.dumps(terms, ensure_ascii=False), interval_hours, now))
    conn.commit()
    return dict(conn.execute('SELECT * FROM paper_watches WHERE project_id=? AND query=?',
                             (project_id, query.strip())).fetchone())


def inbox(conn, project_id, limit=50):
    rows = conn.execute('SELECT * FROM paper_recommendations WHERE project_id=? ORDER BY last_seen DESC,id DESC LIMIT ?',
                        (project_id, max(1, min(limit, 100)))).fetchall()
    return [{**dict(row), **{k: json.loads(row[k]) for k in ('source', 'matches', 'channels')}} for row in rows]
