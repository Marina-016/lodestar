"""Persistent headless conversation service with owner-scoped research evidence."""
from __future__ import annotations

import json
import uuid

from lodestar.agent.loop import ResearchAgent
from lodestar.agent.project_plan import generate
from lodestar.memory import learning, repo




class ConversationAgent:
    def __init__(self, ws, llm):
        self.ws, self.llm = ws, llm

    def start(self, user_id='default', project_id=None):
        if not user_id.strip():
            raise ValueError('user_id is required')
        if project_id is not None and not any(p['id'] == project_id for p in repo.list_projects(self.ws.conn)):
            raise ValueError('unknown registered project')
        conversation_id = uuid.uuid4().hex[:12]
        repo.create_conversation(self.ws.conn, conversation_id)
        with self.ws.conn:
            self.ws.conn.execute('INSERT INTO agent_sessions(conversation_id,user_id,project_id) VALUES(?,?,?)',
                                 (conversation_id, user_id, project_id))
        return conversation_id

    def _session(self, conversation_id, user_id):
        row = self.ws.conn.execute('SELECT * FROM agent_sessions WHERE conversation_id=? AND user_id=?',
                                   (conversation_id, user_id)).fetchone()
        if row is None:
            raise ValueError('session not found for this user')
        return dict(row)

    def history(self, conversation_id, user_id='default', limit=20):
        self._session(conversation_id, user_id)
        rows = self.ws.conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?',
                                    (conversation_id, max(1, min(limit, 50)))).fetchall()
        return [dict(row) for row in reversed(rows)]

    def turn(self, conversation_id, message, **kwargs):
        from lodestar.llm import LLMError
        self._session(conversation_id, kwargs.get('user_id', 'default'))
        try:
            return self._turn(conversation_id, message, **kwargs)
        except LLMError:
            answer = '模型调用失败，已保留会话与原始证据，可以重试。'
            repo.add_message(self.ws.conn, conversation_id, 'assistant', answer, kind='error')
            return {'status': 'error', 'conversation_id': conversation_id, 'answer': answer}

    def _turn(self, conversation_id, message, *, user_id='default', intent='auto',
             technology=None, method='', feedback='discussed', days=7):
        session = self._session(conversation_id, user_id)
        if not message.strip():
            raise ValueError('message is required')
        from lodestar.agent.routing import route
        decision = route(message)
        automatic = intent == 'auto'
        if automatic:
            intent = decision.intent
            if intent == 'research':
                # Conversational news/search requests use the same adaptive loop.
                # The full report pipeline remains available via explicit research.
                intent = 'followup'
            if intent == 'feedback':
                feedback = 'self_report'
                if not technology:
                    # Use an explicit topic from the latest message, never invent a concept.
                    for previous in reversed(self.history(conversation_id, user_id)):
                        metadata = json.loads(previous.get('metadata') or '{}')
                        if previous['role'] != 'user':
                            continue
                        if metadata.get('technology'):
                            technology = metadata['technology']
                            method = metadata.get('method', '')
                            break
                        # A newer unlabelled topic cannot inherit an older label.
                        break
                if not technology:
                    intent = 'followup'
            if intent == 'plan' and session['project_id'] is None:
                # Discuss a provisional approach; a grounded project plan still
                # requires explicit project binding through the plan interface.
                intent = 'followup'
        if intent not in {'research', 'followup', 'plan', 'feedback'}:
            raise ValueError('unsupported intent')
        if intent == 'feedback' and (not technology or feedback not in {'discussed', 'self_report', 'attempted'}):
            raise ValueError('feedback requires a technology and a non-mastery observation')
        if intent == 'plan' and session['project_id'] is None:
            raise ValueError('select a registered project when starting this session')
        user_message = repo.add_message(self.ws.conn, conversation_id, 'user', message,
                                        metadata={'intent': intent, 'technology': technology, 'method': method})
        if intent == 'feedback':
            observation = learning.record(self.ws.conn, user_id=user_id, technology=technology,
                method=method, event=feedback, actor='user', task_id=session['task_id'],
                evidence=json.dumps({'message_id': user_message['id'], 'content': message}, ensure_ascii=False))
            answer = '已记录接触/自评证据；没有验证依据，不升级掌握程度。'
            result = {'evidence': observation}
        elif intent == 'research':
            result = ResearchAgent(self.ws, llm=self.llm, judge=self.llm, interactive=False).run(
                message, user_id=user_id, project_id=session['project_id'], discovery_days=days,
                apply_updates='pending', learning_technology=technology)
            if result.get('error'):
                if result.get('read_sources'):
                    with self.ws.conn:
                        self.ws.conn.execute('UPDATE agent_sessions SET task_id=?, evidence=? WHERE conversation_id=?',
                            (result['task_id'], json.dumps(result['read_sources'], ensure_ascii=False), conversation_id))
                repo.add_message(self.ws.conn, conversation_id, 'assistant', result['error'], kind='error')
                return result
            answer = result['brief_md']
            with self.ws.conn:
                self.ws.conn.execute('UPDATE agent_sessions SET task_id=?, evidence=? WHERE conversation_id=?',
                    (result['task_id'], json.dumps(result.get('read_sources', []), ensure_ascii=False), conversation_id))
        else:
            sources = json.loads(session['evidence'])
            supplements = []
            if intent == 'plan' and sources:
                from dataclasses import replace
                from types import SimpleNamespace
                from lodestar.tools.registry import call_tool
                read_ws = SimpleNamespace(config=replace(self.ws.config, full_text_enabled=True), conn=self.ws.conn)
                from lodestar.trace.recorder import Trace
                trace = Trace(self.ws.conn, session['task_id'], self.ws.config.workspace_dir) if session['task_id'] else None
                for index, source in enumerate(sources[:2]):
                    if source.get('source_type') != 'paper':
                        continue
                    params = {'url': source['url'], 'full_text': True,
                              'query': message, 'char_budget': self.ws.config.read_char_budget}
                    if trace:
                        trace.tool_call('read_paper', params)
                    read = call_tool(read_ws, 'read_paper', params)
                    if trace:
                        trace.tool_result('read_paper', read)
                    supplements.append({'url': source['url'], 'error': read.get('error'),
                                        'coverage': read.get('coverage'), 'read_depth': read.get('read_depth')})
                    if (not read.get('error') and read.get('text')
                            and read.get('query_matched') is not False
                            and not (source.get('read_depth') == 'full' and read.get('read_depth') != 'full')):
                        sources[index] = {**source, 'content': read['text'],
                            'read_depth': read.get('read_depth'), 'coverage': read.get('coverage'),
                            'evidence_spans': read.get('evidence_spans', [])}
                with self.ws.conn:
                    self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                        (json.dumps(sources, ensure_ascii=False), conversation_id))
                if trace:
                    trace.dump_jsonl()
            if intent == 'plan':
                result = generate(self.ws, self.llm, message, sources, session['project_id'])
                answer = result['plan']
            else:
                context = {'message': message,
                    'require_sources': decision.intent == 'research',
                    'history': [{'role': m['role'], 'content': m['content'][:3000]} for m in self.history(conversation_id,user_id,8)],
                    'learning': learning.recall(self.ws.conn, message + ' ' + (technology or ''), user_id),
                    'papers': [{**s, 'content': s.get('content', '')[:12000]} for s in sources[:5]],
                    'supplement_reads': supplements}
                from lodestar.agent.dialogue import gather
                context = gather(self.ws, self.llm, context, session['project_id'])
                events = context.pop('dialogue_events')
                sources = context['papers']
                supplements = [{'url': event['params']['url'],
                                'error': event['result'].get('error'),
                                'coverage': event['result'].get('coverage'),
                                'read_depth': event['result'].get('read_depth')}
                               for event in events if event.get('action') == 'read_paper'
                               and event.get('params', {}).get('url')]
                with self.ws.conn:
                    self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                        (json.dumps(sources, ensure_ascii=False), conversation_id))
                if session['task_id']:
                    from lodestar.trace.recorder import Trace
                    trace = Trace(self.ws.conn, session['task_id'], self.ws.config.workspace_dir)
                    for event in events:
                        if 'result' in event:
                            trace.tool_call(event['action'], event['params'])
                            trace.tool_result(event['action'], event['result'])
                    trace.dump_jsonl()
                from lodestar.agent.explanation import explain
                answer, grounding = explain(self.llm, context)
                from lodestar.agent.scope import annotate_scope
                answer = annotate_scope(answer)
                result = {'status': 'answered', 'evidence_reused': len(sources), 'supplement_reads': supplements,
                          'grounding': grounding, 'dialogue_events': events}
        assistant_message = repo.add_message(self.ws.conn, conversation_id, 'assistant', answer,
                         task_id=result.get('task_id') or session['task_id'], metadata={'intent': intent, 'grounding': result.get('grounding'), 'dialogue_events': result.get('dialogue_events')})
        if intent == 'followup':
            from lodestar.agent.exposure import record_exposure
            from lodestar.llm import LLMError
            try:
                grounding = result.get('grounding') or {}
                exposure_text = ('\n\n'.join(claim['text'] for claim in grounding['validated_claims'])
                    if 'validated_claims' in grounding else answer)
                result['learning_exposure'] = record_exposure(self.ws, self.llm, session['task_id'],
                    user_id, exposure_text, sources, technology=technology, goal=message)
                result['learning_exposure_status'] = 'recorded' if result['learning_exposure'] else 'no_supported_methods'
            except (LLMError, ValueError, TypeError, AttributeError) as error:
                result['learning_exposure_status'] = 'error'
                result['learning_exposure_error'] = type(error).__name__
                result['warning'] = '讲解已保留，方法接触记录更新未完成；未提升掌握程度。'
            metadata = {'intent': intent, 'grounding': result.get('grounding'), 'dialogue_events': result.get('dialogue_events'), **{k: v for k, v in result.items()
                if k.startswith('learning_exposure')}}
            with self.ws.conn:
                self.ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?',
                    (json.dumps(metadata, ensure_ascii=False), assistant_message['id']))
        return {'status': 'ok', **result, 'conversation_id': conversation_id, 'answer': answer, 'intent': intent,
                'route_reason': decision.reason if automatic else 'explicit intent'}
