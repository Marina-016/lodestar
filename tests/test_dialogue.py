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
                    {'action': 'answer'}]
                self.llm.complete.return_value = text
                agent = ConversationAgent(self.ws, self.llm)
                with patch('lodestar.tools.registry.call_tool') as tool:
                    result = agent.turn(agent.start(), question)
                self.assertEqual(result['answer'], text)
                tool.assert_not_called()
                self.assertEqual(result['grounding']['format'], 'markdown')

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

    def test_unknown_tools_and_out_of_scope_reads_cannot_trigger_effects(self):
        for action in [{'action': 'record_learning_evidence'},
                       {'action': 'read_paper', 'url': ['bad type']},
                       {'action': 'read_paper', 'url': 'http://localhost/private'}]:
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

    def test_empty_model_answer_is_rejected_without_repair_loop(self):
        from lodestar.llm import LLMError
        self.llm.complete.return_value = ''
        with self.assertRaises(LLMError): explain(self.llm, {'papers':[]})
        self.llm.complete.assert_called_once()
        self.llm.complete_json.assert_not_called()


    def test_markdown_answer_is_preserved_including_intro_and_comparison(self):
        text = '根据摘要，先关注这两篇。\n\n| 论文 | 方向 |\n|---|---|\n| A | 记忆 |\n\n我建议先读 A。'
        self.llm.complete.return_value = text
        answer, audit = explain(self.llm, {'papers':[]})
        self.assertEqual(answer,text)
        self.assertEqual(audit['format'],'markdown')
        self.llm.complete_json.assert_not_called()


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

    def test_project_plan_language_stays_in_model_loop(self):
        from lodestar.memory import repo
        project_id = repo.upsert_project(self.ws.conn, 'demo')
        self.llm.complete_json.side_effect = [
            {'action': 'answer'}]
        self.llm.complete.return_value = '先比较两种可行方案。'
        agent = ConversationAgent(self.ws, self.llm)
        with patch('lodestar.agent.routing.route', side_effect=AssertionError('legacy router')), patch(
                'lodestar.agent.conversation.generate') as plan:
            result = agent.turn(agent.start(project_id=project_id), '给项目生成方案，先讨论思路')
        plan.assert_not_called()
        self.assertEqual(result['answer'], '先比较两种可行方案。')
        self.assertEqual(result['route_reason'], 'model-led dialogue')

    def test_quoted_retrieval_prohibition_does_not_override_model(self):
        self.llm.complete_json.side_effect = [
            {'action': 'search_papers', 'query': 'retrieval policy'}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            gather(self.ws, self.llm, {'message': '找研究讨论“不要搜索”指令的论文', 'papers': []})
        tool.assert_called_once()

    def test_model_can_answer_without_search_when_user_requests_it(self):
        self.llm.complete_json.return_value = {'action': 'answer'}
        with patch('lodestar.tools.registry.call_tool') as tool:
            gather(self.ws, self.llm, {'message': '不要联网，解释缓存', 'papers': []})
        tool.assert_not_called()

    def test_model_marks_current_source_requirement_without_keywords(self):
        self.llm.complete_json.return_value = {'action': 'answer', 'require_sources': True}
        context = gather(self.ws, self.llm, {'message': '它刚刚发布的版本改变了什么', 'papers': []})
        self.assertTrue(context['require_sources'])

    def test_paper_overview_keeps_chinese_explanation(self):
        url = 'https://arxiv.org/abs/2601.00001'
        text = f'根据摘要，这篇论文研究记忆检索，值得关注它如何筛选上下文。[论文]({url})'
        self.llm.complete.return_value = text
        answer, audit = explain(self.llm, {'papers':[], 'candidates':[
            {'url':url,'title':'AI research','abstract':'The method selects relevant context.'}]})
        self.assertEqual(answer,text)
        self.assertNotIn('未核对正文',answer)
        self.assertEqual(audit['source_depths'][url],'abstract')


    def test_ordinary_overview_does_not_need_json_blocks_or_quotes(self):
        self.llm.complete.return_value = '可以先看模型记忆和 Agent 评测两个方向。'
        answer, _ = explain(self.llm, {'papers':[]})
        self.assertIn('模型记忆',answer)
        self.llm.complete_json.assert_not_called()


    def test_model_controls_actual_paper_search_order(self):
        self.llm.complete_json.side_effect = [
            {'action': 'search_papers', 'query': 'agents', 'sort_by': 'submittedDate'}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            gather(self.ws, self.llm, {'message': '看看最近的论文', 'papers': []})
        self.assertEqual(tool.call_args.args[2]['sort_by'], 'submittedDate')

    def test_repeated_operation_ignores_incidental_model_flags(self):
        self.llm.complete_json.side_effect = [
            {'action': 'search_papers', 'query': 'agents'},
            {'action': 'search_papers', 'query': 'agents', 'require_sources': True}]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            context = gather(self.ws, self.llm, {'message': '找论文', 'papers': []})
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(context['dialogue_events'][-1]['error'], 'repeated action stopped')

    def test_web_candidates_remain_snippets_in_model_context(self):
        url = 'https://openreview.net/forum?id=example'
        self.llm.complete.return_value = f'这里有一个相关候选：[论文]({url})'
        _, audit = explain(self.llm, {'papers':[], 'candidates':[
            {'url':url,'title':'Agent evaluation paper','snippet':'Search result.'}]})
        self.assertEqual(audit['source_depths'][url],'search_snippet')


    def test_arxiv_timeout_reports_source_failure_not_empty_topic(self):
        import requests
        from lodestar.tools.arxiv_search import tool_search_papers
        self.ws.config.search_mode = 'live'
        with patch('lodestar.tools.arxiv_search._search_arxiv', side_effect=requests.Timeout('timeout')):
            result = tool_search_papers(self.ws, 'agents')
        self.assertEqual(result['failure_kind'], 'provider_unavailable')
        self.assertIn('search_web', result['recovery'])

    def test_model_selects_independent_trending_discovery(self):
        self.llm.complete_json.side_effect = [
            {'action': 'discover_papers', 'kind': 'trending', 'query': ''}, {'action': 'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            gather(self.ws, self.llm, {'message': '看看热门论文', 'papers': []})
        self.assertEqual(tool.call_args.args[1], 'discover_papers')
        self.assertEqual(tool.call_args.args[2]['kind'], 'trending')

    def test_model_can_read_abstract_without_forced_full_text(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.complete_json.side_effect = [{'action': 'read_paper', 'url': url}, {'action':'answer'}]
        with patch('lodestar.tools.registry.call_tool', return_value={'text':'An abstract.'}) as tool:
            gather(self.ws, self.llm, {'message':'读摘要', 'papers':[{'url':url}]})
        self.assertFalse(tool.call_args.args[2]['full_text'])

    def test_current_discovery_does_not_strip_natural_intro(self):
        self.llm.complete.return_value = '下面是本周的论文，我先按研究方向介绍。'
        answer, _ = explain(self.llm, {'require_sources':True,'papers':[]})
        self.assertEqual(answer,self.llm.complete.return_value)


    def test_candidate_list_survives_restart_and_can_be_read_on_followup(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.complete_json.side_effect = [
            {'action':'search_papers','query':'agent'}, {'action':'answer'}]
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        self.llm.complete.return_value = f'这是一篇 Agent 论文：[论文]({url})'
        with patch('lodestar.tools.registry.call_tool', return_value={'sources':[{'url':url,'title':'Agent','source_type':'paper'}]}):
            first = agent.turn(session,'找论文')
        self.assertEqual(first['evidence_reused'],0)
        self.llm.complete_json.side_effect = [
            {'action':'read_paper','url':url}, {'action':'answer'},
            {'items':[]}]
        # New agent instance restores candidate links from persisted message metadata.
        with patch('lodestar.tools.registry.call_tool', return_value={'text':'Abstract evidence.'}) as tool:
            second = ConversationAgent(self.ws,self.llm).turn(session,'读一下第一篇摘要')
        self.assertEqual(tool.call_args.args[2]['url'],url)
        self.assertEqual(second['evidence_reused'],1)

    def test_model_can_return_code_without_host_prefixes(self):
        text = '```python\nprint("hello")\n```'
        self.llm.complete.return_value = text
        answer, _ = explain(self.llm, {'papers':[]})
        self.assertEqual(answer,text)
