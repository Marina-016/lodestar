"""Initialization must stay local and never multiply one inference implicitly."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError


class RuntimeInitializationTests(unittest.TestCase):
    def test_mock_and_dashscope_start_without_loading_anthropic_dependency_tree(self):
        # A fresh interpreter catches eager imports that an already imported SDK hides.
        source = '''
import importlib.abc, os, sys
class RejectUnusedSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('anthropic', 'httpx2', 'httpcore2', 'truststore'):
            raise AssertionError('Unused SDK import: ' + fullname)
sys.meta_path.insert(0, RejectUnusedSDK())
from lodestar.config import Config
from lodestar.llm import LLMClient
LLMClient(Config(llm_mode='mock')).close()
os.environ['DASHSCOPE_API_KEY'] = 'offline-fixture'
client = LLMClient(Config(llm_mode='live', llm_provider='dashscope',
    llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'))
assert client._dashscope is not None
client.close()
print('startup_without_unused_sdk')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', source],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf8', errors='replace'))
        self.assertIn(b'startup_without_unused_sdk', result.stdout)

    def test_missing_credentials_fail_before_sdk_import(self):
        with patch.dict(os.environ, {}, clear=True), patch('lodestar.llm._anthropic_sdk',
                side_effect=AssertionError('SDK should not be imported')):
            with self.assertRaisesRegex(LLMError, 'Missing ANTHROPIC'):
                LLMClient(Config(llm_mode='live'))

    def test_missing_anthropic_sdk_has_explicit_configuration_error(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'offline-fixture'}, clear=True), \
                patch('lodestar.llm.importlib.import_module', side_effect=ImportError('missing')):
            with self.assertRaises(LLMError) as caught:
                LLMClient(Config(llm_mode='live'))
        self.assertEqual(caught.exception.code, 'configuration')

    def _memory_client(self, respond):
        import anthropic
        package = next(base.__module__.split('.')[0]
            for base in anthropic.DefaultHttpxClient.__mro__
            if base.__module__.split('.')[0] in ('httpx', 'httpx2'))
        http = importlib.import_module(package)
        original = anthropic.Anthropic
        configuration = tempfile.TemporaryDirectory()
        self.addCleanup(configuration.cleanup)

        def construct(**kwargs):
            # Keep real SDK construction and retry handling, replacing only the wire.
            kwargs['api_key'] = 'offline-fixture'
            kwargs['http_client'] = anthropic.DefaultHttpxClient(
                transport=http.MockTransport(respond))
            with patch.dict(os.environ, {'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': ''}):
                return original(**kwargs)

        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'offline-fixture',
                'ANTHROPIC_CONFIG_DIR': configuration.name}, clear=True), \
                patch('lodestar.llm._anthropic_sdk', return_value=SimpleNamespace(Anthropic=construct)):
            client = LLMClient(Config(llm_mode='live', max_tokens=2048,
                llm_thinking_budget=1024, llm_timeout_s=2))
        self.addCleanup(client.close)
        return client, http

    def test_retryable_http_errors_send_exactly_one_request(self):
        for status in (429, 500, 529):
            with self.subTest(status=status):
                requests = []

                def respond(request):
                    requests.append(request)
                    return http.Response(status, json={'type': 'error',
                        'error': {'type': 'api_error', 'message': 'private-response-sentinel'}})

                client, http = self._memory_client(respond)
                with self.assertRaises(LLMError) as caught:
                    client.complete('test', 'system', 'user')
                self.assertNotIn('private-response-sentinel', str(caught.exception))
                self.assertEqual(caught.exception.__cause__.status_code, status)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].headers.get('x-stainless-retry-count'), '0')

    def test_connection_error_sends_exactly_one_request(self):
        requests = []

        def respond(request):
            requests.append(request)
            raise http.ConnectError('private-connection-sentinel', request=request)

        client, http = self._memory_client(respond)
        with self.assertRaises(LLMError) as caught:
            client.complete('test', 'system', 'user')
        self.assertNotIn('private-connection-sentinel', str(caught.exception))
        self.assertEqual(len(requests), 1)

    def test_streaming_retryable_error_sends_exactly_one_request(self):
        requests = []

        def respond(request):
            requests.append(request)
            return http.Response(529, json={'type': 'error',
                'error': {'type': 'overloaded_error', 'message': 'offline fixture'}})

        client, http = self._memory_client(respond)
        with self.assertRaises(LLMError):
            client.dialogue_step('system', [{'role': 'user', 'content': 'user'}], [])
        self.assertEqual(len(requests), 1)

    def test_empty_text_does_not_retry_or_change_reasoning_budget(self):
        for thinking in (False, True):
            with self.subTest(thinking=thinking):
                requests = []

                def respond(request):
                    requests.append(request)
                    return http.Response(200, json={'id': 'msg_offline', 'type': 'message',
                        'role': 'assistant', 'model': 'offline-fixture', 'content': [],
                        'stop_reason': 'end_turn', 'stop_sequence': None,
                        'usage': {'input_tokens': 1, 'output_tokens': 0}})

                client, http = self._memory_client(respond)
                client.config.llm_thinking = thinking
                with self.assertRaises(LLMError) as caught:
                    client.complete('test', 'system', 'user')
                self.assertEqual(caught.exception.code, 'empty_response')
                self.assertEqual(len(requests), 1)
                payload = json.loads(requests[0].content)
                self.assertEqual(payload['thinking']['type'], 'enabled' if thinking else 'disabled')
                self.assertEqual(payload['max_tokens'], 2048)
                if thinking:
                    self.assertEqual(payload['thinking']['budget_tokens'], 1024)


if __name__ == '__main__':
    unittest.main()
