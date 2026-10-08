import tempfile
import unittest
from pathlib import Path

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient
from lodestar.memory import learning


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(llm_mode='mock',search_mode='mock',
            db_path=Path(self.tmp.name)/'db',workspace_dir=Path(self.tmp.name)/'ws')
        self.ws = Workspace(self.cfg)
        self.agent = ConversationAgent(self.ws,LLMClient(self.cfg))

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def test_research_followup_survives_restart(self):
        session = self.agent.start('alice')
        research = self.agent.turn(session,'agent memory research',user_id='alice',intent='research')
        self.assertNotIn('error',research)
        self.ws.close()
        self.ws = Workspace(self.cfg)
        self.agent = ConversationAgent(self.ws,LLMClient(self.cfg))
        answer = self.agent.turn(session,'Explain the method',user_id='alice')
        self.assertGreater(answer['evidence_reused'],0)
        self.assertEqual(len(self.agent.history(session,'alice')),4)

    def test_feedback_never_upgrades_acknowledgement(self):
        session = self.agent.start('alice')
        self.agent.turn(session,'懂了',user_id='alice',intent='feedback',technology='Harness',feedback='self_report')
        self.assertEqual(learning.profile(self.ws.conn,'alice')[0]['mastery'],'unknown')
        with self.assertRaises(ValueError):
            self.agent.turn(session,'懂了',user_id='alice',intent='feedback',technology='Harness',feedback='demonstrated_application')

    def test_owner_and_project_boundaries(self):
        session = self.agent.start('alice')
        with self.assertRaises(ValueError):
            self.agent.history(session,'bob')
        with self.assertRaises(ValueError):
            self.agent.turn(session,'Use it in my project',user_id='alice',intent='plan')
        self.assertEqual(self.agent.history(session,'alice'),[])

    def test_synthesis_failure_is_error_with_recoverable_evidence(self):
        from unittest.mock import patch
        from lodestar.llm import LLMError
        from lodestar.memory import repo
        session = self.agent.start('alice')
        original = self.agent.llm.complete
        def fail_synthesis(role, *args, **kwargs):
            if role == 'synthesis':
                raise LLMError('simulated timeout')
            return original(role, *args, **kwargs)
        with patch.object(self.agent.llm, 'complete', side_effect=fail_synthesis):
            result = self.agent.turn(session, 'agent memory research', user_id='alice', intent='research')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(repo.get_task(self.ws.conn, result['task_id'])['status'], 'error')
        self.assertTrue((Path(result['workspace_dir']) / 'read_evidence.json').exists())
        self.assertEqual(learning.profile(self.ws.conn, 'alice'), [])
        self.ws.close()
        self.ws = Workspace(self.cfg)
        self.agent = ConversationAgent(self.ws, LLMClient(self.cfg))
        followup = self.agent.turn(session, 'Compare these methods', user_id='alice', intent='followup')
        self.assertGreater(followup['evidence_reused'], 0)
