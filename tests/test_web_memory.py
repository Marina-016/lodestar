"""Payload-level checks for shared identity, scoped facts and task delegation."""
import json
import threading
import unittest
from unittest.mock import Mock, patch

from lodestar.agent.dialogue import gather
from lodestar.memory import learning, repo
from lodestar.web import memory
from lodestar.web.server import WebApp, Conflict
from lodestar.web.tools import assistant_tools
from tests import test_web
from tests.test_web import FakeLLM


class SharedMemoryTest(unittest.TestCase):
    setUp = test_web.WebTest.setUp
    tearDown = test_web.WebTest.tearDown
    wait = test_web.WebTest.wait
    gather = staticmethod(test_web.WebTest.gather)
    def project(self, name):
        return self.app.project({'name': name, 'description': 'Synthetic test project'})

    def context(self, item, text='Explain Harness'):
        captured = []
        def capture(ws, llm, context, project_id, **callbacks):
            captured.append(context)
            return self.gather(ws, llm, context, project_id, **callbacks)
        with patch('lodestar.agent.dialogue.gather', side_effect=capture):
            run = self.wait(self.app.submit(item, text, str(len(self.app.state(item)['messages']))))
        self.assertEqual(run['run']['status'], 'completed')
        return captured[0]

    def test_personal_and_learning_shared_but_transcripts_remain_local(self):
        a, b = self.app.create('A'), self.app.create('B')
        self.app.note(a, {'content': 'SHARED_PREFERENCE'})
        with self.app.workspace() as ws:
            learning.record(ws.conn, technology='Harness', method='Routing', event='self_report', evidence='USER_REPORT', user_id=memory.USER)
        self.context(a, 'PRIVATE_A_MESSAGE')
        packet = self.context(b)
        self.assertIn('SHARED_PREFERENCE', json.dumps(packet))
        self.assertIn('USER_REPORT', json.dumps(packet))
        self.assertNotIn('PRIVATE_A_MESSAGE', json.dumps(packet))
        self.assertEqual(self.app.state(a)['learning'], self.app.state(b)['learning'])

    def test_project_and_task_memory_do_not_leak_into_other_contexts(self):
        p, q = self.project('P'), self.project('Q')
        a, b, c = self.app.create('A', p), self.app.create('B', p), self.app.create('C', q)
        self.app.note(a, {'content': 'PROJECT_P_CANARY', 'scope': 'project'})
        self.app.note(a, {'content': 'TASK_A_CANARY', 'scope': 'task'})
        self.assertIn('PROJECT_P_CANARY', json.dumps(self.context(b)))
        self.assertNotIn('TASK_A_CANARY', json.dumps(self.context(b)))
        self.assertNotIn('PROJECT_P_CANARY', json.dumps(self.context(c)))
        self.assertNotIn('PROJECT_P_CANARY', json.dumps(self.context('main')))

    def test_correct_disable_forget_retire_old_payloads_and_revisions(self):
        a, b = self.app.create('A'), self.app.create('B')
        self.app.note(a, {'content': 'OLD_CANARY'})
        note = self.app.state(a)['notes'][0]
        self.context(a, 'OLD_CANARY was my preference')
        self.context(b, 'OLD_CANARY was my preference')
        self.app.note('main', {'id': note['id'], 'content': 'NEW_PREFERENCE'})
        for task in (a, b):
            packet = self.context(task, 'What preference should you use?')
            self.assertNotIn('OLD_CANARY', json.dumps(packet))
            self.assertIn('NEW_PREFERENCE', json.dumps(packet))
            self.assertGreater(len(self.app.state(task)['messages']), 2)
        self.app.note(a, {'id': note['id'], 'status': 'disabled'})
        self.assertNotIn('NEW_PREFERENCE', json.dumps(self.context(b, 'Continue')))
        self.app.note(a, {'id': note['id'], 'status': 'deleted'})
        with self.app.workspace() as ws:
            revisions = list(ws.conn.execute('SELECT content FROM assistant_memory_revisions WHERE memory_id=?', (note['id'],)))
            self.assertTrue(all(r['content'] is None for r in revisions))

    def test_global_hub_can_correct_other_project_but_model_cannot(self):
        p = self.project('P')
        task = self.app.create('A', p)
        self.app.note(task, {'content': 'Project fact', 'scope': 'project'})
        note = self.app.state()['notes'][0]
        self.app.note('main', {'id': note['id'], 'content': 'Corrected project fact'})
        with self.app.workspace() as ws:
            tools = assistant_tools(self.app, self.app.item(ws, 'main'), 'fake', 'Remember', lambda: None)
        with self.assertRaises(LookupError):
            tools['manage_memory']['handler']({'action': 'forget', 'id': note['id']})

    def test_old_web_data_migrates_once_with_ids_preserved(self):
        task = self.app.create('Legacy')
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("DELETE FROM web_migrations WHERE name='shared_memory_v1'")
            ws.conn.execute("UPDATE agent_sessions SET user_id=? WHERE conversation_id=?", (memory.USER + ':' + task, self.app.item(ws, task)['conversation_id']))
            ws.conn.execute("INSERT INTO web_notes(id,item_id,content) VALUES('legacy-note',?,'LEGACY_TASK_FACT')", (task,))
            learning.record(ws.conn, technology='Harness', method='Legacy', event='discussed', evidence='Legacy evidence', user_id=memory.USER + ':' + task)
        reopened = WebApp(self.config, llm_factory=FakeLLM)
        self.assertEqual(reopened.state(task)['notes'][0]['id'], 'legacy-note')
        self.assertEqual(reopened.state(task)['notes'][0]['scope'], 'task')
        self.assertEqual(reopened.state()['learning'][0]['method'], 'Legacy')
        reopened.note(task, {'id': 'legacy-note', 'status': 'deleted'})
        self.assertEqual(WebApp(self.config, llm_factory=FakeLLM).state(task)['notes'], [])

    def test_inflight_shared_context_blocks_mutation_and_stale_edits(self):
        task = self.app.create('Busy')
        self.app.note('main', {'content': 'Shared'})
        note = self.app.state()['notes'][0]
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('busy',?,'busy','running','x','{}')", (task,))
        with self.assertRaises(Conflict):
            self.app.note('main', {'id': note['id'], 'status': 'deleted'})
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("UPDATE web_runs SET status='completed' WHERE id='busy'")
        self.app.note('main', {'id': note['id'], 'content': 'New'})
        with self.assertRaises(Conflict):
            self.app.note('main', {'id': note['id'], 'revision': 1, 'content': 'Stale'})

    def test_learning_correction_downgrades_to_self_report_and_revoke_recomputes(self):
        with self.app.workspace() as ws:
            record = learning.record(ws.conn, technology='Harness', method='Routing', event='demonstrated_implementation', evidence='user code', user_id=memory.USER)
        self.app.learning({'id': record['evidence_id'], 'action': 'correct', 'evidence': 'I have only read it'})
        profile = self.app.state()['learning'][0]
        self.assertEqual(profile['mastery'], 'unknown')
        self.assertEqual(profile['evidence'][0]['event'], 'self_report')
        self.app.learning({'id': profile['evidence'][0]['id'], 'action': 'revoke'})
        self.assertEqual(self.app.state()['learning'], [])

    def test_model_dispatches_two_tasks_and_they_run_without_exposure(self):
        calls, lock = [], threading.Lock()
        class Scripted(FakeLLM):
            def __init__(self, cfg):
                super().__init__(cfg)
                self.steps = 0
            def dialogue_step(self, system, messages, tools, on_text=None):
                self.steps += 1
                text = json.dumps(messages)
                with lock:
                    calls.append(text)
                if 'DISPATCH_TWO' in text and self.steps == 1:
                    return {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': name, 'name': 'create_task', 'input': {'name': name, 'instruction': 'Research ' + name}} for name in ('Harness', 'RAG')]}
                answer = 'Research complete' if 'DISPATCH_TWO' not in text else 'Two tasks started'
                if on_text: on_text(answer)
                return {'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}
        self.app.llm_factory = Scripted
        self.app.note('main', {'content': 'SHARED_RESEARCH_PREFERENCE'})
        result = self.wait(self.app.submit('main', 'DISPATCH_TWO', 'dispatch'))
        self.assertEqual(result['run']['status'], 'completed')
        tasks = [t for t in self.app.state()['items'] if t['id'] != 'main']
        self.assertEqual(len(tasks), 2)
        for task in tasks:
            self.wait(self.app.state(task['id'])['run']['id'])
            self.assertEqual(len(self.app.state(task['id'])['messages']), 2)
            self.assertEqual(task['parent_id'], 'main')
        self.assertTrue(all('SHARED_RESEARCH_PREFERENCE' in c for c in calls))
        self.assertEqual(self.app.state()['learning'], [])

    def test_forget_tool_clears_live_and_reserved_answer_context(self):
        self.app.note('main', {'content': 'FORGOTTEN_CANARY'})
        note = self.app.state()['notes'][0]
        self.context('main', 'FORGOTTEN_CANARY')
        with self.app.workspace() as ws:
            item = self.app.item(ws, 'main')
        tools = assistant_tools(self.app, item, 'not-a-run', 'Forget my old preference', lambda: None)
        responses = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'forget', 'name': 'manage_memory', 'input': {'action': 'forget', 'id': note['id']}}]},
                     {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Forgotten'}]}]
        client = Mock()
        client.dialogue_step.side_effect = responses
        with self.app.workspace() as ws:
            context = {'message': 'Forget my old preference', 'history': [{'role': 'assistant', 'content': 'FORGOTTEN_CANARY'}],
                       'managed_memory': ['FORGOTTEN_CANARY'], 'papers': [], 'candidates': []}
            gather(ws, client, context, max_calls=1, extra_tools=tools)
        self.assertNotIn('FORGOTTEN_CANARY', json.dumps(client.dialogue_step.call_args.args[1]))

    def test_background_cannot_relabel_personal_memory_or_record_user_learning(self):
        project = self.project('Background project')
        task = self.app.create('Background', project)
        self.app.note('main', {'content': 'PERSONAL_CANARY'})
        note = self.app.state()['notes'][0]
        with self.app.workspace() as ws:
            tools = assistant_tools(self.app, self.app.item(ws, task), 'background', 'Research', lambda: None, user_authored=False)
        self.assertNotIn('record_learning', tools)
        for action in ('save', 'forget'):
            with self.assertRaises(ValueError):
                tools['manage_memory']['handler']({'action': action, 'id': note['id'], 'scope': 'project', 'content': 'Changed'})
        tools['manage_memory']['handler']({'action': 'save', 'scope': 'task', 'content': 'Research finding'})
        self.assertEqual(next(n for n in self.app.state()['notes'] if n['id'] == note['id'])['content'], 'PERSONAL_CANARY')

    def test_learning_tool_attaches_user_evidence_and_never_demonstrates_mastery(self):
        self.context('main', 'I tried dynamic routing in my own prototype')
        with self.app.workspace() as ws:
            tools = assistant_tools(self.app, self.app.item(ws, 'main'), 'evidence', 'I tried dynamic routing in my own prototype', lambda: None)
        tools['record_learning']['handler']({'technology': 'Harness', 'method': 'Routing', 'event': 'attempted'})
        profile = self.app.state()['learning'][0]
        self.assertEqual(profile['mastery'], 'unknown')
        evidence = json.loads(profile['evidence'][0]['evidence'])
        self.assertIn('I tried dynamic routing', evidence['content'])
        self.assertIsInstance(evidence['message_id'], int)
        self.assertNotIn('demonstrated_implementation', tools['record_learning']['input_schema']['properties']['event']['enum'])

    def test_automatic_recall_is_bounded_and_snapshot_matches_payload(self):
        for i in range(24):
            self.app.note('main', {'content': ('Routing preference ' if i == 23 else 'Unrelated preference ') + str(i) + 'x' * 1900})
        packet = self.context('main', 'Routing')
        self.assertLessEqual(sum(map(len, packet['managed_memory'])), 8000)
        self.assertIn('Routing preference', json.dumps(packet['managed_memory']))
        snapshot = self.app.state()['messages'][-1]['metadata']['web_context']['memory']
        self.assertEqual({r['id'] for r in snapshot}, {json.loads(r)['id'] for r in packet['managed_memory']})
