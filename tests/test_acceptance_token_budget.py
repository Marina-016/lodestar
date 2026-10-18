"""Local reservation safeguards are estimates, not provider billing guarantees."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import replace
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

from lodestar.config import Config
from lodestar.llm import LLMError
from scripts.eval_workspace import ModelBudget, run


class MemoryAdapter(requests.adapters.BaseAdapter):
    def __init__(self, body, *, status=200, stream=False, gate=None):
        self.body, self.status, self.stream, self.gate = body, status, stream, gate
        self.attempts = 0
        self.started = threading.Event()

    def send(self, request, **kwargs):
        self.attempts += 1
        self.started.set()
        if self.gate and not self.gate.wait(timeout=3):
            raise AssertionError('Test response was not released')
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = self.status
        response.headers['Content-Type'] = 'text/event-stream' if self.stream else 'application/json'
        if self.status == 307:
            response.headers['Location'] = 'https://dashscope.aliyuncs.com/another-inference'
        response.raw = BytesIO(self.body)
        return response

    def close(self):
        pass


def plain(usage=None):
    body = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Local answer'}}]}
    if usage is not None:
        body['usage'] = usage
    return json.dumps(body).encode('utf8')


def stream(usage=None, *, done=True, error=False):
    data = [{'choices': [{'delta': {'content': 'Local answer'}, 'finish_reason': 'stop'}]}]
    if usage is not None:
        data.append({'usage': usage, 'choices': []})
    if error:
        data.append({'error': {'message': 'PRIVATE_ERROR_CANARY'}})
    body = b''.join(('data: '+json.dumps(item)+'\n\n').encode() for item in data)
    return body + (b'data: [DONE]\n\n' if done else b'')


class AcceptanceTokenBudgetTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'PRIVATE_KEY_CANARY'})
        env.start()
        self.addCleanup(env.stop)
        self.config = Config(llm_mode='live', llm_provider='dashscope', model='offline-fixture',
            max_tokens=128, llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')

    def client(self, budget, adapter):
        client = budget.client(self.config)
        client._dashscope.http.mount('https://', adapter)
        self.addCleanup(client.close)
        return client

    def call(self, client, streaming=False):
        if streaming:
            return client.dialogue_step('PRIVATE_PROMPT_CANARY', [], [])
        return client.complete('test', 'system', 'PRIVATE_PROMPT_CANARY')

    def test_insufficient_quota_blocks_before_any_physical_request(self):
        budget = ModelBudget(24, max_total_tokens=1)
        adapter = MemoryAdapter(plain({'total_tokens': 30}))
        with self.assertRaises(LLMError) as caught:
            self.call(self.client(budget, adapter))
        self.assertEqual(caught.exception.code, 'acceptance_token_budget')
        self.assertEqual(adapter.attempts, 0)
        self.assertEqual(budget.used, 0)
        self.assertEqual(budget.token_blocked, 1)
        self.assertEqual(budget.token_used, 0)

    def test_successful_json_and_stream_usage_refund_once(self):
        for streaming, usage in ((False, {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}),
                                 (True, {'input_tokens': 10, 'output_tokens': 20})):
            with self.subTest(streaming=streaming):
                budget = ModelBudget(24, max_total_tokens=10000)
                adapter = MemoryAdapter(stream(usage) if streaming else plain(usage), stream=streaming)
                self.call(self.client(budget, adapter), streaming)
                record = budget.token_report()['attempts'][0]
                self.assertEqual(record['state'], 'reported_usage')
                self.assertEqual(record['reported_tokens'], 30)
                self.assertEqual(budget.token_used, 30)
                self.assertGreater(record['reserved_tokens'], 30)
                budget.finish_tokens({'total_tokens': 1}, success=True)
                self.assertEqual(budget.token_used, 30, 'A second settlement must not refund twice')
                encoded = json.dumps(budget.token_report())
                self.assertNotIn('PRIVATE_PROMPT_CANARY', encoded)
                self.assertNotIn('PRIVATE_KEY_CANARY', encoded)

    def test_missing_usage_http_error_and_interrupted_stream_keep_full_reservation(self):
        cases = [('missing', MemoryAdapter(plain()), False, False),
                 ('http_error', MemoryAdapter(plain({'total_tokens': 30}), status=429), False, True),
                 ('interrupted', MemoryAdapter(stream({'total_tokens': 30}, done=False), stream=True), True, True),
                 ('stream_error', MemoryAdapter(stream({'total_tokens': 30}, error=True), stream=True), True, True)]
        for name, adapter, streaming, fails in cases:
            with self.subTest(case=name):
                budget = ModelBudget(24, max_total_tokens=10000)
                client = self.client(budget, adapter)
                if fails:
                    with self.assertRaises(LLMError):
                        self.call(client, streaming)
                else:
                    self.call(client, streaming)
                record = budget.token_report()['attempts'][0]
                self.assertEqual(budget.token_used, record['reserved_tokens'])
                self.assertIsNone(record['reported_tokens'])
                self.assertEqual(record['state'], 'error_reserved' if fails else 'unknown_usage_reserved')
                self.assertEqual(adapter.attempts, 1)
                self.assertNotIn('PRIVATE_ERROR_CANARY', json.dumps(budget.token_report()))

    def test_invalid_or_excess_usage_stops_further_requests(self):
        usages = [{'prompt_tokens': -1, 'completion_tokens': 20, 'total_tokens': 19},
                  {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 99},
                  {'total_tokens': True}, {'total_tokens': 0}, {'total_tokens': 50000}]
        for usage in usages:
            with self.subTest(usage=usage):
                budget = ModelBudget(24, max_total_tokens=100000)
                adapter = MemoryAdapter(plain(usage))
                client = self.client(budget, adapter)
                self.call(client)
                self.assertTrue(budget.token_stopped)
                record = budget.token_report()['attempts'][0]
                expected = 'usage_exceeded_reservation' if usage == {'total_tokens': 50000} else 'invalid_usage_reserved'
                self.assertEqual(record['state'], expected)
                with self.assertRaises(LLMError) as caught:
                    self.call(client)
                self.assertEqual(caught.exception.code, 'acceptance_token_budget')
                self.assertEqual(adapter.attempts, 1)
                self.assertEqual(budget.used, 1)

    def test_concurrent_clients_share_in_flight_reservations(self):
        budget = ModelBudget(24, max_total_tokens=2000)
        release = threading.Event()
        first_adapter = MemoryAdapter(plain({'total_tokens': 30}), gate=release)
        second_adapter = MemoryAdapter(plain({'total_tokens': 30}))
        first, second = self.client(budget, first_adapter), self.client(budget, second_adapter)
        with ThreadPoolExecutor(max_workers=2) as workers:
            pending = workers.submit(self.call, first)
            try:
                self.assertTrue(first_adapter.started.wait(timeout=2))
                denied = workers.submit(self.call, second)
                with self.assertRaises(LLMError) as caught:
                    denied.result(timeout=2)
                self.assertEqual(caught.exception.code, 'acceptance_token_budget')
                self.assertEqual(second_adapter.attempts, 0)
                self.assertLessEqual(budget.token_used, 2000)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2), 'Local answer')
        self.assertEqual(budget.used, 1)
        self.assertEqual(budget.token_used, 30)

    def test_invalid_payloads_are_rejected_without_counting_physical_attempts(self):
        bodies = [None, b'not-json', b'[]', b'{}', b'{"max_tokens": false}', b'{"max_tokens": 0}', b'\xff']
        for body in bodies:
            with self.subTest(body=body):
                budget = ModelBudget(24, max_total_tokens=10000)
                request = requests.Request('POST', 'https://example.invalid/chat/completions', data=body).prepare()
                with self.assertRaises(LLMError):
                    budget.reserve(request)
                self.assertEqual(budget.used, 0)
                self.assertEqual(budget.token_used, 0)

    def test_reservation_counts_utf8_bytes_and_explicit_reasoning_allowance(self):
        payload = {'messages': [{'content': '研究记忆'}], 'max_tokens': 128,
                   'enable_thinking': True, 'thinking_budget': 1024}
        body = json.dumps(payload, ensure_ascii=False).encode('utf8')
        request = requests.Request('POST', 'https://example.invalid/chat/completions', data=body).prepare()
        budget = ModelBudget(24, max_total_tokens=10000)
        budget.reserve(request)
        self.assertEqual(budget.token_used, len(body)+128+1024+1024)

    def test_equivalent_ascii_escaped_and_utf8_json_fit_the_same_reservation_cap(self):
        payload = {'messages': [{'role': 'user', 'content': '研究长期记忆🙂\n统一状态'}],
                   'max_tokens': 128, 'enable_thinking': False}
        normalized = json.dumps(payload, ensure_ascii=False).encode('utf8')
        escaped = json.dumps(payload, ensure_ascii=True, indent=2).encode('utf8')
        utf8 = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf8')
        cap = len(normalized)+128+1024
        self.assertGreater(len(escaped)+128+1024, cap, 'Old wire-byte estimate would reject this encoding')
        self.assertNotEqual(escaped, utf8)
        estimates = []
        for body in (escaped, utf8):
            budget = ModelBudget(24, max_total_tokens=cap)
            request = requests.Request('POST', 'https://example.invalid/chat/completions', data=body).prepare()
            budget.reserve(request)
            self.assertEqual(budget.used, 1)
            estimates.append(budget.token_used)
            # Normalizing input does not relax the full reservation on failure.
            budget.finish_tokens()
            self.assertEqual(budget.token_used, cap)
            self.assertEqual(budget.token_report()['attempts'][0]['state'], 'error_reserved')
        self.assertEqual(estimates, [cap, cap])

    def test_last_denial_is_one_numeric_threshold_snapshot_without_body(self):
        payload = {'messages': [{'content': 'PRIVATE_PROMPT_CANARY'}],
                   'max_tokens': 128, 'enable_thinking': False}
        request = requests.Request('POST', 'https://example.invalid/chat/completions', json=payload).prepare()
        estimate = ModelBudget.estimate_tokens(request)
        budget = ModelBudget(24, max_total_tokens=estimate+100)
        budget.reserve(request)
        with self.assertRaises(LLMError):
            budget.reserve(request)
        diagnostic = budget.token_report()['last_denial']
        self.assertEqual(diagnostic, {'reason': 'insufficient_reservation',
            'estimate': estimate, 'accounted': estimate, 'remaining': 100})
        payload['messages'][0]['content'] *= 2
        bigger = requests.Request('POST', 'https://example.invalid/chat/completions', json=payload).prepare()
        with self.assertRaises(LLMError):
            budget.reserve(bigger)
        latest = budget.token_report()['last_denial']
        self.assertIsInstance(latest, dict)
        self.assertEqual(set(latest), {'reason', 'estimate', 'accounted', 'remaining'})
        self.assertGreater(latest['estimate'], diagnostic['estimate'])
        self.assertEqual(latest['accounted'], estimate)
        self.assertEqual(latest['remaining'], 100)
        self.assertEqual(budget.max_total_tokens, estimate+100)
        self.assertEqual(budget.used, 1)
        self.assertNotIn('PRIVATE_PROMPT_CANARY', json.dumps(budget.token_report()))

    def test_default_and_effort_settings_are_refused_only_in_protected_acceptance(self):
        for mode in ('default', 'effort'):
            with self.subTest(mode=mode):
                self.config = replace(self.config, model='qwen3.8-flash',
                    llm_thinking_mode=mode, llm_thinking=mode == 'effort', llm_reasoning_effort='low')
                protected = ModelBudget(24, max_total_tokens=10000)
                adapter = MemoryAdapter(plain({'total_tokens': 30}))
                with self.assertRaises(LLMError) as caught:
                    self.call(self.client(protected, adapter))
                self.assertEqual(caught.exception.code, 'acceptance_token_budget')
                self.assertEqual(adapter.attempts, 0)
                self.assertEqual(protected.used, 0)
                self.assertEqual(self.config.llm_thinking_mode, mode)
                unprotected = MemoryAdapter(plain())
                self.assertEqual(self.call(self.client(ModelBudget(24), unprotected)), 'Local answer')
                self.assertEqual(unprotected.attempts, 1)

    def test_missing_ambiguous_or_unbounded_thinking_cannot_be_estimated(self):
        options = [{}, {'enable_thinking': None}, {'enable_thinking': 0}, {'enable_thinking': 1},
                   {'enable_thinking': 'false'}, {'enable_thinking': True},
                   {'enable_thinking': True, 'thinking_budget': 0},
                   {'enable_thinking': True, 'thinking_budget': True},
                   {'enable_thinking': True, 'thinking_budget': 1.5}]
        for option in options:
            with self.subTest(option=option):
                budget = ModelBudget(24, max_total_tokens=10000)
                request = requests.Request('POST', 'https://example.invalid/chat/completions',
                    json={'max_tokens': 128, **option}).prepare()
                with self.assertRaises(LLMError):
                    budget.reserve(request)
                self.assertEqual(budget.used, 0)

    def test_redirect_does_not_send_a_second_inference(self):
        budget = ModelBudget(24, max_total_tokens=10000)
        adapter = MemoryAdapter(b'', status=307)
        with self.assertRaises(LLMError):
            self.call(self.client(budget, adapter))
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(budget.token_report()['attempts'][0]['state'], 'error_reserved')

    def test_token_protection_rejects_anthropic_before_sdk_construction(self):
        budget = ModelBudget(24, max_total_tokens=10000)
        with patch('scripts.eval_workspace.LLMClient.__init__', side_effect=AssertionError('No SDK construction')):
            with self.assertRaisesRegex(ValueError, 'DashScope only'):
                budget.client(Config(llm_mode='live', llm_provider='anthropic'))

    def test_invalid_token_caps_and_unprotected_live_factory_are_rejected(self):
        for limit in (0, -1, True, 1.5, '1000'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ModelBudget(24, max_total_tokens=limit)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'acceptance'
            with self.assertRaisesRegex(ValueError, 'guarded DashScope'):
                run(self.config, target, max_total_tokens=10000, llm_factory=lambda cfg: None)
            self.assertFalse(target.exists())

    def test_offline_run_reports_estimate_scope_without_account_claims(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(StringIO()):
            report = run(Config(llm_mode='mock', search_mode='mock'), Path(tmp)/'acceptance', max_total_tokens=10000)
        self.assertEqual(report['model_attempts'], 0)
        self.assertEqual(report['token_budget']['accounted_tokens'], 0)
        self.assertTrue(report['token_budget']['enabled'])
        self.assertIn('not a supplier billing upper bound', report['token_budget']['estimate'])
        self.assertIn('no account balance', report['token_budget']['scope'])
        self.assertTrue(all(report['checks'].values()))


if __name__ == '__main__':
    unittest.main()
