import json
import unittest

from tests import test_dialogue as fixtures
from lodestar.agent.conversation import ConversationAgent
from lodestar.llm import LLMError


class StreamingTests(unittest.TestCase):
    setUp = fixtures.DialogueTests.setUp
    tearDown = fixtures.DialogueTests.tearDown

    def test_deltas_arrive_before_completion_and_answer_saved_once(self):
        delivered = []
        def stream(*args, on_text):
            on_text('你好')
            self.assertEqual(delivered, ['你好'])
            on_text('！')
            return fixtures.answer('你好！')
        self.llm.complete_json.return_value = {'action': 'answer'}
        self.llm.dialogue_step.side_effect = stream
        self.llm.complete_json.side_effect = None
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        result = agent.turn(session, '你好', on_text=delivered.append)
        self.assertEqual(result['answer'], '你好！')
        self.assertEqual(agent.history(session)[-1]['content'], '你好！')
        self.assertEqual(len(agent.history(session)), 2)
        self.llm.complete.assert_not_called()

    def test_interrupted_stream_preserves_partial_as_error(self):
        def stream(*args, on_text):
            on_text('已生成的内容')
            raise LLMError('broken', code='stream_interrupted', role='conversation')
        self.llm.complete_json.return_value = {'action': 'answer'}
        self.llm.dialogue_step.side_effect = stream
        agent = ConversationAgent(self.ws, self.llm)
        session = agent.start()
        result = agent.turn(session, '你好', on_text=lambda text: None)
        self.assertEqual(result['status'], 'error')
        self.assertIn('回答未完成', result['answer'])
        message = agent.history(session)[-1]
        self.assertEqual(message['kind'], 'error')
        self.assertEqual(json.loads(message['metadata'])['partial_answer'], '已生成的内容')
        self.llm.dialogue_step.assert_called_once()
