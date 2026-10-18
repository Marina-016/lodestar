"""Freeze cached public PDFs and replay their evidence without external retrieval."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from lodestar.retrieval import terms
from lodestar.tools.paper_read import _extract_pdf_text, _parse_sections, _pdf_body_result

_SOURCE_FIELDS = {'url', 'title', 'abstract', 'authors', 'date', 'updated_at', 'retrieved_at'}
_URL = re.compile(r'https://arxiv\.org/abs/(\d{4}\.\d{4,5}v\d+)')


class PublicPaperFixture:
    """A bounded corpus, not a live search engine or an independent fact checker."""
    FIXTURE_ID = 'frozen-public-pdfs-v1'

    def __init__(self, packet):
        if (not isinstance(packet, dict) or set(packet) !=
                {'schema_version', 'synthetic', 'frozen_at', 'papers'} or
                packet['schema_version'] != 1 or packet['synthetic'] is not False):
            raise ValueError('Expected a version-1 frozen public PDF packet')
        if not isinstance(packet['frozen_at'], str):
            raise ValueError('frozen_at must be an ISO timestamp')
        now = datetime.fromisoformat(packet['frozen_at'])
        if now.tzinfo is None:
            raise ValueError('frozen_at requires a timezone')
        if not isinstance(packet['papers'], list) or not 2 <= len(packet['papers']) <= 8:
            raise ValueError('Public comparison requires 2-8 frozen papers')
        seen = set()
        for paper in packet['papers']:
            if not isinstance(paper, dict) or set(paper) != {'source', 'text', 'text_sha256', 'pdf_sha256'}:
                raise ValueError('Unexpected paper fields; keep review annotations separate')
            source = paper['source']
            if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
                raise ValueError('Unexpected source fields; metadata must exclude review annotations')
            if any(not isinstance(source[k], str) or not source[k].strip() for k in _SOURCE_FIELDS-{'authors'}):
                raise ValueError('Source metadata requires nonempty strings')
            if not isinstance(source['authors'], list) or any(not isinstance(a, str) for a in source['authors']):
                raise ValueError('Source authors must be strings')
            if not _URL.fullmatch(source['url']) or source['url'] in seen:
                raise ValueError('Expected unique, versioned arXiv source URLs')
            seen.add(source['url'])
            for field in ('date', 'updated_at', 'retrieved_at'):
                datetime.fromisoformat(source[field])
            text = paper['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 2_000_000:
                raise ValueError('Expected bounded extracted PDF text')
            if paper['text_sha256'] != sha256(text.encode('utf-8')).hexdigest():
                raise ValueError('Extracted document hash mismatch')
            if not isinstance(paper['pdf_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', paper['pdf_sha256']):
                raise ValueError('Expected original PDF hash')
        self._packet = deepcopy(packet)
        self._papers = {p['source']['url']: p for p in self._packet['papers']}
        self.FIXED_NOW = packet['frozen_at']

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def snapshot(self):
        return deepcopy(self._packet)

    def human_facts(self):
        return {'review_only': True, 'status': 'pending', 'claims': [],
                'note': 'No reference answers supplied. Review actual delivered passages independently.'}

    def candidates(self):
        return [{**deepcopy(p['source']), 'source_type': 'paper', 'date_basis': 'first_submission',
                 'publication_status': 'not_checked', 'peer_review_status': 'not_verified',
                 'read_depth': 'abstract', 'coverage': 'abstract_only',
                 'evidence_origin': 'frozen_public_pdf'} for p in self._packet['papers']]

    def verify_publication(self, ws, source):
        if source.get('url') not in self._papers:
            return {'error': 'Unknown source; no network fallback'}
        return {'url': source['url'], 'publication_status': 'not_checked',
                'peer_review_status': 'not_verified',
                'publication_evidence': 'This corpus contains no publication verification observation.',
                'lookup_note': 'No external lookup executed; publication status remains unknown.'}

    def call_tool(self, ws, name, params):
        try:
            if name == 'search_papers':
                return self._search(**params)
            if name == 'verify_paper' and set(params) == {'url'}:
                return self.verify_publication(ws, params)
            if name != 'read_paper':
                return {'error': 'Tool unavailable in frozen public corpus; no network or real-tool fallback',
                        'failure_kind': 'frozen_corpus_unsupported_tool'}
            if set(params)-{'url', 'query', 'full_text', 'char_budget'}:
                raise ValueError('Unexpected read arguments')
            url, query = params['url'], params.get('query', '')
            full = params.get('full_text', False)
            budget = params.get('char_budget', ws.config.read_char_budget)
            if type(full) is not bool or not isinstance(query, str) or type(budget) is not int or budget < 1:
                raise ValueError('Invalid reading depth, query or budget')
            paper = self._papers[url]
            source = next(s for s in self.candidates() if s['url'] == url)
            if full:
                result = _pdf_body_result(paper['text'], _parse_sections(paper['text']), '',
                                         source['title'], url, budget, query)
            else:
                result = {'text': source['abstract'][:budget], 'read_depth': 'abstract',
                          'coverage': 'abstract_only', 'full_text_ok': False,
                          'truncated': len(source['abstract']) > budget}
            return {**source, **result}
        except (TypeError, ValueError, KeyError) as error:
            return {'error': str(error), 'failure_kind': 'frozen_corpus_invalid_request'}

    def _search(self, query=None, queries=None, max_results=4, sort_by='relevance', days=None):
        if (query is None) == (queries is None):
            raise ValueError('Provide query or queries, exclusively')
        queries = [query] if queries is None else queries
        if (not isinstance(queries, list) or not 1 <= len(queries) <= 3 or
                any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in queries)):
            raise ValueError('Provide 1-3 bounded nonempty queries')
        if type(max_results) is not int or not 1 <= max_results <= 32:
            raise ValueError('max_results must be 1-32')
        if sort_by not in ('relevance', 'submittedDate'):
            raise ValueError('Unsupported sort_by')
        default_window = days is None and sort_by == 'submittedDate'
        days = 90 if default_window else days
        if days is not None and (type(days) is not int or not 0 <= days <= 365):
            raise ValueError('days must be 0-365')
        now = datetime.fromisoformat(self.FIXED_NOW)
        since = (now-timedelta(days=days)).date() if days else None
        found = []
        for source in self.candidates():
            date = datetime.fromisoformat(source['date']).date()
            if since and not since <= date <= now.date():
                continue
            available = terms(source['title']+' '+source['abstract'])
            matched = [q for q in queries if terms(q) and terms(q) <= available]
            if matched:
                found.append({**source, 'matched_queries': matched})
        if sort_by == 'submittedDate':
            found.sort(key=lambda s: s['date'], reverse=True)
        from lodestar.tools.arxiv_search import time_window
        return {'sources': found[:max_results], 'query_results': [
                    {'query': q, 'count': sum(q in s['matched_queries'] for s in found)} for q in queries],
                'time_window': {**time_window(days or 0, now), 'default_applied': default_window},
                'scope': 'Frozen corpus only. Plain lexical AND matching, not live arXiv discovery or comprehensive search.'}


def freeze(packet_dir, output_file):
    """Build a self-contained packet from existing collection artifacts; never fetch."""
    root = Path(packet_dir).resolve()
    status = json.loads((root/'collection-status.json').read_text(encoding='utf-8'))
    if status.get('synthetic') is not False:
        raise ValueError('Expected an existing nonsynthetic public collection')
    papers = []
    for item in status['papers']:
        match = _URL.fullmatch(item['url'])
        if not match:
            raise ValueError('Expected versioned public arXiv URL')
        metadata_path = (root/item['official_metadata_file']).resolve()
        if not metadata_path.is_relative_to(root):
            raise ValueError('Metadata file is outside collection directory')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('http_status') != 200 or metadata.get('response_url') != item['url']:
            raise ValueError('Official metadata identity is not established')
        pdf = (root/'pdf-cache'/('arxiv_'+match.group(1)+'.pdf')).resolve()
        if not pdf.is_relative_to(root):
            raise ValueError('PDF file is outside collection directory')
        text, _ = _extract_pdf_text(pdf)
        if not text:
            raise ValueError('Cached PDF has no extracted text')
        values = metadata['metadata']
        source = {'url': item['url'], 'title': values['citation_title'][0],
                  'abstract': values['citation_abstract'][0], 'authors': values['citation_author'],
                  'date': item['first_submission_verified'], 'updated_at': item['version_date_verified'],
                  'retrieved_at': metadata['retrieved_at']}
        papers.append({'source': source, 'text': text, 'text_sha256': sha256(text.encode('utf-8')).hexdigest(),
                       'pdf_sha256': sha256(pdf.read_bytes()).hexdigest()})
    fixture = PublicPaperFixture({'schema_version': 1, 'synthetic': False,
                                 'frozen_at': status['created_at'], 'papers': papers})
    with Path(output_file).open('xb') as target:
        target.write(json.dumps(fixture.snapshot(), ensure_ascii=False, indent=2).encode('utf-8'))
    return fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet-dir', type=Path, required=True)
    parser.add_argument('--output-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        fixture = freeze(args.packet_dir, args.output_file)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({'packet': str(args.output_file), 'papers': len(fixture.candidates()),
                      'model_calls': 0, 'network_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
