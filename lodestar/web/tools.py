"""Small host capabilities for model-led task and memory decisions."""
from __future__ import annotations

import hashlib
import json

from lodestar.memory import assistant, learning
from lodestar.web import memory


def assistant_tools(app, item, run_id, message, check_cancel, *, user_authored=True):
    def definition(description, properties, handler, required=()):
        return {'description': description, 'input_schema': {'type': 'object',
            'properties': properties, 'required': list(required), 'additionalProperties': False},
            'handler': handler}

    def read_memory(args):
        with app.workspace() as ws:
            current = app.item(ws, item['id'])
            rows = memory.notes(ws, current, active_only=True)
            query = args.get('query', '').strip()
            if query:
                from lodestar.retrieval import terms
                wanted = terms(query)
                rows = sorted(rows, key=lambda r: -len(wanted & terms(r['content'])))
                rows = [r for r in rows if wanted & terms(r['content'])]
            return {'memories': assistant.select_rows(rows, query, budget=12000, limit=30), 'learning': learning.recall(ws.conn, query, memory.USER)
                    if query else learning.profile(ws.conn, memory.USER, limit=20)}

    def manage_memory(args):
        if not user_authored and args.get('scope', 'personal') == 'personal':
            raise ValueError('后台研究只保存任务或项目事实，不更新用户个人记忆')
        check_cancel()
        with app.lock, app.workspace() as ws, ws.conn:
            current = app.item(ws, item['id'])
            if not user_authored and args.get('id'):
                previous = ws.conn.execute('SELECT scope FROM assistant_memories WHERE id=? AND user_id=?',
                                           (args['id'], memory.USER)).fetchone()
                if previous and previous['scope'] == 'personal':
                    raise ValueError('后台研究不能修改个人记忆')
            user_message = ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                          (item['conversation_id'],)).fetchone()
            data = {k: v for k, v in args.items() if k != 'action'}
            if args['action'] == 'forget':
                if not args.get('id'):
                    raise ValueError('遗忘需要记忆 ID，请先读取记忆')
                data['status'] = 'deleted'
            result = memory.edit(ws, current, data, allow_run=run_id,
                                 source='conversation', source_message_id=user_message['id'] if user_message else None)
            result['managed_memory'] = assistant.prompt_rows(assistant.select_rows(memory.notes(ws, current, active_only=True), message))
            return result

    def list_tasks(args):
        with app.workspace() as ws:
            return {'tasks': [{k: row[k] for k in ('id', 'name', 'status', 'run_status', 'project_id', 'parent_id')}
                              for row in app.task_list(ws) if row['id'] != 'main']}

    def read_task(args):
        with app.workspace() as ws:
            other = app.item(ws, args['id'])
            rows = ws.conn.execute('SELECT role,content,metadata FROM messages WHERE conversation_id=? AND id>? ORDER BY id DESC LIMIT 6',
                                    (other['conversation_id'], other['context_after'])).fetchall()
            return {'id': other['id'], 'name': other['name'], 'status': other['status'],
                    'messages': [{'role': r['role'], 'content': r['content'][:12000],
                                  'sources': json.loads(r['metadata']).get('source_details', [])}
                                 for r in reversed(rows)]}

    def create_task(args):
        check_cancel()
        key = run_id + ':' + hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        project = args.get('project_id', item['project_id'])
        task_id = app.create(args['name'], project, parent_id=item['id'], dispatch_key=key)
        with app.workspace() as ws:
            child = app.item(ws, task_id)
        if not child['settings'] or child['settings'] == '{}':
            app.update(task_id, {'settings': json.loads(item['settings'])})
        check_cancel()
        child_run = app.submit(task_id, args['instruction'], key, background=True)
        return {'id': task_id, 'run_id': child_run, 'name': args['name'], 'status': 'queued',
                'note': 'Task runs separately. Its output is research material, not evidence of user mastery. Use read_task to retrieve its result.'}

    def record_learning(args):
        check_cancel()
        with app.lock, app.workspace() as ws:
            row = ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1", (item['conversation_id'],)).fetchone()
            if row is None:
                raise ValueError('缺少用户消息证据')
            return learning.record(ws.conn, user_id=memory.USER, technology=args['technology'],
                method=args.get('method', ''), event=args['event'], actor='user',
                evidence=json.dumps({'message_id': row['id'], 'content': message}, ensure_ascii=False))

    tools = {
        'read_memory': definition('Read your user\'s relevant personal/project facts and learning evidence. Query is optional.',
                                 {'query': {'type': 'string'}}, read_memory),
        'manage_memory': definition('Save a durable user preference/fact, or correct/forget an existing memory by ID. Use only user-provided facts or confirmed project decisions. Never store technology definitions, infer mastery, or turn background reading into user exposure. scope defaults to personal; project requires a bound project; task keeps a local note. Read existing IDs before modifying them.',
            {'action': {'type': 'string', 'enum': ['save', 'forget']}, 'id': {'type': 'string'},
             'content': {'type': 'string', 'maxLength': 2000},
             'scope': {'type': 'string', 'enum': ['personal', 'project', 'task']}}, manage_memory, ['action']),
        'list_tasks': definition('List task names and current execution states without loading their transcripts.', {}, list_tasks),
        'read_task': definition('Read a selected task\'s recent results and source metadata when relevant to the user request. Old context retired by memory corrections is excluded.',
                                {'id': {'type': 'string'}}, read_task, ['id']),
    }
    if user_authored:
        tools['record_learning'] = definition('Record the current user\'s explicit discussion, attempt or self-report about a technology/method. The host attaches the actual user message as evidence. No event here establishes verified mastery. Do not record an assistant answer or research assignment as learning evidence.',
            {'technology': {'type': 'string', 'minLength': 1, 'maxLength': 200},
             'method': {'type': 'string', 'maxLength': 300},
             'event': {'type': 'string', 'enum': ['discussed', 'attempted', 'self_report']}},
            record_learning, ['technology', 'event'])
    if item['id'] == 'main':
        tools['create_task'] = definition('Dispatch a separate background research task when the user asks to delegate work or research multiple topics separately. Give it a self-contained instruction with relevant constraints. Personal memory and bound project memory are shared; transcripts stay separate. Do not create tasks for ordinary quick questions. Project ID defaults to the current project. Do not claim results before reading them.',
            {'name': {'type': 'string', 'minLength': 1, 'maxLength': 80},
             'instruction': {'type': 'string', 'minLength': 1, 'maxLength': 24000},
             'project_id': {'type': 'integer', 'minimum': 1}}, create_task, ['name', 'instruction'])
    return tools
