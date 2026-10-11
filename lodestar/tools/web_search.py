"""Web search with explicit provider failures and an opt-in official MCP backend."""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from lodestar.config import Config
from lodestar.tools.registry import register

LITE_URL = "https://lite.duckduckgo.com/lite/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def _search_duckduckgo(query: str, max_results: int, timeout: int) -> tuple[list[dict], str]:
    # Bound public-engine latency; challenge pages must not become empty success.
    resp = requests.post(LITE_URL, data={"q": query}, timeout=min(timeout, 8),
                         headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    if resp.status_code == 202 or soup.select_one('#challenge-form, .anomaly-modal'):
        raise ValueError('DuckDuckGo 要求人工验证，当前请求无法取得搜索结果')
    links = soup.select("a.result-link")
    if not links and 'no results found' not in soup.get_text(' ', strip=True).lower():
        raise ValueError('DuckDuckGo 返回的页面不包含可识别的搜索结果')
    snippets = soup.select("td.result-snippet")
    sources = []
    seen = set()
    for i, a in enumerate(links):
        url = _normalize_url(a.get("href"))
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        snippet = re.sub(r"\s+", " ", snippets[i].get_text(" ", strip=True)) if i < len(snippets) else ""
        if not url.startswith(("https://", "http://")) or not title or url in seen:
            continue
        seen.add(url)
        sources.append({
            "source_type": "web",
            "title": title[:300],
            "url": url,
            "authors": [],
            "date": "",
            "snippet": snippet[:600],
            "dedup_key": _dedup_key(title),
            "provider": "duckduckgo",
            "query": query,
        })
        if len(sources) >= max_results:
            break
    note = f"DuckDuckGo 返回 {len(sources)} 条（query={query!r}）"
    if not sources:
        note += "。没有找到匹配结果。"
    return sources, note


def _dedup_key(title: str) -> str:
    return "title:" + re.sub(r"[^a-z0-9]+", "", title.lower())[:80]


def tool_search_web(ws, query: str, max_results: int | None = None):
    cfg: Config = ws.config
    if not isinstance(query, str) or not query.strip():
        return {"sources": [], "error": "请输入有效的搜索词"}
    max_results = cfg.web_results_per_query if max_results is None else max_results
    if not isinstance(max_results, int) or not 1 <= max_results <= 20:
        return {"sources": [], "error": "max_results 应为 1–20"}
    if cfg.search_mode == "mock":
        from lodestar.fixtures import topic_from_text, web_for
        sources = web_for(topic_from_text(query))
        return {"sources": [{**w, "query": query} for w in sources][:max_results],
                "note": f"mock 离线检索（query={query!r}）"}
    if cfg.web_search_backend == "aliyun_mcp":
        from lodestar.providers.aliyun_search import SearchUnavailable, normalize, request
        from lodestar.providers.search_quota import QuotaUnavailable
        try:
            result = request(query, quota_path=cfg.workspace_dir / 'search' / 'free-quota.db',
                             timeout=cfg.tool_timeout_s, max_results=max_results)
            return normalize(result, query, max_results)
        except QuotaUnavailable as error:
            return {'sources': [], 'query': query, 'error': str(error),
                    'failure_kind': 'free_quota_unverified', 'provider': 'aliyun_websearch_mcp'}
        except SearchUnavailable as error:
            return {'sources': [], 'query': query, 'error': str(error),
                    'failure_kind': 'provider_unavailable', 'provider': 'aliyun_websearch_mcp'}
    if cfg.web_search_backend != "duckduckgo":
        return {"sources": [], "error": f"不支持 web_search_backend={cfg.web_search_backend!r}（支持 duckduckgo / aliyun_mcp）"}
    try:
        sources, note = _search_duckduckgo(query, max_results, cfg.tool_timeout_s)
        return {"sources": sources, "note": note, "query": query}
    except (requests.RequestException, ValueError):
        return {"sources": [], "query": query, "error": "网页搜索暂不可用：公共搜索入口超时或要求人工验证。",
                "failure_kind": "provider_unavailable",
                "recovery": "可改写查询重试，或用 read_webpage 直接读取已知的官方页面。论文检索是独立工具，不能代替最新新闻或市场份额证据。"}


register(
    name="search_web",
    description="按 query 检索网页，返回 {sources:[{title,url,snippet,dedup_key}]}",
    fn=tool_search_web,
    parameters={"query": {"type": "string", "required": True}, "max_results": {"type": "integer"}},
)
