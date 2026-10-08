import unittest
from unittest.mock import Mock
from lodestar.agent.explanation import explain


class ExplanationTests(unittest.TestCase):
    def test_unmatched_claim_is_not_delivered_and_inference_is_labelled(self):
        llm = Mock()
        llm.complete_json.return_value = {'claims': [
            {'text': '直接描述文件编辑', 'paper_url': 'https://paper', 'quote': 'The context is an editable file.'},
            {'text': '引用必定完整保存', 'paper_url': 'https://paper', 'quote': 'This quote is absent from the source.'}],
            'hypotheses': ['保留出处也许需要独立记录'], 'gaps': ['未提供引用机制']}
        answer, audit = explain(llm, {'papers':[{'source_type':'paper','url':'https://paper',
                                       'content':'The context is an editable file.'}]})
        self.assertIn('直接描述文件编辑', answer)
        self.assertNotIn('引用必定完整保存', answer)
        self.assertIn('尚未验证的推测', answer)
        self.assertIn('当前证据缺口', answer)
        self.assertFalse(audit['contract_valid'])
        self.assertEqual(audit['semantic_review'], 'required')

    def test_malformed_lists_never_become_assertions(self):
        llm = Mock(); llm.complete_json.return_value = {'claims':'unsupported', 'hypotheses':[None], 'gaps':[]}
        answer, audit = explain(llm, {'papers':[]})
        self.assertNotIn('unsupported', answer)
        self.assertFalse(audit['contract_valid'])

    def test_generic_background_remains_available_without_paper_claims(self):
        llm = Mock(); llm.complete_json.return_value = {'claims':[], 'hypotheses':[], 'gaps':[],
            'background':['Harness 通常编排模型与工具调用。']}
        answer, audit = explain(llm, {'papers':[]})
        self.assertIn('Harness', answer)
        self.assertIn('不写入个人学习记忆', answer)
        self.assertEqual(audit['validated_claims'], [])

    def test_only_pdf_whitespace_is_restored_and_original_draft_retained(self):
        llm = Mock(); llm.complete_json.return_value = {'claims':[
            {'text':'编辑上下文', 'paper_url':'https://paper', 'quote':'The context is an editable file.'}], 'hypotheses':[], 'gaps':[]}
        answer, audit = explain(llm, {'papers':[{'source_type':'paper', 'url':'https://paper',
                                       'content':'The context is an\neditable file.'}]})
        self.assertTrue(audit['contract_valid'])
        self.assertTrue(audit['validated_claims'][0]['whitespace_restored'])
        self.assertEqual(audit['draft']['claims'][0]['quote'], 'The context is an editable file.')
        self.assertIn('[原文出处](https://paper)', answer)
