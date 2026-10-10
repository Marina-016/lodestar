import json
import unittest
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from tests import test_dialogue as fixtures
from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.dialogue import gather
from lodestar.tools.web_search import tool_search_web
from lodestar.tools.web_read import tool_read_webpage


class NewsDialogueTests(unittest.TestCase):
    setUp = fixtures.DialogueTests.setUp
    tearDown = fixtures.DialogueTests.tearDown

    def test_web_read_retains_declared_publication_time_and_truncation(self):
        self.ws.config.search_mode = 'live'
        page = SimpleNamespace(text='<html><head><meta property="article:published_time" content="2026-10-10T08:00:00Z"></head>'
            '<body>' + 'Article text ' * 30 + '</body></html>', raise_for_status=lambda: None)
        with patch('lodestar.tools.web_read.requests.get', return_value=page):
            result = tool_read_webpage(self.ws, 'https://example.com', char_budget=40)
        self.assertEqual(result['date'], '2026-10-10')
        self.assertEqual(result['date_basis'], 'page_declared_publication_time')
        self.assertTrue(result['truncated'])
        self.assertIn('预算=40字符', result['text'])
    def test_auto_news_uses_web_loop_with_real_clock(self):
        url = 'https://example.com/news'
        quote = 'Published today: a new open model is available.'
        self.llm.dialogue_step.side_effect = [
            fixtures.calls({'action': 'search_web', 'query': 'AI news'}),
            fixtures.calls({'action': 'read_webpage', 'url': url}),
            fixtures.answer(f'来源报道发布了新模型。[来源]({url})')]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': [{'url': url}]}, {'text': quote}]), patch(
                'lodestar.agent.conversation.ResearchAgent') as research:
            agent = ConversationAgent(self.ws, self.llm)
            answer = agent.turn(agent.start(), '今天ai有什么最新进展')
        research.assert_not_called()
        self.assertIn('[来源]', answer['answer'])
        self.assertNotIn('Research Brief', answer['answer'])
        context = json.loads(self.llm.dialogue_step.call_args_list[0].args[1][0]['content'].split('\n', 1)[1].split('\n\nUser request:', 1)[0])
        self.assertEqual(context['current_time'][:10], datetime.now().astimezone().date().isoformat())

    def test_empty_search_can_rewrite_before_reading(self):
        self.llm.dialogue_step.side_effect = [
            fixtures.calls({'action': 'search_web', 'query': 'narrow'}),
            fixtures.calls({'action': 'search_web', 'query': 'broader'}), fixtures.answer()]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': []}, {'sources': [{'url': 'https://example.com'}]}]) as tool:
            context = gather(self.ws, self.llm, {'message': '今日新闻', 'papers': []})
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(context['papers'], [])  # snippets never become read evidence

    def test_empty_primary_web_search_uses_independent_web_fallback(self):
        self.ws.config.search_mode = 'live'
        with patch('lodestar.tools.web_search._search_duckduckgo', return_value=([], 'empty')), patch(
                'lodestar.tools.web_search._search_bing', return_value=[{'url': 'https://example.com'}]) as fallback:
            result = tool_search_web(self.ws, 'AI news')
        fallback.assert_called_once()
        self.assertEqual(result['fallback'], 'bing')
        self.assertEqual(len(result['sources']), 1)

    def test_news_answer_receives_clock_and_actual_source_errors(self):
        text = '当前来源请求失败，无法确认今天的消息。'
        self.llm.dialogue_step.side_effect = [fixtures.calls({'action': 'search_web', 'query': 'AI news'}), fixtures.answer(text)]
        with patch('lodestar.tools.registry.call_tool', return_value={'error': 'network timeout'}):
            result = gather(self.ws, self.llm, {'papers': [], 'message': '今天有什么新闻'})
        messages = self.llm.dialogue_step.call_args.args[1]
        self.assertIn('network timeout', messages[-2]['content'][0]['content'])
        self.assertIn('current_time', messages[0]['content'])
        self.assertEqual(result['answer'], text)
        self.assertIn('not factual verification', result['grounding']['validation_scope'])
