"""Owned facts with explicit scopes; transcripts remain in their tasks."""
from __future__ import annotations

import json
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_memories (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, scope TEXT NOT NULL,
 project_id INTEGER, item_id TEXT, content TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
 source TEXT NOT NULL DEFAULT 'user', source_message_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS assistant_memory_owner ON assistant_memories(user_id,scope,status);
CREATE TABLE IF NOT EXISTS assistant_memory_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
 revision INTEGER NOT NULL, action TEXT NOT NULL, content TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def visible(conn, user_id, *, project_id=None, item_id=None, active_only=False):
    predicate = "user_id=? AND (scope='personal' OR (scope='project' AND project_id=?) OR (scope='task' AND item_id=?))"
    if active_only:
        predicate += " AND status='active'"
    return [dict(row) for row in conn.execute(
        f'SELECT * FROM assistant_memories WHERE {predicate} ORDER BY updated_at DESC,id',
        (user_id, project_id, item_id))]


def save(conn, user_id, *, content, scope='personal', project_id=None,
         item_id=None, memory_id=None, status='active', source='user', source_message_id=None):
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 2000:
        raise ValueError('记忆内容应为 1–2000 字')
    if scope not in {'personal', 'project', 'task'} or status not in {'active', 'disabled'}:
        raise ValueError('记忆范围或状态无效')
    if (scope == 'project' and project_id is None) or (scope == 'task' and not item_id):
        raise ValueError('请先选择记忆归属')
    project_id = project_id if scope == 'project' else None
    item_id = item_id if scope == 'task' else None
    if memory_id:
        old = conn.execute('SELECT * FROM assistant_memories WHERE id=? AND user_id=?',
                           (memory_id, user_id)).fetchone()
        if old is None:
            raise LookupError('找不到这条记忆')
        revision = old['revision'] + 1
        conn.execute('UPDATE assistant_memories SET content=?,scope=?,project_id=?,item_id=?,status=?,revision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                     (content.strip(), scope, project_id, item_id, status, revision, memory_id))
        action = 'updated'
    else:
        memory_id, revision, action = uuid.uuid4().hex[:12], 1, 'created'
        conn.execute('INSERT INTO assistant_memories(id,user_id,scope,project_id,item_id,content,status,source,source_message_id) VALUES(?,?,?,?,?,?,?,?,?)',
                     (memory_id, user_id, scope, project_id, item_id, content.strip(), status, source, source_message_id))
    conn.execute('INSERT INTO assistant_memory_revisions(memory_id,revision,action,content) VALUES(?,?,?,?)',
                 (memory_id, revision, action, content.strip()))
    return dict(conn.execute('SELECT * FROM assistant_memories WHERE id=?', (memory_id,)).fetchone())


def forget(conn, user_id, memory_id):
    old = conn.execute('SELECT * FROM assistant_memories WHERE id=? AND user_id=?',
                       (memory_id, user_id)).fetchone()
    if old is None:
        raise LookupError('找不到这条记忆')
    conn.execute('DELETE FROM assistant_memories WHERE id=?', (memory_id,))
    conn.execute('UPDATE assistant_memory_revisions SET content=NULL WHERE memory_id=?', (memory_id,))
    conn.execute('INSERT INTO assistant_memory_revisions(memory_id,revision,action) VALUES(?,?,?)',
                 (memory_id, old['revision'] + 1, 'forgotten'))


def prompt_rows(rows):
    return [json.dumps({key: row[key] for key in ('id', 'scope', 'content', 'revision')},
                       ensure_ascii=False) for row in rows]


def select_rows(rows, query='', *, budget=8000, limit=20):
    """Bound automatic recall; explicit read_memory can retrieve other facts."""
    from lodestar.retrieval import terms
    wanted = terms(query)
    ranked = sorted(rows, key=lambda row: -len(wanted & terms(row['content'])))
    selected, used = [], 0
    for row in ranked:
        size = len(prompt_rows([row])[0])
        if used + size <= budget:
            selected.append(row)
            used += size
        if len(selected) == limit:
            break
    return selected
