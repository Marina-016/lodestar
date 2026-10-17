"""Physical attempt limits cover both provider protocols without cloud requests."""
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch

import requests

from lodestar.config import Config
from lodestar.llm import LLMError, error_details
from scripts.eval_workspace import ModelBudget


class MemoryAdapter(requests.adapters.BaseAdapter):
    def __init__(self, body, status=200, mime='application/json'):
        self.body, self.status, self.mime = body, status, mime
        self.attempts = 0
        self.closed = False

    def send(self, request, **kwargs):
        self.attempts += 1
        response = requests.Response()
        response.status_code = self.status
        response.headers['Content-Type'] = self.mime
        response.request = request
        response.raw = BytesIO(self.body)
        return response

    def close(self):
        self.closed = True


def sse(*payloads):
    return b''.join(('data: '+json.dumps(p)+'\n\n').encode() for p in payloads)+b'data: [DONE]\n\n'


class DashScopeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'offline-fixture'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = Config(llm_mode='live', llm_provider='dashscope', model='offline-fixture',
            llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')

    def client(self, budget, adapter):
        client = budget.client(self.config)
        client._dashscope.http.mount('https://', adapter)
        self.addCleanup(client.close)
        return client

    def test_plain_and_streaming_clients_share_the_same_physical_limit(self):
        budget = ModelBudget(2)
        streaming = MemoryAdapter(sse({'choices': [{'delta': {'content': 'fixture'}, 'finish_reason': 'stop'}]}),
                                  mime='text/event-stream')
        plain = MemoryAdapter(json.dumps({'choices': [{'message': {'content': 'fixture'},
                                                       'finish_reason': 'stop'}]}).encode())
        first, second = self.client(budget, streaming), self.client(budget, plain)
        self.assertEqual(first.dialogue_step('system', [], [])['content'][0]['text'], 'fixture')
        self.assertEqual(second.complete('test', 'system', 'fixture'), 'fixture')
        with self.assertRaises(LLMError) as caught:
            second.complete('test', 'system', 'fixture')
        self.assertEqual(caught.exception.code, 'acceptance_budget')
        self.assertEqual(budget.used, 2)
        self.assertEqual(budget.blocked, 1)
        self.assertEqual(streaming.attempts, 1)
        self.assertEqual(plain.attempts, 1)
        self.assertEqual(len(budget.responses), 2)
        serialized = json.dumps(budget.responses)
        self.assertNotIn('offline-fixture', serialized)
        self.assertNotIn('dashscope.aliyuncs.com', serialized)
        second.close()
        self.assertTrue(plain.closed)

    def test_stream_error_keeps_partial_text_without_retry_or_body_logging(self):
        budget = ModelBudget(3)
        body = sse({'choices': [{'delta': {'content': 'partial'}}]},
                   {'error': {'message': 'PRIVATE_UPSTREAM_BODY'}})
        adapter = MemoryAdapter(body, mime='text/event-stream')
        client = self.client(budget, adapter)
        text = []
        with self.assertRaises(LLMError) as caught:
            client.dialogue_step('system', [], [], text.append)
        self.assertEqual(text, ['partial'])
        self.assertEqual(caught.exception.code, 'upstream_stream_error')
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(budget.used, 1)
        self.assertNotIn('PRIVATE_UPSTREAM_BODY', str(caught.exception)+json.dumps(budget.responses))
        self.assertEqual(budget.responses[0]['body_observation'], 'not_collected')

    def test_http_failure_consumes_one_attempt_and_preserves_safe_classification(self):
        budget = ModelBudget(3)
        adapter = MemoryAdapter(b'{"error":"PRIVATE_UPSTREAM_BODY"}', status=429)
        client = self.client(budget, adapter)
        with self.assertRaises(LLMError) as caught:
            client.complete('test', 'system', 'fixture')
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(budget.used, 1)
        self.assertEqual(error_details(caught.exception)['code'], 'rate_limit')
        self.assertNotIn('PRIVATE_UPSTREAM_BODY', str(caught.exception)+json.dumps(budget.responses))

    def test_disabled_model_calls_remain_disabled(self):
        self.config.model_calls_disabled = True
        budget = ModelBudget(3)
        with self.assertRaises(LLMError):
            budget.client(self.config)
        self.assertEqual(budget.used, 0)
