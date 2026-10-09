import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.routing import route
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient
from lodestar.memory import learning, repo


class RoutingTests(unittest.TestCase):
    def test_routes_and_questions_are_not_feedback(self):
        self.assertEqual(route('今天 Harness 有什么新进展？').intent,'research')
        self.assertEqual(route('能用在我的项目吗').intent,'plan')
        self.assertEqual(route('我懂了').intent,'feedback')
        self.assertEqual(route('“懂了”是什么意思？').intent,'followup')
        self.assertTrue(route('这个方法具体怎么做').supplement)

    def test_auto_feedback_without_topic_answers_without_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            ws=Workspace(Config(llm_mode='mock',search_mode='mock',db_path=Path(temp)/'db',workspace_dir=Path(temp)/'ws'))
            try:
                agent=ConversationAgent(ws,LLMClient(ws.config))
                session=agent.start()
                self.assertEqual(agent.turn(session,'懂了')['status'],'answered')
                self.assertEqual(learning.profile(ws.conn),[])
                agent.turn(session,'介绍 Harness',technology='Harness')
                result=agent.turn(session,'懂了')
                self.assertEqual(result['intent'],'feedback')
                self.assertEqual(learning.profile(ws.conn)[0]['mastery'],'unknown')
            finally:
                ws.close()

    def test_failed_supplement_preserves_evidence_and_traces(self):
        with tempfile.TemporaryDirectory() as temp:
            ws=Workspace(Config(llm_mode='mock',search_mode='mock',db_path=Path(temp)/'db',workspace_dir=Path(temp)/'ws'))
            try:
                agent=ConversationAgent(ws,LLMClient(ws.config))
                session=agent.start()
                repo.create_task(ws.conn,'task','Harness',{},llm_mode='mock')
                source={'source_type':'paper','url':'https://arxiv.org/abs/2609.33439','content':'original body','read_depth':'full'}
                with ws.conn:
                    ws.conn.execute('UPDATE agent_sessions SET task_id=?, evidence=? WHERE conversation_id=?',('task',json.dumps([source]),session))
                with patch('lodestar.tools.registry.call_tool',return_value={'error':'network failed'}):
                    result=agent.turn(session,'方法具体怎么做')
                self.assertEqual(result['supplement_reads'][0]['error'],'network failed')
                self.assertIn('original body',agent._session(session,'default')['evidence'])
                events=repo.list_trace_events(ws.conn,'task')
                self.assertEqual([e['kind'] for e in events],['tool_call','tool_result'])
            finally:
                ws.close()
