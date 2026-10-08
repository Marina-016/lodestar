import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lodestar.agent.demo import run_demo


class AgentDemoTests(unittest.TestCase):
    def test_isolated_fixture_and_no_network(self):
        with tempfile.TemporaryDirectory() as root, patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            output = Path(root) / 'demo'
            result = run_demo(output)
            self.assertEqual(result['model_calls'], 0)
            self.assertGreater(result['evidence_reused'], 0)
            self.assertEqual(result['mastery'], 'unknown')
            self.assertTrue((output / 'demo.db').exists())
            with self.assertRaises(FileExistsError):
                run_demo(output)
