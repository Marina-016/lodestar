import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lodestar.config import Config
from lodestar.tools.paper_read import _read_arxiv_full


class PaperCoverageTests(unittest.TestCase):
    def test_nonstandard_headings_return_raw_excerpt_and_explicit_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(pdf_cache_dir=Path(directory))
            with patch('lodestar.tools.paper_read._download_pdf', return_value=True), patch(
                'lodestar.tools.paper_read._extract_pdf_text',
                return_value=('3 Multi-Agent Collaboration\nactual method body', {'introduction':'intro'})):
                result = _read_arxiv_full(cfg,'2609.33439v1',{'title':'Raven'},'Abstract',
                                         'https://arxiv.org/abs/2609.33439v1',1000)
            self.assertEqual(result['coverage'],'raw_body_excerpt')
            self.assertIn('actual method body',result['text'])
            self.assertIn('not complete coverage',result['note'])
