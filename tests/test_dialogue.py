import copy
import json
import tempfile
from datetime import datetime, timezone
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from lodestar.agent.dialogue import gather, tool_definitions
from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMError


def answer(text='A natural answer.'):
    return {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}


def calls(*operations):
    return {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': f'call_{i}', 'name': op['action'],
         'input': {k: v for k, v in op.items() if k != 'action'}}
        for i, op in enumerate(operations)]}


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Config(llm_mode='mock', search_mode='mock',
            db_path=Path(self.temp.name) / 'db', workspace_dir=Path(self.temp.name) / 'ws'))
        self.llm = Mock()
        self.llm.dialogue_step.return_value = answer()
        self.llm.complete_json.return_value = {'items': []}

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def run_dialogue(self, **kwargs):
        return gather(self.ws, self.llm, kwargs.pop('context', {'message': '研究这个方向', 'papers': []}), **kwargs)

    def test_direct_answers_without_json_planner_or_generator(self):
        for question, text in [('你好', '你好！'), ('Translate 缓存', 'Cache.'),
                               ('Python 示例', '~~~python\nprint(1)\n~~~')]:
            self.llm.dialogue_step.return_value = answer(text)
            agent = ConversationAgent(self.ws, self.llm)
            with patch('lodestar.tools.registry.call_tool') as tool:
                result = agent.turn(agent.start(), question)
            self.assertEqual(result['answer'], text)
            tool.assert_not_called()
        self.llm.complete.assert_not_called()
        self.llm.complete_json.assert_not_called()

    def test_search_read_answer_preserves_native_message_history(self):
        url = 'https://arxiv.org/abs/2601.00001'
        responses = iter([calls({'action': 'search_papers', 'query': 'memory'}),
                          calls({'action': 'read_paper', 'url': url}), answer()])
        snapshots = []
        def step(system, messages, tools, on_text):
            snapshots.append(copy.deepcopy(messages))
            return next(responses)
        self.llm.dialogue_step.side_effect = step
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': [{'url': url, 'abstract': 'Full abstract'}]},
                {'text': 'Body evidence', 'read_depth': 'full'}]):
            context = self.run_dialogue()
        self.assertEqual(context['papers'][0]['content'], 'Body evidence')
        self.assertEqual(snapshots[1][-2]['content'][0]['type'], 'tool_use')
        self.assertEqual(snapshots[1][-1]['content'][0]['tool_use_id'], 'call_0')
        self.assertIn('Full abstract', snapshots[2][-3]['content'][0]['content'])
        self.assertIn('Body evidence', snapshots[2][-1]['content'][0]['content'])
        self.llm.complete_json.assert_not_called()

    def test_reserved_answer_retains_method_and_experiment_reads(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.ws.config.enrich_venues = False
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': url,
            'full_text': True, 'query': query}) for query in ('method', 'experiment')] + [answer()]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'text': 'METHOD PASSAGE', 'read_depth': 'full', 'coverage': 'query_excerpt'},
                {'text': 'EXPERIMENT PASSAGE', 'read_depth': 'full', 'coverage': 'query_excerpt'}]):
            context = self.run_dialogue(max_calls=2, context={'message': '比较方法和实验', 'papers': [{'url': url}]})
        final_packet = self.llm.dialogue_step.call_args.args[1][0]['content']
        for text in ('METHOD PASSAGE', 'EXPERIMENT PASSAGE'):
            self.assertEqual(final_packet.count(text), 1)
            self.assertIn(text, context['papers'][0]['content'])
        self.assertEqual(context['grounding']['completion_mode'], 'reserved_answer')

    def test_multiple_reads_survive_agent_restart_and_followup(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.ws.config.enrich_venues = False
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': 'agent'}),
            calls({'action': 'read_paper', 'url': url, 'full_text': True, 'query': 'method'}),
            calls({'action': 'read_paper', 'url': url, 'full_text': True, 'query': 'experiment'}), answer()]
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        with patch('lodestar.tools.registry.call_tool', side_effect=[{'sources': [{'url': url}]},
                {'text': 'PERSISTED METHOD', 'read_depth': 'full'},
                {'text': 'PERSISTED EXPERIMENT', 'read_depth': 'full'}]):
            agent.turn(session, '分析论文')
        config = self.ws.config
        self.ws.close()
        self.ws = Workspace(config)
        self.llm.dialogue_step.side_effect = [answer()]
        with patch('lodestar.tools.registry.call_tool') as tool:
            ConversationAgent(self.ws, self.llm).turn(session, '实验是否支持这个机制？')
        tool.assert_not_called()
        packet = next(m['content'] for m in self.llm.dialogue_step.call_args.args[1]
                      if m['role'] == 'user' and m['content'].startswith('Current source context'))
        self.assertEqual(packet.count('PERSISTED METHOD'), 1)
        self.assertEqual(packet.count('PERSISTED EXPERIMENT'), 1)

    def test_explicit_plan_read_uses_the_same_non_destructive_evidence_merge(self):
        from lodestar.memory import repo
        project = repo.upsert_project(self.ws.conn, 'demo', url='https://github.com/example/demo')
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start(project_id=project)
        source = {'url': 'https://paper', 'source_type': 'paper', 'read_depth': 'full',
                  'content': 'OLD METHOD', 'read_query': 'method'}
        with self.ws.conn:
            self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                                 (json.dumps([source]), session))
        with patch('lodestar.tools.registry.call_tool', return_value={
                'text': 'NEW EXPERIMENT', 'read_depth': 'full'}), patch(
                'lodestar.agent.conversation.generate', return_value={'plan': 'A proposal'}) as generate:
            agent.turn(session, '提出方案', intent='plan')
        merged = generate.call_args.args[3][0]
        self.assertIn('OLD METHOD', merged['content'])
        self.assertIn('NEW EXPERIMENT', merged['content'])

    def test_read_refresh_failure_keeps_known_record_and_avoids_repeating_lookup(self):
        url = 'https://arxiv.org/abs/2601.00001'
        source = {'url': url, 'source_type': 'paper', 'publication_status': 'publication_record_found',
                  'venue': 'Journal', 'external_ids': {'DOI': 'known'},
                  'record_url': 'https://publisher/paper', 'verified_at': '2020-01-01T00:00:00+00:00'}
        failure = {'url': url, 'publication_status': 'unresolved', 'venue': None,
                   'external_ids': {}, 'verified_at': datetime.now(timezone.utc).isoformat(),
                   'publication_evidence': 'Provider unavailable'}
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': url,
            'full_text': True, 'query': query}) for query in ('method', 'experiment')] + [answer()]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'text': 'Method', 'read_depth': 'full'}, {'text': 'Experiment', 'read_depth': 'full'}]), patch(
                'lodestar.agent.dialogue.verify_publication', return_value=failure) as lookup:
            context = self.run_dialogue(context={'message': '分析论文', 'papers': [], 'candidates': [source]})
        lookup.assert_called_once()
        for saved in (context['papers'][0], context['candidates'][0]):
            self.assertEqual(saved['publication_status'], 'publication_record_found')
            self.assertEqual(saved['venue'], 'Journal')
            self.assertEqual(saved['external_ids'], {'DOI': 'known'})
            self.assertEqual(saved['latest_publication_lookup']['publication_status'], 'unresolved')

    def test_empty_results_can_be_rewritten(self):
        self.llm.dialogue_step.side_effect = [
            calls({'action': 'search_papers', 'query': 'AI4AI'}),
            calls({'action': 'search_papers', 'query': 'automated research'}), answer()]
        with patch('lodestar.tools.registry.call_tool', side_effect=[
                {'sources': []}, {'sources': [{'url': 'https://paper', 'abstract': 'Evidence'}]}]) as tool:
            result = self.run_dialogue()
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['papers'], [])

    def test_unknown_tools_bad_arguments_and_unseen_urls_cannot_execute(self):
        for operation in [
                {'action': 'record_learning_evidence'},
                {'action': 'read_paper', 'url': ['bad type']},
                {'action': 'read_webpage', 'url': 'http://localhost/private'},
                {'action': 'search_papers', 'query': 'AI', 'days': True},
                {'action': 'search_papers', 'query': 'AI', 'days': 999},
                {'action': 'search_web', 'query': 'AI', 'write': True},
                {'action': 'project_context', 'query': 'private'}]:
            self.llm.dialogue_step.side_effect = [calls(operation), answer()]
            with patch('lodestar.tools.registry.call_tool') as tool:
                result = self.run_dialogue()
            tool.assert_not_called()
            self.assertIn('error', result['dialogue_events'][0]['result'])

    def test_invalid_argument_json_returns_repairable_result(self):
        response = calls({'action': 'search_web', 'query': 'AI'})
        response['content'][0]['input'] = '{"query":'
        self.llm.dialogue_step.side_effect = [response, answer()]
        with patch('lodestar.tools.registry.call_tool') as tool:
            result = self.run_dialogue()
        tool.assert_not_called()
        self.assertIn('object', result['dialogue_events'][0]['result']['error'])

    def test_parallel_batch_is_bounded_and_reserved_answer_has_evidence(self):
        self.llm.dialogue_step.side_effect = [calls(*[
            {'action': 'search_web', 'query': f'query {i}'} for i in range(4)]), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            result = self.run_dialogue(max_calls=2)
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(len(result['dialogue_events']), 2)
        final = self.llm.dialogue_step.call_args
        self.assertEqual(final.args[2], [])
        self.assertEqual(len(final.args[1]), 1)
        self.assertIn('operations', final.args[1][0]['content'])
        self.assertNotIn('dialogue_events', final.args[1][0]['content'])
        self.assertEqual(result['grounding']['completion_mode'], 'reserved_answer')

    def test_no_extra_tool_execution_after_budget(self):
        self.llm.dialogue_step.return_value = calls({'action': 'search_web', 'query': 'retry'})
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            result = self.run_dialogue(max_calls=2)
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(self.llm.dialogue_step.call_count, 3)
        self.assertEqual(result['grounding']['completion_mode'], 'evidence_fallback')
        self.assertIn('未获得可用证据', result['answer'])

    def test_independent_queries_count_against_operation_budget(self):
        self.llm.dialogue_step.side_effect = [
            calls({'action': 'search_papers', 'queries': ['agent memory', 'agent evaluation', 'automated research']}),
            answer('已有证据的回答')]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            result = self.run_dialogue(max_calls=2)
        self.assertEqual(tool.call_args.args[2]['queries'], ['agent memory', 'agent evaluation'])
        self.assertEqual(result['dialogue_events'][0]['cost'], 2)
        self.assertEqual(result['dialogue_events'][0]['result']['skipped_queries'], ['automated research'])
        self.assertEqual(result['grounding']['search_operations'], 2)
        self.assertEqual(self.llm.dialogue_step.call_count, 2)

    def test_broken_final_tool_request_preserves_retrieved_candidates(self):
        self.llm.dialogue_step.return_value = calls({'action': 'search_papers', 'query': 'agent'})
        with patch('lodestar.tools.registry.call_tool', return_value={
                'sources': [{'url': 'https://paper', 'title': 'Candidate'}]}):
            result = self.run_dialogue(max_calls=1)
        self.assertIn('[Candidate](https://paper)', result['answer'])
        self.assertIn('仍需核验', result['answer'])
        self.assertEqual(result['grounding']['completion_mode'], 'evidence_fallback')

    def test_invalid_batch_arguments_do_not_execute(self):
        for arguments in [{'queries': []}, {'queries': ['a'] * 4}, {'queries': ['a', 1]},
                          {'queries': ['a'], 'query': 'b'}]:
            self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', **arguments}), answer()]
            with patch('lodestar.tools.registry.call_tool') as tool:
                self.run_dialogue()
            tool.assert_not_called()

    def test_failed_or_unmatched_read_preserves_original_body(self):
        paper = {'url': 'https://paper', 'content': 'Original', 'read_depth': 'full'}
        for result in [{'error': 'timeout'}, {'text': 'Unmatched', 'query_matched': False},
                       {'text': 'Abstract', 'read_depth': 'abstract'}]:
            self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': paper['url']}), answer()]
            with patch('lodestar.tools.registry.call_tool', return_value=result):
                context = self.run_dialogue(context={'message': '读论文', 'papers': [paper]})
            self.assertEqual(context['papers'], [paper])

    def test_model_selects_third_source_and_abstract_by_default(self):
        papers = [{'url': f'https://paper/{i}'} for i in range(3)]
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': papers[2]['url']}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Abstract'}) as tool:
            self.run_dialogue(context={'message': '第三篇', 'papers': papers})
        self.assertEqual(tool.call_args.args[2]['url'], papers[2]['url'])
        self.assertFalse(tool.call_args.args[2]['full_text'])

    def test_project_context_enforces_bound_export_scope(self):
        self.llm.dialogue_step.side_effect = [calls({'action': 'project_context', 'query': '缓存'}), answer()]
        self.ws.config.llm_mode = 'live'
        with patch('lodestar.agent.dialogue.collect', return_value={'status': 'needs_export_scope'}) as collect:
            result = self.run_dialogue(project_id=7)
        self.assertEqual(collect.call_args.args[3], 7)
        self.assertTrue(collect.call_args.kwargs['live'])
        self.assertEqual(result['dialogue_events'][0]['result']['status'], 'needs_export_scope')
        self.assertNotIn('project_context', [t['name'] for t in tool_definitions(None)])

    def test_project_plan_words_do_not_trigger_legacy_workflow(self):
        agent = ConversationAgent(self.ws, self.llm)
        with patch('lodestar.agent.conversation.generate') as plan, patch(
                'lodestar.agent.conversation.ResearchAgent') as research:
            result = agent.turn(agent.start(), '先讨论我的项目方案，不要实现')
        plan.assert_not_called()
        research.assert_not_called()
        self.assertEqual(result['route_reason'], 'model-led dialogue')

    def test_empty_answer_is_error_not_false_success(self):
        self.llm.dialogue_step.return_value = answer(' ')
        with self.assertRaises(LLMError):
            self.run_dialogue()
        self.llm.dialogue_step.assert_called_once()

    def test_model_controls_search_order_and_discovery(self):
        self.llm.dialogue_step.side_effect = [
            calls({'action': 'search_papers', 'query': 'agent', 'sort_by': 'submittedDate'}),
            calls({'action': 'discover_papers', 'kind': 'trending'}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            self.run_dialogue()
        self.assertEqual(tool.call_args_list[0].args[2]['sort_by'], 'submittedDate')
        self.assertNotIn('days', tool.call_args_list[0].args[2])
        self.assertEqual(tool.call_args_list[1].args[2]['kind'], 'trending')

    def test_candidates_survive_new_agent_and_followup_can_read_them(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': 'agent'}), answer()]
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': [{'url': url}]}):
            agent.turn(session, '找论文')
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': url}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Evidence'}) as tool:
            result = ConversationAgent(self.ws, self.llm).turn(session, '第一篇呢')
        self.assertEqual(tool.call_args.args[2]['url'], url)
        self.assertEqual(result['evidence_reused'], 1)

    def test_failure_keeps_evidence_and_partial_text(self):
        def fail(system, messages, tools, on_text):
            on_text('部分回答')
            raise LLMError('network', role='conversation')
        self.llm.dialogue_step.side_effect = fail
        with self.assertRaises(LLMError) as raised:
            self.run_dialogue(context={'message': '继续', 'papers': [{'url': 'https://paper'}]})
        self.assertEqual(raised.exception.partial_answer, '部分回答')
        self.assertEqual(len(raised.exception.dialogue_context['papers']), 1)

    def test_configurable_budget_can_finish_search_read_and_verification(self):
        self.ws.config.dialogue_max_operations = 7
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_web', 'query': str(i)}) for i in range(7)] + [answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            result = self.run_dialogue()
        self.assertEqual(tool.call_count, 7)
        self.assertEqual(result['grounding']['search_operations'], 7)

    def test_current_evidence_follows_history_without_fake_assistant_ack(self):
        history = [{'role': 'user', 'content': 'Earlier request'},
                   {'role': 'assistant', 'content': 'OLD UNSUPPORTED CLAIM'}]
        self.run_dialogue(context={'message': 'Current request', 'papers': [], 'history': history})
        messages = self.llm.dialogue_step.call_args.args[1]
        self.assertEqual(messages[:2], history)
        current = messages[len(history)]['content']
        self.assertIn('Current source context', current)
        self.assertEqual(sum('OLD UNSUPPORTED CLAIM' in str(m['content']) for m in messages), 1)
        self.assertTrue(current.endswith('Current request'))

    def test_read_returns_metadata_without_an_extra_model_verification_turn(self):
        url = 'https://arxiv.org/abs/2601.00001'
        publication = {'url': url, 'publication_status': 'publication_record_found',
                       'venue': 'Journal', 'verified_at': '2026-10-10', 'record_url': 'https://publisher/paper'}
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': url,
                                                  'full_text': True, 'query': 'optimizer experiment'}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Body', 'read_depth': 'full'} ) as tool, patch(
                'lodestar.agent.dialogue.verify_publication', return_value=publication) as verify:
            result = self.run_dialogue(context={'message': '这篇有什么进展', 'papers': [], 'candidates': [
                {'url': url, 'source_type': 'paper', 'date': '2026-01-01', 'title': 'Title'}]})
        verify.assert_called_once()
        self.assertEqual(tool.call_args.args[2]['query'], 'optimizer experiment')
        self.assertEqual(result['papers'][0]['date'], '2026-01-01')
        self.assertEqual(result['papers'][0]['publication_status'], 'publication_record_found')
        self.assertEqual(result['dialogue_events'][0]['result']['publication']['venue'], 'Journal')

    def test_read_does_not_repeat_existing_publication_lookup(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': url}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Abstract'}), patch(
                'lodestar.agent.dialogue.verify_publication') as verify:
            result = self.run_dialogue(context={'message': '读一下', 'papers': [], 'candidates': [
                {'url': url, 'verified_at': datetime.now(timezone.utc).isoformat(), 'publication_status': 'publication_record_found', 'venue': 'Journal'}]})
        verify.assert_not_called()
        self.assertEqual(result['papers'][0]['venue'], 'Journal')

    def test_publisher_url_can_be_read_on_followup(self):
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_webpage', 'url': 'https://publisher/paper'}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Publisher record'}) as tool:
            self.run_dialogue(context={'message': '核对出版页', 'papers': [
                {'url': 'https://arxiv.org/abs/2601.00001', 'record_url': 'https://publisher/paper'}]})
        self.assertEqual(tool.call_args.args[2]['url'], 'https://publisher/paper')

    def test_repeat_search_does_not_erase_confirmed_publication(self):
        url = 'https://arxiv.org/abs/2601.00001'
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': 'agent'}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={
                'sources': [{'url': url, 'publication_status': 'not_checked', 'venue': None}]}):
            result = self.run_dialogue(context={'message': '查新进展', 'papers': [], 'candidates': [
                {'url': url, 'publication_status': 'publication_record_found', 'venue': 'Journal'}]})
        self.assertEqual(result['candidates'][0]['venue'], 'Journal')
        self.assertEqual(result['candidates'][0]['publication_status'], 'publication_record_found')

    def test_unversioned_citation_resolves_only_a_known_arxiv_paper(self):
        known = 'https://arxiv.org/abs/2601.00001v2'
        self.ws.config.enrich_venues = False
        self.llm.dialogue_step.side_effect = [calls({'action': 'read_paper', 'url': known[:-2]}), answer()]
        with patch('lodestar.tools.registry.call_tool', return_value={'text': 'Evidence'}) as tool:
            self.run_dialogue(context={'message': '读论文', 'papers': [], 'candidates': [{'url': known}]})
        self.assertEqual(tool.call_args.args[2]['url'], known)

    def test_unseen_publication_lookup_is_blocked(self):
        self.llm.dialogue_step.side_effect = [calls({'action': 'verify_paper', 'url': 'https://arxiv.org/abs/2601.00002'}), answer()]
        with patch('lodestar.agent.dialogue.verify_publication') as lookup:
            result = self.run_dialogue()
        lookup.assert_not_called()
        self.assertIn('error', result['dialogue_events'][0]['result'])

    def test_later_broad_queries_do_not_evict_first_search_within_default_budget(self):
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': str(i)}) for i in range(7)] + [answer()]
        results = [{'sources': [{'url': f'https://paper/{query}/{rank}', 'title': f'Paper {query}-{rank}'}
                               for rank in range(4)]} for query in range(7)]
        with patch('lodestar.tools.registry.call_tool', side_effect=results):
            result = self.run_dialogue()
        self.assertEqual(len(result['candidates']), 28)
        self.assertEqual(result['candidates'][0]['url'], 'https://paper/0/0')
