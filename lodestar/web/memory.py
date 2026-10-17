"""Web memory migration, ownership and invalidation in one place."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from lodestar.memory import assistant, learning

USER = 'web:local'


class MemoryBusy(ValueError):
    pass


def initialize(ws):
    ws.conn.executescript(assistant.SCHEMA)
    columns = {r['name'] for r in ws.conn.execute('PRAGMA table_info(web_items)')}
    for name, declaration in {'project_id': 'INTEGER', 'parent_id': 'TEXT',
                              'context_after': 'INTEGER NOT NULL DEFAULT 0',
                              'dispatch_key': 'TEXT'}.items():
        if name not in columns:
            ws.conn.execute(f'ALTER TABLE web_items ADD COLUMN {name} {declaration}')
    ws.conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS web_dispatch_key ON web_items(dispatch_key) WHERE dispatch_key IS NOT NULL')
    run_columns = {r['name'] for r in ws.conn.execute('PRAGMA table_info(web_runs)')}
    if 'policy' not in run_columns:
        ws.conn.execute("ALTER TABLE web_runs ADD COLUMN policy TEXT NOT NULL DEFAULT '{}'")
    ws.conn.execute('CREATE TABLE IF NOT EXISTS web_migrations(name TEXT PRIMARY KEY)')
    ws.conn.execute("UPDATE web_items SET name='主对话' WHERE id='main' AND name='主空间'")
    if ws.conn.execute("SELECT 1 FROM web_migrations WHERE name='shared_memory_v1'").fetchone():
        return
    for item in ws.conn.execute("SELECT * FROM web_items WHERE owner='local'").fetchall():
        ws.conn.execute('UPDATE agent_sessions SET user_id=? WHERE conversation_id=? AND user_id=?',
                         (USER, item['conversation_id'], USER + ':' + item['id']))
        ws.conn.execute('UPDATE learning_events SET user_id=? WHERE user_id=?',
                         (USER, USER + ':' + item['id']))
    for note in ws.conn.execute('SELECT * FROM web_notes').fetchall():
        # Preserve old task-only facts until the user explicitly promotes them.
        scope = 'personal' if note['item_id'] == 'main' else 'task'
        ws.conn.execute('INSERT OR IGNORE INTO assistant_memories(id,user_id,scope,item_id,content,status,revision,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (note['id'], USER, scope, note['item_id'] if scope == 'task' else None,
                          note['content'], note['status'], note['revision'], 'legacy', note['updated_at']))
    ws.conn.execute("INSERT INTO web_migrations(name) VALUES('shared_memory_v1')")


def notes(ws, item, *, active_only=False):
    return assistant.visible(ws.conn, USER, project_id=item['project_id'],
                             item_id=item['id'], active_only=active_only)


def affected(ws, row):
    if row['scope'] == 'personal':
        return [dict(r) for r in ws.conn.execute("SELECT * FROM web_items WHERE owner='local'")]
    column, value = ('project_id', row['project_id']) if row['scope'] == 'project' else ('id', row['item_id'])
    return [dict(r) for r in ws.conn.execute(f"SELECT * FROM web_items WHERE owner='local' AND {column}=?", (value,))]


def invalidate(ws, items, *, allow_run=None):
    """Retire stale model history, while retaining the user's visible transcripts.

