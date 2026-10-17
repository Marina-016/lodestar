"""Bounded task waits observe host state; they never start inference or jobs."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.llm import LLMError
from lodestar.memory import repo
from lodestar.web.server import WebApp
from lodestar.web.tools import assistant_tools


class Clock:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if not 0 < seconds <= .1:
            raise AssertionError('Task waiting must use short positive intervals')
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class TaskWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lodestar-task-wait-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.model = Mock(side_effect=AssertionError('No model may be constructed'))
        self.app = WebApp(Config(llm_mode='mock', search_mode='mock',
            db_path=root/'web.db', workspace_dir=root), llm_factory=self.model)
        self.task_id = self.app.create('Delayed child')
        with self.app.workspace() as ws:
            self.child = self.app.item(ws, self.task_id)
            parent = self.app.item(ws, 'main')
        self.cancelled = threading.Event()

        def check_cancel():
            if self.cancelled.is_set():
                raise LLMError('Parent cancelled', code='cancelled')

        self.tools = assistant_tools(self.app, parent, 'parent-fixture', 'Read the child', check_cancel)
        self.read = self.tools['read_task']['handler']

    def tearDown(self):
        self.model.assert_not_called()

    def run_row(self, status='running', run_id='child-run'):
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute('INSERT INTO web_runs(id,item_id,request_key,status,message,config) '
                'VALUES(?,?,?,?,?,?)', (run_id, self.task_id, run_id, status, 'Fixture task', '{}'))
        return run_id

    def test_default_wait_returns_delayed_result_without_holding_lock_or_connection(self):
        self.run_row()
        ready, done = threading.Event(), threading.Event()
        errors = []
        opened = []
        workspace = self.app.workspace

        @contextmanager
        def tracked_workspace(*args, **kwargs):
            with workspace(*args, **kwargs) as ws:
                opened.append(ws)
                try:
                    yield ws
                finally:
                    opened.remove(ws)

        def complete_child():
            try:
                if not ready.wait(timeout=2):
                    raise AssertionError('Host never waited')
                # This cannot proceed if the parent holds app.lock while waiting.
                with self.app.lock, self.app.workspace() as ws, ws.conn:
                    repo.add_message(ws.conn, self.child['conversation_id'], 'assistant',
                        'Delayed answer', metadata={'source_details': [{'url': 'https://example.invalid/body'}]})
                    ws.conn.execute("UPDATE web_runs SET status='completed' WHERE id='child-run'")
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        def release_child():
            self.assertEqual(opened, [], 'Host must close its read connection before waiting')
            ready.set()
            self.assertTrue(done.wait(timeout=2), 'Child could not progress during host wait')

        worker = threading.Thread(target=complete_child, daemon=True)
        worker.start()
        try:
            clock = Clock(release_child)
            with patch.object(self.app, 'workspace', tracked_workspace), patch('lodestar.web.tools.time', clock):
                result = self.read({'id': self.task_id})
        finally:
            ready.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result['run_status'], 'completed')
        self.assertEqual(result['wait'], {'requested_seconds': 10, 'elapsed_seconds': .1,
            'reached_terminal': True, 'outcome': 'terminal'})
        self.assertEqual(result['messages'][0]['content'], 'Delayed answer')
        self.assertEqual(result['messages'][0]['sources'], [{'url': 'https://example.invalid/body'}])
        self.assertEqual(len(clock.sleeps), 1)

    def test_zero_wait_reads_running_snapshot_without_sleep(self):
        self.run_row()
        clock = Clock()
        with patch('lodestar.web.tools.time', clock):
            result = self.read({'id': self.task_id, 'wait_seconds': 0})
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(result['run_status'], 'running')
        self.assertFalse(result['wait']['reached_terminal'])
        self.assertEqual(result['wait']['outcome'], 'snapshot')

    def test_terminal_and_not_started_return_immediately(self):
        clock = Clock()
        with patch('lodestar.web.tools.time', clock):
            result = self.read({'id': self.task_id, 'wait_seconds': 20})
            self.assertIsNone(result['run_id'])
            self.assertEqual(result['wait']['outcome'], 'not_started')
            self.assertFalse(result['wait']['reached_terminal'])
            self.run_row('completed')
            for status in ('completed', 'error', 'cancelled', 'interrupted'):
                with self.subTest(status=status), self.app.workspace() as ws, ws.conn:
                    ws.conn.execute('UPDATE web_runs SET status=? WHERE id=?', (status, 'child-run'))
                result = self.read({'id': self.task_id, 'wait_seconds': 20})
                self.assertEqual(result['run_status'], status)
                self.assertTrue(result['wait']['reached_terminal'])
                self.assertEqual(result['wait']['outcome'], 'terminal')
        self.assertEqual(clock.sleeps, [])

    def test_timeout_returns_active_state_without_cancelling_or_resubmitting_child(self):
        self.run_row()
        clock = Clock()
        with patch('lodestar.web.tools.time', clock):
            result = self.read({'id': self.task_id, 'wait_seconds': 1})
        self.assertEqual(result['run_status'], 'running')
        self.assertEqual(result['wait']['outcome'], 'timeout')
        self.assertEqual(result['wait']['elapsed_seconds'], 1)
        self.assertFalse(result['wait']['reached_terminal'])
        self.assertLessEqual(clock.now, 1)
        with self.app.workspace() as ws:
            self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM web_runs').fetchone()[0], 1)
            self.assertEqual(ws.conn.execute('SELECT status FROM web_runs').fetchone()[0], 'running')

    def test_parent_cancellation_interrupts_wait(self):
        self.run_row()
        clock = Clock(self.cancelled.set)
        with patch('lodestar.web.tools.time', clock):
            with self.assertRaises(LLMError) as caught:
                self.read({'id': self.task_id, 'wait_seconds': 20})
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertEqual(len(clock.sleeps), 1)

    def test_cancellation_is_checked_again_before_reading_messages(self):
        self.run_row('completed')
        original = self.app.item

        def cancel_after_item_read(ws, item_id):
            value = original(ws, item_id)
            self.cancelled.set()
            return value

        with patch.object(self.app, 'item', side_effect=cancel_after_item_read):
            with self.assertRaises(LLMError) as caught:
                self.read({'id': self.task_id})
        self.assertEqual(caught.exception.code, 'cancelled')

    def test_messages_keep_retirement_limit_truncation_and_sources(self):
        self.run_row('completed')
        with self.app.workspace() as ws, ws.conn:
            retired = repo.add_message(ws.conn, self.child['conversation_id'], 'assistant', 'Retired private context')
            ws.conn.execute('UPDATE web_items SET context_after=? WHERE id=?', (retired['id'], self.task_id))
            for index in range(8):
                repo.add_message(ws.conn, self.child['conversation_id'], 'assistant',
                    f'Kept {index} ' + ('x' * 13000 if index == 7 else ''),
                    metadata={'source_details': [{'url': f'https://example.invalid/{index}'}]})
        result = self.read({'id': self.task_id})
        self.assertEqual(len(result['messages']), 6)
        self.assertEqual(result['messages'][0]['content'], 'Kept 2 ')
        self.assertEqual(len(result['messages'][-1]['content']), 12000)
        self.assertNotIn('Retired private context', json.dumps(result))
        self.assertEqual(result['messages'][-1]['sources'], [{'url': 'https://example.invalid/7'}])

    def test_run_and_messages_are_from_one_read_snapshot(self):
        self.run_row('completed')
        with self.app.workspace() as ws:
            repo.add_message(ws.conn, self.child['conversation_id'], 'assistant', 'Old completed answer')
        original = self.app.item
        inserted = False

        def start_new_run_after_snapshot_begins(ws, item_id):
            nonlocal inserted
            value = original(ws, item_id)
            if not inserted:
                inserted = True
                self.run_row('running', 'new-run')
                with self.app.workspace() as other_ws:
                    repo.add_message(other_ws.conn, self.child['conversation_id'], 'user', 'New run input')
            return value

        with patch.object(self.app, 'item', side_effect=start_new_run_after_snapshot_begins):
            result = self.read({'id': self.task_id, 'wait_seconds': 0})
        self.assertEqual(result['run_id'], 'child-run')
        self.assertEqual(result['run_status'], 'completed')
        self.assertEqual([m['content'] for m in result['messages']], ['Old completed answer'])
        # The returned snapshot does not claim that future/new runs are finished.
        with self.app.workspace() as ws:
            self.assertEqual(ws.conn.execute('SELECT status FROM web_runs WHERE id=?', ('new-run',)).fetchone()[0], 'running')

    def test_invalid_wait_parameters_fail_without_querying(self):
        for value in (-1, 21, True, False, None, 1.5, '10'):
            with self.subTest(value=value), patch.object(self.app, 'workspace') as workspace:
                with self.assertRaises(ValueError):
                    self.read({'id': self.task_id, 'wait_seconds': value})
                workspace.assert_not_called()
        rule = self.tools['read_task']['input_schema']['properties']['wait_seconds']
        self.assertEqual(rule, {'type': 'integer', 'minimum': 0, 'maximum': 20})


if __name__ == '__main__':
    unittest.main()
