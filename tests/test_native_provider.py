import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError
from lodestar.providers.dialogue import _openai_messages
from tests.test_dialogue import calls


class NativeProviderTests(unittest.TestCase):
    def client(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        client._client = Mock()
        return client

    def test_anthropic_preserves_native_tool_blocks_and_streams_text(self):
        client = self.client()
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.text_stream = iter(['先', '检索'])
        block = Mock()
        block.model_dump.return_value = calls({'action': 'search_web', 'query': 'AI'})['content'][0]
        stream.get_final_message.return_value = SimpleNamespace(stop_reason='tool_use', content=[block])
        client._client.messages.stream.return_value = manager
        messages = [{'role': 'user', 'content': 'AI news'}]
        tools = [{'name': 'search_web', 'description': 'Search', 'input_schema': {'type': 'object'}}]
        delivered = []
        result = client.dialogue_step('system', messages, tools, delivered.append)
        self.assertEqual(delivered, ['先', '检索'])
        self.assertEqual(result['content'][0]['name'], 'search_web')
        params = client._client.messages.stream.call_args.kwargs
        self.assertEqual(params['messages'], messages)
        self.assertEqual(params['tools'], tools)
        self.assertNotIn('tool_choice', params)
        self.assertEqual(params['thinking'], {'type': 'disabled'})

    def test_anthropic_explicitly_enables_reasoning_and_reserves_answer_budget(self):
        client = self.client()
        client.config.llm_thinking = True
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.text_stream = iter([])
        stream.get_final_message.return_value = SimpleNamespace(stop_reason='end_turn', content=[])
        client._client.messages.stream.return_value = manager
        client.dialogue_step('system', [], [])
        params = client._client.messages.stream.call_args.kwargs
        self.assertEqual(params['thinking'], {'type': 'enabled', 'budget_tokens': 4096})
        self.assertGreater(params['max_tokens'], params['thinking']['budget_tokens'])
        self.assertNotIn('temperature', params.get('extra_body', {}))
        self.assertTrue(client.last_dialogue_observation['thinking_requested'])
        self.assertFalse(client.last_dialogue_observation['thinking_observed'])

    def test_anthropic_adaptive_reasoning_uses_effort_not_fixed_budget(self):
        client = self.client()
        client.config.llm_thinking = True
        client.config.llm_thinking_mode = 'adaptive'
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.text_stream = iter([])
        block = Mock()
        block.model_dump.return_value = {'type': 'thinking', 'thinking': 'private', 'signature': 'signed'}
        stream.get_final_message.return_value = SimpleNamespace(stop_reason='end_turn', content=[block])
        client._client.messages.stream.return_value = manager
        result = client.dialogue_step('system', [], [])
        params = client._client.messages.stream.call_args.kwargs
        self.assertEqual(params['thinking'], {'type': 'adaptive'})
        self.assertEqual(params['output_config'], {'effort': 'high'})
        self.assertEqual(result['content'][0]['signature'], 'signed')
        self.assertTrue(client.last_dialogue_observation['thinking_observed'])
        self.assertNotIn('private', json.dumps(client.last_dialogue_observation))

    def test_invalid_reasoning_budget_fails_before_request(self):
        client = self.client()
        client.config.llm_thinking = True
        client.config.max_tokens = 4096
        with self.assertRaises(LLMError) as error:
            client.dialogue_step('system', [], [])
        self.assertEqual(error.exception.code, 'configuration')
        client._client.messages.stream.assert_not_called()

    def test_anthropic_truncation_does_not_become_answer(self):
        client = self.client()
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.text_stream = iter(['partial'])
        stream.get_final_message.return_value = SimpleNamespace(stop_reason='max_tokens')
        client._client.messages.stream.return_value = manager
        delivered = []
        with self.assertRaises(LLMError) as error:
            client.dialogue_step('system', [], [], delivered.append)
        self.assertEqual(error.exception.code, 'truncated')
        self.assertEqual(delivered, ['partial'])
        client._client.messages.stream.assert_called_once()

    def test_openai_conversion_pairs_tool_ids(self):
        messages = [calls({'action': 'search_web', 'query': 'AI'}),
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call_0',
                                                'content': '{"sources": []}'}]}]
        converted = _openai_messages(messages)
        self.assertEqual(converted[0]['tool_calls'][0]['id'], converted[1]['tool_call_id'])
        self.assertEqual(converted[1]['role'], 'tool')
        self.assertEqual(json.loads(converted[0]['tool_calls'][0]['function']['arguments']), {'query': 'AI'})

    def test_openai_stream_assembles_fragmented_native_arguments(self):
        client = self.client()
        client._dashscope = SimpleNamespace(base_url='https://dashscope.aliyuncs.com', key='fixture', usage=[])
        response = MagicMock()
        response.__enter__.return_value = response
        chunks = [
            {'choices': [{'delta': {'content': '查找中', 'tool_calls': [{'index': 0, 'id': 't1',
                'function': {'name': 'search_web', 'arguments': '{"query":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"AI"}'}}]},
                          'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'total_tokens': 12}}]
        response.iter_lines.return_value = iter(['data: ' + json.dumps(c) for c in chunks] + ['data: [DONE]'])
        delivered = []
        client.config.llm_thinking = True
        with patch('lodestar.providers.dialogue.requests.post', return_value=response) as request:
            result = client.dialogue_step('system', [{'role': 'user', 'content': 'AI'}], [], delivered.append)
        self.assertTrue(request.call_args.kwargs['json']['enable_thinking'])
        self.assertEqual(request.call_args.kwargs['json']['thinking_budget'], 4096)
        self.assertEqual(result['content'][1]['input'], {'query': 'AI'})
        self.assertEqual(result['content'][1]['id'], 't1')
        self.assertEqual(delivered, ['查找中'])
        self.assertEqual(client._dashscope.usage[0]['total_tokens'], 12)

    def test_openai_reasoning_is_preserved_for_tools_but_not_streamed_as_answer(self):
        client = self.client()
        client._dashscope = SimpleNamespace(base_url='https://dashscope.aliyuncs.com', key='fixture', usage=[])
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = iter([
            'data: ' + json.dumps({'choices': [{'delta': {'reasoning_content': 'private reasoning'}}]}),
            'data: ' + json.dumps({'choices': [{'delta': {'content': 'final'}, 'finish_reason': 'stop'}]}),
            'data: [DONE]'])
        delivered = []
        with patch('lodestar.providers.dialogue.requests.post', return_value=response):
            result = client.dialogue_step('system', [], [], delivered.append)
        self.assertEqual(delivered, ['final'])
        self.assertEqual(_openai_messages([result])[0]['reasoning_content'], 'private reasoning')
        self.assertTrue(client.last_dialogue_observation['thinking_observed'])
        self.assertNotIn('private', json.dumps(client.last_dialogue_observation))

    def test_openai_unfinished_stream_is_error(self):
        client = self.client()
        client._dashscope = SimpleNamespace(base_url='https://dashscope.aliyuncs.com', key='fixture', usage=[])
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = iter(['data: {"choices": [{"delta": {"content": "partial"}}]}'])
        with patch('lodestar.providers.dialogue.requests.post', return_value=response):
            with self.assertRaises(LLMError) as error:
                client.dialogue_step('system', [], [])
        self.assertEqual(error.exception.code, 'stream_interrupted')
