import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.context import Workspace
from lodestar import paper_impact as impact
from lodestar.memory import papers
from lodestar.agent.dialogue import gather


URL = 'https://arxiv.org/abs/2609.12354v2'
DOI = '10.1234/example'
S2 = {'paperId': 'a'*40, 'title': 'An example paper', 'externalIds': {'ArXiv': '2609.12354', 'DOI': DOI},
      'publicationDate': '2026-09-11', 'citationCount': 0, 'influentialCitationCount': 0}
OA = {'id': 'https://openalex.org/W123', 'doi': 'https://doi.org/'+DOI,
      'title': 'An example paper', 'publication_date': '2026-09-11', 'cited_by_count': 12,
      'fwci': 1.75, 'citation_normalized_percentile': {'value': .92},
      'primary_topic': {'subfield': {'display_name': 'Artificial Intelligence'}},
      'locations': [{'landing_page_url': 'https://arxiv.org/abs/2609.12354v1'}]}


def response(value, status=200):
    result = Mock(status_code=status)
    result.json.return_value = value
    return result


class PaperImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ws = Workspace(Config(llm_mode='mock', search_mode='mock', db_path=root/'db', workspace_dir=root))
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.clock = patch.object(impact, '_now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.ws.close)

    def test_counts_are_separate_zero_survives_and_reading_does_not_fetch(self):
        with patch.object(impact.requests, 'get', side_effect=[response(S2), response(OA)]) as get:
            with self.ws.conn:
                value = impact.lookup(self.ws.conn, URL)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(value['providers']['semantic_scholar']['data']['citation_count'], 0)
            self.assertEqual(value['providers']['openalex']['data']['citation_count'], 12)
            self.assertEqual(value['providers']['openalex']['data']['citation_percentile'], .92)
            self.assertTrue(value['recent'])
            self.assertEqual(get.call_args.kwargs['allow_redirects'], False)
            impact.lookup(self.ws.conn, URL, refresh=True)
            impact.lookup(self.ws.conn, URL)
            saved = papers.save(self.ws.conn, {'title': 'An example paper', 'url': URL})
            self.assertEqual(saved['impact']['providers'], value['providers'])
            self.assertEqual(get.call_count, 2)

    def test_failed_refresh_retains_values_and_original_observation_time(self):
        with patch.object(impact.requests, 'get', side_effect=[response(S2), response(OA)]), self.ws.conn:
            first = impact.lookup(self.ws.conn, URL)
        with patch.object(impact, '_now', return_value=self.now+timedelta(days=2)):
            with patch.object(impact.requests, 'get', return_value=response({}, 429)) as get, self.ws.conn:
                latest = impact.lookup(self.ws.conn, URL, refresh=True)
                for provider in impact.PROVIDERS:
                    before, after = first['providers'][provider], latest['providers'][provider]
                    self.assertEqual(after['data'], before['data'])
                    self.assertEqual(after['fetched_at'], before['fetched_at'])
                    self.assertEqual(after['status'], 'rate_limited')
                    self.assertTrue(after['stale'])
                impact.lookup(self.ws.conn, URL)
                self.assertEqual(get.call_count, 2)

    def test_title_similarity_is_never_sufficient_for_identity(self):
        wrong = copy.deepcopy(OA)
        wrong['locations'] = [{'landing_page_url': 'https://arxiv.org/abs/2609.99999'}]
        with patch.object(impact.requests, 'get', side_effect=[response({}, 429), response({'results': [wrong]})]), self.ws.conn:
            value = impact.lookup(self.ws.conn, URL, title='An example paper')
        self.assertEqual(value['providers']['openalex']['status'], 'not_found')
        self.assertEqual(value['providers']['openalex']['data'], {})

    def test_arxiv_location_can_confirm_openalex_when_semantic_scholar_is_unavailable(self):
        with patch.object(impact.requests, 'get', side_effect=[response({}, 429), response({'results': [OA]})]), self.ws.conn:
            value = impact.lookup(self.ws.conn, URL, title='An example paper')
        self.assertEqual(value['providers']['openalex']['data']['match_method'], 'arxiv_location')
        self.assertEqual(value['providers']['semantic_scholar']['data'], {})

    def test_invalid_fields_are_unknown_and_new_version_does_not_reset_age(self):
        invalid = {**OA, 'fwci': float('nan'), 'cited_by_count': -1,
                   'citation_normalized_percentile': {'value': 1.2}}
        with patch.object(impact.requests, 'get', return_value=response(invalid)):
            data, status = impact._openalex(None, DOI, '')
        self.assertEqual(status, 'ok')
        for field in ('fwci', 'citation_count', 'citation_percentile'):
            self.assertIsNone(data[field])
        self.assertFalse(impact.cached(self.ws.conn, 'https://arxiv.org/abs/1706.03762v99')['recent'])

    def test_url_scope_and_redirects_cannot_be_used_as_an_arbitrary_fetcher(self):
        with patch.object(impact.requests, 'get') as get:
            for url in ('http://127.0.0.1/private', 'https://example.com/paper', 'https://doi.org/not-a-doi'):
                with self.assertRaises(ValueError): impact.lookup(self.ws.conn, url)
            get.assert_not_called()
        with patch.object(impact.requests, 'get', return_value=response({}, 302)), self.ws.conn:
            value = impact.lookup(self.ws.conn, URL)
        self.assertEqual(value['providers']['semantic_scholar']['status'], 'unavailable')

    def test_doi_identity_and_bad_semantic_identity_are_checked(self):
        with patch.object(impact.requests, 'get', side_effect=[response(S2), response(OA)]), self.ws.conn:
            value = impact.lookup(self.ws.conn, 'https://doi.org/'+DOI)
        self.assertEqual(value['providers']['semantic_scholar']['data']['match_method'], 'doi')
        bad = {**S2, 'externalIds': {'ArXiv': '2609.99999'}}
        with patch.object(impact.requests, 'get', return_value=response(bad)):
            data, status = impact._semantic('2609.12354', None)
        self.assertIsNone(data)
        self.assertEqual(status, 'identity_mismatch')

    def test_dialogue_receives_metrics_and_provenance_without_creating_memory(self):
        from lodestar.memory.assistant import SCHEMA
        self.ws.conn.executescript(SCHEMA)
        llm = Mock()
        llm.dialogue_step.side_effect = [
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'impact-1',
                'name': 'get_paper_impact', 'input': {'url': URL}}]},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': '这里是引用指标，不等于质量评分。'}]},
        ]
        with patch.object(impact.requests, 'get', side_effect=[response(S2), response(OA)]):
            result = gather(self.ws, llm, {'message': '查询论文引用指标', 'papers': []}, max_calls=1)
        self.assertEqual(result['grounding']['tool_dispatches'], 1)
        packet = llm.dialogue_step.call_args.args[1][0]['content']
        self.assertIn('influential_citation_count', packet)
        self.assertIn('https://openalex.org/W123', packet)
        self.assertIn('citation_metrics_only', packet)
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM assistant_memories').fetchone()[0], 0)

    def test_library_refresh_is_explicit_and_constructs_no_model(self):
        from dataclasses import replace
        from lodestar.web.server import WebApp
        config = replace(self.ws.config, db_path=Path(self.temp.name)/'web.db')
        factory = Mock(side_effect=AssertionError('No model requests'))
        app = WebApp(config, llm_factory=factory)
        try:
            with patch.object(impact.requests, 'get', side_effect=[response(S2), response(OA)]) as get:
                paper = app.paper_library({'title': 'An example paper', 'url': URL})
                get.assert_not_called()
                value = app.paper_library({'action': 'refresh_impact', 'id': paper['paper']['id']})
                self.assertEqual(value['impact']['providers']['openalex']['data']['fwci'], 1.75)
                self.assertEqual(get.call_count, 2)
                factory.assert_not_called()
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
