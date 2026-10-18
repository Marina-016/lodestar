"""Long learning evidence is bounded only at model boundaries, never in storage."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from lodestar.agent.dialogue import gather
from lodestar.agent.evidence import answer_context
from lodestar.memory import learning, repo
from lodestar.memory.db import open_db
from lodestar.web import memory
from lodestar.web.tools import assistant_tools
from tests import test_web


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class LearningContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.temp.name)/'db')

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def records(self):
        for index in range(4):
            evidence = encoded({'message_id': index+100, 'corrects': index+50,
                'content': 'BEGIN ' + ('quoted " \\ 中文 ' * 1600) + ' END: I have not implemented it.'})
            learning.record(self.conn, technology=f'Technology{index}', method='Mechanism',
                event='self_report', evidence=evidence)
        return learning.profile(self.conn)

    def test_automatic_source_packet_bounds_learning_and_preserves_reference_without_text_fragment(self):
        records = self.records()
        original = deepcopy(records)
        context = {'current_time': 'now', 'papers': [], 'candidates': [], 'dialogue_events': [], 'learning': records}
        packet = answer_context(context)
        self.assertLessEqual(len(encoded(packet['learning'])), 12000)
        self.assertTrue(packet['learning_context']['truncated'])
        self.assertEqual(len(packet['learning']), 4)
        for profile in packet['learning']:
            self.assertEqual(profile['mastery'], 'unknown')
            row = profile['evidence'][0]
            self.assertTrue(row['evidence_omitted'])
            self.assertEqual(row['evidence'], '')
            self.assertIsInstance(row['source_message_id'], int)
            self.assertIsInstance(row['corrects_evidence_id'], int)
        self.assertEqual(records, original)
        self.assertEqual(learning.profile(self.conn), original)

    def test_context_budget_counts_json_escaping_and_discloses_omitted_profiles(self):
        records = self.records()
        for budget in (2, 400, 1200, 6000, 12000):
            with self.subTest(budget=budget):
                packet = learning.model_context(records, budget=budget)
                self.assertLessEqual(len(encoded(packet['profiles'])), budget)
                self.assertEqual(packet['coverage']['returned_profiles'], len(packet['profiles']))
                self.assertEqual(packet['coverage']['profiles_omitted'], len(records)-len(packet['profiles']))
                self.assertEqual(packet['coverage']['serialized_profile_chars'], len(encoded(packet['profiles'])))
        self.assertEqual(learning.model_context(records, budget=2)['profiles'], [])

    def test_old_mastery_support_survives_projection_and_revocation_recomputes_it(self):
        proof = learning.record(self.conn, technology='Harness', method='Routing', event='demonstrated_explanation',
            evidence=encoded({'message_id': 10, 'content': 'Reviewed long user explanation '+ 'x'*24000}))
        for index in range(25):
            learning.record(self.conn, technology='Harness', method='Routing', event='self_report',
                            evidence='Later report '+ 'y'*2000)
        records = learning.profile(self.conn)
        packet = learning.model_context(records)
        row = packet['profiles'][0]
        self.assertEqual(row['mastery'], 'can_explain')
        self.assertEqual(row['mastery_evidence']['id'], proof['evidence_id'])
        self.assertEqual(row['mastery_evidence']['source_message_id'], 10)
        self.assertTrue(row['mastery_evidence']['evidence_omitted'])
        self.assertEqual(row['event_count'], 26)
        learning.revoke(self.conn, proof['evidence_id'])
        row = learning.model_context(learning.profile(self.conn))['profiles'][0]
        self.assertEqual(row['mastery'], 'unknown')
        self.assertIsNone(row['mastery_evidence'])

    def test_small_records_are_preserved_and_legacy_prompt_is_bounded(self):
        learning.record(self.conn, technology='ReAct', event='self_report', evidence='Only read the abstract')
        records = learning.profile(self.conn)
        packet = learning.model_context(records)
        self.assertEqual(packet['profiles'], records)
        self.assertFalse(packet['coverage']['truncated'])
        records = self.records()
        prompt = learning.prompt_context(records)
        payload = json.loads(prompt.split('\n', 1)[1])
        self.assertLessEqual(len(encoded(payload['profiles'])), 12000)
        self.assertTrue(payload['coverage']['truncated'])


class WebLearningContextTests(unittest.TestCase):
    setUp = test_web.WebTest.setUp
    tearDown = test_web.WebTest.tearDown

    def tools(self, message='Show my learning'):
        with self.app.workspace() as ws:
            item = self.app.item(ws, 'main')
        return assistant_tools(self.app, item, 'context-test', message, lambda: None)

    def seed(self):
        evidence = encoded({'message_id': 42, 'content': 'x'*23000, 'corrects': 7})
        with self.app.workspace() as ws:
            saved = learning.record(ws.conn, user_id=memory.USER, technology='ReAct', event='self_report', evidence=evidence)
        return saved, evidence

    def test_read_memory_bounds_learning_without_changing_full_ui_or_database(self):
        saved, original = self.seed()
        result = self.tools()['read_memory']['handler']({})
        self.assertLessEqual(len(encoded(result['learning'])), 12000)
        self.assertEqual(result['learning'][0]['evidence'][0]['id'], saved['evidence_id'])
        self.assertEqual(result['learning'][0]['evidence'][0]['source_message_id'], 42)
        self.assertTrue(result['learning_context']['truncated'])
        self.assertEqual(self.app.state()['learning'][0]['evidence'][0]['evidence'], original)
        self.assertEqual(result['retrieval']['returned_learning_profiles'], len(result['learning']))

    def test_reserved_answer_and_native_tool_result_never_receive_unbounded_learning(self):
        self.seed()
        llm = Mock()
        llm.dialogue_step.side_effect = [
            {'role': 'assistant', 'content': [{'type':'tool_use','id':'read','name':'read_memory','input':{}}]},
            {'role': 'assistant', 'content': [{'type':'text','text':'Bounded response.'}]}]
        with self.app.workspace() as ws:
            context = {'message':'Show learning','papers':[], 'learning':learning.profile(ws.conn,memory.USER)}
            gather(ws,llm,context,max_calls=1,extra_tools=self.tools())
        final = llm.dialogue_step.call_args.args[1][0]['content']
        packet = json.loads(final.split('\n',1)[1].split('\n\nUser request:\n',1)[0])
        self.assertLessEqual(len(encoded(packet['learning'])),12000)
        result = packet['operations'][0]['result']
        self.assertLessEqual(len(encoded(result['learning'])),12000)
        self.assertTrue(result['learning_context']['truncated'])
        self.assertNotIn('x'*13000, final)

    def test_correction_reset_returns_bounded_learning_with_actual_user_source(self):
        saved, _ = self.seed()
        message = 'I only read the summary. '+ 'y'*21000 + ' I did not implement this.'
        with self.app.workspace() as ws:
            item = self.app.item(ws,'main')
            source = repo.add_message(ws.conn,item['conversation_id'],'user',message)
        result = self.tools(message)['record_learning']['handler']({'action':'correct','id':saved['evidence_id']})
        self.assertTrue(result['context_reset'])
        self.assertLessEqual(len(encoded(result['learning'])),12000)
        evidence = result['learning'][0]['evidence'][0]
        self.assertEqual(evidence['id'],result['evidence_id'])
        self.assertEqual(evidence['source_message_id'],source['id'])
        self.assertTrue(evidence['evidence_omitted'])
        self.assertEqual(result['learning'][0]['mastery'],'unknown')
        full = self.app.state()['learning'][0]['evidence'][0]
        self.assertEqual(json.loads(full['evidence'])['content'],message)
