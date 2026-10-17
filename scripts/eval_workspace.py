"""Isolated HTTP -> agent -> model acceptance; semantic quality requires review.

Default is offline plumbing, not autonomous-model acceptance. Live runs require
explicit --live, respect the model-disable switch, and never use paid search.
Artifacts persist in a fresh ignored directory; no real Web database is opened.
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
    def __init__(self, limit):
        if not 1 <= limit <= 100:
            raise ValueError('Model attempt limit must be 1-100')
        self.limit, self.used, self.blocked = limit, 0, 0
        self.lock = threading.Lock()
        self.stopped = False
        self.failures = []
        self.responses = []

    def reserve(self, request):
        path = request.url if isinstance(request.url, str) else request.url.path
        path = urlsplit(path).path.rstrip('/')
        if request.method != 'POST' or not path.endswith(('/messages', '/chat/completions')):
            return
        with self.lock:
            if self.stopped or self.used >= self.limit:
                self.blocked += 1
                raise LLMError('Acceptance model-attempt limit reached', code='acceptance_budget')
            self.used += 1
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
        budget = self
        class ObservedClient(LLMClient):
            def dialogue_step(self, *args, **kwargs):
                try:
                    return super().dialogue_step(*args, **kwargs)
                except LLMError as error:
                    cause = error.__cause__ or error
                    frames = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                              for f in traceback.extract_tb(cause.__traceback__)[-8:]]
                    with budget.lock:
                        budget.failures.append({**error_details(error), 'frames': frames})
                    raise
        class GuardedSession(requests.Session):
            def send(self, request, **kwargs):
                attempt = budget.reserve(request)
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


def run(config, output_dir, *, scenario='direct', max_model_calls=24, wait_seconds=180, llm_factory=None):
    if scenario not in ('direct', 'research', 'workspace', 'quality'):
        raise ValueError('Unknown acceptance scenario')
    if scenario == 'quality' and config.search_mode != 'mock':
        raise ValueError('Quality evidence is fixed and synthetic; live search is not allowed')
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
                    wait_seconds=wait_seconds, llm_factory=llm_factory, fixture=fixture)


def _run(config, output_dir, *, scenario, max_model_calls, wait_seconds, llm_factory, fixture):
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)  # Never reuse a real/user database.
    cfg = replace(config, db_path=root/'web.db', workspace_dir=root, demo_replay=False,
                  project_model_allowed_repository='', project_model_allowed_paths=())
    if cfg.search_mode == 'live' and cfg.web_search_backend != 'duckduckgo':
        raise ValueError('Acceptance only allows public search; paid/MCP search is not enabled')
    budget = ModelBudget(max_model_calls)
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

    def save():
        with budget.lock:
            report['model_attempts'] = budget.used
            report['blocked_model_attempts'] = budget.blocked
            report['model_failures'] = list(budget.failures)
            report['transport_responses'] = json.loads(json.dumps(budget.responses))
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
    parser.add_argument('--scenario', choices=('direct', 'research', 'workspace', 'quality'), default='direct')
    parser.add_argument('--max-model-calls', type=int, default=24)
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
    cfg = replace(cfg, search_mode='live' if args.live_search else 'mock')
    root = args.output_dir or PROJECT_ROOT/'workspace'/'acceptance'/uuid.uuid4().hex
    try:
        report = run(cfg, root, scenario=args.scenario, max_model_calls=args.max_model_calls)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({'report': str(root/'report.json'), 'mode': cfg.llm_mode,
                      'checks': report['checks'], 'model_attempts': report['model_attempts'],
                      'semantic_review': report['semantic_review']}, ensure_ascii=False), flush=True)
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
