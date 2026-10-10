import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.routing import route
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient, LLMError
from lodestar.memory import learning, repo
from tests.test_dialogue import calls


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ws=Workspace(Config(llm_mode='mock',search_mode='mock',db_path=Path(self.tmp.name)/'db',workspace_dir=Path(self.tmp.name)/'ws'))
        self.agent=ConversationAgent(self.ws,LLMClient(self.ws.config))
        self.session=self.agent.start()

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def test_changed_topic_does_not_inherit_old_feedback_target(self):
        self.agent.turn(self.session,'介绍 Harness',technology='Harness')
        self.agent.turn(self.session,'换个话题，介绍 diffusion')
        result=self.agent.turn(self.session,'懂了')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(learning.profile(self.ws.conn),[])

    def test_model_failure_is_recorded_and_retry_works(self):
        failed=Mock()
        failed.dialogue_step.side_effect=LLMError('provider failure')
        self.agent.llm=failed
        result=self.agent.turn(self.session,'介绍 Harness')
        self.assertEqual(result['status'],'error')
        self.assertEqual(self.agent.history(self.session)[-1]['kind'],'error')
        self.agent.llm=LLMClient(self.ws.config)
        self.assertEqual(self.agent.turn(self.session,'继续解释')['status'],'answered')

    def test_failed_dialogue_retains_retrieved_candidates_and_safe_diagnostic(self):
        failed = Mock()
        failed.dialogue_step.side_effect = [
            calls({'action': 'search_web', 'query': 'AI'}),
            LLMError('private provider output', code='stream_interrupted', role='conversation')]
        self.agent.llm = failed
        with patch('lodestar.tools.registry.call_tool', return_value={
                'sources': [{'url': 'https://example.com', 'snippet': 'A public result'}]}):
            result = self.agent.turn(self.session, '今天有什么ai新闻')
        self.assertIn('对话', result['answer'])
        self.assertNotIn('private provider output', json.dumps(result))
        metadata = json.loads(self.agent.history(self.session)[-1]['metadata'])
        self.assertEqual(metadata['model_error']['code'], 'stream_interrupted')
        self.assertEqual(metadata['candidates'][0]['url'], 'https://example.com')
        self.assertEqual(len(metadata['dialogue_events']), 1)

    def test_no_match_supplement_preserves_original_body(self):
        source={'url':'https://arxiv.org/abs/2609.33439','source_type':'paper','content':'useful body','read_depth':'full'}
        with self.ws.conn:
            self.ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',(json.dumps([source]),self.session))
        with patch('lodestar.tools.registry.call_tool',return_value={'text':'unmatched intro','read_depth':'full','query_matched':False}):
            self.agent.turn(self.session,'具体如何实现')
        self.assertIn('useful body',self.agent._session(self.session,'default')['evidence'])

    def test_negated_requests_do_not_trigger_actions(self):
        self.assertEqual(route('不要重新检索，继续解释').intent,'followup')
        self.assertEqual(route('不用生成方案，解释我的项目约束').intent,'followup')
