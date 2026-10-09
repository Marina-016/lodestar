import os
import unittest
from unittest.mock import Mock, patch
from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError

class DashScopeTests(unittest.TestCase):
    def config(self):
        return Config(llm_provider='dashscope',llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',model='qwen-flash')
    def test_adapter_and_usage(self):
        response=Mock(ok=True)
        response.json.return_value={'model':'qwen-flash','choices':[{'finish_reason':'stop','message':{'content':'OK'}}],'usage':{'total_tokens':12}}
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-not-real'}), patch('lodestar.providers.dashscope.requests.post',return_value=response) as call:
            client=LLMClient(self.config())
            self.assertEqual(client.complete('test','system','user',max_tokens=20),'OK')
            self.assertFalse(call.call_args.kwargs['json']['enable_thinking'])
            self.assertEqual(client._dashscope.usage[0]['total_tokens'],12)
    def test_truncation_is_not_success(self):
        response=Mock(ok=True)
        response.json.return_value={'choices':[{'finish_reason':'length','message':{'content':'partial'}}]}
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-not-real'}), patch('lodestar.providers.dashscope.requests.post',return_value=response):
            with self.assertRaises(LLMError): LLMClient(self.config()).complete('test','system','user')
    def test_unofficial_endpoint_rejected(self):
        cfg=self.config(); cfg.llm_base_url='https://example.com/v1'
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-not-real'}):
            with self.assertRaises(LLMError): LLMClient(cfg)
    def test_kill_switch_prevents_network_even_with_credentials(self):
        cfg = self.config()
        cfg.model_calls_disabled = True
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test-not-real'}), patch('lodestar.providers.dashscope.requests.post') as request:
            with self.assertRaisesRegex(LLMError, 'disabled'):
                LLMClient(cfg)
            request.assert_not_called()
        cfg.llm_mode = 'mock'
        self.assertIsNone(LLMClient(cfg)._dashscope)

    def test_structured_mode_is_only_used_for_json_object_calls(self):
        cfg = Config(llm_mode='live',llm_provider='dashscope',llm_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
        response = unittest.mock.Mock()
        response.ok = True
        response.json.return_value = {'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        with patch.dict('os.environ',{'DASHSCOPE_API_KEY':'fixture'}), patch('lodestar.providers.dashscope.requests.post',return_value=response) as call:
            client = LLMClient(cfg)
            client.complete_json('probe','Return JSON','hello')
            self.assertEqual(call.call_args.kwargs['json']['response_format'],{'type':'json_object'})
            client.complete('probe','Return prose','hello')
            self.assertNotIn('response_format',call.call_args.kwargs['json'])
