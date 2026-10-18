"""Offline recovery after a parent budget failure, using real host tool dispatch.

This exercises production WebApp directly. It deliberately does not import the
acceptance runner: that runner cancels remaining jobs during its own cleanup.
"""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.llm import LLMError
from lodestar.web.server import WebApp


PARTIAL = '两个研究任务已经提交，汇总尚未完成。'
FINAL_ALPHA = 'ALPHA_FINAL：工具参数校验属于运行环境的职责。'
FINAL_BETA = 'BETA_FINAL：检索结果是证据材料，不是用户掌握程度。'
DELEGATE = '分别派发两个研究任务。本轮不要联网，不要记成我已经学会。'
RECOVER = 'RECOVER_SUMMARY：现在读取两个任务的终稿再汇总，不要重新派发。'


class _Scenario:
    def __init__(self):
        self.release_beta = threading.Event()
        self.beta_started = threading.Event()
        self.lock = threading.Lock()
        self.model_steps = []
        self.dispatched = []
        self.recovered = []

    def snapshot(self):
        with self.lock:
            return list(self.model_steps)


class _ScriptedClient:
    def __init__(self, config, scenario):
        self.config, self.scenario = config, scenario
        self.steps = 0
        self.phase = None

    def close(self):
        pass

    @staticmethod
    def _tool_results(messages):
        return [json.loads(block['content']) for block in messages[-1]['content']
                if block['type'] == 'tool_result']

    @staticmethod
    def _calls(name, arguments):
        return {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': f'{name}-{index}', 'name': name, 'input': value}
            for index, value in enumerate(arguments)]}

    @staticmethod
    def _answer(text, on_text):
        if on_text:
            on_text(text)
        return {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}

    def dialogue_step(self, system, messages, tools, on_text=None):
        self.steps += 1
        if self.phase is None:
            packet = json.dumps(messages, ensure_ascii=False)
            if any(tool['name'] == 'create_task' for tool in tools):
                self.phase = 'recover' if 'RECOVER_SUMMARY' in packet else 'dispatch'
            else:
                self.phase = 'alpha' if 'RECOVERY_ALPHA' in packet else 'beta'
        with self.scenario.lock:
            self.scenario.model_steps.append((self.phase, self.steps))
        if self.phase == 'dispatch':
            if self.steps == 1:
                return self._calls('create_task', [
                    {'name': name, 'instruction': instruction,
                     'constraints': ['本轮不要联网', '不要记成我已经学会'],
                     'external_retrieval': 'blocked'}
                    for name, instruction in [('Alpha', 'RECOVERY_ALPHA：解释工具参数校验。'),
                                              ('Beta', 'RECOVERY_BETA：解释检索与学习的区别。')]])
            if self.steps == 2:
                results = self._tool_results(messages)
                if len(results) != 2 or any(result.get('status') != 'submitted' for result in results):
                    raise AssertionError('Both production dispatches must succeed before the failure')
                self.scenario.dispatched = results
                if on_text:
                    on_text(PARTIAL)
                raise LLMError('Synthetic local acceptance budget failure', code='acceptance_token_budget')
        elif self.phase == 'alpha':
            return self._answer(FINAL_ALPHA, on_text)
        elif self.phase == 'beta':
            self.scenario.beta_started.set()
            if not self.scenario.release_beta.wait(timeout=10):
                raise AssertionError('Delayed synthetic child was not released')
            return self._answer(FINAL_BETA, on_text)
        elif self.phase == 'recover':
            if self.steps == 1:
                return self._calls('read_task', [{'id': task['id'], 'wait_seconds': 1}
                                               for task in self.scenario.dispatched])
            if self.steps == 2:
                results = self._tool_results(messages)
                if len(results) != 2 or any(result.get('run_status') != 'completed' for result in results):
                    raise AssertionError('Recovery must consume two actual terminal task results')
                self.scenario.recovered = results
                # Derive the synthetic summary from actual tool output, not fixtures
                # independently available to the model before read_task ran.
                final_answers = [next(message['content'] for message in reversed(result['messages'])
                                      if message['role'] == 'assistant') for result in results]
                return self._answer('恢复汇总：\n' + '\n'.join(final_answers), on_text)
        raise AssertionError(f'Unexpected synthetic model step: {self.phase}/{self.steps}')


class WorkspaceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='lodestar-recovery-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.scenario = _Scenario()
        self.app = WebApp(Config(llm_mode='mock', search_mode='mock', model='offline-recovery',
            db_path=root/'web.db', workspace_dir=root),
            llm_factory=lambda config: _ScriptedClient(config, self.scenario))
        self.workers = []
        native_thread = threading.Thread

        def tracked_worker(**kwargs):
            worker = native_thread(**kwargs)
            self.workers.append(worker)
            return worker

        worker_patch = patch('lodestar.web.server.threading.Thread', side_effect=tracked_worker)
        worker_patch.start()
        self.addCleanup(worker_patch.stop)
        self.addCleanup(self._finish_workers)

    def _finish_workers(self):
        self.scenario.release_beta.set()
        deadline = time.monotonic()+10
        for worker in self.workers:
            worker.join(timeout=max(0, deadline-time.monotonic()))
            self.assertFalse(worker.is_alive(), 'Synthetic production worker did not terminate')
        self.assertEqual(self.app.cancel_signals, {})

    def _wait(self, run_id):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            value = self.app.events(run_id, 0)
            if value['run']['status'] not in ('queued', 'running', 'cancelling'):
                return value
            time.sleep(.01)
        self.fail('Synthetic production run did not reach a terminal state')

    def _counts(self):
        with self.app.workspace() as ws:
            return {table: ws.conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
                    for table in ('web_items', 'web_runs', 'learning_events')}

    def test_budget_failed_parent_can_recover_without_cancelling_or_replaying_children(self):
        with patch('lodestar.tools.registry.call_tool', side_effect=AssertionError('No external tool expected')) as external, \
                patch.object(self.app, 'cancel', wraps=self.app.cancel) as cancel:
            failed_id = self.app.submit('main', DELEGATE, 'original-delegation')
            failed = self._wait(failed_id)
            self.assertEqual(failed['run']['status'], 'error')
            failed_result = json.loads(failed['run']['result'])
            self.assertEqual(failed_result['model_error']['code'], 'acceptance_token_budget')
            self.assertIn(PARTIAL, failed_result['answer'])
            receipts = failed_result['action_receipts']
            self.assertEqual(len(receipts), 2)
            self.assertTrue(all(receipt['status'] == 'submitted' for receipt in receipts))
            self.assertEqual({receipt['id'] for receipt in receipts},
                             {task['id'] for task in self.scenario.dispatched})
            alpha, beta = self.scenario.dispatched
            self.assertTrue(self.scenario.beta_started.wait(timeout=5))
            self.assertEqual(self._wait(alpha['run_id'])['run']['status'], 'completed')
            self.assertEqual(self.app.events(beta['run_id'], 0)['run']['status'], 'running')
            self.assertFalse(self.app.cancel_signals[beta['run_id']].is_set())
            cancel.assert_not_called()
            before_steps, before_counts = self.scenario.snapshot(), self._counts()
            self.assertEqual(before_counts, {'web_items': 3, 'web_runs': 3, 'learning_events': 0})
            self.assertEqual(self.app.submit('main', DELEGATE, 'original-delegation'), failed_id)
            self.assertEqual(self.scenario.snapshot(), before_steps)
            self.assertEqual(self._counts(), before_counts)
            self.scenario.release_beta.set()
            self.assertEqual(self._wait(beta['run_id'])['run']['status'], 'completed')
            recovered_id = self.app.submit('main', RECOVER, 'recover-summary')
            recovered = self._wait(recovered_id)
            self.assertEqual(recovered['run']['status'], 'completed')
            result = json.loads(recovered['run']['result'])
            self.assertIn(FINAL_ALPHA, result['answer'])
            self.assertIn(FINAL_BETA, result['answer'])
            reads = [event for event in result['dialogue_events'] if event['action'] == 'read_task']
            self.assertEqual(len(reads), 2)
            self.assertTrue(all(event['executed'] and event['result']['wait']['reached_terminal']
                                and event['result']['run_status'] == 'completed' for event in reads))
            self.assertEqual({event['result']['id'] for event in reads}, {task['id'] for task in receipts})
            self.assertTrue(all(value['wait']['outcome'] == 'terminal' for value in self.scenario.recovered))
            self.assertEqual(self._counts(), {'web_items': 3, 'web_runs': 4, 'learning_events': 0})
            final_steps = self.scenario.snapshot()
            self.assertEqual(self.app.submit('main', RECOVER, 'recover-summary'), recovered_id)
            self.assertEqual(self.scenario.snapshot(), final_steps)
            self.assertEqual(self._counts(), {'web_items': 3, 'web_runs': 4, 'learning_events': 0})
            history = self.app.state()['messages']
            failed_message = next(message for message in history if message['role'] == 'assistant'
                                  and message['metadata'].get('run_id') == failed_id)
            self.assertEqual(failed_message['kind'], 'error')
            self.assertEqual(failed_message['metadata']['partial_answer'], PARTIAL)
            self.assertEqual(failed_message['metadata']['action_receipts'], receipts)
            self.assertEqual(self.app.events(failed_id, 0)['run']['status'], 'error')
            self.assertEqual(self.app.state()['learning'], [])
            cancel.assert_not_called()
            external.assert_not_called()
