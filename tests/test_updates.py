import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.tools.updates import tool_search_updates


class CurrentUpdateToolTests(unittest.TestCase):
    def setUp(self):
        self.mock_ws = SimpleNamespace(config=Config(search_mode='mock'))
        self.live_ws = SimpleNamespace(config=Config(search_mode='live', tool_timeout_s=10))

    def test_mock_records_authority_and_never_exposes_twitter_source(self):
        result = tool_search_updates(self.mock_ws, 'OpenAI 最近发布了什么', 'official')
        self.assertTrue(result['sources'])
        self.assertTrue(all(item['authority'] == 'primary' for item in result['sources']))
        self.assertTrue(all(item['dynamic_source'] == 'official' for item in result['sources']))
        self.assertIn('retrieved_at', result['sources'][0])
        self.assertIn('error', tool_search_updates(self.mock_ws, 'anything', 'twitter'))

    def test_official_lookup_filters_untrusted_hosts(self):
        with patch('lodestar.tools.updates.tool_search_web', return_value={'sources': [
                {'url': 'https://openai.com/news/update', 'title': 'Official'},
                {'url': 'https://lookalike.test/openai', 'title': 'Not official'}]}):
            result = tool_search_updates(self.live_ws, 'OpenAI 最新发布', 'official')
        self.assertEqual([item['title'] for item in result['sources']], ['Official'])
        self.assertEqual(result['sources'][0]['source_type'], 'official_release')

    def test_github_uses_public_api_and_surfaces_repository_time(self):
        response = Mock(status_code=200, headers={'x-ratelimit-remaining': '59'})
        response.json.return_value = {'items': [{'html_url': 'https://github.com/openai/example',
            'full_name': 'openai/example', 'pushed_at': '2026-11-09T12:00:00Z', 'description': 'SDK'}]}
        with patch('lodestar.tools.updates.requests.get', return_value=response) as get:
            result = tool_search_updates(self.live_ws, 'OpenAI SDK', 'github')
        self.assertEqual(result['sources'][0]['source_type'], 'github_update')
        self.assertEqual(result['sources'][0]['date'], '2026-11-09')
        self.assertEqual(result['sources'][0]['authority'], 'primary')
        self.assertEqual(get.call_args.args[0], 'https://api.github.com/search/repositories')

    def test_recent_paper_route_keeps_arxiv_provenance(self):
        with patch('lodestar.tools.updates.tool_search_papers', return_value={'sources': [
                {'url': 'https://arxiv.org/abs/2601.00001', 'title': 'Paper', 'source_type': 'paper'}]}):
            result = tool_search_updates(self.live_ws, 'memory', 'papers', days=14)
        self.assertEqual(result['sources'][0]['authority'], 'paper')
        self.assertEqual(result['sources'][0]['dynamic_source'], 'papers')
