import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lodestar.config import Config
from lodestar.llm import LLMClient


class LLMCompatibilityTests(unittest.TestCase):
    def test_temperature_is_forwarded_through_sdk_extension_body(self):
        client = LLMClient(Config(llm_mode='mock'))
        client.mode = 'live'
        # Match the newer SDK's keyword boundary (no temperature parameter).
        def create(*, model, max_tokens, extra_body, system, messages, thinking):
            self.assertEqual(extra_body, {'temperature': 0.2})
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='OK')])
        client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
        self.assertEqual(client.complete('probe', 'Reply OK', 'Hello'), 'OK')
