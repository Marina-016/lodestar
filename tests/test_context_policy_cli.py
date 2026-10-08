import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lodestar.cli import cmd_context_policy
from lodestar.config import Config


class ContextPolicyCliTests(unittest.TestCase):
    def test_validation_does_not_create_client_or_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); protocol = root / 'protocol.json'
            protocol.write_text(json.dumps({'version': 1, 'dataset_kind': 'functional_fixture',
                'context_budget': 100, 'cases': [{'id': 'one', 'question': 'Known?',
                'packets': [{'id': 'p1', 'text': 'Nothing known.'}],
                'expected': {'answer': 'UNKNOWN', 'citations': []}}]}), encoding='utf-8')
            args = SimpleNamespace(protocol=str(protocol), out=str(root/'out'), run_live=False, free_quota_confirmed=False)
            with patch('lodestar.cli.LLMClient', side_effect=AssertionError('No model client')), contextlib.redirect_stdout(io.StringIO()) as stdout:
                cmd_context_policy(args, Config())
            self.assertEqual(json.loads(stdout.getvalue())['model_calls'], 0)
            self.assertFalse((root/'out').exists())
            args.run_live = True
            for provider, mode, disabled, confirmed in [('dashscope','live',False,False),
                    ('dashscope','live',True,True), ('anthropic','live',False,True), ('dashscope','mock',False,True)]:
                cfg = Config(llm_provider=provider,llm_mode=mode,model_calls_disabled=disabled)
                args.free_quota_confirmed = confirmed
                with patch('lodestar.cli.LLMClient', side_effect=AssertionError('No model client')):
                    with self.assertRaises(ValueError): cmd_context_policy(args, cfg)
            self.assertFalse((root/'out').exists())
