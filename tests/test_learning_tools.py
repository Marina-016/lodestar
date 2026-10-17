"""Chat learning edits retire old evidence without promoting self-reports."""
import json
import unittest

from lodestar.memory import learning, repo
from lodestar.web import memory
from lodestar.web.tools import assistant_tools
from tests import test_web


class LearningToolTests(unittest.TestCase):
    setUp = test_web.WebTest.setUp
    tearDown = test_web.WebTest.tearDown
    wait = test_web.WebTest.wait

    def seed(self, *, user_id=memory.USER):
        with self.app.workspace() as ws:
            return learning.record(ws.conn, user_id=user_id, technology='Agent', method='Memory',
                event='demonstrated_implementation', evidence='OLD_LEARNING_CANARY',
                paper_url='https://example.invalid/source')['evidence_id']

    def tool(self, message='I only read the abstract; I have not implemented it.', run='edit'):
        with self.app.workspace() as ws, ws.conn:
            item = self.app.item(ws, 'main')
            repo.add_message(ws.conn, item['conversation_id'], 'user', message)
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES(?,'main',?,'running',?,'{}')", (run, run, message))
        return assistant_tools(self.app, item, run, message, lambda: None)['record_learning']['handler']

    def test_correction_works_in_own_run_and_uses_actual_user_evidence(self):
        evidence_id = self.seed()
        task = self.app.create('Other task')
        with self.app.workspace() as ws, ws.conn:
            for item_id in ('main', task):
                item = self.app.item(ws, item_id)
                repo.add_message(ws.conn, item['conversation_id'], 'assistant', 'OLD_LEARNING_CANARY')
        result = self.tool()({'action': 'correct', 'id': evidence_id})
        self.assertTrue(result['context_reset'])
        self.assertEqual(result['event'], 'self_report')
        profile = self.app.state()['learning'][0]
        self.assertEqual(profile['mastery'], 'unknown')
        self.assertEqual(profile['papers'], ['https://example.invalid/source'])
        observation = json.loads(profile['evidence'][0]['evidence'])
        self.assertEqual(observation['corrects'], evidence_id)
        self.assertEqual(observation['content'], 'I only read the abstract; I have not implemented it.')
        with self.app.workspace() as ws:
            source = ws.conn.execute('SELECT role,content FROM messages WHERE id=?', (observation['message_id'],)).fetchone()
            self.assertEqual(source['role'], 'user')
            self.assertEqual(source['content'], observation['content'])
            self.assertIsNotNone(ws.conn.execute('SELECT revoked_at FROM learning_events WHERE id=?', (evidence_id,)).fetchone()[0])
            for item_id in ('main', task):
                self.assertGreater(self.app.item(ws, item_id)['context_after'], 0)
            receipts = [json.loads(r[0]) for r in ws.conn.execute("SELECT data FROM web_events WHERE run_id='edit' AND kind='action'")]
        self.assertEqual(receipts[0]['operation'], 'correct')
        self.assertNotIn('OLD_LEARNING_CANARY', json.dumps(result)+json.dumps(receipts))

    def test_revoke_removes_active_profile_and_has_no_replacement(self):
        evidence_id = self.seed()
        result = self.tool('Forget that learning record.')({'action': 'revoke', 'id': evidence_id})
        self.assertTrue(result['revoked'])
        self.assertEqual(result['learning'], [])
        self.assertEqual(self.app.state()['learning'], [])
        with self.app.workspace() as ws:
            self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0], 1)

    def test_other_active_run_blocks_edit_without_partial_invalidation(self):
        evidence_id = self.seed()
        task = self.app.create('Busy task')
        handler = self.tool()
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('busy',?,'busy','running','x','{}')", (task,))
        with self.assertRaises(memory.MemoryBusy):
            handler({'action': 'correct', 'id': evidence_id})
        self.assertEqual(self.app.state()['learning'][0]['mastery'], 'can_implement')
        with self.app.workspace() as ws:
            self.assertEqual(self.app.item(ws, 'main')['context_after'], 0)
            self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0], 1)

    def test_foreign_and_retired_ids_cannot_be_edited(self):
        foreign = self.seed(user_id='other-user')
        handler = self.tool()
        with self.assertRaises(LookupError):
            handler({'action': 'correct', 'id': foreign})
        own = self.seed()
        handler({'action': 'revoke', 'id': own})
        with self.assertRaises(LookupError):
            handler({'action': 'correct', 'id': own})

    def test_invalid_or_missing_operation_arguments_do_not_mutate(self):
        evidence_id = self.seed()
        handler = self.tool()
        for args in ({}, {'action': 'correct'}, {'action': 'revoke', 'id': True},
                     {'action': 'correct', 'id': evidence_id, 'event': 'demonstrated_implementation'},
                     {'action': 'revoke', 'id': evidence_id, 'technology': 'Changed'},
                     {'technology': 'Agent', 'event': 'attempted', 'id': evidence_id}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                handler(args)
        self.assertEqual(self.app.state()['learning'][0]['mastery'], 'can_implement')

    def test_failed_correction_rolls_back_context_and_records(self):
        evidence_id = self.seed()
        handler = self.tool()
        with self.assertRaises(ValueError):
            handler({'action': 'correct', 'id': evidence_id, 'technology': ''})
        with self.app.workspace() as ws:
            self.assertEqual(self.app.item(ws, 'main')['context_after'], 0)
            self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0], 1)
            self.assertIsNone(ws.conn.execute('SELECT revoked_at FROM learning_events WHERE id=?', (evidence_id,)).fetchone()[0])

    def test_native_tool_loop_discards_old_learning_after_correction(self):
        evidence_id = self.seed()
        captured = []
        class Scripted(test_web.FakeLLM):
            def dialogue_step(self, system, messages, tools, on_text=None):
                captured.append(json.dumps(messages))
                if len(captured) == 1:
                    return {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'correct',
                        'name': 'record_learning', 'input': {'action': 'correct', 'id': evidence_id}}]}
                return {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Corrected as a self-report.'}]}
        self.app.llm_factory = Scripted
        result = self.wait(self.app.submit('main', 'Correct my Agent Memory record: I only read an abstract.', 'native-edit'))
        self.assertEqual(result['run']['status'], 'completed')
        self.assertIn('OLD_LEARNING_CANARY', captured[0])
        self.assertNotIn('OLD_LEARNING_CANARY', captured[1])
        self.assertIn('self_report', captured[1])
        self.assertEqual(self.app.state()['learning'][0]['mastery'], 'unknown')
        receipts = self.app.state()['messages'][-1]['metadata']['action_receipts']
        self.assertEqual(receipts[0]['operation'], 'correct')

    def test_ui_correction_retains_existing_size_limit(self):
        evidence_id = self.seed()
        with self.assertRaises(ValueError):
            self.app.learning({'id': evidence_id, 'action': 'correct', 'evidence': 'x' * 2001})
        self.assertEqual(self.app.state()['learning'][0]['mastery'], 'can_implement')
