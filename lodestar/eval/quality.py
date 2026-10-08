"""Mechanical evidence checks, explicitly separate from semantic quality review."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


def _url(value: str) -> str:
    parts = urlsplit(value.rstrip('.,;，。；'))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), parts.query, ''))


def check_citations(answer: str, sources: list[dict]) -> dict:
    allowed = {_url(s['url']) for s in sources if s.get('url')}
    cited = {_url(u) for u in re.findall(r'https?://[^\s\)\]\}>"\u4e00-\u9fff]+', answer)}
    unsupported = sorted(cited - allowed)
    return {'answer_nonempty': bool(answer.strip()), 'source_count': len(allowed),
        'cited_source_urls': sorted(cited & allowed), 'unknown_urls': unsupported,
        'citation_present': bool(cited & allowed),
        'mechanical_status': 'pass' if answer.strip() and cited & allowed and not unsupported else 'needs_review',
        'semantic_review': 'pending',
        'limitations': ['URL matching does not prove the cited text supports a claim.',
                       'Title-only citations require manual review.',
                       'Method correctness, limitations and project applicability require semantic review.']}


def model_check(cfg) -> dict:
    """One bounded live request. Missing credentials never fall back to mock."""
    import os
    from dataclasses import replace
    from lodestar.llm import LLMClient, LLMError
    credentials = bool(os.getenv('DASHSCOPE_API_KEY')) if cfg.llm_provider == 'dashscope' else bool(os.getenv('ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_AUTH_TOKEN'))
    if not credentials:
        return {'status': 'blocked', 'reason': 'missing_model_credentials',
                'model': cfg.model, 'live_request_performed': False}
    try:
        client = LLMClient(replace(cfg, llm_mode='live', max_tokens=100, llm_timeout_s=20))
        text = client.complete('connection_check', 'Return exactly LODESTAR_OK.',
                               'This is a connection check, not a research task.', max_tokens=50)
        return {'status': 'ok' if text.strip() == 'LODESTAR_OK' else 'needs_review',
                'model': cfg.model, 'live_request_performed': True,
                'quality_verified': False}
    except LLMError:
        return {'status': 'error', 'reason': 'model_request_failed',
                'model': cfg.model, 'quality_verified': False}
