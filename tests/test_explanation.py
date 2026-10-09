import json
import unittest
from unittest.mock import Mock

from lodestar.agent.explanation import explain
from lodestar.llm import LLMError


class ExplanationTests(unittest.TestCase):
    def test_full_abstract_reaches_model_and_chinese_intro_reaches_user(self):
        abstract = 'Opening sentence. ' * 90 + 'IMPORTANT END OF ABSTRACT'
        url = 'https://arxiv.org/abs/2601.00001'
        llm = Mock()
        llm.complete.return_value = f'根据摘要，这篇工作讨论长期记忆。结尾强调实际约束。[论文]({url})'
        answer, audit = explain(llm, {'papers':[], 'candidates':[
            {'url':url,'abstract':abstract,'snippet':abstract[:600]}]})
        payload = json.loads(llm.complete.call_args.args[2])
        self.assertEqual(payload['sources'][0]['content'],abstract)
        self.assertIn('结尾强调实际约束',answer)
        self.assertEqual(answer,llm.complete.return_value)
        self.assertEqual(audit['source_depths'][url],'abstract')

    def test_body_overrides_abstract_without_losing_publication_date(self):
        url = 'https://paper'
        llm = Mock();llm.complete.return_value = '正文提供了更多细节。'
        explain(llm, {'candidates':[{'url':url,'date':'2026-01-01','abstract':'Abstract'}],
            'papers':[{'url':url,'content':'Method details','read_depth':'full'}]})
        source = json.loads(llm.complete.call_args.args[2])['sources'][0]
        self.assertEqual(source['content'],'Method details')
        self.assertEqual(source['read_depth'],'full')
        self.assertEqual(source['date'],'2026-01-01')

    def test_tool_failure_and_project_context_are_available_to_model(self):
        llm = Mock();llm.complete.return_value = '来源暂不可用。'
        events = [{'action':'search_papers','result':{'error':'timeout','sources':[]}},
                  {'action':'project_context','result':{'documents':[{'path':'a.py','content':'approved'}]}}]
        explain(llm, {'papers':[], 'tool_results':events})
        payload = json.loads(llm.complete.call_args.args[2])
        self.assertEqual(payload['tool_results'][0]['result']['error'],'timeout')
        self.assertEqual(payload['tool_results'][1]['result']['documents'][0]['content'],'approved')

    def test_empty_answer_does_not_claim_task_success(self):
        llm = Mock();llm.complete.return_value = '   '
        with self.assertRaises(LLMError): explain(llm, {'papers':[]})
        llm.complete.assert_called_once()
