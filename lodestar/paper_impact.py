"""On-demand citation observations; no quality score and no background requests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
import re
from urllib.parse import quote

import requests

from lodestar.memory.papers import identity

PROVIDERS = ('semantic_scholar', 'openalex')
OA_FIELDS = 'id,doi,title,publication_date,cited_by_count,fwci,citation_normalized_percentile,locations,primary_topic'
S2_FIELDS = 'paperId,title,externalIds,publicationDate,citationCount,influentialCitationCount'


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _number(value, *, integer=False, maximum=None):
    if type(value) not in ((int,) if integer else (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        return None
    return value


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _doi(value):
    text = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or ''), flags=re.I).lower()
    return text if re.fullmatch(r'10\.\d{4,9}/[^\s?#]+', text) and len(text) <= 500 else None


def locator(url):
    key, canonical = identity(url)
    if key.startswith('arxiv:'):
        return key, canonical, key[6:], None
    doi = _doi(key[4:]) if key.startswith('doi:') else None
    if doi:
        return key, canonical, None, doi
    raise ValueError('引用指标查询目前支持 arXiv 或 DOI 论文链接')


def _get(url, params, headers, check_cancel=None):
    """Only callers' fixed academic API hosts; never follow arbitrary redirects."""
    if check_cancel:
        from lodestar.cancellation import interruptible
        return interruptible(lambda emit: _get(url, params, headers),
                             check_cancel=check_cancel, timeout_s=15)
    try:
        response = requests.get(url, params=params, headers=headers, timeout=(3, 9), allow_redirects=False)
        status = response.status_code
        if status != 200:
            return None, {404: 'not_found', 429: 'rate_limited', 401: 'authentication',
                          403: 'authentication'}.get(status, 'unavailable')
        value = response.json()
        return (value, 'ok') if isinstance(value, dict) else (None, 'invalid_data')
    except (requests.RequestException, ValueError):
        # Never expose upstream errors, request headers, API keys or raw bodies.
        return None, 'unavailable'


def _semantic(arxiv, doi, check_cancel=None):
    headers = {'User-Agent': 'Lodestar/0.5 paper-impact'}
    if os.getenv('SEMANTIC_SCHOLAR_API_KEY'):
        headers['x-api-key'] = os.environ['SEMANTIC_SCHOLAR_API_KEY']
    identifier = 'ARXIV:' + arxiv if arxiv else 'DOI:' + doi
    raw, status = _get('https://api.semanticscholar.org/graph/v1/paper/' + quote(identifier, safe=''),
                       {'fields': S2_FIELDS}, headers, check_cancel)
    if raw is None:
        return None, status
    external = raw.get('externalIds') or {}
    if not isinstance(external, dict):
        return None, 'invalid_data'
    if ((arxiv and re.sub(r'v\d+$', '', str(external.get('ArXiv', ''))) != arxiv)
            or (doi and _doi(external.get('DOI')) != doi)):
        return None, 'identity_mismatch'
    record = str(raw.get('paperId', ''))
    if not re.fullmatch(r'[a-fA-F0-9]{40}', record):
        return None, 'invalid_data'
    return {'record_url': 'https://www.semanticscholar.org/paper/' + record,
            'title': str(raw.get('title') or '')[:500], 'doi': _doi(external.get('DOI')),
            'publication_date': str(_date(raw.get('publicationDate')) or '') or None,
            'citation_count': _number(raw.get('citationCount'), integer=True),
            'influential_citation_count': _number(raw.get('influentialCitationCount'), integer=True),
            'match_method': 'arxiv_id' if arxiv else 'doi'}, 'ok'


def _oa_matches(raw, arxiv, doi):
    if doi:
        return _doi(raw.get('doi')) == doi
    for location in raw.get('locations') or []:
        if not isinstance(location, dict):
            continue
        for name in ('landing_page_url', 'pdf_url'):
            try:
                key, _ = identity(location.get(name, ''))
            except ValueError:
                continue
            if key == 'arxiv:' + arxiv:
                return True
    return False


