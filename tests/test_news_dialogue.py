import json
import unittest
from datetime import datetime
from unittest.mock import patch

from tests import test_dialogue as fixtures
from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.dialogue import gather
from lodestar.agent.explanation import explain
from lodestar.tools.web_search import tool_search_web


class NewsDialogueTests(unittest.TestCase):
    setUp = fixtures.DialogueTests.setUp
    tearDown = fixtures.DialogueTests.tearDown
    def test_auto_news_uses_web_loop_with_real_clock(self):
        url = 'https://example.com/news'
        quote = 'Published today: a new open model is available.'
        self.llm.complete_json.side_effect = [
            {'action': 'search_web', 'query': 'AI news'},
            {'action': 'read_webpage', 'url': url}, {'action': 'answer'},
            {'blocks': [{'kind': 'claim', 'text': '官方发布了新模型。', 'paper_url': url, 'quote': quote}]}]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': [{'url': url}]}, {'text': quote}]), patch(
                'lodestar.agent.conversation.ResearchAgent') as research:
            agent = ConversationAgent(self.ws, self.llm)
            answer = agent.turn(agent.start(), '今天ai有什么最新进展')
        research.assert_not_called()
        self.assertIn('[来源]', answer['answer'])
        self.assertNotIn('Research Brief', answer['answer'])
        context = json.loads(self.llm.complete_json.call_args_list[0].args[2])
        self.assertEqual(context['current_time'][:10], datetime.now().astimezone().date().isoformat())

    def test_empty_search_can_rewrite_before_reading(self):
        self.llm.complete_json.side_effect = [
            {'action': 'search_web', 'query': 'narrow'},
            {'action': 'search_web', 'query': 'broader'}, {'action': 'answer'}]
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

    def test_news_cannot_bypass_citations_via_background(self):
        self.llm.complete_json.return_value = {'blocks': [
            {'kind': 'background', 'text': 'A new model was released today.'},
            {'kind': 'gap', 'text': '没有可核对的今日来源。'}]}
        answer, audit = explain(self.llm, {'papers': [], 'require_sources': True})
        self.assertNotIn('A new model', answer)
        self.assertFalse(audit['contract_valid'])
        self.assertEqual(self.llm.complete_json.call_count, 2)
