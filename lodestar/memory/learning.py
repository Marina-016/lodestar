"""User-specific learning evidence, separate from research knowledge.

Store relationships and observations, never a technology encyclopedia. Exposure
does not establish mastery; only user-authored demonstrations do.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    technology TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence TEXT NOT NULL,
    paper_url TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS learning_by_user
ON learning_events(user_id, technology, method, id);
"""

EVENTS = {'explained', 'discussed', 'attempted', 'self_report',
          'demonstrated_explanation', 'demonstrated_implementation', 'demonstrated_application'}
MASTERY = {'demonstrated_explanation': 'can_explain',
           'demonstrated_implementation': 'can_implement',
           'demonstrated_application': 'can_apply'}
LEVELS = ['unknown', 'can_explain', 'can_implement', 'can_apply']


def record(conn: sqlite3.Connection, *, technology: str, event: str, evidence: str,
           user_id: str = 'default', method: str = '', actor: str = 'user',
           paper_url: str = '', task_id: str | None = None) -> dict:
    """Append an auditable observation. Caller supplies evidence, not a level."""
    if not user_id.strip() or not technology.strip() or not evidence.strip():
        raise ValueError('user_id, technology and evidence must be nonempty')
    if event not in EVENTS or actor not in {'user', 'agent'}:
        raise ValueError('unsupported event or actor')
    if event != 'explained' and actor != 'user':
        raise ValueError('only user evidence can establish engagement or mastery')
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        cursor = conn.execute(
            'INSERT INTO learning_events '
            '(user_id,technology,method,event,actor,evidence,paper_url,task_id,created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (user_id.strip(), technology.strip(), method.strip(), event, actor,
             evidence.strip(), paper_url, task_id, now))
    return {'evidence_id': cursor.lastrowid, 'event': event, 'created_at': now}


def profile(conn: sqlite3.Connection, user_id: str = 'default',
            technology: str | None = None, limit: int = 100) -> list[dict]:
    """Bound returned methods, not the history establishing their mastery.

    Full active history determines mastery and paper relationships. Only recent
    evidence rows and paper URLs are bounded for display and model context.
    """
    params = [user_id]
    predicate = 'user_id=? AND revoked_at IS NULL'
    if technology:
        predicate += ' AND technology=? COLLATE NOCASE'
        params.append(technology)
    groups = conn.execute(
        f'SELECT technology,method,MAX(id) AS latest FROM learning_events WHERE {predicate} '
        'GROUP BY technology COLLATE NOCASE,method COLLATE NOCASE '
        'ORDER BY latest DESC LIMIT ?',
        [*params, max(1, min(int(limit), 500))]).fetchall()
    result = []
    for group in groups:
        group_predicate = ('user_id=? AND revoked_at IS NULL '
                           'AND technology=? COLLATE NOCASE AND method=? COLLATE NOCASE')
        group_params = (user_id, group['technology'], group['method'])
        stats = conn.execute(
            f"SELECT COUNT(*) AS count, COUNT(DISTINCT NULLIF(paper_url,'')) AS paper_count, MAX(CASE event "
            "WHEN 'demonstrated_application' THEN 3 WHEN 'demonstrated_implementation' THEN 2 "
            "WHEN 'demonstrated_explanation' THEN 1 ELSE 0 END) AS level "
            f"FROM learning_events WHERE {group_predicate}", group_params).fetchone()
        evidence = [dict(row) for row in conn.execute(
            f'SELECT * FROM learning_events WHERE {group_predicate} ORDER BY id DESC LIMIT 20',
            group_params).fetchall()]
        # Preserve the actual historical support for the level, even when old.
        mastery = LEVELS[stats['level']]
        mastery_evidence = None
        if mastery != 'unknown':
            event = next(event for event, level in MASTERY.items() if level == mastery)
            row = conn.execute(f'SELECT * FROM learning_events WHERE {group_predicate} '
                               'AND event=? ORDER BY id DESC LIMIT 1', (*group_params, event)).fetchone()
            mastery_evidence = dict(row)
        papers = conn.execute(f"SELECT paper_url,MAX(id) AS latest FROM learning_events "
                              f"WHERE {group_predicate} AND paper_url<>'' "
                              'GROUP BY paper_url ORDER BY latest DESC LIMIT 50', group_params).fetchall()
        result.append({'technology': group['technology'], 'method': group['method'],
                       'mastery': mastery, 'mastery_evidence': mastery_evidence,
                       'evidence': evidence, 'event_count': stats['count'],
                       'papers': [row['paper_url'] for row in papers],
                       'paper_count': stats['paper_count'], 'papers_truncated': stats['paper_count'] > 50})
    return result


def recall(conn: sqlite3.Connection, query: str, user_id: str = 'default', limit: int = 8) -> list[dict]:
    """Filter by stored technology/method before projecting a bounded evidence history."""
    from lodestar.retrieval import terms
    wanted = terms(query)
    if not wanted:
        return []
    rows = conn.execute('SELECT DISTINCT technology,method FROM learning_events '
                        'WHERE user_id=? AND revoked_at IS NULL', (user_id,)).fetchall()
    ranked = []
    for row in rows:
        score = len(wanted & terms(row['technology'] + ' ' + row['method']))
        if score:
            ranked.append((score, row['technology'], row['method']))
    results = []
    for score, technology, method in sorted(ranked, key=lambda r: (-r[0], r[1], r[2]))[:max(1,min(limit,20))]:
        for entry in profile(conn, user_id, technology, limit=500):
            if entry['method'].casefold() == method.casefold():
                entry['recall_term_matches'] = score
                entry['evidence'] = entry['evidence'][:5]
                results.append(entry)
    return results


def revoke(conn: sqlite3.Connection, evidence_id: int, user_id: str = 'default') -> bool:
    with conn:
        result = conn.execute('UPDATE learning_events SET revoked_at=? '
                              'WHERE id=? AND user_id=? AND revoked_at IS NULL',
                              (datetime.now(timezone.utc).isoformat(), evidence_id, user_id))
    return result.rowcount == 1


def prompt_context(records: list[dict]) -> str:
    return ('用户学习证据（仅记录技术/方法的接触与掌握；阅读不代表掌握；'
            'self_report 是自评，不是验证；未记录的技术掌握程度为 unknown）：\n'
            + json.dumps(records, ensure_ascii=False))
