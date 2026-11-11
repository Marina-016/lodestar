import json
import unittest
from unittest.mock import patch

from tests.test_dialogue import DialogueTests, answer
from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.paper_recommendations import discovery_request
from lodestar.memory import repo


class PaperRecommendationTests(unittest.TestCase):
    setUp = DialogueTests.setUp
    tearDown = DialogueTests.tearDown

    def test_link_corrections_keep_the_actual_paper_and_do_not_call_the_model(self):
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        cue = {'url': 'https://arxiv.org/abs/2609.12354v1', 'title': 'CueMem: Memory', 'source_type': 'paper'}
        opd = {'url': 'https://arxiv.org/abs/2611.08453v1',
               'title': 'Unpacking the Compute Efficiency of Strong-to-Weak OPD against GRPO', 'source_type': 'paper'}
        other = {'url': 'https://arxiv.org/abs/2610.22732', 'title': 'TokenEconomizer: Coding Agents', 'source_type': 'paper'}
        repo.add_message(self.ws.conn, session, 'user', '介绍 CueMem')
        repo.add_message(self.ws.conn, session, 'assistant', cue['title'], metadata={'source_details': [cue]})
        repo.add_message(self.ws.conn, session, 'user', '再介绍一篇论文')
        repo.add_message(self.ws.conn, session, 'assistant', opd['title'] + '\n对比 CueMem。',
                         metadata={'source_details': [cue, opd]})
        repo.add_message(self.ws.conn, session, 'user', '论文链接')
        repo.add_message(self.ws.conn, session, 'assistant', other['title'], metadata={'source_details': [other]})
        for question in ('论文链接', '我说给我上一篇论文的链接'):
            result = agent.turn(session, question, track_exposure=False)
            self.assertIn(opd['url'], result['answer'])
            self.assertNotIn(cue['url'], result['answer'])
            self.assertEqual(result['route_reason'], 'resolved paper link')
            self.assertEqual(result['referenced_papers'][0]['url'], opd['url'])
        self.llm.dialogue_step.assert_not_called()

    def test_link_without_a_known_paper_asks_for_a_title_instead_of_inventing_one(self):
        agent = ConversationAgent(self.ws, self.llm)
        result = agent.turn(agent.start(), '论文链接', track_exposure=False)
        self.assertEqual(result['source_details'], [])
        self.assertIn('哪篇论文', result['answer'])
        self.llm.dialogue_step.assert_not_called()

    def test_new_recommendation_searches_and_filters_versions_of_introduced_papers(self):
        old = {'url': 'https://arxiv.org/abs/2609.12354v1', 'title': 'CueMem: Memory',
               'source_type': 'paper', 'content': 'OLD_BODY_CANARY'}
        new = {'url': 'https://arxiv.org/abs/2611.00001', 'title': 'A Different Paper', 'abstract': 'New method'}
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        with self.ws.conn:
            self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                                 (json.dumps([old]), session))
        self.llm.dialogue_step.return_value = answer('CueMem: Memory is the paper we discussed.')
        agent.turn(session, '解释 CueMem', track_exposure=False)
        self.llm.dialogue_step.return_value = answer('A Different Paper [原文](https://arxiv.org/abs/2611.00001)')
        with patch('lodestar.tools.registry.call_tool', return_value={'sources': [
                {**old, 'url': 'https://arxiv.org/abs/2609.12354v2'}, new]}) as tool:
            result = agent.turn(session, '给我介绍一篇论文吧', track_exposure=False)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(tool.call_args.args[1], 'discover_papers')
        event = result['dialogue_events'][0]
        self.assertEqual(event['result']['excluded_previously_introduced'], 1)
        self.assertEqual([s['url'] for s in result['candidates']], [new['url']])
        packet = next(m['content'] for m in self.llm.dialogue_step.call_args.args[1]
                      if isinstance(m['content'], str) and m['content'].startswith('Current source context'))
        self.assertNotIn('OLD_BODY_CANARY', packet)
        self.assertIn('A Different Paper', packet)

    def test_explicit_old_paper_and_followups_preserve_existing_evidence(self):
        old = {'url': 'https://arxiv.org/abs/2609.12354', 'title': 'CueMem: Memory'}
        for question in ('继续讲那篇论文', '介绍 CueMem 论文', '复习之前的论文', '不用搜，介绍一篇论文'):
            self.assertFalse(discovery_request(question, known=[old]))
        self.assertFalse(discovery_request('介绍一篇论文', reference_text='用户指定的资料'))
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        with self.ws.conn:
            self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                                 (json.dumps([old]), session))
        with patch('lodestar.tools.registry.call_tool') as tool:
            result = agent.turn(session, '继续讲那篇论文', track_exposure=False)
        tool.assert_not_called()
        self.assertEqual(result['evidence_reused'], 1)
