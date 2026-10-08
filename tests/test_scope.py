import unittest
from lodestar.agent.scope import annotate_scope, NOTE


class ScopeTests(unittest.TestCase):
    def test_broad_extrapolation_marked_without_altering_claim_or_link(self):
        original = '自动进化是 Harness 优化的主流技术方向。[论文](https://arxiv.org/abs/1234)'
        result = annotate_scope(original)
        self.assertIn(NOTE, result)
        self.assertIn(original, result)
        self.assertEqual(annotate_scope(result), result)

    def test_bounded_description_and_negation_are_not_marked(self):
        for text in ('这篇论文提出 Host Agent 编排方法。', '不能证明该方法是主流。'):
            self.assertEqual(annotate_scope(text), text)
