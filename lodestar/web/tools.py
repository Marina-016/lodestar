"""Small host capabilities for model-led task and memory decisions."""
from __future__ import annotations

import hashlib
import json
import time

from lodestar.memory import assistant, learning
from lodestar.web import memory



def _task_transcript(ws, item, run, rows):
    """Link messages to owned runs and disclose bounded transcript coverage."""
    parsed = [(row, json.loads(row['metadata'])) for row in reversed(rows)]
    run_ids = {meta.get('run_id') for _, meta in parsed if isinstance(meta.get('run_id'), str)}
    linked_runs = {}
    if run_ids:
        placeholders = ','.join('?' for _ in run_ids)
        linked_runs = {r['id']: r['status'] for r in ws.conn.execute(
            f'SELECT id,status FROM web_runs WHERE item_id=? AND id IN ({placeholders})',
            [item['id'], *run_ids])}
    messages = []
    budget = 12000
    marker = '\n[Middle of message omitted; only beginning and ending retained]\n'
    for row, meta in parsed:
        original = row['content']
        truncated = len(original) > budget
        content = original
        if truncated:
            remaining = budget-len(marker)
            head = remaining*2//3
            content = original[:head]+marker+original[-(remaining-head):]
        recorded = meta.get('run_id')
        linked = recorded if isinstance(recorded, str) and recorded in linked_runs else None
        messages.append({'id': row['id'], 'role': row['role'], 'kind': row['kind'],
            'run_id': linked, 'run_status': linked_runs.get(linked),
            'run_link': 'current_run' if linked and run and linked == run['id'] else
                        'earlier_run' if linked else 'unlinked',
            'content': content, 'original_content_chars': len(original), 'content_truncated': truncated,
            'content_coverage': 'head_and_tail_excerpt' if truncated else 'complete_message',
            'sources': meta.get('source_details', [])})
    visible = ws.conn.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=? AND id>?',
                              (item['conversation_id'], item['context_after'])).fetchone()[0]
    current = [m for m in messages if m['run_link'] == 'current_run']
    return messages, {'visible_messages': visible, 'returned_messages': len(messages),
        'message_limit': 6, 'messages_truncated': visible > len(messages),
        'content_char_budget_per_message': budget,
        'truncated_message_ids': [m['id'] for m in messages if m['content_truncated']],
        'current_run_message_ids': [m['id'] for m in current],
        'current_run_assistant_message_ids': [m['id'] for m in current if m['role'] == 'assistant'],
        'note': 'Bounded task transcript, not the entire history. Unlinked messages are not assigned to the latest run. '
                'No returned current-run message does not prove no result exists. Terminal status includes failure/cancellation. '
                'Source entries are metadata observed by this task, not delivery of their original bodies to the reader.'}


