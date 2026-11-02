"""User-provided arXiv links authorize reads, never arbitrary model-invented URLs."""
from unittest.mock import patch
from tests import test_dialogue
from tests.test_dialogue import calls, answer
import unittest


class UserPaperURLTests(unittest.TestCase):
    setUp=test_dialogue.DialogueTests.setUp
    tearDown=test_dialogue.DialogueTests.tearDown
    run_dialogue=test_dialogue.DialogueTests.run_dialogue

    def read(self, message, requested, *, policy=None, history=None):
        self.ws.config.enrich_venues=False
        self.llm.dialogue_step.side_effect=[calls({'action':'read_paper','url':requested,'full_text':True}),answer()]
        with patch('lodestar.tools.registry.call_tool',return_value={'text':'Observed method evidence','read_depth':'full'}) as tool:
            result=self.run_dialogue(context={'message':message,'papers':[],'history':history or []},execution_policy=policy)
        return result,tool

    def test_explicit_versioned_user_link_is_read_without_forced_search(self):
        url='https://arxiv.org/abs/2310.08560v2'
        result,tool=self.read('阅读这篇的方法和实验：'+url+'。',url)
        self.assertEqual(tool.call_count,1)
        self.assertEqual(tool.call_args.args[1],'read_paper')
        self.assertEqual(result['papers'][0]['read_depth'],'full')
        self.assertEqual(result['candidates'][0]['read_depth'],'metadata')

    def test_official_pdf_link_normalizes_to_versioned_abstract_url(self):
        canonical='https://arxiv.org/abs/2310.08560v2'
        result,tool=self.read('读 https://arxiv.org/pdf/2310.08560v2.pdf',canonical)
        self.assertEqual(tool.call_count,1)
        self.assertEqual(tool.call_args.args[2]['url'],canonical)

    def test_model_invented_or_history_only_urls_are_not_authorized(self):
        url='https://arxiv.org/abs/2310.08560v2'
        for message,history in [('解释机制',[]),('继续',[{'role':'assistant','content':url}])]:
            with self.subTest(message=message):
                result,tool=self.read(message,url,history=history)
                tool.assert_not_called()
                self.assertIn('error',result['dialogue_events'][0]['result'])

    def test_confusable_hosts_and_extra_path_are_not_user_sources(self):
        for value in ['https://arxiv.org.evil.test/abs/2310.08560v2',
                      'https://evil.test/arxiv.org/abs/2310.08560v2',
                      'https://arxiv.org/abs/2310.08560v2/other',
                      'http://127.0.0.1/abs/2310.08560v2']:
            with self.subTest(value=value):
                _,tool=self.read(value,value)
                tool.assert_not_called()

    def test_background_network_prohibition_still_wins_over_explicit_url(self):
        url='https://arxiv.org/abs/2310.08560v2'
        result,tool=self.read('读 '+url,url,policy={'external_retrieval':'blocked'})
        tool.assert_not_called()
        self.assertFalse(result['dialogue_events'][0]['executed'])
