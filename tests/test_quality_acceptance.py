"""Fixed-evidence HTTP acceptance is plumbing, never an automatic quality score."""
import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.llm import LLMClient
from lodestar.tools import registry
from lodestar.web.server import make_server
from scripts.eval_workspace import run
from scripts import quality_fixtures as fixture


class FixtureModel(LLMClient):
    """Scripted tool decisions used solely to exercise the real HTTP/agent path."""
    observed = []

    def __init__(self, config):
        super().__init__(config)
        self.steps = 0

    def dialogue_step(self, system, messages, tools, on_text=None):
        self.observed.append(json.dumps({'system': system, 'messages': messages}, ensure_ascii=False))
        request = next((m['content'] for m in reversed(messages)
                        if m['role'] == 'user' and isinstance(m['content'], str)), '')
        actions = []
        if self.steps == 0:
            if '推荐近 7 天' in request:
                actions = [('search_papers', {'query': 'Agent memory', 'days': 7})]
            elif '比较刚才两篇' in request:
                actions = [('read_paper', {'url': s['url'], 'full_text': True})
                           for s in fixture.candidates() if s['fixture_key'] in ('A', 'B')]
            elif '哪篇更权威' in request:
                actions = [('verify_paper', {'url': s['url']})
                           for s in fixture.candidates() if s['fixture_key'] in ('A', 'B')]
        self.steps += 1
        if actions:
            return {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': f'fixture-call-{i}', 'name': name, 'input': args}
                for i, (name, args) in enumerate(actions)]}
        answer = 'Scripted fixture answer; semantic correctness has not been reviewed.'
        if on_text:
            on_text(answer)
        return {'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}


class QualityAcceptanceTests(unittest.TestCase):
    def test_fixed_sources_use_real_http_and_preserve_review_separation(self):
        original = registry.call_tool
        original_connect = socket.socket.connect

        def local_only(sock, address):
            if address[0] not in ('127.0.0.1', '::1'):
                raise AssertionError('Fixed-evidence acceptance must never connect to an external host')
            return original_connect(sock, address)

        FixtureModel.observed = []
        with tempfile.TemporaryDirectory(prefix='lodestar-quality-http-') as tmp:
            root = Path(tmp)
            existing = root/'user.db'
            existing.write_bytes(b'USER_DATABASE_UNCHANGED')
            with contextlib.redirect_stdout(io.StringIO()), patch('socket.socket.connect', local_only):
                report = run(Config(llm_mode='mock', search_mode='mock', db_path=existing),
                             root/'acceptance', scenario='quality', llm_factory=FixtureModel)
            self.assertEqual(existing.read_bytes(), b'USER_DATABASE_UNCHANGED')
            self.assertTrue(all(report['checks'].values()), report)
            self.assertEqual(report['semantic_review'], 'not_applicable_offline_fixture')
            self.assertEqual(report['model_attempts'], 0)
            self.assertEqual(report['remaining_runs_at_exit'], 0)
            self.assertEqual(len(report['turns']), 4)
            expected = {s['url'] for s in fixture.candidates() if s['fixture_key'] in ('A', 'B')}
            self.assertEqual(set(report['observed_coverage']['body_reads']), expected)
            self.assertEqual(set(report['observed_coverage']['publication_lookups']), expected)
            snapshot = (root/'acceptance'/'source-snapshot.json').read_bytes()
            self.assertEqual(report['source_fixture']['sha256'], sha256(snapshot).hexdigest())
            self.assertTrue(report['source_fixture']['synthetic'])
            facts = json.loads((root/'acceptance'/'human-facts.json').read_text(encoding='utf-8'))
            self.assertTrue(facts['review_only'])
            observed = '\n'.join(FixtureModel.observed)
            self.assertIn(fixture.FIXED_NOW, observed)
            self.assertIn('Only the three most recent events', observed)
            self.assertNotIn('relative_gain_percent', observed)
            self.assertNotIn('recent_relevant_keys', observed)
            self.assertIs(registry.call_tool, original)

    def test_server_start_failure_closes_socket_and_restores_fixture_tools(self):
        original = registry.call_tool
        servers = []

        def bind(app, port):
            server = make_server(app, port)
            servers.append(server)
            return server

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'acceptance'
            with patch('scripts.eval_workspace.make_server', side_effect=bind), patch(
                    'scripts.eval_workspace.threading.Thread.start', side_effect=RuntimeError('PRIVATE_STARTUP')):
                with self.assertRaises(RuntimeError):
                    run(Config(llm_mode='mock', search_mode='mock'), target, scenario='quality')
            self.assertEqual(servers[0].fileno(), -1)
            self.assertIs(registry.call_tool, original)
            report = json.loads((target/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['semantic_review'], 'not_run')
            self.assertFalse(report['checks']['server_started'])
            self.assertNotIn('PRIVATE_STARTUP', json.dumps(report))

    def test_live_search_cannot_be_combined_with_fixed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'reject'
            with self.assertRaisesRegex(ValueError, 'live search'):
                run(Config(llm_mode='mock', search_mode='live'), target, scenario='quality')
            self.assertFalse(target.exists())

    def test_unknown_scenario_is_not_silently_treated_as_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'reject'
            with self.assertRaisesRegex(ValueError, 'Unknown acceptance scenario'):
                run(Config(llm_mode='mock', search_mode='mock'), target, scenario='typo')
            self.assertFalse(target.exists())