def _openalex(arxiv, doi, title, check_cancel=None):
    headers = {'User-Agent': 'Lodestar/0.5 paper-impact'}
    if os.getenv('OPENALEX_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['OPENALEX_API_KEY']
    if doi:
        raw, status = _get('https://api.openalex.org/works/https://doi.org/' + quote(doi, safe='/'),
                           {'select': OA_FIELDS}, headers, check_cancel)
        candidates = [raw] if raw else []
    elif title:
        raw, status = _get('https://api.openalex.org/works',
                           {'search': title[:300], 'per_page': 5, 'select': OA_FIELDS}, headers, check_cancel)
        candidates = raw.get('results', []) if raw else []
    else:
        return None, 'needs_identifier'
    if raw is None:
        return None, status
    if not isinstance(candidates, list):
        return None, 'invalid_data'
    # Title search discovers candidates only. Exact DOI/arXiv links confirm identity.
    matches = [item for item in candidates if isinstance(item, dict) and _oa_matches(item, arxiv, doi)]
    if len(matches) != 1:
        return None, 'not_found' if not matches else 'ambiguous'
    value = matches[0]
    record = str(value.get('id', ''))
    if not re.fullmatch(r'https://openalex.org/W\d+', record):
        return None, 'invalid_data'
    percentile = value.get('citation_normalized_percentile')
    percentile = percentile if isinstance(percentile, dict) else {}
    topic = value.get('primary_topic')
    topic = topic if isinstance(topic, dict) else {}
    subfield = topic.get('subfield') or {}
    return {'record_url': record, 'title': str(value.get('title') or '')[:500],
            'publication_date': str(_date(value.get('publication_date')) or '') or None,
            'citation_count': _number(value.get('cited_by_count'), integer=True),
            'fwci': _number(value.get('fwci')),
            'citation_percentile': _number(percentile.get('value'), maximum=1),
            'subfield': str(subfield.get('display_name') or '')[:200] if isinstance(subfield, dict) else '',
            'match_method': 'doi' if doi else 'arxiv_location'}, 'ok'


def cached(conn, url):
    key, canonical, arxiv, _ = locator(url)
    providers = {}
    for row in conn.execute('SELECT * FROM paper_impact_cache WHERE paper_key=?', (key,)):
        providers[row['provider']] = {
            'status': row['status'], 'attempted_at': row['attempted_at'],
            'fetched_at': row['fetched_at'], 'data': json.loads(row['payload']),
            'stale': bool(row['fetched_at']) and (row['status'] != 'ok' or
                _now() - datetime.fromisoformat(row['fetched_at']) > timedelta(days=1))}
    dates = [_date(p['data'].get('publication_date')) for p in providers.values()]
    dates = [value for value in dates if value is not None]
    # arXiv identifiers encode the first submission month, never revision recency.
    if arxiv:
        try:
            dates.append(date(2000 + int(arxiv[:2]), int(arxiv[2:4]), 1))
        except ValueError:
            pass
    first = min(dates) if dates else None
    recent = first is not None and 0 <= (_now().date() - first).days <= 180
    return {'paper_url': canonical, 'providers': providers, 'recent': recent,
            'observation_window_days': 180,
            'first_publication_date': first.isoformat() if first else None,
            'date_precision': 'month' if arxiv else 'day',
            'note': '新近论文，影响力尚待观察。' if recent else '引用指标反映已有引用影响，不等于论文质量或项目适用性。'}


def lookup(conn, url, *, title='', refresh=False, check_cancel=None):
    if check_cancel:
        check_cancel()
    key, _, arxiv, doi = locator(url)
    current = cached(conn, url)
    observations = []
    for provider in PROVIDERS:
        old = current['providers'].get(provider)
        if old:
            age = _now() - datetime.fromisoformat(old['attempted_at'])
            wait = timedelta(days=1) if old['status'] == 'ok' else timedelta(minutes=10)
            if age < (timedelta(minutes=1) if refresh else wait):
                continue
        if provider == 'semantic_scholar':
            data, status = _semantic(arxiv, doi, check_cancel)
        else:
            s2 = current['providers'].get('semantic_scholar', {}).get('data', {})
            data, status = _openalex(arxiv, doi or s2.get('doi'), title or s2.get('title', ''), check_cancel)
        now = _now().isoformat()
        observations.append((key, provider, json.dumps(data or {}, ensure_ascii=False),
                             now if status == 'ok' else None, now, status))
        current['providers'][provider] = {'data': data if status == 'ok' else (old or {}).get('data', {})}
    # Finish network IO before opening a write transaction, so chatting can continue.
    if check_cancel:
        check_cancel()
    for observation in observations:
        # Failed refreshes retain the last successful observation and its timestamp.
        conn.execute('''INSERT INTO paper_impact_cache(paper_key,provider,payload,fetched_at,attempted_at,status)
            VALUES(?,?,?,?,?,?) ON CONFLICT(paper_key,provider) DO UPDATE SET
            payload=CASE WHEN excluded.status='ok' THEN excluded.payload ELSE paper_impact_cache.payload END,
            fetched_at=CASE WHEN excluded.status='ok' THEN excluded.fetched_at ELSE paper_impact_cache.fetched_at END,
            attempted_at=excluded.attempted_at,status=excluded.status''',
            observation)
    return cached(conn, url)
