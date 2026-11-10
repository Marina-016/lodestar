"""Regression coverage for the explicit, local-only paper library."""
import json
from pathlib import Path
import tempfile
import unittest

from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.memory import papers, repo


class PaperLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ws = Workspace(Config(db_path=root/'library.db', workspace_dir=root/'tasks', model_calls_disabled=True))
        self.project = repo.upsert_project(self.ws.conn, 'Paper project', status='active')

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def test_manual_paper_is_deduplicated_by_arxiv_identity_and_scoped_to_project(self):
        first = papers.save(self.ws.conn, {
            'title': 'A useful paper', 'url': 'https://arxiv.org/pdf/2601.00001v2.pdf',
            'authors': ['Ada', 'Grace'], 'published_year': 2026, 'venue': 'arXiv',
            'keywords': ['memory', 'agents'], 'project_id': self.project,
            'status': 'reading', 'priority': 'high', 'relevance': 'high',
            'tags': ['prototype'], 'note': 'Compare the retrieval method.',
        })
        second = papers.save(self.ws.conn, {
            'title': 'A useful paper, updated', 'url': 'https://arxiv.org/abs/2601.00001v1',
            'project_id': self.project, 'status': 'read', 'priority': 'normal',
            'relevance': 'adopted', 'authors': [], 'keywords': [],
        })
        self.assertEqual(first['paper']['id'], second['paper']['id'])
        self.assertEqual(second['paper']['canonical_url'], 'https://arxiv.org/abs/2601.00001')
        result = papers.catalog(self.ws.conn, project_id=self.project, status='read')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['papers'][0]['title'], 'A useful paper, updated')
        detail = papers.detail(self.ws.conn, first['paper']['id'], self.project)
        self.assertEqual(detail['project']['relevance'], 'adopted')
        self.assertEqual(detail['project']['tags'], [])

    def test_research_inbox_transfer_preserves_saved_depth_without_fetching(self):
        source = {
            'title': 'Inbox paper', 'url': 'https://arxiv.org/abs/2602.12345v3',
            'authors': ['Researcher'], 'date': '2026-02-12', 'venue': 'arXiv',
            'keywords': ['evaluation'], 'record_url': 'https://example.org/record/123',
        }
        recommendation = self.ws.conn.execute(
            'INSERT INTO paper_recommendations(project_id,paper_key,source,matches,first_seen,last_seen,channels,state) VALUES(?,?,?,?,?,?,?,?)',
            (self.project, 'arxiv:2602.12345', json.dumps(source), '[]', '2026-02-12T00:00:00Z',
             '2026-02-12T00:00:00Z', '[]', 'read')).lastrowid
        self.ws.conn.execute(
            'INSERT INTO paper_candidate_reads(recommendation_id,status,evidence,created_at) VALUES(?,?,?,?)',
            (recommendation, 'read', json.dumps({'read_depth': 'full', 'content': 'bounded evidence'}), '2026-02-12T00:00:00Z'))
        self.ws.conn.commit()

        result = papers.attach_inbox(self.ws.conn, self.project, recommendation)
        self.assertEqual(result['paper']['metadata_source'], 'research_inbox')
        self.assertEqual(result['paper']['canonical_url'], 'https://arxiv.org/abs/2602.12345')
        self.assertEqual(result['project']['reading_depth'], 'full')
        self.assertEqual(result['project']['status'], 'read')
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM paper_candidate_reads').fetchone()[0], 1)

    def test_link_must_be_safe_https_without_credentials_or_fragment(self):
        for url in ('http://example.org/paper', 'https://user@example.org/paper', 'https://example.org/paper#part'):
            with self.assertRaises(ValueError):
                papers.save(self.ws.conn, {'title': 'Unsafe', 'url': url, 'authors': [], 'keywords': []})
