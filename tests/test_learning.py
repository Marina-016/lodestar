import tempfile
import unittest
from pathlib import Path

from lodestar.memory.db import open_db
from lodestar.memory import learning


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.tmp.name) / 'memory.db')

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_exposure_is_not_mastery_and_tracks_paper_method(self):
        learning.record(self.conn, technology='Harness', method='Tool routing',
                        event='explained', actor='agent', evidence='Explanation delivered',
                        paper_url='https://arxiv.org/abs/2601.00001')
        result = learning.profile(self.conn)[0]
        self.assertEqual(result['mastery'], 'unknown')
        self.assertEqual(result['method'], 'Tool routing')
        self.assertEqual(len(result['papers']), 1)

    def test_user_evidence_and_revocation(self):
        event = learning.record(self.conn, technology='Harness', event='demonstrated_implementation',
                                evidence='User implementation and test output')
        self.assertEqual(learning.profile(self.conn)[0]['mastery'], 'can_implement')
        self.assertFalse(learning.revoke(self.conn, event['evidence_id'], 'other'))
        self.assertTrue(learning.revoke(self.conn, event['evidence_id']))
        self.assertEqual(learning.profile(self.conn), [])

    def test_agent_cannot_establish_user_mastery(self):
        with self.assertRaises(ValueError):
            learning.record(self.conn, technology='Harness', event='demonstrated_application',
                            actor='agent', evidence='Agent ran the experiment')

    def test_self_report_and_methods_are_separate(self):
        learning.record(self.conn, technology='Harness', method='A', event='self_report', evidence='I know A')
        learning.record(self.conn, technology='Harness', method='B', event='demonstrated_explanation', evidence='User explains B')
        levels = {p['method']: p['mastery'] for p in learning.profile(self.conn)}
        self.assertEqual(levels, {'A': 'unknown', 'B': 'can_explain'})
        self.assertEqual(learning.profile(self.conn, 'another-user'), [])

    def test_old_mastery_and_paper_survive_recent_observations(self):
        old = learning.record(self.conn, technology='Harness', method='Routing',
            event='demonstrated_implementation', evidence='Reviewed implementation',
            paper_url='https://arxiv.org/abs/old')
        for index in range(105):
            learning.record(self.conn, technology='Harness', method='Routing',
                event='self_report', evidence=f'Later discussion {index}')
        profile = learning.profile(self.conn, limit=1)[0]
        self.assertEqual(profile['mastery'], 'can_implement')
        self.assertEqual(profile['mastery_evidence']['id'], old['evidence_id'])
        self.assertEqual(profile['event_count'], 106)
        self.assertIn('https://arxiv.org/abs/old', profile['papers'])
        self.assertLessEqual(len(profile['evidence']), 20)
        learning.revoke(self.conn, old['evidence_id'])
        self.assertEqual(learning.profile(self.conn)[0]['mastery'], 'unknown')

    def test_hot_method_does_not_evict_another_method(self):
        learning.record(self.conn, technology='Harness', method='OldMethod',
            event='demonstrated_explanation', evidence='Reviewed explanation')
        for index in range(105):
            learning.record(self.conn, technology='Harness', method='HotMethod',
                event='self_report', evidence=f'Discussion {index}')
        result = learning.recall(self.conn, 'Harness OldMethod')
        old = next(item for item in result if item['method'] == 'OldMethod')
        self.assertEqual(old['mastery'], 'can_explain')
