"""Isolated HTTP -> agent -> model acceptance; semantic quality requires review.

Default is offline plumbing, not autonomous-model acceptance. Live runs require
explicit --live, respect the model-disable switch, and never use paid search.
Artifacts persist in a fresh ignored directory; no real Web database is opened.
Optional DashScope token protection reserves conservative local estimates. It is
not a supplier billing bound or account balance, and cannot cover other clients.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
from hashlib import sha256
import importlib
import json
import re
import threading
import time
import traceback
import uuid
from urllib.parse import urlsplit
from dataclasses import replace
from pathlib import Path

import requests

from lodestar.chat_settings import configure, snapshot
from lodestar.config import PROJECT_ROOT, load_config
from lodestar.llm import LLMClient, LLMError, error_details
from lodestar.web.server import WebApp, make_server


class ModelBudget:
    """Count physical inference attempts across foreground/background clients."""
    def __init__(self, limit, max_total_tokens=None):
        if not 1 <= limit <= 100:
            raise ValueError('Model attempt limit must be 1-100')
        if max_total_tokens is not None and (type(max_total_tokens) is not int or max_total_tokens < 1):
            raise ValueError('max_total_tokens must be a positive integer')
        self.limit, self.used, self.blocked = limit, 0, 0
        self.lock = threading.Lock()
        self.stopped = False
        self.failures = []
        self.responses = []
        self.max_total_tokens = max_total_tokens
        self.token_used, self.token_blocked, self.token_stopped = 0, 0, False
        self.last_denial = None
        self.token_reservations = {}
        self._token_local = threading.local()

    @staticmethod
    def estimate_tokens(request):
        """Decoded JSON UTF-8 bytes + output/reasoning allowances + overhead.

        Only these transient local variables contain the request body. This is a
        conservative estimate, not a tokenizer or a supplier billing guarantee.
        """
        body = request.body
        if isinstance(body, str):
            body = body.encode('utf-8')
        if not isinstance(body, bytes):
            raise ValueError('Expected a JSON request body')
        payload = json.loads(body.decode('utf-8'))
        ceiling = payload.get('max_tokens') if isinstance(payload, dict) else None
        if type(ceiling) is not int or ceiling < 1:
            raise ValueError('Expected a positive max_tokens')
        if payload.get('enable_thinking') is not False:
            thinking = payload.get('thinking_budget')
            if payload.get('enable_thinking') is not True or type(thinking) is not int or thinking < 1:
                raise ValueError('Expected explicit disabled or bounded thinking')
            ceiling += thinking
        # Wire escaping/whitespace must not change the decoded prompt estimate.
        normalized = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return len(normalized) + ceiling + 1024

    def finish_tokens(self, usage=None, *, success=False):
        """Refund only complete successful calls with coherent reported usage."""
        if self.max_total_tokens is None:
            return
        attempt = getattr(self._token_local, 'attempt', None)
        with self.lock:
            record = self.token_reservations.get(attempt)
            if not record or record['state'] != 'reserved':
                return
            if not success:
                record['state'] = 'error_reserved'
                return
            if not isinstance(usage, dict) or not usage:
                record['state'] = 'unknown_usage_reserved'
                return
            totals = []
            valid = True
            for left, right in (('input_tokens', 'output_tokens'), ('prompt_tokens', 'completion_tokens')):
                if left in usage or right in usage:
                    values = (usage.get(left), usage.get(right))
                    if any(type(value) is not int or value < 0 for value in values):
                        valid = False
                    else:
                        totals.append(sum(values))
            if 'total_tokens' in usage:
                value = usage['total_tokens']
                if type(value) is not int or value < 0:
                    valid = False
                else:
                    totals.append(value)
            if not valid or (totals and (len(set(totals)) != 1 or totals[0] <= 0)):
                record['state'] = 'invalid_usage_reserved'
                self.token_stopped = True
            elif not totals:
                record['state'] = 'unknown_usage_reserved'
            else:
                actual = totals[0]
                self.token_used += actual-record['reserved_tokens']
                record.update(accounted_tokens=actual, reported_tokens=actual,
                    state='reported_usage' if actual <= record['reserved_tokens'] else 'usage_exceeded_reservation')
                if actual > record['reserved_tokens']:
                    self.token_stopped = True

    def token_report(self):
        with self.lock:
            return {'enabled': self.max_total_tokens is not None, 'max_total_tokens': self.max_total_tokens,
                    'accounted_tokens': self.token_used, 'blocked_attempts': self.token_blocked,
                    'stopped': self.token_stopped,
                    'last_denial': dict(self.last_denial) if self.last_denial else None,
                    'attempts': [{'attempt': attempt, **record}
                                 for attempt, record in self.token_reservations.items()],
                    'scope': 'This acceptance run only; no account balance or other-client consumption is covered.',
                    'estimate': 'Decoded JSON reserialized with ensure_ascii=False, UTF-8 bytes '
                                '+ max_tokens + explicit thinking allowance + 1024. '
                                'A conservative local estimate, not a supplier billing upper bound. '
                                'Errors, interrupted calls and unknown usage retain the full reservation. '
                                'Protected runs require explicit thinking off or a positive fixed thinking budget; '
                                'default/effort settings are refused only by this local acceptance guard.'}

    def reserve(self, request):
        path = request.url if isinstance(request.url, str) else request.url.path
        path = urlsplit(path).path.rstrip('/')
        if request.method != 'POST' or not path.endswith(('/messages', '/chat/completions')):
            return
        estimate = None
        if self.max_total_tokens is not None:
            try:
                estimate = self.estimate_tokens(request)
            except (ValueError, TypeError, UnicodeError):
                with self.lock:
                    self.token_blocked += 1
                    self.token_stopped = True
                    self.last_denial = {'reason': 'unsafe_estimate', 'estimate': None,
                        'accounted': self.token_used, 'remaining': max(0, self.max_total_tokens-self.token_used)}
                raise LLMError('Acceptance request has no safe token estimate', code='acceptance_token_budget') from None
        with self.lock:
            if self.stopped or self.used >= self.limit:
                self.blocked += 1
                raise LLMError('Acceptance model-attempt limit reached', code='acceptance_budget')
            if estimate is not None and (self.token_stopped or self.token_used + estimate > self.max_total_tokens):
                self.token_blocked += 1
                self.last_denial = {'reason': 'stopped' if self.token_stopped else 'insufficient_reservation',
                    'estimate': estimate, 'accounted': self.token_used,
                    'remaining': max(0, self.max_total_tokens-self.token_used)}
                raise LLMError('Acceptance token reservation limit reached', code='acceptance_token_budget')
            self.used += 1
            if estimate is not None:
                self.token_used += estimate
                self.token_reservations[self.used] = {'reserved_tokens': estimate, 'accounted_tokens': estimate,
                    'reported_tokens': None, 'state': 'reserved'}
                self._token_local.attempt = self.used
            if isinstance(getattr(request, 'extensions', None), dict):
                request.extensions['lodestar_attempt'] = self.used
            return self.used

    def observe_response(self, response):
        """Transport shape only: no text, reasoning, other headers or URLs."""
        if response.request.method != 'POST' or not response.request.url.path.rstrip('/').endswith('/messages'):
            return
        record = {'attempt': response.request.extensions.get('lodestar_attempt'),
                  'http_status': response.status_code,
                  'content_type': response.headers.get('content-type', '').split(';')[0][:80],
                  'bytes_received': 0, 'body_shape': None, 'sse_events': {}, 'data_types': {}, 'closed': False}
        with self.lock:
            self.responses.append(record)
        original, budget = response.stream, self
        # The installed SDK may use httpx2; use its actual stream base class.
        http = importlib.import_module(type(response).__module__.split('.')[0])
        known = {'message_start', 'message_delta', 'message_stop', 'content_block_start',
                 'content_block_delta', 'content_block_stop', 'ping', 'error'}
        class ObservedStream(http.SyncByteStream):
            def __iter__(self):
                pending = b''
                sniff = b''
                for chunk in original:
                    with budget.lock:
                        record['bytes_received'] += len(chunk)
                        if record['body_shape'] is None:
                            sniff = (sniff+chunk)[:128]
                            head = sniff.lstrip()
                            record['body_shape'] = ('sse' if head.startswith((b'event:', b'data:', b':')) else
                                'json_like' if head.startswith((b'{', b'[')) else 'html_like' if head.startswith(b'<') else
                                'other' if len(head) >= 6 else None)
                        pending += chunk
                        lines = pending.split(b'\n')
                        pending = lines.pop()[-65536:]  # Diagnostic parser only; never alter delivered bytes.
                        for line in lines:
                            if len(line) > 65536:
                                continue
                            field, _, value = line.partition(b':')
                            kind = None
                            if field == b'event':
                                kind = value.strip().decode('ascii', errors='ignore')
                                counts = record['sse_events']
                            elif field == b'data':
                                try:
                                    payload = json.loads(value)
                                    kind = payload.get('type') if isinstance(payload, dict) else None
                                except (ValueError, UnicodeError):
                                    continue
                                counts = record['data_types']
                            if kind is not None:
                                key = kind if isinstance(kind, str) and kind in known else 'other'
                                counts[key] = counts.get(key, 0)+1
                    yield chunk

            def close(self):
                try:
                    original.close()
                finally:
                    with budget.lock:
                        record['closed'] = True
        response.stream = ObservedStream()

    def client(self, config):
        if config.llm_mode == 'live' and config.llm_provider not in ('anthropic', 'dashscope'):
            raise ValueError('This acceptance request guard supports Anthropic and DashScope only')
        if self.max_total_tokens is not None and config.llm_mode == 'live' and config.llm_provider != 'dashscope':
            raise ValueError('Optional acceptance token protection currently supports DashScope only')
        budget = self
        class ObservedClient(LLMClient):
            def _complete_live(self, *args, **kwargs):
                if budget.max_total_tokens is None:
                    return super()._complete_live(*args, **kwargs)
                budget._token_local.attempt = None
                before = len(self._dashscope.usage)
                try:
                    result = super()._complete_live(*args, **kwargs)
                except Exception:
                    budget.finish_tokens()
                    raise
                usage = self._dashscope.usage[-1] if len(self._dashscope.usage) > before else None
                budget.finish_tokens(usage, success=True)
                return result

            def dialogue_step(self, *args, **kwargs):
                if budget.max_total_tokens is not None:
                    budget._token_local.attempt = None
                try:
                    result = super().dialogue_step(*args, **kwargs)
                    observation = self.last_dialogue_observation or {}
                    budget.finish_tokens(observation.get('usage'), success=True)
                    return result
                except LLMError as error:
                    budget.finish_tokens()
                    cause = error.__cause__ or error
                    frames = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                              for f in traceback.extract_tb(cause.__traceback__)[-8:]]
                    with budget.lock:
                        budget.failures.append({**error_details(error), 'frames': frames})
                    raise
        class GuardedSession(requests.Session):
            def send(self, request, **kwargs):
                attempt = budget.reserve(request)
                if attempt is not None:
                    kwargs['allow_redirects'] = False  # A redirect must not trigger a second inference.
                response = super().send(request, **kwargs)
                if attempt is not None:
                    # No response body, prompt, credential or URL is retained here.
                    # SSE body-shape diagnostics above remain Anthropic-specific.
                    record = {'attempt': attempt, 'protocol': 'openai_compatible',
                              'http_status': response.status_code,
                              'content_type': response.headers.get('content-type', '').split(';')[0][:80],
                              'body_observation': 'not_collected'}
                    with budget.lock:
                        budget.responses.append(record)
                return response

        client = ObservedClient(config)
        if client._dashscope is not None:
            # A separate session per client avoids global requests monkeypatches
            # and shares the physical-attempt counter across foreground/children.
            client._dashscope.http = GuardedSession()
        if client._client is not None:
            from anthropic import DefaultHttpxClient
            original = client._client
            client._client = original.with_options(max_retries=0,
                http_client=DefaultHttpxClient(timeout=config.llm_timeout_s,
                                              event_hooks={'request': [self.reserve], 'response': [self.observe_response]}))
            original.close()
        return client


def run(config, output_dir, *, scenario='direct', max_model_calls=24, max_total_tokens=None, wait_seconds=180, llm_factory=None):
    if scenario not in ('direct', 'research', 'workspace', 'quality', 'learning'):
        raise ValueError('Unknown acceptance scenario')
    if scenario == 'quality' and config.search_mode != 'mock':
        raise ValueError('Quality evidence is fixed and synthetic; live search is not allowed')
    if scenario == 'learning' and config.search_mode != 'mock':
        raise ValueError('Learning uses this isolated conversation; live search is not allowed')
    with ExitStack() as sources:
        fixture = None
        if scenario == 'quality':
            from scripts import quality_fixtures as fixture
            from unittest.mock import patch

            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    value = datetime.fromisoformat(fixture.FIXED_NOW)
                    return value.astimezone(tz) if tz else value

            # Evaluation-only injection; never alter the production tool registry.
            sources.enter_context(patch('lodestar.tools.registry.call_tool', fixture.call_tool))
            sources.enter_context(patch('lodestar.agent.dialogue.verify_publication', fixture.verify_publication))
            sources.enter_context(patch('lodestar.agent.dialogue.datetime', FixedDateTime))
            sources.enter_context(patch('lodestar.agent.evidence.datetime', FixedDateTime))
        return _run(config, output_dir, scenario=scenario, max_model_calls=max_model_calls,
                    max_total_tokens=max_total_tokens, wait_seconds=wait_seconds,
                    llm_factory=llm_factory, fixture=fixture)


def _run(config, output_dir, *, scenario, max_model_calls, max_total_tokens, wait_seconds, llm_factory, fixture):
    if max_total_tokens is not None and config.llm_mode == 'live':
        if config.llm_provider != 'dashscope' or llm_factory is not None:
            raise ValueError('Token protection requires the guarded DashScope acceptance client')
    budget = ModelBudget(max_model_calls, max_total_tokens)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)  # Never reuse a real/user database.
    cfg = replace(config, db_path=root/'web.db', workspace_dir=root, demo_replay=False,
                  project_model_allowed_repository='', project_model_allowed_paths=())
    if cfg.search_mode == 'live' and cfg.web_search_backend != 'duckduckgo':
        raise ValueError('Acceptance only allows public search; paid/MCP search is not enabled')
    frozen_sources = None
    if fixture is not None:
        frozen_sources = json.dumps(fixture.snapshot(), ensure_ascii=False, sort_keys=True, indent=2)
        (root/'source-snapshot.json').write_bytes(frozen_sources.encode('utf-8'))
        # Review answers stay on disk, never in tool results or model context.
        (root/'human-facts.json').write_text(
            json.dumps(fixture.human_facts(), ensure_ascii=False, indent=2), encoding='utf-8')
    app = WebApp(cfg, llm_factory=llm_factory or budget.client)
    server = make_server(app, 0)
    try:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
    except Exception as error:
        # shutdown waits forever if serve_forever never started; only close here.
        try:
            (root/'report.json').write_text(json.dumps({
                'mode': cfg.llm_mode, 'semantic_review': 'not_run',
                'failure_type': type(error).__name__,
                'scope': 'acceptance HTTP server did not start; no model request submitted',
                'checks': {'server_started': False}}, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            server.server_close()
        raise
    base = f'http://127.0.0.1:{server.server_port}'
    client = requests.Session()
    client.trust_env = False
    report = {'configuration': snapshot(cfg), 'mode': cfg.llm_mode, 'search_mode': cfg.search_mode,
              'semantic_review': 'pending' if cfg.llm_mode == 'live' else 'not_applicable_offline_fixture',
              'scope': 'local HTTP/backend; not browser visual acceptance or public deployment',
              'max_model_attempts': max_model_calls, 'model_attempts': 0, 'turns': [], 'checks': {}}
    if fixture is not None:
        report['source_fixture'] = {'id': fixture.FIXTURE_ID, 'synthetic': True,
            'fixed_now': fixture.FIXED_NOW, 'sha256': sha256(frozen_sources.encode('utf-8')).hexdigest(),
            'snapshot': 'source-snapshot.json', 'human_review_only': 'human-facts.json'}
        report['quality_cards'] = ['Q02', 'Q03', 'Q04']
        report['scope'] += '; fixed synthetic evidence, not real discovery or paper correctness'

    if scenario == 'learning':
        report['semantic_review'] = 'pending'
        report['quality_cards'] = ['Q07']
        report['scope'] += '; isolated learning lifecycle, not automatic semantic verification'

    def learning_snapshot():
        from lodestar.memory import learning
        with app.workspace() as ws:
            rows = ws.conn.execute('SELECT * FROM learning_events WHERE user_id=? ORDER BY id',
                                   (app.user(),)).fetchall()
            events = []
            for row in rows:
                try:
                    evidence = json.loads(row['evidence'])
                except (ValueError, TypeError):
                    evidence = {}
                if not isinstance(evidence, dict):
                    evidence = {}
                message_id = evidence.get('message_id')
                source = ws.conn.execute('SELECT id,role,content FROM messages WHERE id=?',
                                         (message_id,)).fetchone() if type(message_id) is int else None
                events.append({'id': row['id'], 'technology': row['technology'], 'method': row['method'],
                    'event': row['event'], 'actor': row['actor'], 'active': row['revoked_at'] is None,
                    'revoked_at': row['revoked_at'], 'source': {
                        'message_id': message_id, 'corrects': evidence.get('corrects'),
                        'message': dict(source) if source else None}})
            return {'events': events, 'profile': learning.profile(ws.conn, user_id=app.user()),
                    'active_event_ids': [event['id'] for event in events if event['active']],
                    'verified_mastery_event_ids': [event['id'] for event in events if event['event'] in learning.MASTERY],
                    'observation': 'observed_learning_events' if events else 'not_established_no_events'}

    def save():
        with budget.lock:
            report['model_attempts'] = budget.used
            report['blocked_model_attempts'] = budget.blocked
            report['model_failures'] = list(budget.failures)
            report['transport_responses'] = json.loads(json.dumps(budget.responses))
        if max_total_tokens is not None:
            report['token_budget'] = budget.token_report()
        (root/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def get(path, **params):
        response = client.get(base+path, params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    def wait(phase, item_id, run_id):
        started, after, deltas, first_text = time.monotonic(), 0, 0, None
        while time.monotonic()-started < wait_seconds:
            value = get('/api/events', run=run_id, after=after)
            for event in value['events']:
                after = max(after, event['id'])
                if event['kind'] == 'text':
                    deltas += 1
                    if first_text is None:
                        first_text = round(time.monotonic()-started, 3)
            if value['run']['status'] not in ('queued', 'running', 'cancelling'):
                result = json.loads(value['run'].get('result') or '{}')
                record = {'phase': phase, 'item_id': item_id, 'run_id': run_id,
                    'status': value['run']['status'], 'answer': result.get('answer'),
                    'model_error': result.get('model_error'), 'grounding': result.get('grounding'),
                    'sources': result.get('source_details', []),
                    'action_receipts': result.get('action_receipts', []),
                    'execution_policy': json.loads(value['run'].get('policy') or '{}'),
                    'learning_exposure_status': result.get('learning_exposure_status'),
                    'warning': result.get('warning'),
                    'text_deltas': deltas, 'first_observed_text_s': first_text,
                    'observed_wait_s': round(time.monotonic()-started, 3),
                    'actions': [{'action': e.get('action'), 'params': e.get('params'),
                                 'executed': e.get('executed', True), 'error': e.get('result', {}).get('error'),
                                 'repeat_of': e.get('result', {}).get('repeat_of')}
                                for e in result.get('dialogue_events', [])]}
                if scenario == 'learning':
                    # Observe only persisted host state. Never inject expected
                    # answers into tools or score the generated prose here.
                    record['learning_snapshot'] = learning_snapshot()
                report['turns'].append(record)
                save()
                print(json.dumps(record, ensure_ascii=False), flush=True)
                if record['status'] != 'completed':
                    raise RuntimeError('Acceptance run did not complete; inspect the isolated report')
                return record
            time.sleep(.1)
        app.cancel(item_id, run_id)
        raise TimeoutError('Acceptance observation timed out; isolated runs are being cancelled')

    def turn(phase, message):
        response = client.post(base+'/api/send', json={'item': 'main', 'message': message,
                                                     'key': uuid.uuid4().hex}, timeout=5)
        response.raise_for_status()
        return wait(phase, 'main', response.json()['id'])

    try:
        response = client.get(base+'/', timeout=5)
        response.raise_for_status()
        token = re.search(r'<meta name="lodestar-token" content="([^"]+)"', response.text)
        if not token:
            raise RuntimeError('Missing bootstrap token')
        client.headers.update({'X-Lodestar-Token': token.group(1), 'Origin': base})
        if scenario == 'direct':
            turn('direct', '不用工具，用一句话解释 Agent harness，面向刚学编程的人。')
            turn('followup', '把刚才的解释换成餐厅的类比，不要标题。')
        elif scenario == 'research':
            turn('research', '目前在 AI4AI 方面有什么最新的权威论文？只推荐两篇。')
            turn('compare', '比较这两篇的核心机制、实验支持和适用边界，不要只复述摘要；'
                 '说明哪些是论文结论、哪些是你的推断。')
            turn('concise-followup', '不用检索，用两句话概括这两篇最重要的区别，不要标题。')
        elif scenario == 'learning':
            report['learning_initial_snapshot'] = learning_snapshot()
            report['checks']['learning_started_empty'] = not report['learning_initial_snapshot']['events']
            save()
            for phase, message in (
                ('learning-record', '我只看过 ReAct 的思考与行动交替方法，没有自己实现过。请记住这次接触，不要算作掌握。'),
                ('learning-correct', '纠正刚才的记录：其实我只看了摘要，没有认真学，也没有实践。'),
                ('learning-recall', '我接触过哪些技术和方法？目前掌握情况怎样？'),
                ('learning-forget', '请忘记刚才关于 ReAct 的学习证据。'),
                ('learning-recall-after-forget', '现在我接触过哪些技术和方法？还有刚才的学习记录吗？')):
                turn(phase, message)
            observed = [record['learning_snapshot'] for record in report['turns']]
            first, corrected, recalled, forgotten, after = observed
            initial_ids, corrected_ids, recalled_ids = (
                set(value['active_event_ids']) for value in (first, corrected, recalled))
            corrected_retired = {event['id'] for event in corrected['events'] if not event['active']}
            forgotten_retired = {event['id'] for event in forgotten['events'] if not event['active']}
            report['checks'].update(
                learning_evidence_recorded=bool(initial_ids),
                learning_correction_replaced_evidence=bool(initial_ids and corrected_ids and
                    initial_ids <= corrected_retired and initial_ids.isdisjoint(corrected_ids)),
                learning_recall_kept_active_evidence=bool(corrected_ids and recalled_ids == corrected_ids),
                learning_evidence_revoked=bool(recalled_ids and recalled_ids <= forgotten_retired and
                    not forgotten['active_event_ids']),
                learning_remained_empty_after_forget=bool(recalled_ids and not after['active_event_ids'] and
                    not after['profile']),
                learning_no_verified_mastery=all(not value['verified_mastery_event_ids'] for value in observed))
            report['learning_observation'] = ('observed_learning_events' if any(value['events'] for value in observed)
                                              else 'not_established_no_events')
            report['learning_check_scope'] = 'Persisted lifecycle only; generated meaning requires human review.'
        elif scenario == 'quality':
            turn('fixed-discovery', '现在只研究 QF-1 虚构验收材料，不把它们当现实论文。'
                 '推荐近 7 天两篇 Agent 记忆论文，分别说明为什么值得看，给出来源。')
            turn('fixed-compare', '比较刚才两篇怎样管理记忆、各自适用什么场景，'
                 '哪些结论能从实验中看出来？不要只复述摘要。')
            turn('fixed-publication-numbers', '这里 A 是 Window Memory，B 是 Retrieved Memory。'
                 '哪篇更权威？A 的效果提升和成本变化怎么算？能直接比较 A 和 B 谁更好吗？'
                 '核对发表状态，区分事实和你的推断。')
            turn('fixed-concise-followup', '不用再检索，用两句话保留刚才最重要的结论和限制，不要标题。')
            report['observed_coverage'] = {
                'body_reads': [a['params'].get('url') for t in report['turns'] for a in t['actions']
                               if a['action'] == 'read_paper' and a['executed'] and not a['error']
                               and a['params'].get('full_text')],
                'publication_lookups': [a['params'].get('url') for t in report['turns'] for a in t['actions']
                                        if a['action'] == 'verify_paper' and a['executed'] and not a['error']]}
            # Coverage is evidence for a human, never a semantic pass/fail score.
        else:
            turn('dispatch', '请分别派发 Agent harness 和 RAG 两个独立研究任务。'
                 '这里 harness 指模型周围的工具调用、上下文和运行控制。本轮不要联网，'
                 '各用已有知识解释关键机制、适用边界和尚需查证之处。不要把后台研究记成我已经学会了。')
            tasks = [t for t in get('/api/tasks')['items'] if t['id'] != 'main']
            report['checks']['two_tasks_dispatched'] = len(tasks) == 2
            report['checks']['separate_conversations'] = len(tasks) == 2 and len({t['conversation_id'] for t in tasks}) == 2
            receipts = report['turns'][-1]['action_receipts']
            report['checks']['dispatch_receipts_match_tasks'] = len(tasks) == 2 and {r['id'] for r in receipts if r['action'] == 'create_task' and r['status'] == 'submitted'} == {t['id'] for t in tasks}
            constrained, no_external = [], []
            for task in tasks:
                state = get('/api/state', item=task['id'])
                policy = json.loads((state['run'] or {}).get('policy') or '{}')
                constrained.append(policy.get('external_retrieval') == 'blocked' and
                    any('不要联网' in q for q in policy.get('constraints', [])))
            report['checks']['child_no_retrieval_constraint_preserved'] = len(constrained) == 2 and all(constrained)
            save()
            for task in tasks:
                state = get('/api/state', item=task['id'])
                if not state['run']:
                    raise RuntimeError('Dispatched task has no run')
                child = wait('child:'+task['name'], task['id'], state['run']['id'])
                no_external.append(not any(a['executed'] and a['action'] in
                    {'search_papers', 'discover_papers', 'search_web', 'read_paper', 'read_webpage', 'verify_paper'} for a in child['actions']))
            report['checks']['children_did_not_dispatch_external_retrieval'] = len(no_external) == 2 and all(no_external)
            report['checks']['background_did_not_record_user_learning'] = len(tasks) == 2 and not get('/api/state')['learning']
            if tasks:
                turn('summarize', '读取刚才两个任务的结果，用一小段话说明它们在哪些地方互补；不要重新研究。')
            turn('remember', '请记住我的个人研究偏好：先给结论再讲依据，发表状态未知就明确说未知。')
            before = [n for n in get('/api/state')['notes'] if n['status'] == 'active']
            report['checks']['personal_fact_saved'] = bool(before)
            turn('correct', '把刚才那条偏好纠正为：先解释机制，再给结论；发表状态未知就明确说未知。')
            state = get('/api/state')
            after = [n for n in state['notes'] if n['status'] == 'active']
            # Updating in place and forgetting/replacing are both valid host actions.
            # This checks persisted state change, not the meaning of generated prose.
            report['checks']['personal_fact_changed'] = bool(before and after) and (
                {(n['id'], n['content'], n['revision']) for n in before}
                != {(n['id'], n['content'], n['revision']) for n in after})
        report['checks']['all_observed_runs_completed'] = all(t['status'] == 'completed' for t in report['turns'])
    except Exception as error:
        report['failure_type'] = type(error).__name__  # Never serialize SDK bodies or bootstrap secrets.
        report['checks']['all_observed_runs_completed'] = False
    finally:
        with budget.lock:
            budget.stopped = True
        with app.workspace() as ws:
            active = ws.conn.execute("SELECT item_id,id FROM web_runs WHERE status IN ('queued','running','cancelling')").fetchall()
        for item in active:
            app.cancel(item['item_id'], item['id'])
        deadline = time.monotonic()+min(cfg.llm_timeout_s+2, 30)
        while app.cancel_signals and time.monotonic() < deadline:
            time.sleep(.1)
        report['remaining_runs_at_exit'] = len(app.cancel_signals)
        report['checks']['model_attempt_budget_not_exhausted'] = budget.blocked == 0
        if max_total_tokens is not None:
            report['checks']['token_budget_not_exhausted'] = budget.token_blocked == 0 and not budget.token_stopped
        if app.cancel_signals:
            report['checks']['all_observed_runs_completed'] = False
        save()
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--live-search', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--thinking', choices=('on', 'off'))
    parser.add_argument('--scenario', choices=('direct', 'research', 'workspace', 'quality', 'learning'), default='direct')
    parser.add_argument('--max-model-calls', type=int, default=24)
    parser.add_argument('--max-total-tokens', type=int,
                        help='Optional local DashScope reservation cap; not an account balance or exact billing limit')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    cfg = load_config()
    if not args.live:
        cfg = replace(cfg, llm_mode='mock')
    elif cfg.model_calls_disabled:
        parser.error('Model calls are disabled; this runner will not override the switch')
    try:
        cfg = configure(cfg, model=args.model, thinking=args.thinking)
    except ValueError as error:
        parser.error(str(error))
    if args.scenario == 'quality' and args.live_search:
        parser.error('Quality uses frozen synthetic evidence; do not combine it with --live-search')
    if args.scenario == 'learning' and args.live_search:
        parser.error('Learning uses isolated user evidence; do not combine it with --live-search')
    cfg = replace(cfg, search_mode='live' if args.live_search else 'mock')
    root = args.output_dir or PROJECT_ROOT/'workspace'/'acceptance'/uuid.uuid4().hex
    try:
        report = run(cfg, root, scenario=args.scenario, max_model_calls=args.max_model_calls,
                     max_total_tokens=args.max_total_tokens)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({'report': str(root/'report.json'), 'mode': cfg.llm_mode,
                      'checks': report['checks'], 'model_attempts': report['model_attempts'],
                      'semantic_review': report['semantic_review']}, ensure_ascii=False), flush=True)
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
