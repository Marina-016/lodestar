import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from lodestar.agent.exposure import record_exposure
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.memory import learning


class ExposureTests(unittest.TestCase):
    def test_both_quotes_required_and_mastery_stays_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = Workspace(Config(db_path=Path(temp) / 'db', workspace_dir=Path(temp) / 'ws'))
            try:
                llm = Mock()
                item = {'technology': 'PaperSystemName', 'method': 'Tool routing',
                    'paper_url': 'https://arxiv.org/abs/2601.00001',
                    'paper_quote': 'routing policy', 'explanation_quote': '路由策略'}
                llm.complete_json.return_value = {'items': [item, {**item, 'paper_quote': 'fabricated quote'}]}
                result = record_exposure(ws, llm, 'task', 'u', '介绍路由策略',
                    [{'source_type': 'paper', 'url': item['paper_url'], 'content': 'The routing policy improves selection.'}], technology='Harness')
                self.assertEqual(len(result), 1)
                self.assertEqual(learning.profile(ws.conn, 'u')[0]['technology'], 'Harness')
                self.assertEqual(learning.profile(ws.conn, 'u')[0]['mastery'], 'unknown')
                self.assertEqual(learning.profile(ws.conn, 'u')[0]['papers'], [item['paper_url']])
            finally:
                ws.close()

    def test_array_response_keeps_quote_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = Workspace(Config(db_path=Path(temp) / 'db', workspace_dir=Path(temp) / 'ws'))
            try:
                llm = Mock()
                item = {'technology': 'Harness', 'method': 'Routing',
                        'paper_url': 'https://arxiv.org/abs/2601.00001',
                        'paper_quote': 'routing policy', 'explanation_quote': '路由策略'}
                llm.complete_json.return_value = [item, {**item, 'explanation_quote': '未讲解的方法'}]
                result = record_exposure(ws, llm, 'task', 'u', '介绍路由策略',
                    [{'source_type': 'paper', 'url': item['paper_url'], 'content': 'A routing policy.'}])
                self.assertEqual(len(result), 1)
                self.assertEqual(learning.profile(ws.conn, 'u')[0]['mastery'], 'unknown')
                self.assertTrue(llm.complete_json.call_args.kwargs['allow_list'])
            finally:
                ws.close()
