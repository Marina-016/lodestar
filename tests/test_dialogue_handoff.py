import json
import unittest
from unittest.mock import patch
from lodestar.agent.dialogue import gather
from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.handoff import retrieval_requested
from lodestar.llm import LLMError
from tests.test_dialogue import DialogueTests, answer, calls


class HandoffTests(unittest.TestCase):
    setUp = DialogueTests.setUp
    tearDown = DialogueTests.tearDown

    def test_empty_promise_gets_one_required_retrieval_repair_then_real_results(self):
        history = [{'role': 'user', 'content': '讨论生成中接收新消息的机制。'},
                   {'role': 'assistant', 'content': '我先搜一下这个方向的文献。'}]
        self.llm.dialogue_step.side_effect = [answer('好，这就去搜。'),
            calls({'action': 'search_papers', 'queries': ['streaming dialogue interruption']}),
            answer('这次检索没有找到直接证明这种产品机制的论文；不能把前面的推测当事实。')]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            result = gather(self.ws, self.llm, {'message': '去搜呀', 'history': history, 'papers': []}, max_calls=3)
        self.assertEqual(tool.call_count, 1)
        repair = self.llm.dialogue_step.call_args_list[1]
        self.assertEqual(repair.kwargs['tool_choice'], 'required')
        self.assertNotIn('manage_memory', {d['name'] for d in repair.args[2]})
        self.assertIn(history[0]['content'], repair.args[1][0]['content'])
        self.assertEqual(result['grounding']['tool_dispatches'], 1)
        self.assertNotIn('这就去搜', result['answer'])

    def test_broken_gateway_cannot_complete_with_two_promises_or_loop_forever(self):
        self.llm.dialogue_step.return_value = answer('好，这就去搜。')
        with self.assertRaises(LLMError) as caught:
            gather(self.ws, self.llm, {'message': '搜索实时语音交互的论文', 'papers': []})
        self.assertEqual(caught.exception.code, 'action_incomplete')
        self.assertEqual(caught.exception.partial_answer, '')
        self.assertEqual(self.llm.dialogue_step.call_count, 2)

    def test_topical_recommendation_never_starts_with_unrelated_general_ai_feed(self):
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': 'duplex dialogue'}), answer('No matching paper confirmed.')]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}) as tool:
            gather(self.ws, self.llm, {'message': '找一下生成中接收新消息的论文', 'papers': [],
                'paper_recommendation': {'discover_new': True, 'excluded': []}})
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(tool.call_args.args[1], 'search_papers')

    def test_new_lookup_retires_old_paper_focus_and_body_but_keeps_recent_user_topic(self):
        agent = ConversationAgent(self.ws, self.llm)
        cid = agent.start()
        old = {'url': 'https://arxiv.org/abs/2611.00001', 'title': 'OLD_VERIFIER_PAPER', 'content': 'OLD_BODY_CANARY'}
        with self.ws.conn:
            self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?', (json.dumps([old]), cid))
        from lodestar.memory import repo
        repo.add_message(self.ws.conn, cid, 'user', '现在讨论全双工语音交互。')
        repo.add_message(self.ws.conn, cid, 'assistant', '我先搜一下。', metadata={'paper_focus': old, 'candidates': [old]})
        self.llm.dialogue_step.side_effect = [calls({'action': 'search_papers', 'query': 'full duplex dialogue'}), answer('未找到足够依据。')]
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': []}):
            agent.turn(cid, '去搜呀', track_exposure=False)
        request = self.llm.dialogue_step.call_args_list[0].args[1][0]['content']
        self.assertNotIn('OLD_BODY_CANARY', request)
        self.assertNotIn('OLD_VERIFIER_PAPER', request)
        self.assertIn('全双工语音交互', request)

    def test_lookup_permission_is_not_derived_from_quoted_data_or_revived_by_greetings(self):
        history = [{'role': 'assistant', 'content': '我先搜一下论文。'}]
        self.assertFalse(retrieval_requested('这个词怎么翻译', history, '去搜最新论文'))
        self.assertFalse(retrieval_requested('这个词什么意思', [
            {'role':'user','content':'去查论文'}, {'role':'assistant','content':'我先搜一下。'}], 'selected word'))
        for text in ['你好', '不用搜，解释就行', '不用再检索，用两句话保留结论', '不要重新查论文', '教我怎么搜索论文']:
            self.assertFalse(retrieval_requested(text, history))
        self.assertFalse(retrieval_requested('你查一下记忆', history))
        self.assertTrue(retrieval_requested('ok', history))
        self.assertTrue(retrieval_requested('刚刚说的那个方向', [{'role':'user','content':'去查一下论文'}]))
        self.assertFalse(retrieval_requested('比较刚才这些论文的方法', [
            {'role':'user','content':'请找出这些历史论文'},
            {'role':'assistant','content':'已查到两篇，来源已给出。'}]))

    def test_genuinely_missing_topic_can_be_clarified_without_forcing_tools(self):
        self.llm.dialogue_step.return_value = answer('想查什么方向的论文？')
        result = gather(self.ws, self.llm, {'message': '查下论文', 'papers': []})
        self.assertEqual(result['grounding']['tool_dispatches'], 0)
        self.assertEqual(self.llm.dialogue_step.call_count, 1)

    def test_repair_rejects_mutating_calls_even_if_gateway_ignores_tool_allowlist(self):
        from unittest.mock import Mock
        handler = Mock(return_value={'ok':True})
        self.llm.dialogue_step.side_effect = [answer('好，这就去搜。'),
            calls({'action':'manage_memory','content':'unauthorized'}), answer('仍然没有完成。')]
        with self.assertRaises(LLMError):
            gather(self.ws,self.llm,{'message':'搜索全双工语音论文','papers':[]},extra_tools={
                'manage_memory':{'description':'fixture','input_schema':{'type':'object','properties':{'content':{'type':'string'}},'required':['content']},'handler':handler}})
        handler.assert_not_called()

    def test_budget_exhaustion_cannot_end_with_a_promise_to_keep_searching(self):
        self.llm.dialogue_step.side_effect = [calls({'action':'search_papers','query':'duplex speech'}),
            answer('刚才 arXiv 和网页搜索都卡住了，我换个路子再试。')]
        with patch('lodestar.tools.registry.call_tool',return_value={'error':'fixture connection failure'}):
            result = gather(self.ws,self.llm,{'message':'查一下全双工语音论文','papers':[]},max_calls=1)
        self.assertEqual(result['grounding']['completion_mode'],'evidence_fallback')
        self.assertNotIn('再试',result['answer'])
        self.assertIn('未获得可用证据',result['answer'])