This deliberately conservative boundary prevents an old answer/summary from
reintroducing a removed fact. No semantic-erasure guarantee is made about data
already sent to a provider or explicitly supplied again by the user.
"""
    for item in items:
        busy = ws.conn.execute("SELECT id FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling') AND id<>?",
                                (item['id'], allow_run or '')).fetchone()
        if busy:
            raise MemoryBusy('相关任务仍在运行，请停止或等待结束后修改共享记忆')
    for item in items:
        last = ws.conn.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE conversation_id=?',
                                (item['conversation_id'],)).fetchone()[0]
        ws.conn.execute('UPDATE web_items SET context_after=? WHERE id=?', (last, item['id']))
        ws.conn.execute("UPDATE agent_sessions SET evidence='[]' WHERE conversation_id=?", (item['conversation_id'],))


def edit(ws, item, data, *, allow_run=None, source='user', source_message_id=None, catalog=False):
    old = None
    if data.get('id'):
        rows = [dict(r) for r in ws.conn.execute('SELECT * FROM assistant_memories WHERE user_id=?', (USER,))] if catalog else notes(ws, item)
        old = next((r for r in rows if r['id'] == data['id']), None)
        if old is None:
            raise LookupError('找不到这条记忆')
        if 'revision' in data and data['revision'] != old['revision']:
            raise MemoryBusy('记忆已更新，请刷新后再编辑')
    scope = data.get('scope', old['scope'] if old else 'personal')
    project_id = data.get('project_id', old['project_id'] if old and old['scope'] == scope else item['project_id']) if catalog else item['project_id']
    target_item = old['item_id'] if old and old['scope'] == scope == 'task' else item['id']
    if scope == 'project' and (type(project_id) is not int or not ws.conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone()):
        raise ValueError('请选择有效项目')
    target = {'scope': scope, 'project_id': project_id, 'item_id': target_item}
    impacted = {r['id']: r for row in ([old] if old else []) + [target] for r in affected(ws, row)}
    # Additions also invalidate prior model assumptions, but do not discard history.
    if old:
        invalidate(ws, impacted.values(), allow_run=allow_run)
    elif any(ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling') AND id<>?",
                            (r['id'], allow_run or '')).fetchone() for r in impacted.values()):
        raise MemoryBusy('相关任务仍在运行，请等待结束后添加共享记忆')
    if old and data.get('status') == 'deleted':
        assistant.forget(ws.conn, USER, old['id'])
        # Retire duplicate legacy storage too; migration never runs a second time.
        ws.conn.execute('DELETE FROM web_notes WHERE id=?', (old['id'],))
        return {'id': old['id'], 'deleted': True, 'context_reset': True}
    saved = assistant.save(ws.conn, USER, content=data.get('content', old['content'] if old else ''),
        scope=scope, project_id=project_id, item_id=target_item, memory_id=old['id'] if old else None,
        status=data.get('status', old['status'] if old else 'active'), source=source,
        source_message_id=source_message_id)
    return {'id': saved['id'], 'revision': saved['revision'], 'context_reset': bool(old)}


def edit_learning(ws, data, *, allow_run=None, source_message_id=None):
    row = ws.conn.execute('SELECT * FROM learning_events WHERE id=? AND user_id=? AND revoked_at IS NULL',
                          (data.get('id'), USER)).fetchone()
    if row is None:
        raise LookupError('找不到这条学习证据')
    invalidate(ws, affected(ws, {'scope': 'personal'}), allow_run=allow_run)
    result = {'id': row['id'], 'context_reset': True}
    if data.get('action') == 'correct':
        # A manual correction is a self-report, never a verified demonstration.
        content = data.get('evidence', '')
        limit = 24000 if source_message_id is not None else 2000
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= limit:
            raise ValueError(f'纠正说明应为 1–{limit} 字')
        observation = {'corrects': row['id'], 'content': content.strip()}
        if source_message_id is not None:
            observation['message_id'] = source_message_id
        evidence = json.dumps(observation, ensure_ascii=False)
        technology, method = data.get('technology', row['technology']), data.get('method', row['method'])
        if not isinstance(technology, str) or not technology.strip() or len(technology) > 200 or not isinstance(method, str) or len(method) > 300:
            raise ValueError('技术或方法无效')
        cursor = ws.conn.execute('INSERT INTO learning_events(user_id,technology,method,event,actor,evidence,paper_url,task_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (USER, technology.strip(), method.strip(), 'self_report', 'user', evidence, row['paper_url'], row['task_id'], datetime.now(timezone.utc).isoformat()))
        result.update(evidence_id=cursor.lastrowid, event='self_report')
    elif data.get('action') != 'revoke':
        raise ValueError('学习操作无效')
    else:
        result['revoked'] = True
    ws.conn.execute('UPDATE learning_events SET revoked_at=? WHERE id=? AND user_id=?',
                     (datetime.now(timezone.utc).isoformat(), row['id'], USER))
    return result
