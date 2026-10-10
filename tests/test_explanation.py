"""Evidence survives the native conversation; no separate answer-generation stage."""
import json
import unittest
from unittest.mock import patch
from tests import test_dialogue as fixtures
from lodestar.agent.dialogue import gather


class ExplanationTests(unittest.TestCase):
    setUp = fixtures.DialogueTests.setUp
    tearDown = fixtures.DialogueTests.tearDown

    def test_full_abstract_and_publication_date_reach_model(self):
        abstract = 'Opening. ' * 90 + 'IMPORTANT END'
        context = {'message': '解释一下', 'papers': [], 'candidates': [
            {'url': 'https://paper', 'date': '2026-01-01', 'abstract': abstract}]}
        result = gather(self.ws, self.llm, context)
        saved = json.loads(self.llm.dialogue_step.call_args.args[1][0]['content'].split('\n', 1)[1].split('\n\nUser request:', 1)[0])
        self.assertEqual(saved['sources'][0]['abstract'], abstract)
        self.assertEqual(saved['sources'][0]['date'], '2026-01-01')
        self.assertEqual(result['grounding']['source_depths']['https://paper'], 'abstract')

    def test_body_and_candidate_metadata_remain_available(self):
        context = {'message': '解释', 'papers': [{'url': 'https://paper', 'content': 'Method', 'read_depth': 'full'}],
                   'candidates': [{'url': 'https://paper', 'date': '2026-01-01', 'abstract': 'Abstract'}]}
        result = gather(self.ws, self.llm, context)
        self.assertEqual(result['grounding']['source_depths']['https://paper'], 'full')
        self.assertEqual(result['candidates'][0]['date'], '2026-01-01')

    def test_snippets_never_claim_body_or_abstract_depth(self):
        result = gather(self.ws, self.llm, {'message': '介绍', 'papers': [],
            'candidates': [{'url': 'https://web', 'snippet': 'Search snippet'}]})
        self.assertEqual(result['grounding']['source_depths']['https://web'], 'search_snippet')

    def test_markdown_is_not_reformatted(self):
        text = '先比较。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n结论。'
        self.llm.dialogue_step.return_value = fixtures.answer(text)
        result = gather(self.ws, self.llm, {'message': '比较', 'papers': []})
        self.assertEqual(result['answer'], text)
        self.llm.complete.assert_not_called()
        self.llm.complete_json.assert_not_called()
