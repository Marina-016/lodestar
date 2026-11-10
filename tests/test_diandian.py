import os
import unittest
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError
from lodestar.providers.diandian import DiandianClient


class DianDianTests(unittest.TestCase):
    def config(self):
        return Config(llm_provider='diandian', model='dots3-note-prev',
                      judge_model='dots3-note-prev',
                      llm_base_url='https://note3-prev-api.askdiandian.com/v1')

    def test_completion_uses_documented_endpoint_and_header(self):
        response = Mock(ok=True)
        response.json.return_value = {
            'model': 'dots3-note-prev',
            'choices': [{'finish_reason': 'stop', 'message': {'content': 'OK'}}],
            'usage': {'total_tokens': 12},
        }
        with patch.dict(os.environ, {'DIANDIAN_API_KEY': 'test-not-real'}), \
                patch('lodestar.providers.diandian.requests.post', return_value=response) as call:
            client = LLMClient(self.config())
            self.addCleanup(client.close)
            self.assertEqual(client.complete('test', 'system', 'user', max_tokens=20), 'OK')
        self.assertEqual(call.call_args.args[0], 'https://note3-prev-api.askdiandian.com/v1/chat/completions')
        self.assertEqual(call.call_args.kwargs['headers'], {'api-key': 'test-not-real'})
        self.assertNotIn('enable_thinking', call.call_args.kwargs['json'])
        self.assertEqual(client._diandian.usage[0]['total_tokens'], 12)

    def test_explicit_thinking_uses_only_documented_field(self):
        cfg = self.config()
        cfg.llm_thinking = True
        adapter = None
        with patch.dict(os.environ, {'DIANDIAN_API_KEY': 'test-not-real'}):
            adapter = DiandianClient(cfg)
        self.assertEqual(adapter.openai_options(), {'chat_template_kwargs': {'enable_thinking': True}})

    def test_rejects_non_documented_endpoint_and_catalog_guessing(self):
        cfg = self.config()
        cfg.llm_base_url = 'https://example.test/v1'
        with patch.dict(os.environ, {'DIANDIAN_API_KEY': 'test-not-real'}):
            with self.assertRaises(LLMError):
                LLMClient(cfg)
        with patch.dict(os.environ, {'DIANDIAN_API_KEY': 'test-not-real'}):
            client = LLMClient(self.config())
            self.addCleanup(client.close)
            with self.assertRaisesRegex(LLMError, 'catalog'):
                client.list_models()

    def test_kill_switch_prevents_client_creation(self):
        cfg = self.config()
        cfg.model_calls_disabled = True
        with patch.dict(os.environ, {'DIANDIAN_API_KEY': 'test-not-real'}):
            with self.assertRaisesRegex(LLMError, 'disabled'):
                LLMClient(cfg)


if __name__ == '__main__':
    unittest.main()
