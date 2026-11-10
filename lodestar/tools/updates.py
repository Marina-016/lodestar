"""Bounded, on-demand current-information lookups.

This is deliberately not a feed or a background crawler. A call represents one
user-requested turn and returns small, attributable source records only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from lodestar.tools.arxiv_search import tool_search_papers
from lodestar.tools.registry import register
from lodestar.tools.web_search import tool_search_web


OFFICIAL_DOMAINS = {
    'openai': ('openai.com',), 'chatgpt': ('openai.com',), 'gpt': ('openai.com',),
    'anthropic': ('anthropic.com',), 'claude': ('anthropic.com',),
    'google': ('blog.google', 'deepmind.google'), 'gemini': ('blog.google', 'deepmind.google'),
    'deepmind': ('deepmind.google',), 'meta': ('ai.meta.com', 'about.fb.com'),
    'llama': ('ai.meta.com',), 'microsoft': ('microsoft.com',), 'copilot': ('microsoft.com',),
    'xai': ('x.ai',), 'grok': ('x.ai',), 'github': ('github.blog', 'docs.github.com'),
}
SOURCES = {'official', 'github', 'papers', 'web', 'hn'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dedup(title, url):
    compact = re.sub(r'[^a-z0-9]+', '', (title or '').lower())[:80]
    return 'dynamic:' + (compact or url)


def _tag(sources, *, query, source, authority, provider):
    retrieved_at = _now()
    return [{**item, 'query': query, 'dynamic_source': source, 'authority': authority,
             'provider': item.get('provider') or provider, 'retrieved_at': retrieved_at,
             'dedup_key': item.get('dedup_key') or _dedup(item.get('title', ''), item.get('url', ''))}
            for item in sources if isinstance(item, dict) and isinstance(item.get('url'), str)]


def _official_domains(query):
    lower = query.lower()
    return next((domains for name, domains in OFFICIAL_DOMAINS.items()
                 if re.search(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])', lower)), ())


def _official(ws, query, max_results):
    domains = _official_domains(query)
    if not domains:
        return {'sources': [], 'query': query, 'source': 'official',
                'recovery': '未识别出可限定的公司或模型；可改用网页检索，或在问题中写明公司名称。'}
    # One primary domain per operation keeps this source bounded rather than
    # turning a chat request into an invisible multi-site crawl.
    domain = domains[0]
    result = tool_search_web(ws, f'site:{domain} {query}', max_results)
    raw = result.get('sources', [])
    if ws.config.search_mode != 'mock':
        raw = [item for item in raw if urlparse(item.get('url', '')).hostname and
               (urlparse(item['url']).hostname == domain or urlparse(item['url']).hostname.endswith('.' + domain))]
    sources = _tag(raw, query=query, source='official', authority='primary', provider='official_web')
    return {**{key: value for key, value in result.items() if key != 'sources'},
            'sources': [{**item, 'source_type': 'official_release'} for item in sources],
            'source': 'official', 'official_domain': domain,
            **({'recovery': '官方域名检索没有匹配项；这不表示该公司没有更新。可换关键词或使用网页来源。'}
               if not sources and not result.get('error') else {})}


def _github_repo(query):
    match = re.search(r'(?:https?://)?github\.com/([\w.-]+)/([\w.-]+)', query, re.I)
    return (match.group(1), match.group(2).removesuffix('.git')) if match else None


def _github(ws, query, max_results):
    if ws.config.search_mode == 'mock':
        from lodestar.fixtures import topic_from_text, web_for
        return {'sources': [{**item, 'source_type': 'github_update'} for item in _tag(
                    web_for(topic_from_text(query))[:max_results], query=query, source='github',
                    authority='primary', provider='github_api')],
                'query': query, 'source': 'github', 'note': 'mock GitHub 检索'}
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Lodestar-current-search'}
    timeout = min(ws.config.tool_timeout_s, 15)
    try:
        repo = _github_repo(query)
        if repo:
            endpoint = f'https://api.github.com/repos/{repo[0]}/{repo[1]}/releases'
            response = requests.get(endpoint, params={'per_page': max_results}, headers=headers, timeout=timeout)
            if response.status_code == 404:
                endpoint = f'https://api.github.com/repos/{repo[0]}/{repo[1]}/commits'
                response = requests.get(endpoint, params={'per_page': max_results}, headers=headers, timeout=timeout)
            payload = response.json() if response.status_code < 400 else []
            entries = payload if isinstance(payload, list) else []
            sources = []
            for item in entries:
                release_url = item.get('html_url') or item.get('url')
                if not release_url:
                    continue
                date = (item.get('published_at') or item.get('created_at') or
                        item.get('commit', {}).get('author', {}).get('date') or '')
                title = item.get('name') or item.get('tag_name') or item.get('sha') or f'{repo[0]}/{repo[1]} update'
                sources.append({'url': release_url, 'title': title, 'date': date[:10],
                                'date_basis': 'release_or_commit_time',
                                'snippet': item.get('body') or item.get('commit', {}).get('message', '')})
        else:
            response = requests.get('https://api.github.com/search/repositories',
                                    params={'q': query, 'sort': 'updated', 'order': 'desc', 'per_page': max_results},
                                    headers=headers, timeout=timeout)
            payload = response.json() if response.status_code < 400 else {}
            sources = [{'url': item['html_url'], 'title': item.get('full_name') or item['html_url'],
                        'date': (item.get('pushed_at') or item.get('updated_at') or '')[:10],
                        'date_basis': 'repository_push_time', 'snippet': item.get('description') or ''}
                       for item in payload.get('items', []) if item.get('html_url')]
        if response.status_code >= 400:
            retry_at = response.headers.get('x-ratelimit-reset')
            return {'sources': [], 'query': query, 'source': 'github', 'provider': 'github_api',
                    'error': f'GitHub API 暂不可用（HTTP {response.status_code}）',
                    'failure_kind': 'rate_limited' if response.status_code in (403, 429) else 'provider_unavailable',
                    **({'retry_after': retry_at} if retry_at else {})}
        return {'sources': [{**item, 'source_type': 'github_update'} for item in _tag(
                    sources, query=query, source='github', authority='primary', provider='github_api')],
                'query': query, 'source': 'github', 'provider': 'github_api',
                'rate_limit_remaining': response.headers.get('x-ratelimit-remaining')}
    except (requests.RequestException, ValueError, TypeError):
        return {'sources': [], 'query': query, 'source': 'github', 'provider': 'github_api',
                'error': 'GitHub 公开接口暂不可用。', 'failure_kind': 'provider_unavailable',
                'recovery': '请稍后重试；未配置令牌时使用 GitHub 的公开限额。'}


def _papers(ws, query, days, max_results):
    result = tool_search_papers(ws, query, max_results=max_results, sort_by='submittedDate', days=days)
    return {**{key: value for key, value in result.items() if key != 'sources'},
            'sources': _tag(result.get('sources', []), query=query, source='papers',
                            authority='paper', provider='arxiv'), 'source': 'papers'}


def _web(ws, query, max_results):
    result = tool_search_web(ws, query, max_results)
    return {**{key: value for key, value in result.items() if key != 'sources'},
            'sources': _tag(result.get('sources', []), query=query, source='web',
                            authority='secondary', provider='web_search'), 'source': 'web'}


def _hn(ws, query, max_results):
    if ws.config.search_mode == 'mock':
        from lodestar.fixtures import topic_from_text, web_for
        return {'sources': [{**item, 'source_type': 'community_signal'} for item in _tag(
                    web_for(topic_from_text(query))[:max_results], query=query, source='hn',
                    authority='signal', provider='hn_algolia')],
                'query': query, 'source': 'hn', 'note': 'mock HN 社区信号'}
    try:
        response = requests.get('https://hn.algolia.com/api/v1/search_by_date',
                                params={'query': query, 'tags': 'story', 'hitsPerPage': max_results},
                                timeout=min(ws.config.tool_timeout_s, 15))
        if response.status_code >= 400:
            raise requests.HTTPError(response.status_code)
        sources = []
        for item in response.json().get('hits', []):
            url = item.get('url') or (f"https://news.ycombinator.com/item?id={item['objectID']}" if item.get('objectID') else '')
            if url:
                sources.append({'url': url, 'title': item.get('title') or item.get('story_title') or url,
                                'date': (item.get('created_at') or '')[:10], 'date_basis': 'community_post_time',
                                'snippet': item.get('story_text') or '', 'authors': [item.get('author', '')]})
        return {'sources': [{**item, 'source_type': 'community_signal'} for item in _tag(
                    sources, query=query, source='hn', authority='signal', provider='hn_algolia')],
                'query': query, 'source': 'hn', 'provider': 'hn_algolia',
                'note': '社区讨论是线索，不是发布事实；请以官方或项目来源核实。'}
    except (requests.RequestException, ValueError, TypeError):
        return {'sources': [], 'query': query, 'source': 'hn', 'provider': 'hn_algolia',
                'error': 'HN 社区检索暂不可用。', 'failure_kind': 'provider_unavailable'}


def tool_search_updates(ws, query, source='web', days=7, max_results=4):
    """Search one current-information source class for the active dialogue turn."""
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        return {'sources': [], 'error': 'query 必须是 1–1000 个字符的文本'}
    if source not in SOURCES:
        return {'sources': [], 'error': 'source 必须是 official、github、papers、web 或 hn'}
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
        return {'sources': [], 'error': 'days 必须为 1–30 天'}
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 8:
        return {'sources': [], 'error': 'max_results 必须为 1–8'}
    query = query.strip()
    if source == 'official':
        return _official(ws, query, max_results)
    if source == 'github':
        return _github(ws, query, max_results)
    if source == 'papers':
        return _papers(ws, query, days, max_results)
    if source == 'hn':
        return _hn(ws, query, max_results)
    return _web(ws, query, max_results)


register(
    name='search_updates',
    description='只为当前对话轮次检索近期动态；source 可为 official、github、papers、web、hn，不含 X/Twitter。',
    fn=tool_search_updates,
    parameters={'query': {'type': 'string', 'required': True}, 'source': {'type': 'string', 'required': True},
                'days': {'type': 'integer'}, 'max_results': {'type': 'integer'}},
)
