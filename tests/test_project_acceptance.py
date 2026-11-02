"""Exercise real HTTP, project permissions and payload boundaries without cloud calls."""
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from lodestar.config import Config
from scripts import eval_workspace
from scripts import quality_fixtures


def tool(name, args):
    return {'role':'assistant','content':[{'type':'tool_use','id':name,'name':name,'input':args}]}


class ProjectClient:
    def __init__(self, phase, read_body):
        self.phase, self.read_body, self.step = phase, read_body, 0

    def close(self):
        pass

    def dialogue_step(self, system, messages, tools, on_text=None):
        self.step += 1
        if self.step == 1:
            return tool('project_context', {'query':'memory'}) if self.phase in (0,3) else tool('read_memory', {})
        if self.phase == 0 and self.read_body:
            if self.step == 2:
                return tool('search_papers', {'query':'Retrieved Memory'})
            if self.step in (3,4):
                return tool('read_paper', {'url':quality_fixtures.candidates()[self.step-3]['url'], 'full_text':True})
        text = 'Scripted pipeline fixture; no semantic quality verdict.'
        if on_text:
            on_text(text)
        return {'role':'assistant','content':[{'type':'text','text':text}]}


class ProjectAcceptanceTests(unittest.TestCase):
    def run_case(self, root, *, body=True):
        phases = iter(range(4))
        def factory(config):
            return ProjectClient(next(phases), body)
        original = socket.socket.connect
        def loopback(sock, address):
            if address[0] not in ('127.0.0.1','::1'):
                raise AssertionError('Acceptance attempted an external connection')
            return original(sock,address)
        with patch('socket.socket.connect',loopback), contextlib.redirect_stdout(io.StringIO()):
            return eval_workspace.run(Config(llm_mode='mock',search_mode='mock'),root,
                                      scenario='project',llm_factory=factory)

    def test_approved_evidence_scoped_context_and_revocation_through_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run'
            report=self.run_case(root)
            self.assertTrue(all(report['checks'].values()),report['checks'])
            self.assertEqual(report['semantic_review'],'pending')
            self.assertEqual(report['model_attempts'],0)
            self.assertEqual(report['remaining_runs_at_exit'],0)
            self.assertEqual(report['quality_cards'],['Q06','project-proposal'])
            self.assertTrue(report['source_fixture']['synthetic'])
            observed=json.loads((root/'model-context.json').read_text(encoding='utf-8'))
            self.assertNotIn('UNAPPROVED_FILE_CANARY',json.dumps(observed))
            self.assertFalse(any('APPROVED_MEMORY_DOC' in o['payload'] for o in observed if o['phase']=='project-revoked'))
            self.assertEqual(report,json.loads((root/'report.json').read_text(encoding='utf-8')))

    def test_fluent_answer_without_paper_read_cannot_pass_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=self.run_case(Path(tmp)/'run',body=False)
        self.assertTrue(report['checks']['all_observed_runs_completed'])
        self.assertTrue(report['checks']['approved_file_actually_delivered'])
        self.assertFalse(report['checks']['proposal_body_actually_read'])
        self.assertFalse(all(report['checks'].values()))
        self.assertEqual(report['semantic_review'],'pending')

    def test_live_search_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'never-created'
            with self.assertRaisesRegex(ValueError,'live search'):
                eval_workspace.run(Config(search_mode='live'),root,scenario='project')
            self.assertFalse(root.exists())
