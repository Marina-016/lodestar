import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from lodestar.eval.environment import capture, verify


class EnvironmentTests(unittest.TestCase):
    def test_receipt_has_pins_without_environment_values_or_source_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            distributions = [SimpleNamespace(metadata={'Name': 'requests'}, version='2.32.5'),
                SimpleNamespace(metadata={'Name': 'lodestar'}, version='0.4.7'),
                SimpleNamespace(metadata={'Name': 'bad;command'}, version='1.0')]
            with patch('lodestar.eval.environment.metadata.distributions', return_value=distributions), patch.dict(
                    os.environ, {'DASHSCOPE_API_KEY': 'sentinel-private-value'}):
                hashes = capture(folder)
            root = Path(folder)
            receipt = (root / 'environment.json').read_text()
            self.assertNotIn('sentinel-private-value', receipt)
            self.assertEqual(json.loads(receipt)['packages'], {'lodestar': '0.4.7', 'requests': '2.32.5'})
            self.assertEqual((root / 'requirements-frozen.txt').read_text(), 'requests==2.32.5\n')
            self.assertEqual(verify(root, hashes), [])
            (root / 'requirements-frozen.txt').write_text('tampered')
            self.assertTrue(verify(root, hashes))
            (root / 'environment.json').unlink()
            self.assertTrue(any('Missing' in item for item in verify(root, hashes)))
            self.assertTrue(verify(root, {'../../anything': 'hash'}))
