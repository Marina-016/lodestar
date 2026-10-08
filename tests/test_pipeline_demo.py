import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lodestar.agent.pipeline_demo import run


class PipelineDemoTests(unittest.TestCase):
    def test_isolated_lineage_actual_execution_and_explicit_fixture(self):
        with tempfile.TemporaryDirectory() as temp, patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')):
            root=Path(temp)/'demo'
            manifest=run(root)
            self.assertEqual(manifest['model_calls'],0)
            self.assertEqual(manifest['mode'],'offline_fixture')
            self.assertEqual(manifest['learning_events'],0)
            self.assertEqual(manifest['adoption_status'],'not_decided')
            report=json.loads((root/'ab/result.json').read_text(encoding='utf-8'))
            self.assertEqual(report['verdict'],'measured')
            self.assertEqual(report['case_count'],6)
            self.assertEqual(report['arms']['baseline']['exact_output_rate'],0.5)
            self.assertEqual(report['arms']['candidate']['exact_output_rate'],1)
            self.assertEqual(report['lineage']['plan_id'],manifest['plan_id'])
            self.assertTrue((root/'ab/candidate/stdout.txt').exists())
            with self.assertRaises(FileExistsError):
                run(root)
