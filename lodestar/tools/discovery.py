"""Paper-first discovery with explicit time window and provider-local attention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import requests

from lodestar.tools.arxiv_search import _search_arxiv
from lodestar.tools.registry import register


def discover(ws, query: str, days: int = 7, kind: str = 'recent', limit: int = 20):
    if not isinstance(query, str):
        raise ValueError('query must be text')
    if kind not in {'recent', 'trending'}:
        raise ValueError('kind must be recent or trending')
    days = max(1, min(int(days), 30))
    limit = max(1, min(int(limit), 60))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    if ws.config.search_mode == 'mock':
        from lodestar.fixtures import papers_for, topic_from_text
        return {'status': 'ok', 'sources': papers_for(topic_from_text(query))[:limit],
                'mode': 'mock', 'kind': kind, 'retrieved_at': now.isoformat(),
                'coverage': 'Offline fixtures; not current papers or observed popularity.'}
    provider = 'arxiv' if kind == 'recent' else 'huggingface'
    try:
        if kind == 'recent':
            sources = _search_arxiv(query.strip() or 'cat:cs.AI', limit, ws.config.tool_timeout_s,
                ws.config.arxiv_search_field, sort_by='submittedDate',
                since=since, until=now)
        else:
            response = requests.get('https://huggingface.co/api/daily_papers',
                params={'sort': 'trending', 'limit': limit}, timeout=ws.config.tool_timeout_s)
            response.raise_for_status()
            terms = set(re.findall(r'[a-z0-9]+', query.lower()))
            sources = []
            for rank, item in enumerate(response.json(), 1):
                paper = item['paper']
                title, abstract = paper.get('title', ''), paper.get('summary', '')
                if terms and not terms.intersection(re.findall(r'[a-z0-9]+', (title + ' ' + abstract).lower())):
                    continue
                paper_id = re.sub(r'v\d+$', '', paper['id'])
                sources.append({'source_type': 'paper', 'title': title,
                    'url': f'https://arxiv.org/abs/{paper_id}',
                    'authors': [a['name'] for a in paper.get('authors', [])],
                    'date': paper.get('publishedAt', '')[:10], 'snippet': abstract[:600],
                    'abstract': abstract,
                    'dedup_key': f'arxiv:{paper_id}', 'hf_rank': rank,
                    'hf_upvotes': paper.get('upvotes'),
                    'hf_submitted_at': paper.get('submittedOnDailyAt')})
        for source in sources:
            source.update(provider=provider, discovery_kind=kind, retrieved_at=now.isoformat())
        return {'status': 'ok', 'sources': sources, 'kind': kind, 'mode': 'live',
                'retrieved_at': now.isoformat(),
                **({'from_utc': since.isoformat(), 'to_utc': now.isoformat()} if kind == 'recent' else {}),
                'coverage': f'Bounded {provider} results; '
                'trending is platform attention, not global popularity.'}
    except (requests.RequestException, ValueError, KeyError) as exc:
        return {'status': 'error', 'sources': [], 'kind': kind,
                'provider': provider, 'retrieved_at': now.isoformat(),
                'error': f'{provider} discovery failed: {exc}',
                'failure_kind': getattr(exc, 'failure_kind', 'invalid_query' if isinstance(exc, ValueError) else 'provider_unavailable'),
                'retry_after': getattr(exc, 'retry_after', None),
                'recovery': 'For arXiv outages, discover_papers kind=trending uses independent HF data. '
                            'HF is platform popularity, not a substitute for the requested submission window.'}


register('discover_papers', 'Discover recent arXiv submissions (empty query browses cs.AI) or HF trending papers; report coverage and failures.',
         discover, {'query': {'type': 'string', 'required': True},
                    'days': {'type': 'integer'}, 'kind': {'type': 'string'},
                    'limit': {'type': 'integer'}})
