"""Regression checks for learning recall identity and disclosed coverage."""
import tempfile
import unittest
from pathlib import Path

from lodestar.memory import learning
from lodestar.memory.db import open_db
from lodestar.web import memory
from lodestar.web.tools import assistant_tools
from tests import test_web


class LearningRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.temp.name)/'db')

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_case_variants_do_not_duplicate_one_method_or_consume_slots(self):
        for technology, method in [('ReAct', 'Reason Act'), ('react', 'reason act'), ('REACT', 'REASON ACT')]:
            learning.record(self.conn, technology=technology, method=method, event='self_report', evidence='Viewed abstract')
        result = learning.recall(self.conn, 'ReAct', limit=20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['event_count'], 3)
        self.assertEqual(len(result[0]['evidence']), 3)
        self.assertEqual(result[0]['mastery'], 'unknown')

    def test_exact_old_method_can_be_retrieved_beyond_500_newer_methods(self):
        target = learning.record(self.conn, technology='Harness', method='Targetneedle',
            event='demonstrated_explanation', evidence='Reviewed source-bound explanation')
        with self.conn:
            self.conn.executemany('INSERT INTO learning_events(user_id,technology,method,event,actor,evidence,created_at) '
                'VALUES(?,?,?,?,?,?,?)', [('default','Harness',f'NewMethod{i}','self_report','user','Viewed summary','2026-10-18')
                                         for i in range(501)])
        result = learning.recall(self.conn, 'Targetneedle', limit=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['method'], 'Targetneedle')
        self.assertEqual(result[0]['mastery_evidence']['id'], target['evidence_id'])
        self.assertEqual(result[0]['mastery'], 'can_explain')

    def test_recall_discloses_omitted_evidence_and_retains_old_mastery_support(self):
        proof = learning.record(self.conn, technology='ReAct', method='Reasoning',
            event='demonstrated_explanation', evidence='Reviewed explanation')
        for i in range(22):
            learning.record(self.conn, technology='react', method='reasoning', event='self_report', evidence=f'Later note {i}')
        row = learning.recall(self.conn, 'ReAct')[0]
        self.assertEqual(len(row['evidence']), 5)
        self.assertEqual(row['event_count'], 23)
        self.assertTrue(row['evidence_truncated'])
        self.assertEqual(row['mastery_evidence']['id'], proof['evidence_id'])
        learning.revoke(self.conn, proof['evidence_id'])
        row = learning.recall(self.conn, 'ReAct')[0]
        self.assertEqual(row['mastery'], 'unknown')
        self.assertIsNone(row['mastery_evidence'])


class ReadMemoryCoverageTests(unittest.TestCase):
    setUp = test_web.WebTest.setUp
    tearDown = test_web.WebTest.tearDown

    def reader(self):
        with self.app.workspace() as ws:
            item = self.app.item(ws, 'main')
        return assistant_tools(self.app, item, 'recall-test', 'Overview', lambda: None)['read_memory']['handler']

    def test_overview_discloses_profile_limit_without_counting_events_as_methods(self):
        with self.app.workspace() as ws:
            for i in range(25):
                learning.record(ws.conn, user_id=memory.USER, technology='Harness', method=f'Routing{i}',
                                event='self_report', evidence='Viewed method')
            learning.record(ws.conn, user_id=memory.USER, technology='harness', method='routing0',
                            event='self_report', evidence='Second observation')
            learning.record(ws.conn, user_id='other-user', technology='FOREIGN_CANARY',
                            event='self_report', evidence='Foreign evidence')
        result = self.reader()({})
        facts = result['retrieval']
        self.assertEqual(facts['user_active_learning_events'], 26)
        self.assertEqual(facts['user_active_learning_profiles'], 25)
        self.assertEqual(facts['matching_learning_profiles'], 25)
        self.assertEqual(facts['returned_learning_profiles'], 20)
        self.assertTrue(facts['learning_profiles_truncated'])
        self.assertNotIn('FOREIGN_CANARY', str(result))

    def test_filtered_no_match_discloses_available_profiles_and_search_fields(self):
        with self.app.workspace() as ws:
            learning.record(ws.conn, user_id=memory.USER, technology='ReAct', method='Reason Act',
                            event='self_report', evidence='VIEWED_SUMMARY_ONLY')
        self.app.note('main', {'content': 'ReAct preference'})
        result = self.reader()({'query': '技术 方法 学习 掌握'})
        facts = result['retrieval']
        self.assertEqual(facts['user_active_learning_profiles'], 1)
        self.assertEqual(facts['matching_learning_profiles'], 0)
        self.assertEqual(facts['matching_memories'], 0)
        self.assertEqual(facts['returned_learning_profiles'], 0)
        self.assertFalse(facts['learning_profiles_truncated'])
        self.assertFalse(facts['memories_truncated'])
        self.assertEqual(facts['filter_fields']['learning'], ['technology', 'method'])
        self.assertEqual(facts['filter_fields']['memories'], ['content'])

    def test_note_budget_omission_is_reported_separately_from_query_miss(self):
        for i in range(9):
            self.app.note('main', {'content': f'Preference {i} '+ 'x'*1900})
        result = self.reader()({'query': 'Preference'})
        facts = result['retrieval']
        self.assertEqual(facts['matching_memories'], 9)
        self.assertLess(facts['returned_memories'], 9)
        self.assertTrue(facts['memories_truncated'])
        self.assertEqual(facts['matching_learning_profiles'], 0)
        self.assertFalse(facts['learning_profiles_truncated'])
