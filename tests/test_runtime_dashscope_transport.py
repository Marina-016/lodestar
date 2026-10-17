"""DashScope transport injection stays local, bounded, and owned by one client."""
import json
import os
import unittest
from unittest.mock import patch

import requests

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError, error_details
from lodestar.providers.dashscope import DashScopeClient


class MemoryTransport(requests.adapters.BaseAdapter):
    def __init__(self, status=200):
        self.status = status
        self.requests = []
        self.close_count = 0

    def send(self, request, **kwargs):
        self.requests.append((request, kwargs))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = self.status
        if self.status != 200:
            data = {'error': 'PRIVATE_RESPONSE_SENTINEL'}
        elif request.method == 'GET':
            data = {'success': True, 'output': {'models': [{'model': 'offline-fixture'}], 'total': 1}}
        else:
            data = {'model': 'offline-fixture', 'choices': [
                {'finish_reason': 'stop', 'message': {'content': 'Local answer'}}],
                'usage': {'total_tokens': 3}}
        response._content = json.dumps(data).encode('utf8')
        return response

    def close(self):
        self.close_count += 1


class DashScopeTransportTests(unittest.TestCase):
    def config(self):
        return Config(llm_mode='live', llm_provider='dashscope', model='offline-fixture',
            llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')

    def transport(self, status=200):
        session = requests.Session()
        session.trust_env = False
        transport = MemoryTransport(status)
        session.mount('https://dashscope.aliyuncs.com/', transport)
        return session, transport

    def client(self):
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'offline-fixture'}):
            client = LLMClient(self.config())
        self.addCleanup(client.close)
        return client

    def test_injected_session_handles_completion_catalog_and_close(self):
        session, transport = self.transport()
        client = self.client()
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'offline-fixture'}):
            client._dashscope = DashScopeClient(self.config(), http=session)
        with patch('lodestar.providers.dashscope.requests.post', side_effect=AssertionError('Default POST forbidden')), \
                patch('lodestar.providers.dashscope.requests.get', side_effect=AssertionError('Default GET forbidden')):
            self.assertEqual(client.complete('test', 'system', 'user'), 'Local answer')
            self.assertEqual(client.list_models(), ['offline-fixture'])
        self.assertEqual([request.method for request, _ in transport.requests], ['POST', 'GET'])
        self.assertTrue(all(options['verify'] for _, options in transport.requests))
        self.assertEqual(client._dashscope.usage[0]['total_tokens'], 3)
        client.close()
        client.close()
        self.assertEqual(transport.close_count, 1)

    def test_transport_assigned_after_construction_is_owned(self):
        session, transport = self.transport()
        client = self.client()
        client._dashscope.http = session
        client.close()
        self.assertEqual(transport.close_count, 1)

    def test_default_requests_module_is_never_closed(self):
        client = self.client()
        self.assertIs(client._dashscope.http, requests)
        with patch.object(requests, 'close', create=True) as close:
            client.close()
            close.assert_not_called()

    def test_http_errors_keep_classification_without_retry_or_body_leak(self):
        for status, code in ((401, 'authentication'), (429, 'rate_limit'), (500, 'http_error')):
            with self.subTest(status=status):
                session, transport = self.transport(status)
                client = self.client()
                client._dashscope.http = session
                with patch('lodestar.providers.dashscope.requests.post',
                        side_effect=AssertionError('Default POST forbidden')):
                    with self.assertRaises(LLMError) as caught:
                        client.complete('test', 'system', 'user')
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(error_details(caught.exception)['code'], code)
                self.assertEqual(error_details(caught.exception)['http_status'], status)
                self.assertNotIn('PRIVATE_RESPONSE_SENTINEL', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
