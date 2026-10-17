"""QF-1 synthetic evidence for isolated, human-reviewed acceptance.

No network, registry mutation, model call, or semantic answer scorer. Search text
is recorded, not interpreted: this fixture does not simulate relevance search.
The runner must freeze its own clock and inject these functions explicitly.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

FIXTURE_ID = 'QF-1'
FIXED_NOW = '2026-10-17T12:00:00+08:00'
_NOW = datetime.fromisoformat(FIXED_NOW)
_NOTICE = 'Synthetic QF-1 acceptance evidence; not real papers or research findings.'
_BASE = 'https://example.invalid/quality/'

_SOURCES = (
    {'fixture_key': 'A', 'title': 'Window Memory — SYNTHETIC',
     'url': _BASE + 'window-v1', 'date': '2026-10-12',
     'updated_at': '2026-10-16T00:00:00+08:00', 'version': 'window-v1',
     'abstract': 'A synthetic window-memory study reports a direction of improvement. '
                 'This abstract supplies no method parameters or experiment numbers.'},
    {'fixture_key': 'B', 'title': 'Retrieved Memory — SYNTHETIC',
     'url': _BASE + 'retrieval-v1', 'date': '2026-10-15',
     'updated_at': '2026-10-15T00:00:00+08:00', 'version': 'retrieval-v1',
     'abstract': 'A synthetic Agent-memory study retrieves stored events on demand. '
                 'This abstract supplies no retrieval count or experiment numbers.'},
    {'fixture_key': 'C', 'title': 'Older Popular Memory — SYNTHETIC',
     'url': _BASE + 'older-v2', 'date': '2026-09-30',
     'updated_at': '2026-10-16T00:00:00+08:00', 'version': 'older-v2',
     'abstract': 'An older synthetic Agent-memory paper with a recent update.',
     'popularity': {'score': 10000, 'channel': 'synthetic_popularity_signal'}},
    {'fixture_key': 'D', 'title': 'Protein Search — SYNTHETIC',
     'url': _BASE + 'protein-v1', 'date': '2026-10-16',
     'updated_at': '2026-10-16T00:00:00+08:00', 'version': 'protein-v1',
     'abstract': 'This synthetic study concerns protein retrieval, not personal research '
                 'Agents or their memory.'},
)
_BODY = {
    'A': (
        ('method', 'Method §2',
         'Only the three most recent events enter model context. Events removed from '
         'that window cannot be recalled by this method.'),
        ('experiment', 'Experiment §4',
         'On the same 100 synthetic tasks with the same base model, the baseline '
         'completes 60 tasks and the window method completes 72. Mean input tokens '
         'are 1000 and 800 respectively. This excerpt supplies no repeated runs, '
         'variance, confidence intervals, online-user results, or component ablations.'),
    ),
    'B': (
        ('method', 'Method §3',
         'Events remain in external storage. Each query retrieves two relevant events. '
         'Retrieval can miss relevant events; storing all events does not mean injecting '
         'all events into model context.'),
        ('experiment', 'Experiment §5',
         'On another set of 100 synthetic tasks with the same base model, the baseline '
         'completes 30 tasks and the retrieval method completes 45. These tasks differ '
         'from the window study. This excerpt supplies neither a shared-protocol direct '
         'comparison nor a component ablation.'),
    ),
}
_HUMAN_FACTS = {
    'fixture_id': FIXTURE_ID,
    'synthetic': True,
    'review_only': True,
    'recent_relevant_keys': ['A', 'B'],
    'A': {'publication': 'unknown', 'absolute_gain_percentage_points': 12,
          'relative_gain_percent': 20, 'input_token_reduction_percent': 20},
    'B': {'publication': 'synthetic_record_found',
          'absolute_gain_percentage_points': 15, 'relative_gain_percent': 50},
    'limitations': [
        'A and B use different task sets; their rates or relative gains do not rank the methods.',
        'Input-token reduction alone does not establish the same reduction in total cost.',
        'A recent update or popularity signal does not establish recent first submission.',
        'The excerpts do not establish statistical significance or a unique causal component.',
        'Missing information in an excerpt does not prove absence from the entire paper.',
        'A publication record does not establish scientific quality or project suitability.',
    ],
}


def candidates():
    """Return fresh source metadata without body passages or review answers."""
    return [{**deepcopy(source), 'fixture_id': FIXTURE_ID, 'synthetic': True,
             'source_type': 'paper', 'authors': ['Synthetic Fixture Author'],
             'date_basis': 'first_submission',
             'published_at': source['date'] + 'T00:00:00+08:00',
             'retrieved_at': FIXED_NOW, 'dedup_key': 'qf1:' + source['fixture_key'],
             'snippet': source['abstract'], 'publication_status': 'not_checked',
             'peer_review_status': 'not_verified'} for source in _SOURCES]


def human_facts():
    """Independent review annotations; never include these in model tool results."""
    return deepcopy(_HUMAN_FACTS)


def _source(url):
    for source in candidates():
        if source['url'] == url:
            return source
    raise ValueError('Unknown QF-1 paper URL; no network fallback is available')


def document(url):
    """Return the frozen document for independent span checks by the reviewer."""
    source = _source(url)
    text = source['title'] + '\n\nAbstract\n' + source['abstract']
    spans = []
    abstract_start = text.index(source['abstract'])
    spans.append({'key': 'abstract', 'section': 'Abstract',
                  'start': abstract_start, 'end': abstract_start + len(source['abstract'])})
    for key, heading, body in _BODY.get(source['fixture_key'], ()):
        text += '\n\n' + heading + '\n'
        start = len(text)
        text += body
        spans.append({'key': key, 'section': heading, 'start': start, 'end': len(text)})
    return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'url': url,
            'version': source['version'], 'text': text, 'sections': spans}


def read_paper(url, *, full_text=False, query='', char_budget=12000, section=None):
    """Read bounded excerpts; exact section names select a passage, not a topic."""
    source = _source(url)
    if type(full_text) is not bool or not isinstance(query, str):
        raise ValueError('full_text must be boolean and query must be text')
    if type(char_budget) is not int or char_budget < 1:
        raise ValueError('char_budget must be a positive integer')
    if section not in (None, 'method', 'experiment'):
        raise ValueError('section must be method or experiment')
    if section is not None and not full_text:
        raise ValueError('A body section requires full_text=true')
    selected = section
    if selected is None and query.strip().casefold() in ('method', 'experiment'):
        selected = query.strip().casefold()
    frozen = document(url)
    keys = [selected] if full_text and selected else (
        ['method', 'experiment'] if full_text else ['abstract'])
    available = [span for span in frozen['sections'] if span['key'] in keys]
    if not available:
        raise ValueError('QF-1 has no body snapshot for this known paper')
    chunks, evidence_spans, remaining = [], [], char_budget
    for span in available:
        separator = '\n\n' if chunks else ''
        if remaining <= len(separator):
            break
        passage = frozen['text'][span['start']:span['end']]
        excerpt = passage[:remaining-len(separator)]
        chunks.append(separator + excerpt)
        evidence_spans.append({'section': span['section'], 'start': span['start'],
                               'end': span['start'] + len(excerpt),
                               'version': source['version']})
        remaining -= len(separator) + len(excerpt)
    text = ''.join(chunks)
    expected_length = sum(span['end']-span['start'] for span in available) + 2*(len(available)-1)
    return {**source, 'text': text, 'truncated': len(text) < expected_length,
            'read_depth': 'full' if full_text else 'abstract',
            'full_text_ok': full_text, 'coverage': 'bounded_body_excerpt' if full_text else 'abstract',
            'sections': [span['section'] for span in evidence_spans],
            'evidence_spans': evidence_spans,
            'span_coordinates': 'Offsets in the frozen extracted document before truncation.',
            'query_matched': True if full_text and selected else None,
            'note': _NOTICE + ' Bounded excerpts only, not the whole paper. '
                    'Only exact method/experiment section selection is simulated; '
                    'other query text does not perform semantic passage retrieval.'}


def verify_publication(ws, source):
    """Drop-in replacement for dialogue.verify_publication; ws is never opened."""
    paper = _source(source['url'])
    found = paper['fixture_key'] == 'B'
    result = {'fixture_id': FIXTURE_ID, 'synthetic': True, 'url': paper['url'],
              'publication_status': 'publication_record_found' if found else 'unresolved',
              'peer_review_status': 'fixture_record_only' if found else 'not_verified',
              'verified_at': FIXED_NOW, 'metadata_provider': 'quality_fixture',
              'publication_evidence': ('Synthetic FixtureConf 2026 formal publication record.'
                  if found else 'Unknown: this fixture lookup confirms neither acceptance nor rejection.'),
              'lookup_note': _NOTICE}
    if found:
        result.update(venue='FixtureConf 2026', record_url=_BASE + 'venue-b', is_published=True)
    return result



def manifest():
    """Serializable frozen evidence for report snapshots; no reviewer answers."""
    sources = candidates()
    return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'fixed_now': FIXED_NOW,
            'notice': _NOTICE, 'sources': sources,
            'documents': [document(s['url']) for s in sources if s['fixture_key'] in _BODY],
            'publication_observations': [verify_publication(None, s) for s in sources]}


def snapshot():
    """Alias for runners storing the exact evidence packet and its stable hash."""
    return manifest()


def _search(ws, *, query=None, queries=None, max_results=None, sort_by='relevance', days=None):
    if (query is None) == (queries is None):
        raise ValueError('Provide query or queries, exclusively')
    if queries is None:
        queries = [query]
    if (not isinstance(queries, list) or not 1 <= len(queries) <= 3 or
            any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in queries)):
        raise ValueError('Provide 1-3 nonempty queries, at most 1000 characters each')
    if sort_by not in ('relevance', 'submittedDate', 'lastUpdatedDate'):
        raise ValueError('Unsupported sort_by')
    default_window = days is None and sort_by == 'submittedDate'
    if default_window:
        days = 90
    if days is not None and (type(days) is not int or not 0 <= days <= 365):
        raise ValueError('days must be an integer from 0 to 365')
    limit = max_results if max_results is not None else getattr(
        getattr(ws, 'config', None), 'arxiv_results_per_query', 4)
    if type(limit) is not int or limit < 1:
        raise ValueError('max_results must be a positive integer')
    sources = candidates()
    since = _NOW-timedelta(days=days) if days else None
    if since:
        sources = [s for s in sources if since <= datetime.fromisoformat(s['published_at']) <= _NOW]
    if sort_by != 'relevance':
        field = 'published_at' if sort_by == 'submittedDate' else 'updated_at'
        sources.sort(key=lambda s: s[field], reverse=True)
    queries = list(dict.fromkeys(q.strip() for q in queries))
    sources = [{**s, 'matched_queries': list(queries)} for s in sources[:limit]]
    return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'sources': sources,
            'days': days, 'partial': False,
            'query_results': [{'query': q, 'count': len(sources)} for q in queries],
            'time_window': {'days': days, 'from': since.isoformat() if since else None,
                            'to': FIXED_NOW, 'date_basis': 'first_submission',
                            'default_applied': default_window},
            'note': _NOTICE + ' Fixed candidates, not query relevance matching. '
                    'Date filtering/sorting only; updates do not reset first submission.'}


def _discover(ws, *, query='', kind='recent', days=7, limit=20):
    if kind not in ('recent', 'trending') or not isinstance(query, str):
        raise ValueError('kind must be recent or trending; query must be text')
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError('days must be an integer from 1 to 30')
    if type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError('limit must be an integer from 1 to 60')
    if kind == 'recent':
        return _search(ws, query=query or 'synthetic browse', max_results=limit,
                       sort_by='submittedDate', days=days)
    sources = candidates()
    sources.sort(key=lambda s: s.get('popularity', {}).get('score', 0), reverse=True)
    return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'sources': sources[:limit],
            'note': _NOTICE + ' Synthetic popularity is not publication recency.'}


def call_tool(ws, name, params):
    """Registry-shaped fixture adapter. Unknown requests fail closed."""
    try:
        if not isinstance(params, dict):
            raise ValueError('Tool params must be an object')
        if name == 'search_papers':
            return _search(ws, **params)
        if name == 'discover_papers':
            return _discover(ws, **params)
        if name == 'read_paper':
            options = dict(params)
            options.setdefault('char_budget', getattr(getattr(ws, 'config', None), 'read_char_budget', 12000))
            return read_paper(**options)
        if name == 'verify_paper':
            if set(params) != {'url'}:
                raise ValueError('verify_paper requires only url')
            return verify_publication(ws, params)
        return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'tool': name,
                'error': 'Unsupported QF-1 tool; no network or real-tool fallback is available',
                'failure_kind': 'fixture_unsupported_tool'}
    except (TypeError, ValueError, KeyError) as error:
        return {'fixture_id': FIXTURE_ID, 'synthetic': True, 'tool': name,
                'error': str(error), 'failure_kind': 'fixture_invalid_request'}
