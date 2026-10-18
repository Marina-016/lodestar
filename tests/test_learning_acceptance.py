"""Isolated Q07 HTTP lifecycle observations, never an automatic semantic score."""
import contextlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.llm import LLMError
from scripts import eval_workspace


class _LearningScript:
    def __init__(self, fail_phase=None):
        self.clients = 0
        self.observed = []
        self.readbacks = {}
        self.fail_phase = fail_phase

    def factory(self, config):
        phase = self.clients
        self.clients += 1
        return _LearningClient(config, self, phase)


class _LearningClient:
    def __init__(self, config, script, phase):
        self.config, self.script, self.phase = config, script, phase
        self.step = 0

    def close(self):
        pass

    def _call(self, name, arguments):
        return {'role': 'assistant', 'content': [{'type': 'tool_use',
            'id': f'learning-{self.phase}-{self.step}', 'name': name, 'input': arguments}]}

    def dialogue_step(self, system, messages, tools, on_text=None):
        self.script.observed.append(json.dumps({'system': system, 'messages': messages}, ensure_ascii=False))
        self.step += 1
        if self.phase == self.script.fail_phase:
            raise LLMError('Synthetic acceptance stop', code='acceptance_budget')
        if self.phase == 0 and self.step == 1:
            return self._call('record_learning', {'technology': 'ReAct', 'method': '思考与行动交替',
                                                  'event': 'discussed'})
        if self.phase in (1, 2, 3, 4) and self.step == 1:
            return self._call('read_memory', {})
        if self.phase in (1, 3) and self.step == 2:
            observed = json.loads(messages[-1]['content'][0]['content'])
            evidence_id = observed['learning'][0]['evidence'][0]['id']
            return self._call('record_learning', {'action': 'correct' if self.phase == 1 else 'revoke',
                                                  'id': evidence_id})
        if self.phase in (2, 4) and self.step == 2:
            self.script.readbacks[self.phase] = json.loads(messages[-1]['content'][0]['content'])
        answer = 'Synthetic response. Its meaning has not been semantically reviewed.'
        if on_text:
            on_text(answer)
        return {'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}


class LearningAcceptanceTests(unittest.TestCase):
    def _run_local(self, config, destination, **kwargs):
        original_connect = socket.socket.connect
        connected = []

        def local_only(sock, address):
            if address[0] not in ('127.0.0.1', '::1'):
                raise AssertionError('Learning acceptance must only connect to loopback')
            connected.append(address)
            return original_connect(sock, address)

        with contextlib.redirect_stdout(io.StringIO()), patch('socket.socket.connect', local_only), patch(
                'lodestar.tools.registry.call_tool', side_effect=AssertionError('No external retrieval expected')):
            report = eval_workspace.run(config, destination, scenario='learning', **kwargs)
        self.assertTrue(connected, 'The integration must exercise real local HTTP')
        return report

    def test_scripted_native_tools_record_correct_recall_revoke_without_semantic_verdict(self):
        script = _LearningScript()
        with tempfile.TemporaryDirectory(prefix='lodestar-learning-http-') as tmp:
            root = Path(tmp)
            original = root/'original.db'
            original.write_bytes(b'ORIGINAL_DATABASE_MUST_STAY_UNCHANGED')
            report = self._run_local(Config(llm_mode='mock', search_mode='mock', db_path=original),
                                     root/'acceptance', llm_factory=script.factory)
            self.assertEqual(original.read_bytes(), b'ORIGINAL_DATABASE_MUST_STAY_UNCHANGED')
            self.assertEqual(report['quality_cards'], ['Q07'])
            self.assertEqual(report['semantic_review'], 'pending')
            self.assertEqual(report['model_attempts'], 0)
            self.assertEqual(report['remaining_runs_at_exit'], 0)
            self.assertEqual(script.clients, 5)
            self.assertTrue(all(report['checks'].values()), report['checks'])
            self.assertEqual(len(report['turns']), 5)
            first, corrected, recalled, forgotten, after = [turn['learning_snapshot'] for turn in report['turns']]
            initial_id = first['events'][0]['id']
            self.assertEqual(first['events'][0]['event'], 'discussed')
            self.assertTrue(first['events'][0]['active'])
            self.assertEqual(first['events'][0]['source']['message']['role'], 'user')
            self.assertEqual(len(corrected['events']), 2)
            self.assertFalse(corrected['events'][0]['active'])
            replacement = corrected['events'][1]
            self.assertTrue(replacement['active'])
            self.assertEqual(replacement['event'], 'self_report')
            self.assertEqual(replacement['source']['corrects'], initial_id)
            self.assertEqual(replacement['source']['message']['content'],
                             '纠正刚才的记录：其实我只看了摘要，没有认真学，也没有实践。')
            self.assertEqual(recalled['active_event_ids'], corrected['active_event_ids'])
            self.assertTrue(all(not event['active'] for event in forgotten['events']))
            self.assertEqual(after['active_event_ids'], [])
            self.assertEqual(after['profile'], [])
            self.assertEqual(script.readbacks[2]['learning'][0]['evidence'][0]['id'], replacement['id'])
            self.assertEqual(script.readbacks[2]['learning'][0]['mastery'], 'unknown')
            self.assertEqual(script.readbacks[4]['learning'], [])
            self.assertTrue(all(not turn['learning_snapshot']['verified_mastery_event_ids'] for turn in report['turns']))
            self.assertEqual(first['profile'][0]['mastery'], 'unknown')
            self.assertEqual(corrected['profile'][0]['mastery'], 'unknown')
            operations = [receipt for turn in report['turns'] for receipt in turn['action_receipts']
                          if receipt['action'] == 'record_learning']
            self.assertEqual(len(operations), 3)
            self.assertEqual(operations[0]['evidence_id'], initial_id)
            self.assertEqual(operations[1]['operation'], 'correct')
            self.assertEqual(operations[2]['operation'], 'revoke')
            self.assertTrue(all(action['executed'] and not action['error'] for turn in report['turns']
                                for action in turn['actions']))
            saved = json.loads((root/'acceptance'/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, report)
            connection = sqlite3.connect((root/'acceptance'/'web.db').as_uri()+'?mode=ro&immutable=1', uri=True)
            try:
                self.assertEqual(connection.execute('SELECT count(*) FROM web_items').fetchone()[0], 1)
                self.assertEqual(connection.execute('SELECT count(DISTINCT conversation_id) FROM messages').fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM messages WHERE role='user'").fetchone()[0], 5)
            finally:
                connection.close()
            observed = '\n'.join(script.observed)
            self.assertNotIn('learning_correction_replaced_evidence', observed)
            self.assertNotIn('learning_snapshot', observed)
            self.assertNotIn('semantic_review', observed)

    def test_default_mock_finishes_plumbing_without_claiming_learning_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run_local(Config(llm_mode='mock', search_mode='mock'), Path(tmp)/'mock')
        self.assertEqual(len(report['turns']), 5)
        self.assertTrue(report['checks']['all_observed_runs_completed'])
        self.assertEqual(report['semantic_review'], 'pending')
        self.assertEqual(report['learning_observation'], 'not_established_no_events')
        self.assertFalse(report['checks']['learning_evidence_recorded'])
        self.assertFalse(report['checks']['learning_correction_replaced_evidence'])
        self.assertFalse(report['checks']['learning_evidence_revoked'])
        self.assertFalse(all(report['checks'].values()))
        self.assertTrue(all(turn['learning_snapshot']['events'] == [] for turn in report['turns']))
        self.assertEqual(report['remaining_runs_at_exit'], 0)

    def test_failure_turn_keeps_completed_prior_snapshots_and_actual_event_state(self):
        script = _LearningScript(fail_phase=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'failure'
            report = self._run_local(Config(llm_mode='mock', search_mode='mock'), root,
                                     llm_factory=script.factory)
            self.assertEqual(len(report['turns']), 3)
            failed = report['turns'][-1]
            self.assertEqual(failed['status'], 'error')
            self.assertEqual(failed['model_error']['code'], 'acceptance_budget')
            self.assertEqual(len(failed['learning_snapshot']['events']), 2)
            self.assertEqual(len(failed['learning_snapshot']['active_event_ids']), 1)
            self.assertEqual(report['semantic_review'], 'pending')
            self.assertFalse(report['checks']['all_observed_runs_completed'])
            self.assertEqual(json.loads((root/'report.json').read_text(encoding='utf-8')), report)
            self.assertEqual(report['remaining_runs_at_exit'], 0)

    def test_learning_cli_is_available_without_loading_real_environment(self):
        result = {'checks': {'all_observed_runs_completed': True},
                  'model_attempts': 0, 'semantic_review': 'pending'}
        with contextlib.redirect_stdout(io.StringIO()), patch('sys.argv', ['eval_workspace', '--scenario', 'learning']), patch(
                'scripts.eval_workspace.load_config', return_value=Config(llm_mode='mock')), patch(
                'scripts.eval_workspace.run', return_value=result) as run:
            self.assertEqual(eval_workspace.main(), 0)
        self.assertEqual(run.call_args.kwargs['scenario'], 'learning')
        self.assertEqual(run.call_args.args[0].llm_mode, 'mock')

    def test_learning_rejects_live_search_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'reject'
            with self.assertRaisesRegex(ValueError, 'live search'):
                eval_workspace.run(Config(llm_mode='mock', search_mode='live'), target, scenario='learning')
            self.assertFalse(target.exists())
