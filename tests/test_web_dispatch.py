"""Offline dispatch lifecycle checks; no provider or external retrieval calls."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.web.server import WebApp
from lodestar.web.tools import assistant_tools


class FakeLLM:
    def __init__(self, config):
        self.config = config

    def dialogue_step(self, *args, **kwargs):
        return {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Recovered answer'}]}

    def close(self):
        pass


def inline_worker(**kwargs):
    return SimpleNamespace(start=lambda: kwargs['target'](*kwargs['args']))


class DispatchStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lodestar-dispatch-test-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.factory = Mock(side_effect=FakeLLM)
        self.app = WebApp(Config(llm_mode='mock', search_mode='mock',
            db_path=root/'web.db', workspace_dir=root), llm_factory=self.factory)

    def tearDown(self):
        self.assertEqual(self.app.cancel_signals, {})

    def assert_failed_start(self, failure):
        with self.assertRaises(RuntimeError) as caught:
            self.app.submit('main', 'Original question', 'first')
        self.assertIs(caught.exception, failure)
        self.factory.assert_not_called()
        state = self.app.state()
        run = state['run']
        self.assertEqual(run['status'], 'error')
        self.assertEqual(json.loads(run['result'])['runtime_error']['code'], 'worker_start_failed')
        self.assertEqual([m['role'] for m in state['messages']], ['user', 'assistant'])
        self.assertEqual(state['messages'][-1]['kind'], 'error')
        self.assertNotIn('PRIVATE_STARTUP_BODY', json.dumps(state))
        self.assertEqual(self.app.cancel_signals, {})
        # Replaying the same submission remains idempotent; a new request can run.
        self.assertEqual(self.app.submit('main', 'Original question', 'first'), run['id'])
        with patch('lodestar.web.server.threading.Thread', side_effect=inline_worker):
            next_id = self.app.submit('main', 'Try again', 'second')
        self.assertNotEqual(next_id, run['id'])
        self.assertEqual(self.app.events(next_id, 0)['run']['status'], 'completed')
        self.factory.assert_called_once()

    def test_thread_start_failure_releases_slot_and_retains_safe_record(self):
        failure = RuntimeError('PRIVATE_STARTUP_BODY')
        with patch('lodestar.web.server.threading.Thread.start', side_effect=failure):
            self.assert_failed_start(failure)

    def test_thread_construction_failure_also_releases_slot(self):
        failure = RuntimeError('PRIVATE_STARTUP_BODY')
        with patch('lodestar.web.server.threading.Thread', side_effect=failure):
            self.assert_failed_start(failure)

    def test_acknowledged_stop_stays_cancelled_when_start_then_fails(self):
        failure = RuntimeError('Fixture startup failed after cancellation')
        acknowledged = []

        def stop_then_fail():
            run_id = self.app.state()['run']['id']
            acknowledged.append(self.app.cancel('main', run_id))
            raise failure

        with patch('lodestar.web.server.threading.Thread.start', side_effect=stop_then_fail):
            with self.assertRaises(RuntimeError) as caught:
                self.app.submit('main', 'Stop before starting', 'stop-start')
        self.assertIs(caught.exception, failure)
        self.assertEqual(acknowledged, ['cancelling'])
        state = self.app.state()
        self.assertEqual(state['run']['status'], 'cancelled')
        self.assertEqual(state['messages'][-1]['kind'], 'cancelled')
        self.assertEqual(state['messages'][-1]['metadata']['status'], 'cancelled')
        self.assertEqual(json.loads(state['run']['result'])['runtime_error']['code'], 'worker_start_failed')
        self.assertEqual(self.app.cancel_signals, {})
        self.factory.assert_not_called()
        with patch('lodestar.web.server.threading.Thread', side_effect=inline_worker):
            next_id = self.app.submit('main', 'Continue', 'after-stop-start')
        self.assertEqual(self.app.events(next_id, 0)['run']['status'], 'completed')

    def test_failed_start_can_be_followed_by_a_real_worker(self):
        with patch('lodestar.web.server.threading.Thread.start', side_effect=RuntimeError('Cannot start')):
            with self.assertRaises(RuntimeError):
                self.app.submit('main', 'First question', 'failed')
        native_thread, workers = threading.Thread, []

        def tracked_worker(**kwargs):
            worker = native_thread(**kwargs)
            workers.append(worker)
            return worker

        with patch('lodestar.web.server.threading.Thread', side_effect=tracked_worker):
            run_id = self.app.submit('main', 'Next question', 'recover')
        self.assertEqual(len(workers), 1)
        workers[0].join(timeout=5)
        self.assertFalse(workers[0].is_alive(), 'Recovery worker did not terminate')
        self.assertEqual(self.app.events(run_id, 0)['run']['status'], 'completed')
        self.assertEqual(self.app.state()['messages'][-1]['content'], 'Recovered answer')
        self.factory.assert_called_once()

    def test_child_failure_has_created_not_submitted_receipt_and_error_run(self):
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) "
                "VALUES('parent','main','parent','running','Delegate','{}')")
            tools = assistant_tools(self.app, self.app.item(ws, 'main'), 'parent', 'Delegate', lambda: None)
        with patch('lodestar.web.server.threading.Thread.start', side_effect=RuntimeError('Cannot start')):
            with self.assertRaises(RuntimeError):
                tools['create_task']['handler']({'name': 'Child', 'instruction': 'Research',
                    'constraints': [], 'external_retrieval': 'blocked'})
        with self.app.workspace() as ws:
            receipts = self.app.action_receipts(ws, 'parent')
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['status'], 'created_not_submitted')
        self.assertNotIn('run_id', receipts[0])
        child = self.app.state(receipts[0]['id'])
        self.assertEqual(child['run']['status'], 'error')
        self.assertEqual(json.loads(child['run']['policy'])['external_retrieval'], 'blocked')
        self.factory.assert_not_called()

    def test_cleanup_failure_is_not_suppressed_and_keeps_original_exception(self):
        real_workspace = self.app.workspace
        calls = 0

        @contextmanager
        def workspace(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Fixture persistence failure')
            with real_workspace(*args, **kwargs) as ws:
                yield ws

        failure = RuntimeError('Fixture startup failure')
        with patch.object(self.app, 'workspace', workspace), patch(
                'lodestar.web.server.threading.Thread.start', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                self.app.submit('main', 'Question', 'cleanup-fails')
        self.assertIs(caught.exception.__context__, failure)
        self.assertEqual(self.app.cancel_signals, {})
        self.factory.assert_not_called()
