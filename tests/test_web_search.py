import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from lodestar.config import Config
from lodestar.tools.web_search import tool_search_web


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.ws = SimpleNamespace(config=Config(search_mode='live'))

    def response(self, text, status=200):
        return Mock(text=text, status_code=status, raise_for_status=Mock())

    def test_challenge_is_failure_and_never_uses_off_topic_rss(self):
        for response in (self.response('<form id="challenge-form">captcha</form>'),
                         self.response('verification', 202)):
            with patch('lodestar.tools.web_search.requests.post', return_value=response), \
                    patch('lodestar.tools.web_search.requests.get') as get:
                result = tool_search_web(self.ws, 'Sora video generation')
            self.assertEqual(result['sources'], [])
            self.assertEqual(result['failure_kind'], 'provider_unavailable')
            get.assert_not_called()

    def test_transport_failure_is_distinct_from_valid_empty_results(self):
        with patch('lodestar.tools.web_search.requests.post', side_effect=requests.Timeout()):
            self.assertIn('error', tool_search_web(self.ws, 'Sora'))
        with patch('lodestar.tools.web_search.requests.post', return_value=self.response('<div>No results found for Sora</div>')):
            result = tool_search_web(self.ws, 'Sora')
        self.assertEqual(result['sources'], [])
        self.assertNotIn('error', result)

    def test_unrecognized_200_page_is_not_a_successful_empty_search(self):
        with patch('lodestar.tools.web_search.requests.post', return_value=self.response('<html>Enable JavaScript</html>')):
            result = tool_search_web(self.ws, 'Sora')
        self.assertEqual(result['failure_kind'], 'provider_unavailable')

    def test_live_results_have_provider_query_and_http_urls(self):
        html = '<a class="result-link" href="https://openai.com/sora/">Sora</a><td class="result-snippet">Video generation</td>'
        html += '<a class="result-link" href="javascript:alert(1)">Bad</a>'
        with patch('lodestar.tools.web_search.requests.post', return_value=self.response(html)) as post:
            result = tool_search_web(self.ws, 'OpenAI Sora', 3)
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['query'], 'OpenAI Sora')
        self.assertEqual(result['sources'][0]['provider'], 'duckduckgo')
        self.assertEqual(post.call_args.kwargs['data']['q'], 'OpenAI Sora')

    def test_invalid_input_does_not_reach_search_engine(self):
        for query, count in [('', 3), ('Sora', -1), ('Sora', 500)]:
            with patch('lodestar.tools.web_search.requests.post') as post:
                self.assertIn('error', tool_search_web(self.ws, query, count))
            post.assert_not_called()
