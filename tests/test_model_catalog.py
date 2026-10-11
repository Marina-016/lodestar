import unittest
from unittest.mock import Mock, patch
from lodestar.config import Config
from lodestar.llm import LLMClient


class CatalogTests(unittest.TestCase):
    def test_workspace_catalog_uses_native_api_and_paginates_without_inference(self):
        cfg = Config(llm_mode='live', llm_provider='dashscope', model='qwen3.8-flash',
                     llm_base_url='https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        pages = [Mock(ok=True, json=lambda: {'success': True, 'output': {'total': 101, 'models': [{'model': 'a'}]}}),
                 Mock(ok=True, json=lambda: {'success': True, 'output': {'total': 101, 'models': [{'model': 'b'}]}})]
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'unit-test-key'}), \
                patch('lodestar.providers.dashscope.requests.get', side_effect=pages) as get, \
                patch('lodestar.providers.dashscope.requests.post') as post:
            client = LLMClient(cfg)
            self.assertEqual(client.list_models(), ['a', 'b'])
            client.close()
        self.assertEqual(get.call_args_list[0].args[0], 'https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1/models')
        self.assertEqual([c.kwargs['params']['page_no'] for c in get.call_args_list], [1, 2])
        post.assert_not_called()
