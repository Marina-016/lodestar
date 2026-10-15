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

    def sdk_stream(self, chunks):
        import anthropic
        import importlib
        package = next(t.__module__.split('.')[0] for t in anthropic.DefaultHttpxClient.__mro__
                       if t.__module__.split('.')[0] in ('httpx', 'httpx2'))
        http = importlib.import_module(package)
        seen = []
        class Stream(http.SyncByteStream):
            def __iter__(self):
                yield from chunks
        def respond(request):
            seen.append(request)
            return http.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=Stream())
        client = self.client()
        client._client = anthropic.Anthropic(api_key='offline-fixture', max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=http.MockTransport(respond)))
        return client, seen

    @staticmethod
    def sdk_text_prefix():
        events = [
            {'type': 'message_start', 'message': {'id': 'fixture', 'type': 'message', 'role': 'assistant',
                'model': 'fixture', 'content': [], 'stop_reason': None, 'stop_sequence': None,
                'usage': {'input_tokens': 1, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'partial'}}]
        return b''.join(('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n').encode() for e in events)

    def test_actual_sdk_data_only_error_is_explicit_without_body_leak_or_retry(self):
        from lodestar.llm import error_details
        for envelope in (b'data: {"type":"error","error":{"message":"PRIVATE_CANARY"}}\n\n',
                         b'data: {"type":"error",\ndata: "error":{"message":"PRIVATE_CANARY"}}\n\n',
                         b'data: {"type":"error","error":{"message":"PRIVATE_CANARY"}}'):
            wire = b'data: {"type":"ping"}\n\n'*3 + envelope
            with self.subTest(envelope=envelope[:20]):
                client, seen = self.sdk_stream([wire[i:i+3] for i in range(0, len(wire), 3)])
                try:
                    with self.assertRaises(LLMError) as caught:
                        client.dialogue_step('system', [{'role': 'user', 'content': 'Hello'}], [])
                    diagnostic = error_details(caught.exception)
                    self.assertEqual(diagnostic['code'], 'upstream_stream_error')
                    self.assertIsNone(diagnostic['cause_type'])
                    self.assertNotIn('PRIVATE_CANARY', str(caught.exception)+json.dumps(diagnostic))
                    self.assertEqual(len(seen), 1)
                finally:
                    client.close()

    def test_sdk_data_error_preserves_preceding_text_even_in_the_same_chunk(self):
        client, seen = self.sdk_stream([self.sdk_text_prefix()+b'data: {"type":"error"}\n\n'])
        delivered = []
        try:
            with self.assertRaises(LLMError) as caught:
                client.dialogue_step('system', [], [], delivered.append)
            self.assertEqual(caught.exception.code, 'upstream_stream_error')
            self.assertEqual(delivered, ['partial'])
            self.assertEqual(len(seen), 1)
        finally:
            client.close()

    def test_sdk_successful_stream_still_returns_native_message(self):
        suffix = (b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
        client, seen = self.sdk_stream([self.sdk_text_prefix(), suffix])
        try:
            result = client.dialogue_step('system', [], [])
            self.assertEqual(result['content'], [{'type': 'text', 'text': 'partial'}])
            self.assertEqual(len(seen), 1)
        finally:
            client.close()

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

    def test_anthropic_empty_or_unrecognized_stream_is_interrupted_not_generic_failure(self):
        client = self.client()
        manager = MagicMock()
        stream = manager.__enter__.return_value
        stream.text_stream = iter([])
        stream.get_final_message.side_effect = AssertionError('PRIVATE_RESPONSE_DETAILS')
        client._client.messages.stream.return_value = manager
        with self.assertRaises(LLMError) as caught:
            client.dialogue_step('system', [], [])
        self.assertEqual(caught.exception.code, 'stream_interrupted')
        self.assertEqual(caught.exception.role, 'conversation')
        self.assertNotIn('PRIVATE_RESPONSE_DETAILS', str(caught.exception))
        client._client.messages.stream.assert_called_once()

    def test_actual_sdk_ping_only_sse_is_classified_without_a_second_request(self):
        import anthropic
        import importlib
        from lodestar.llm import error_details
        # Current desktop SDK uses httpx2; public SDK versions may use httpx.
        # Build the in-memory transport from the SDK's actual public client base.
        package = next(t.__module__.split('.')[0] for t in anthropic.DefaultHttpxClient.__mro__
                       if t.__module__.split('.')[0] in ('httpx', 'httpx2'))
        http = importlib.import_module(package)
        seen = []
        def respond(request):
            seen.append(request)
            return http.Response(200, headers={'Content-Type': 'text/event-stream'},
                                  content=b'event: ping\ndata: {"type":"ping"}\n\n')
        client = self.client()
        client._client = anthropic.Anthropic(api_key='offline-fixture', max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=http.MockTransport(respond)))
        try:
            with self.assertRaises(LLMError) as caught:
                client.dialogue_step('system', [{'role': 'user', 'content': 'Hello'}], [])
            self.assertEqual(error_details(caught.exception)['code'], 'stream_interrupted')
            self.assertEqual(len(seen), 1)
        finally:
            client.close()

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

    def test_qwen_native_effort_reaches_stream_request_without_budget(self):
        from lodestar.chat_settings import configure
        client = self.client()
        client.config = configure(Config(llm_provider='dashscope', model='qwen3.8-flash'), thinking='effort', effort='medium')
        client.model = client.config.model
        client._dashscope = SimpleNamespace(base_url='https://dashscope.aliyuncs.com', key='fixture', usage=[])
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = iter([
            'data: ' + json.dumps({'choices': [{'delta': {'content': 'answer'}, 'finish_reason': 'stop'}]}),
            'data: [DONE]'])
        with patch('lodestar.providers.dialogue.requests.post', return_value=response) as request:
            client.dialogue_step('system', [], [])
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'qwen3.8-flash')
        self.assertEqual(payload['reasoning_effort'], 'medium')
        self.assertTrue(payload['enable_thinking'])
        self.assertNotIn('thinking_budget', payload)

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
