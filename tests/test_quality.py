import os
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.eval.quality import check_citations, model_check
from lodestar.llm import LLMClient, LLMError


class QualityTests(unittest.TestCase):
    def test_unknown_citation_fails_mechanical_review(self):
        result=check_citations('Evidence https://arxiv.org/abs/1234 and https://example.com/fake',
                               [{'url':'https://arxiv.org/abs/1234'}])
        self.assertEqual(result['mechanical_status'],'needs_review')
        self.assertEqual(result['unknown_urls'],['https://example.com/fake'])

    def test_matching_url_does_not_certify_semantics(self):
        result=check_citations('[Paper](https://arxiv.org/abs/1234)',[{'url':'https://arxiv.org/abs/1234'}])
        self.assertEqual(result['mechanical_status'],'pass')
        self.assertEqual(result['semantic_review'],'pending')

    def test_missing_credentials_blocks_without_request(self):
        with patch.dict(os.environ,{},clear=True), patch('lodestar.llm.LLMClient') as client:
            result=model_check(Config())
            self.assertEqual(result['status'],'blocked')
            client.assert_not_called()

    def test_live_client_has_explicit_missing_credential_error(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(LLMError):
                LLMClient(Config(llm_mode='live'))

    def test_array_container_requires_explicit_opt_in(self):
        client = LLMClient(Config(llm_mode='mock'))
        with patch('lodestar.llm.MockLLM.complete', return_value='[{"method":"A"}]'):
            with self.assertRaises(LLMError):
                client.complete_json('planner', '', '')
            self.assertEqual(client.complete_json('learning_exposure', '', '', allow_list=True), [{'method': 'A'}])
