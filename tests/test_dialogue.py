import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lodestar.agent.dialogue import gather
from lodestar.agent.explanation import explain
from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.context import Workspace


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Config(llm_mode='mock', search_mode='mock',
            db_path=Path(self.temp.name) / 'db', workspace_dir=Path(self.temp.name) / 'ws'))
        self.llm = Mock()

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def test_direct_answers_need_no_tools_or_papers(self):
        for question, text in [
            ('用一句话解释缓存', '缓存保存可复用结果，减少重复计算。'),
            ('换个话题，给我一个 Python 求和示例', '```python\nprint(sum([1, 2, 3]))\n```'),
            ('Translate 缓存 into English', 'Cache.'),
            ('把刚才的解释缩成十个字', '缓存让重复访问更快。'),
        ]:
            with self.subTest(question=question):
                self.llm.complete_json.side_effect = [
                    {'action': 'answer'}, {'blocks': [{'kind': 'background', 'text': text}]},
                    {'items': []}]
                agent = ConversationAgent(self.ws, self.llm)
                with patch('lodestar.tools.registry.call_tool') as tool:
                    result = agent.turn(agent.start(), question)
                self.assertEqual(result['answer'], text)
                tool.assert_not_called()
                self.assertEqual(result['grounding']['validated_claims'], [])

    def test_model_selects_relevant_source_not_first_two(self):
        papers = [{'url': f'https://arxiv.org/abs/2601.0000{i}', 'source_type': 'paper',
                   'content': 'Existing abstract.'} for i in range(3)]
        self.llm.complete_json.side_effect = [{'action': 'read_paper', 'url': papers[2]['url']}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={
                'text': 'Relevant body evidence.', 'read_depth': 'full'}) as tool:
            context = gather(self.ws, self.llm, {'message': '比较第三篇的方法', 'papers': papers})
        self.assertEqual(tool.call_args.args[2]['url'], papers[2]['url'])
        self.assertEqual(context['papers'][0]['content'], 'Relevant body evidence.')

    def test_search_read_answer_composes_existing_tools(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.complete_json.side_effect = [
            {'action': 'search_papers', 'query': 'memory'}, {'action': 'read_paper', 'url': url}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': [{'url': url, 'snippet': 'Search snippet is not body evidence.'}]},
                {'text': 'The method keeps a bounded memory.', 'read_depth': 'full'}]) as tool:
            context = gather(self.ws, self.llm, {'message': '找一篇解释记忆管理的论文', 'papers': []})
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(len(context['papers']), 1)
        self.assertEqual(context['papers'][0]['read_depth'], 'full')

    def test_constraints_unknown_tools_and_repeats_cannot_trigger_effects(self):
        for action in [{'action': 'record_learning_evidence'},
                       {'action': 'read_paper', 'url': ['bad type']},
                       {'action': 'read_paper', 'url': 'http://localhost/private'},
                       {'action': 'search_papers', 'query': 'memory'}]:
            with self.subTest(action=action):
                self.llm.complete_json.side_effect = None
                self.llm.complete_json.return_value = action
                with patch('lodestar.tools.registry.call_tool') as tool:
                    context = gather(self.ws, self.llm, {'message': '不要联网，只用已有知识', 'papers': []})
                tool.assert_not_called()
                self.assertLessEqual(len(context['dialogue_events']), 3)

    def test_failed_read_preserves_old_evidence_and_reaches_answer(self):
        paper = {'url': 'https://arxiv.org/abs/2601.00001', 'source_type': 'paper', 'content': 'Original'}
        self.llm.complete_json.side_effect = [{'action': 'read_paper', 'url': paper['url']}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={'error': 'timeout'}):
            context = gather(self.ws, self.llm, {'message': '读一下', 'papers': [paper]})
        self.assertEqual(context['papers'], [paper])
        self.assertEqual(context['tool_results'][0]['result']['error'], 'timeout')

    def test_distinct_actions_cannot_exceed_budget(self):
        self.llm.complete_json.side_effect = [
            {'action': 'search_papers', 'query': f'query {index}'} for index in range(5)]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            context = gather(self.ws, self.llm, {'message': '找相关论文', 'papers': []}, max_calls=3)
        self.assertEqual(tool.call_count, 3)
        self.assertEqual(context['tool_budget_remaining'], 0)

    def test_malformed_block_is_not_delivered(self):
        self.llm.complete_json.return_value = {'blocks': [{'kind': [], 'text': 'bad'}]}
        answer, audit = explain(self.llm, {'papers': []})
        self.assertNotIn('bad', answer)
        self.assertFalse(audit['contract_valid'])

    def test_flexible_answer_retains_order_and_filters_unsupported_claim(self):
        self.llm.complete_json.return_value = {'blocks': [
            {'kind': 'background', 'text': '先看这个例子：'},
            {'kind': 'claim', 'text': '有据的方法', 'paper_url': 'https://paper',
             'quote': 'The method uses a bounded context.'},
            {'kind': 'claim', 'text': '不支持的性能提升', 'paper_url': 'https://paper',
             'quote': 'The method always doubles performance.'},
            {'kind': 'hypothesis', 'text': '可能适合你的任务。'}]}
        answer, audit = explain(self.llm, {'papers': [{'url': 'https://paper', 'source_type': 'paper',
            'content': 'The method uses a bounded context.'}]})
        self.assertTrue(answer.startswith('先看这个例子：'))
        self.assertIn('[来源](https://paper)', answer)
        self.assertIn('推测：', answer)
        self.assertNotIn('不支持的性能提升', answer)
        self.assertEqual(len(audit['validated_claims']), 1)

    def test_project_mention_is_not_automatically_a_proposal(self):
        from lodestar.agent.routing import route
        self.assertEqual(route('我的项目里，缓存和记忆有什么区别？').intent, 'followup')

    def test_project_context_stays_bound_and_enforces_export_policy(self):
        self.llm.complete_json.side_effect = [{'action': 'project_context'}, {'action': 'answer'}]
        self.ws.config.llm_mode = 'live'
        with patch('lodestar.agent.dialogue.collect', return_value={
                'status': 'needs_export_scope', 'documents': []}) as collect:
            context = gather(self.ws, self.llm, {'message': '我的项目如何组织缓存', 'papers': []}, project_id=7)
        self.assertEqual(collect.call_args.args[3], 7)
        self.assertTrue(collect.call_args.kwargs['live'])
        self.assertEqual(context['tool_results'][0]['result']['status'], 'needs_export_scope')
