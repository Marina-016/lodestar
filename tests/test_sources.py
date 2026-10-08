import unittest
from lodestar.agent.sources import attach_paper_sources


class SourceTests(unittest.TestCase):
    def test_actual_sources_are_linked_with_bounded_read_scope(self):
        source = {'source_type': 'paper', 'title': 'A [study]',
                  'url': 'https://arxiv.org/abs/2609.37725', 'read_depth': 'full'}
        answer = attach_paper_sources('An explanation without citations', [source, source])
        self.assertIn('https://arxiv.org/abs/2609.37725', answer)
        self.assertEqual(answer.count('https://arxiv.org/abs/2609.37725'), 1)
        self.assertIn('非整篇全文', answer)
        self.assertIn('不代表逐句事实核验', answer)

    def test_no_sources_or_unsafe_links_do_not_create_provenance(self):
        sources = [{'source_type': 'paper', 'url': 'javascript:alert(1)'},
                   {'source_type': 'paper', 'url': 'https://user:secret@example.com/a'},
                   {'source_type': 'web', 'url': 'https://example.com'}]
        self.assertEqual(attach_paper_sources('answer', sources), 'answer')
        self.assertEqual(attach_paper_sources('answer', []), 'answer')
