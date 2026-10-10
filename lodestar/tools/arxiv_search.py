"""search_papers：arXiv API 论文检索（免费、无 Key）。"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

from lodestar.tools import arxiv_client

from lodestar.config import Config
from lodestar.tools.registry import register

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
RECENT_DAYS = 90


def time_window(days, now=None):
    now = now or datetime.now(timezone.utc)
    return {'days': days, 'from': (now-timedelta(days=days)).date().isoformat() if days else None,
            'to': now.date().isoformat(), 'date_basis': 'first_submission',
            'note': 'A recent update does not make the first publication recent.'}


def _clean_arxiv_text(text: str, limit: int | None = 500) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def build_query(query: str, field: str = 'abs') -> str:
    """Preserve explicit arXiv syntax; plain terms have explicit AND semantics."""
    if field not in {'abs', 'ti', 'all'}:
        raise ValueError('unsupported arXiv field')
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError('query must be nonempty text, at most 1000 characters')
    if re.search(r'\b(?:all|abs|ti|au|cat|id|submittedDate):', query):
        return query.strip()
    if any(ch in query for ch in '():[]'):
        raise ValueError('use explicit field prefixes for structured arXiv syntax')
    terms = re.findall(r'"[^"\n]+"|[^\s"]+', query)
    if not terms or query.count('"') % 2:
        raise ValueError('unclosed query phrase')
    return ' AND '.join(f'{field}:{term}' for term in terms)


def _search_arxiv(query: str, max_results: int = 6, timeout: int = 30, field: str = "abs", *, sort_by: str = "relevance",
                  since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    # field: all=全文(噪声多) | abs=摘要(默认，精确) | ti=标题(最严)
    params = {
        "search_query": build_query(query, field),
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
    }
    params['sortBy'] = sort_by
    params['sortOrder'] = 'descending'
    if since is not None and until is not None:
        params['search_query'] = (f'({build_query(query, field)}) AND submittedDate:['
                                 f'{since:%Y%m%d%H%M} TO {until:%Y%m%d%H%M}]')
    feed = arxiv_client.query(params, timeout)
    root = ET.fromstring(feed.text)
    sources = []
    for entry in root.findall(f"{ATOM}entry"):
        arxiv_id_url = entry.findtext(f"{ATOM}id") or ""
        m = re.search(r"/(?:abs|pdf)/([\w.\-]+)$", arxiv_id_url)
        arxiv_id = m.group(1) if m else arxiv_id_url.rstrip("/").split("/")[-1]
        authors = [a.findtext(f"{ATOM}name") or "" for a in entry.findall(f"{ATOM}author")]
        sources.append({
            "source_type": "paper",
            "title": _clean_arxiv_text(entry.findtext(f"{ATOM}title"), 300),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "authors": authors[:5],
            "date": entry.findtext(f"{ATOM}published", "")[:10],
            "date_basis": "first_submission",
            "snippet": _clean_arxiv_text(entry.findtext(f"{ATOM}summary"), 600),
            "abstract": _clean_arxiv_text(entry.findtext(f"{ATOM}summary"), None),
            "dedup_key": "arxiv:" + re.sub(r"v\d+$", "", arxiv_id),
            "paper_id": re.sub(r"v\d+$", "", arxiv_id),
            "version": arxiv_id,
            "published_at": entry.findtext(f"{ATOM}published", ""),
            "updated_at": entry.findtext(f"{ATOM}updated", ""),
            "publication_status": "not_checked",
            "peer_review_status": "not_verified",
            "journal_reference": entry.findtext('{http://arxiv.org/schemas/atom}journal_ref'),
            "author_reported_doi": entry.findtext('{http://arxiv.org/schemas/atom}doi'),
            "author_comment": entry.findtext('{http://arxiv.org/schemas/atom}comment'),
            "retrieved_at": feed.fetched_at, "cache_hit": feed.cache_hit,
        })
    return sources


def tool_search_papers(ws, query: str | None = None, max_results: int | None = None,
                       sort_by: str = "relevance", days: int | None = None, *, queries: list[str] | None = None):
    """Search independent concepts and merge evidence without changing query meaning."""
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365):
        return {'sources': [], 'error': 'days must be an integer from 0 to 365; 0 means unrestricted'}
    # Newest sorting has an explicit, disclosed recent window. Zero opts out.
    default_window = days is None and sort_by == 'submittedDate'
    if default_window:
        days = RECENT_DAYS
    if queries is None:
        result = _search_one(ws, query, max_results, sort_by, days)
        result['time_window'] = {**time_window(days), 'default_applied': default_window}
        return result
    if query is not None or not isinstance(queries, list) or not 1 <= len(queries) <= 3:
        return {'sources': [], 'error': 'Provide either query or 1-3 independent queries'}
    if any(not isinstance(q, str) or not q.strip() or len(q) > 1000 for q in queries):
        return {'sources': [], 'error': 'Each query must be nonempty text, at most 1000 characters'}
    queries = list(dict.fromkeys(q.strip() for q in queries))
    results = [_search_one(ws, q, max_results, sort_by, days) for q in queries]
    merged, by_key = [], {}
    # Interleave results so one broad query cannot crowd out other concepts.
    for index in range(max((len(r['sources']) for r in results), default=0)):
        for q, result in zip(queries, results):
            if index >= len(result['sources']):
                continue
            source = result['sources'][index]
            key = source.get('dedup_key') or source.get('paper_id') or source['url']
            if key not in by_key:
                by_key[key] = {**source, 'matched_queries': []}
                merged.append(by_key[key])
            by_key[key]['matched_queries'].append(q)
    failures = [r for r in results if r.get('error')]
    return {'sources': merged, 'days': days, 'partial': bool(failures),
            'time_window': {**time_window(days), 'default_applied': default_window},
            'query_results': [{'query': q, 'count': len(r['sources']),
                **{k: r[k] for k in ('effective_query', 'error', 'failure_kind', 'recovery') if k in r}}
                for q, r in zip(queries, results)],
            **({'error': 'All independent searches failed'} if len(failures) == len(results) else {})}


def _search_one(ws, query: str, max_results: int | None, sort_by: str, days: int | None):
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        return {'sources': [], 'error': 'query must be nonempty text, at most 1000 characters'}
    if sort_by not in {"relevance", "submittedDate", "lastUpdatedDate"}:
        return {"sources": [], "error": "unsupported sort_by"}
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365):
        return {'sources': [], 'error': 'days must be an integer from 0 to 365; 0 means unrestricted'}
    cfg: Config = ws.config
    max_results = max_results or cfg.arxiv_results_per_query
    if cfg.search_mode == "mock":
        from lodestar.fixtures import papers_for, topic_from_text
        sources = papers_for(topic_from_text(query))
        return {"sources": [{**p, "query": query} for p in sources][:max_results],
                "note": f"mock 离线检索（query={query!r}）"}
    try:
        now = datetime.now(timezone.utc)
        sources = _search_arxiv(query, max_results=max_results, timeout=cfg.tool_timeout_s,
                                field=cfg.arxiv_search_field, sort_by=sort_by,
                                since=now-timedelta(days=days) if days else None, until=now if days else None)
        return {"sources": sources, "days": days,
                'effective_query': build_query(query, cfg.arxiv_search_field),
                **({'recovery': 'This query returned no matches, not evidence that the field has no research. '
                    'Plain terms are ANDed: drop unnecessary words, search one underlying concept, '
                    'or use fielded OR alternatives. Broaden a self-imposed date window or use another '
                    'source within the remaining budget; keep explicit user date limits.'} if not sources else {}),
                "note": f"arXiv({cfg.arxiv_search_field}) 返回 {len(sources)} 条（query={query!r}）"}
    except Exception as e:  # noqa: BLE001
        return {"sources": [], "error": f"arXiv 检索失败: {e}", "note": f"query={query!r}",
                "failure_kind": getattr(e, "failure_kind", "invalid_query" if isinstance(e, ValueError) else "provider_unavailable"),
                "retry_after": getattr(e, "retry_after", None),
                "recovery": ("Correct the arXiv query syntax or parameters." if isinstance(e, ValueError) else
                             "The arXiv API is unavailable, not an empty topic search. Use discover_papers "
                             "kind=trending for independent HF data (not a date-window substitute), "
                             "or search_web for official paper pages; do not repeatedly change words on the failed API.")}


register(
    name="search_papers",
    description="按 query 或 queries（1-3 个独立查询）检索 arXiv 论文；分开搜索、合并去重，保留各查询结果与错误。",
    fn=tool_search_papers,
    parameters={"query": {"type": "string"}, "queries": {"type": "array", "items": {"type": "string"}}, "max_results": {"type": "integer"}, "sort_by": {"type": "string"}, "days": {"type": "integer"}},
)
