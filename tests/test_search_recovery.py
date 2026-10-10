import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lodestar.config import Config
from lodestar.tools.arxiv_search import tool_search_papers


class SearchRecoveryTests(unittest.TestCase):
    def test_independent_queries_interleave_deduplicate_and_keep_provenance(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        first = [{'url': 'https://paper/a', 'dedup_key': 'a'}, {'url': 'https://paper/b', 'dedup_key': 'b'}]
        second = [{'url': 'https://paper/c', 'dedup_key': 'c'}, {'url': 'https://paper/a', 'dedup_key': 'a'}]
        with patch('lodestar.tools.arxiv_search._search_arxiv', side_effect=[first, second]) as search:
            result = tool_search_papers(ws, queries=['agent memory', 'agent evaluation'], days=7)
        self.assertEqual([s['dedup_key'] for s in result['sources']], ['a', 'c', 'b'])
        self.assertEqual(result['sources'][0]['matched_queries'], ['agent memory', 'agent evaluation'])
        self.assertEqual([c.args[0] for c in search.call_args_list], ['agent memory', 'agent evaluation'])
        self.assertTrue(all(c.kwargs['since'] is not None for c in search.call_args_list))
        self.assertNotIn('matched_queries', first[0])

    def test_partial_failure_keeps_other_query_evidence(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.tools.arxiv_search._search_arxiv', side_effect=[
                RuntimeError('offline'), [{'url': 'https://paper', 'abstract': 'Evidence'}]]):
            result = tool_search_papers(ws, queries=['a', 'b'])
        self.assertTrue(result['partial'])
        self.assertNotIn('error', result)
        self.assertEqual(len(result['sources']), 1)
        self.assertIn('error', result['query_results'][0])

    def test_invalid_batches_do_not_contact_provider(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        for queries in ([], ['a'] * 4, ['a', 5], [' ']):
            with patch('lodestar.tools.arxiv_search._search_arxiv') as search:
                self.assertIn('error', tool_search_papers(ws, queries=queries))
            search.assert_not_called()

    def test_empty_result_reports_actual_query_and_recovery(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.tools.arxiv_search._search_arxiv', return_value=[]):
            result = tool_search_papers(ws, 'AI for AI automated machine learning')
        self.assertEqual(result['sources'], [])
        self.assertIsNone(result['days'])
        self.assertIn('abs:AI AND abs:for', result['effective_query'])
        self.assertIn('ANDed', result['recovery'])
        self.assertNotIn('error', result)

    def test_provider_failure_remains_distinct_from_empty_search(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.tools.arxiv_search._search_arxiv', side_effect=RuntimeError('offline')):
            result = tool_search_papers(ws, 'AI4AI')
        self.assertEqual(result['failure_kind'], 'provider_unavailable')
        self.assertIn('error', result)
        self.assertNotIn('effective_query', result)
