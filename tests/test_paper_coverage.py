import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lodestar.config import Config
from lodestar.tools.paper_read import _parse_sections, _read_arxiv_full, tool_read_paper
from types import SimpleNamespace


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

    def test_numbered_headings_use_canonical_section_names(self):
        for method_heading in ('3 Method', '3. Method', '3.Method', '3) Method', '3.1 Method', '3.1. Method', '3\tMethod'):
            with self.subTest(heading=method_heading):
                sections = _parse_sections(method_heading + '\nMETHOD_EVIDENCE\n4.2 Experimental Results\nEXPERIMENT_EVIDENCE')
                self.assertEqual(sections['method'], 'METHOD_EVIDENCE')
                self.assertEqual(sections['experimental_results'], 'EXPERIMENT_EVIDENCE')
        self.assertNotIn('method', _parse_sections('3Method\nordinary text'))

    def test_repeated_recognized_headings_do_not_erase_earlier_evidence(self):
        sections = _parse_sections('Method\nFIRST_METHOD\nMethod\nSECOND_METHOD')
        self.assertIn('FIRST_METHOD', sections['method'])
        self.assertIn('SECOND_METHOD', sections['method'])

    def _arxiv_read(self, text, budget=12000):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(pdf_cache_dir=Path(directory))
            with patch('lodestar.tools.paper_read._download_pdf', return_value=True), patch(
                    'lodestar.tools.paper_read._extract_pdf_text', return_value=(text, _parse_sections(text))):
                return _read_arxiv_full(cfg, '2609.33439v1', {'title': 'Study'}, 'Abstract',
                                        'https://arxiv.org/abs/2609.33439v1', budget)

    def test_approach_and_experimental_evaluation_are_actually_delivered(self):
        result = self._arxiv_read('Approach\nMETHOD_EVIDENCE\nExperimental Evaluation\nEXPERIMENT_EVIDENCE')
        self.assertIn('METHOD_EVIDENCE', result['text'])
        self.assertIn('EXPERIMENT_EVIDENCE', result['text'])
        self.assertIn('bounded', result['coverage'])

    def test_long_introduction_does_not_evict_method_and_experiment(self):
        result = self._arxiv_read('Introduction\n' + 'Background text. ' * 500 +
            '\nMethod\nMETHOD_EVIDENCE\nExperiments\nEXPERIMENT_EVIDENCE', budget=1000)
        self.assertIn('METHOD_EVIDENCE', result['text'])
        self.assertIn('EXPERIMENT_EVIDENCE', result['text'])
        self.assertLessEqual(len(result['text']), 1000)
        self.assertTrue(result['truncated'])

    def test_section_omission_is_reported_even_when_final_text_fits(self):
        result = self._arxiv_read('Method\n' + 'x' * 5000 + '\nExperiments\nRESULT_EVIDENCE')
        self.assertTrue(result['truncated'])
        self.assertIn('RESULT_EVIDENCE', result['text'])

    def test_generic_pdf_query_selects_late_body_and_keeps_original_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(search_mode='live', full_text_enabled=True, pdf_cache_dir=Path(directory))
            text = ('Unrelated introduction.\n' * 600) + 'Mechanismneedle retrieves stored events.'
            with patch('lodestar.tools.paper_read._download_pdf', return_value=True), patch(
                    'lodestar.tools.paper_read._extract_pdf_text', return_value=(text, {})):
                result = tool_read_paper(SimpleNamespace(config=cfg), 'https://example.test/study.pdf',
                    full_text=True, query='Mechanismneedle', char_budget=2000)
            self.assertIn('Mechanismneedle', result['text'])
            self.assertTrue(result['query_matched'])
            self.assertEqual(result['coverage'], 'query_selected_body_excerpts')
            for span in result['evidence_spans']:
                self.assertIn(text[span['start']:span['end']], result['text'])
            self.assertLessEqual(len(result['text']), 2000)

    def test_generic_pdf_cache_does_not_mix_different_urls_with_same_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(search_mode='live', full_text_enabled=True, pdf_cache_dir=Path(directory))
            with patch('lodestar.tools.paper_read._download_pdf', return_value=True) as download, patch(
                    'lodestar.tools.paper_read._extract_pdf_text', return_value=('Method\nBODY', {'method': 'BODY'})):
                for url in ('https://a.test/paper.pdf', 'https://b.test/paper.pdf', 'https://a.test/paper.pdf'):
                    tool_read_paper(SimpleNamespace(config=cfg), url, full_text=True)
            destinations = [call.args[1] for call in download.call_args_list]
            self.assertNotEqual(destinations[0], destinations[1])
            self.assertEqual(destinations[0], destinations[2])

    def test_oversized_abstract_cannot_replace_raw_body_under_full_read_label(self):
        from lodestar.tools.paper_read import _pdf_body_result
        result = _pdf_body_result('BODY_EVIDENCE ' + 'body ' * 200, {}, 'abstract ' * 1000,
                                  'Study', 'https://example.test/study.pdf', 1000)
        self.assertIn('BODY_EVIDENCE', result['text'])
        self.assertLessEqual(len(result['text']), 1000)
        self.assertTrue(result['truncated'])

    def test_query_budget_and_spans_describe_only_delivered_passages(self):
        from lodestar.tools.paper_read import _pdf_body_result
        from lodestar.retrieval import terms
        text = 'needle ' + 'x' * 2000 + ' needle ' + 'y' * 2000
        for budget in (80, 500, 1600, 2400):
            with self.subTest(budget=budget):
                result = _pdf_body_result(text, {}, 'abstract ' * 1000,
                    'Study', 'https://example.test/study.pdf', budget, query='needle')
                self.assertLessEqual(len(result['text']), budget)
                passages = [text[span['start']:span['end']] for span in result['evidence_spans']]
                self.assertEqual('\n\n'.join(passages), result['text'])
                self.assertEqual(result['query_matched'], bool(terms('needle') & terms(result['text'])))
                self.assertTrue(result['truncated'])

    def test_real_pdf_extraction_retains_numbered_approach_and_results(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory) / 'generated.pdf'
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((50, 50), 'Study\n3. Approach\nMETHOD_EVIDENCE ' + 'method details ' * 8 +
                             '\n4. Experimental Results\nEXPERIMENT_EVIDENCE ' + 'measured result ' * 8)
            doc.save(cached)
            doc.close()
            cfg = Config(search_mode='live', full_text_enabled=True, pdf_cache_dir=Path(directory))
            with patch('lodestar.tools.paper_read._pdf_cache_path', return_value=cached), patch(
                    'lodestar.tools.paper_read._download_pdf', return_value=True):
                result = tool_read_paper(SimpleNamespace(config=cfg), 'https://example.test/generated.pdf',
                                         full_text=True, char_budget=2000)
            self.assertIn('METHOD_EVIDENCE', result['text'])
            self.assertIn('EXPERIMENT_EVIDENCE', result['text'])
            self.assertEqual(result['sections'], ['approach', 'experimental_results'])
            self.assertEqual(result['read_depth'], 'full')
