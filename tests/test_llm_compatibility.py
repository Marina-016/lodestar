import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError, error_details


class LLMCompatibilityTests(unittest.TestCase):
    def test_optional_catalog_uses_bounded_read_only_request(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        client._client = Mock()
        service = client._client.with_options.return_value
        service.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id='one'), SimpleNamespace(id='two')])
        self.assertEqual(client.list_models(), ['one', 'two'])
        client._client.with_options.assert_called_once_with(max_retries=0)
        service.models.list.assert_called_once_with(limit=100, timeout=10)

    def test_catalog_failure_does_not_expose_provider_body(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        client._client = Mock()
        client._client.with_options.side_effect = ValueError('private provider response')
        with self.assertRaises(LLMError) as error:
            client.list_models()
        self.assertNotIn('private', str(error.exception))

    def test_plain_completion_honors_explicit_thinking(self):
        client = LLMClient(Config(llm_mode='mock', llm_thinking=True))
        client.mode = 'live'
        client._client = Mock()
        client._client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type='text', text='answer')])
        self.assertEqual(client.complete('conversation', 'Answer', 'Question'), 'answer')
        params = client._client.messages.create.call_args.kwargs
        self.assertEqual(params['thinking'], {'type': 'enabled', 'budget_tokens': 4096})
        self.assertNotIn('extra_body', params)

    def test_explicit_thinking_empty_answer_does_not_silently_disable_it(self):
        client = LLMClient(Config(llm_mode='mock', llm_thinking=True))
        client.mode = 'live'
        client._client = Mock()
        client._client.messages.create.return_value = SimpleNamespace(content=[])
        with self.assertRaises(LLMError) as error:
            client.complete('conversation', 'Answer', 'Question')
        self.assertEqual(error.exception.code, 'empty_response')
        client._client.messages.create.assert_called_once()

    def test_temperature_is_forwarded_through_sdk_extension_body(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        # Match the newer SDK's keyword boundary (no temperature parameter).
        def create(*, model, max_tokens, extra_body, system, messages, thinking):
            self.assertEqual(extra_body, {'temperature': 0.2})
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='OK')])
        client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
        self.assertEqual(client.complete('probe', 'Reply OK', 'Hello'), 'OK')

    def test_invalid_json_repaired_once(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        with patch.object(client, '_complete_live', side_effect=['I will search.', '{"action":"search_web","query":"AI"}']) as call:
            self.assertEqual(client.complete_json('structured_task', 'Choose JSON', '{}')['action'], 'search_web')
        self.assertEqual(call.call_count, 2)

    def test_anthropic_json_uses_native_tool_input_instead_of_prose(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        client._client = Mock()
        client._client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type='text', text='This is not JSON.'),
            SimpleNamespace(type='tool_use', name='submit_result', input={
                'action': 'search_papers', 'query': 'AI agent'})])
        self.assertEqual(client.complete_json('structured_task', 'Choose an action', '{}'),
                         {'action': 'search_papers', 'query': 'AI agent'})
        params = client._client.messages.create.call_args.kwargs
        self.assertEqual(params['tool_choice'], {'type': 'tool', 'name': 'submit_result'})
        self.assertEqual(params['tools'][0]['input_schema']['type'], 'object')

    def test_plain_answers_do_not_force_tool_format(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        client._client = Mock()
        client._client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type='text', text='自然回答')])
        self.assertEqual(client.complete('conversation', 'Answer', '你好'), '自然回答')
        self.assertNotIn('tool_choice', client._client.messages.create.call_args.kwargs)

    def test_invalid_json_stops_after_one_repair_without_exposing_output(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        with patch.object(client, '_complete_live', return_value='private provider output') as call:
            with self.assertRaises(LLMError) as caught:
                client.complete_json('structured_task', 'Choose JSON', '{}')
        self.assertEqual(call.call_count, 2)
        self.assertEqual(error_details(caught.exception)['code'], 'invalid_json')
        self.assertEqual(caught.exception.role, 'structured_task')
        self.assertNotIn('private provider output', str(caught.exception))

    def test_transport_error_is_not_retried_as_json(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        with patch.object(client, '_complete_live', side_effect=LLMError('provider failure')) as call:
            with self.assertRaises(LLMError):
                client.complete_json('structured_task', 'Choose JSON', '{}')
        self.assertEqual(call.call_count, 1)