def assistant_tools(app, item, run_id, message, check_cancel, *, user_authored=True, execution_policy=None):
    def definition(description, properties, handler, required=()):
        return {'description': description, 'input_schema': {'type': 'object',
            'properties': properties, 'required': list(required), 'additionalProperties': False},
            'handler': handler}

    def read_memory(args):
        with app.workspace() as ws, ws.conn:
            ws.conn.execute('BEGIN')  # Counts and bounded results share one read snapshot.
            current = app.item(ws, item['id'])
            rows = memory.notes(ws, current, active_only=True)
            visible_notes = len(rows)
            active_learning = ws.conn.execute(
                'SELECT COUNT(*) FROM learning_events WHERE user_id=? AND revoked_at IS NULL',
                (memory.USER,)).fetchone()[0]
            query = args.get('query', '').strip()
            if query:
                from lodestar.retrieval import terms
                wanted = terms(query)
                rows = sorted(rows, key=lambda r: -len(wanted & terms(r['content'])))
                rows = [r for r in rows if wanted & terms(r['content'])]
            matched_notes = len(rows)
            memories = assistant.select_rows(rows, query, budget=12000, limit=30)
            recall = learning.retrieve(ws.conn, memory.USER, query or None, limit=8 if query else 20)
            learning_packet = learning.model_context(recall['profiles'])
            recalled = learning_packet['profiles']
            return {'memories': memories, 'learning': recalled,
                    'learning_context': learning_packet['coverage'],
                    'retrieval': {'query': query, 'filter_applied': bool(query),
                        'visible_active_notes': visible_notes, 'user_active_learning_events': active_learning,
                        'matching_memories': matched_notes, 'memories_truncated': len(memories) < matched_notes,
                        'user_active_learning_profiles': recall['active_profiles'],
                        'matching_learning_profiles': recall['matching_profiles'],
                        'learning_profiles_truncated': recall['matching_profiles'] > len(recalled),
                        'filter_fields': {'memories': ['content'], 'learning': ['technology', 'method']},
                        'returned_memories': len(memories), 'returned_learning_profiles': len(recalled),
                        'note': 'Query filters lexically; no matches does not mean no stored records. '
                                'Omit query for a bounded overview. Truncation flags distinguish matched-but-omitted '
                                'records from no matches; an overview is not necessarily the whole profile.'}}

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
            app.record_action(ws, run_id, {'action': 'manage_memory', 'id': result['id'],
                'operation': args['action'], **{k: result[k] for k in ('revision', 'deleted') if k in result}})
            result['managed_memory'] = assistant.prompt_rows(assistant.select_rows(memory.notes(ws, current, active_only=True), message))
            return result

    def list_tasks(args):
        with app.workspace() as ws:
            return {'tasks': [{k: row[k] for k in ('id', 'name', 'status', 'run_status', 'project_id', 'parent_id')}
                              for row in app.task_list(ws) if row['id'] != 'main']}

    def read_task(args):
        wait_seconds = args.get('wait_seconds', 10)
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 20:
            raise ValueError('wait_seconds must be an integer from 0 to 20')
        started = time.monotonic()
        deadline = started + wait_seconds
        while True:
            check_cancel()
            with app.workspace() as ws, ws.conn:
                # A read transaction keeps the selected run and transcript in one
                # snapshot, even if a new run starts while we assemble the result.
                ws.conn.execute('BEGIN')
                other = app.item(ws, args['id'])
                run = ws.conn.execute('SELECT id,status FROM web_runs WHERE item_id=? ORDER BY rowid DESC LIMIT 1', (other['id'],)).fetchone()
                active = run is not None and run['status'] in ('queued', 'running', 'cancelling')
                if not active or time.monotonic() >= deadline:
                    check_cancel()
                    rows = ws.conn.execute('SELECT id,role,kind,content,metadata FROM messages WHERE conversation_id=? AND id>? ORDER BY id DESC LIMIT 6',
                                            (other['conversation_id'], other['context_after'])).fetchall()
                    check_cancel()
                    messages, transcript = _task_transcript(ws, other, run, rows)
                    outcome = ('not_started' if run is None else 'terminal' if not active
                               else 'snapshot' if wait_seconds == 0 else 'timeout')
                    return {'id': other['id'], 'name': other['name'], 'status': other['status'],
                            'run_id': run['id'] if run else None, 'run_status': run['status'] if run else None,
                            'messages': messages, 'transcript': transcript,
                            'wait': {'requested_seconds': wait_seconds,
                                     'elapsed_seconds': round(max(0, time.monotonic()-started), 3),
                                     'reached_terminal': run is not None and not active,
                                     'outcome': outcome}}
            # Neither app.lock nor a SQLite read transaction is held here.
            check_cancel()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(.1, remaining))

    def create_task(args):
        check_cancel()
        quotes = args['constraints']
        if (type(quotes) is not list or len(quotes) > 8 or
                any(type(q) is not str or not q.strip() or len(q) > 500 or q not in message for q in quotes)):
            raise ValueError('约束须为本轮用户消息的原文摘录，不可编造或引用其他对话')
        if args['external_retrieval'] not in ('allowed', 'blocked'):
            raise ValueError('外部检索权限无效')
        with app.workspace() as ws:
            source = ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                     (item['conversation_id'],)).fetchone()
        policy = {'external_retrieval': 'blocked' if (execution_policy or {}).get('external_retrieval') == 'blocked'
                  else args['external_retrieval'], 'constraints': quotes,
                  'parent_run_id': run_id, 'source_message_id': source['id'] if source else None}
        key = run_id + ':' + hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        project = args.get('project_id', item['project_id'])
        task_id = app.create(args['name'], project, parent_id=item['id'], dispatch_key=key)
        with app.workspace() as ws:
            child = app.item(ws, task_id)
        receipt = {'action': 'create_task', 'id': task_id, 'name': child['name']}
        try:
            if not child['settings'] or child['settings'] == '{}':
                app.update(task_id, {'settings': json.loads(item['settings'])})
            check_cancel()
            instruction = args['instruction']
            if quotes:
                instruction += '\n\nUser constraints (verbatim from the current delegation request):\n' + json.dumps(quotes, ensure_ascii=False)
            child_run = app.submit(task_id, instruction, key, background=True, policy=policy)
        except Exception:
            with app.lock, app.workspace() as ws, ws.conn:
                app.record_action(ws, run_id, {**receipt, 'status': 'created_not_submitted'})
            raise
        with app.lock, app.workspace() as ws, ws.conn:
            app.record_action(ws, run_id, {**receipt, 'run_id': child_run, 'status': 'submitted',
                                        'external_retrieval': policy['external_retrieval']})
        return {'id': task_id, 'run_id': child_run, 'name': child['name'], 'status': 'submitted', 'policy': policy,
                'note': 'Task runs separately. Its output is research material, not evidence of user mastery. Use read_task to retrieve its result.'}

    def record_learning(args):
        check_cancel()
        action = args.get('action', 'record')
        with app.lock, app.workspace() as ws:
            row = ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1", (item['conversation_id'],)).fetchone()
            if row is None:
                raise ValueError('缺少用户消息证据')
            if action == 'record':
                if not args.get('technology') or not args.get('event') or 'id' in args:
                    raise ValueError('新增需要 technology 和 event；纠正或撤销请指定 action 和已有证据 ID')
                result = learning.record(ws.conn, user_id=memory.USER, technology=args['technology'],
                    method=args.get('method', ''), event=args['event'], actor='user',
                    evidence=json.dumps({'message_id': row['id'], 'content': message}, ensure_ascii=False))
                with ws.conn:
                    app.record_action(ws, run_id, {'action': 'record_learning', 'evidence_id': result['evidence_id'], 'event': args['event']})
                return result
            if action not in ('correct', 'revoke') or type(args.get('id')) is not int or args['id'] < 1:
                raise ValueError('纠正或撤销需要 action 和已有证据 ID，请先读取学习记忆')
            if action == 'revoke' and any(k in args for k in ('technology', 'method', 'event')):
                raise ValueError('撤销只需要证据 ID')
            if action == 'correct' and 'event' in args and args['event'] not in ('discussed', 'attempted', 'self_report'):
                raise ValueError('纠正的 event 无效；纠正结果始终保存为自评')
            # A schema-valid redundant event does not control the replacement:
            # edit_learning attaches the user message and always uses self_report.
            data = {k: v for k, v in args.items() if k in ('action', 'id', 'technology', 'method')}
            data['evidence'] = message
            with ws.conn:
                result = memory.edit_learning(ws, data, allow_run=run_id, source_message_id=row['id'])
                app.record_action(ws, run_id, {'action': 'record_learning', 'operation': action,
                    'id': args['id'], **{k: result[k] for k in ('evidence_id', 'event', 'revoked') if k in result}})
            current = app.item(ws, item['id'])
            learning_packet = learning.model_context(learning.profile(ws.conn, memory.USER, limit=20))
            result['learning'] = learning_packet['profiles']
            result['learning_context'] = learning_packet['coverage']
            result['managed_memory'] = assistant.prompt_rows(assistant.select_rows(memory.notes(ws, current, active_only=True), message))
            return result

    tools = {
        'read_memory': definition('Read visible active personal/project/task facts and your user\'s learning evidence. query is an optional lexical filter; omit it for a bounded overview. Counts cover this space\'s visible notes, the current user\'s events and distinct technology/method profiles. Match counts and truncation flags disclose omitted records. Long evidence may be returned as an explicit omitted-text reference. Empty filtered results do not imply empty memory.',
                                 {'query': {'type': 'string'}}, read_memory),
        'manage_memory': definition('Save a durable user preference/fact, or correct/forget an existing memory by ID. Use only user-provided facts or confirmed project decisions. Never store technology definitions, infer mastery, or turn background reading into user exposure. scope defaults to personal; project requires a bound project; task keeps a local note. Read existing IDs before modifying them.',
            {'action': {'type': 'string', 'enum': ['save', 'forget']}, 'id': {'type': 'string'},
             'content': {'type': 'string', 'maxLength': 2000},
             'scope': {'type': 'string', 'enum': ['personal', 'project', 'task']}}, manage_memory, ['action']),
        'list_tasks': definition('List task names and current execution states without loading their transcripts.', {}, list_tasks),
        'read_task': definition('Read a selected task\'s recent results and source metadata when relevant to the user request. If active, wait up to wait_seconds (default 10, maximum 20) without another model call; 0 returns immediately. The wait outcome and run state are host observations, not a promise of completion. A timeout does not cancel the task. Message run links, kind and truncation are disclosed; source metadata does not mean you read the original bodies. Old context retired by memory corrections is excluded.',
                                {'id': {'type': 'string'},
                                 'wait_seconds': {'type': 'integer', 'minimum': 0, 'maximum': 20}}, read_task, ['id']),
    }
    if user_authored:
        tools['record_learning'] = definition('Record, correct or revoke the current user\'s technology/method learning evidence. action defaults to record and requires technology and event. For correct/revoke, read_memory first to find the evidence id. Corrections always become self_report; an optional discussed/attempted/self_report event is ignored and never upgrades mastery. Revoke accepts only action and id. The host attaches the actual user message and retires stale context after edits. Never record an assistant answer or research assignment as user learning.',
            {'action': {'type': 'string', 'enum': ['record', 'correct', 'revoke']},
             'id': {'type': 'integer', 'minimum': 1},
             'technology': {'type': 'string', 'minLength': 1, 'maxLength': 200},
             'method': {'type': 'string', 'maxLength': 300},
             'event': {'type': 'string', 'enum': ['discussed', 'attempted', 'self_report']}},
            record_learning)
    if item['id'] == 'main':
        tools['create_task'] = definition('Dispatch separate research work only when the user asks. Give a self-contained instruction preserving relevant term meanings, not just topic names. constraints must quote all applicable constraints verbatim from the CURRENT user message; [] if none. Set external_retrieval=blocked if search/network is prohibited; the host enforces it for this child run. Do not omit constraints to enable tools. Only instruction and selected quotes reach the child, not parent history. Personal and bound project memory are shared. Project ID defaults to the current project. Submission is not completion; read_task returns progress/results.',
            {'name': {'type': 'string', 'minLength': 1, 'maxLength': 80},
             'instruction': {'type': 'string', 'minLength': 1, 'maxLength': 14000},
             'constraints': {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}, 'maxItems': 8},
             'external_retrieval': {'type': 'string', 'enum': ['allowed', 'blocked']},
             'project_id': {'type': 'integer', 'minimum': 1}}, create_task, ['name', 'instruction', 'constraints', 'external_retrieval'])
    return tools
