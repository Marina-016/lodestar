"""search_web：网页搜索。V0 后端为 DuckDuckGo Lite（零 Key），接口化便于后续换 Brave/Serper。"""
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
    # DuckDuckGo Lite is occasionally flaky behind corporate proxies. Keep this
    # attempt short so the caller can use the live scholarly fallback below.
    resp = requests.post(LITE_URL, data={"q": query}, timeout=min(timeout, 8),
                         headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")
    sources = []
    seen = set()
    for i, a in enumerate(links):
        url = _normalize_url(a.get("href"))
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        snippet = re.sub(r"\s+", " ", snippets[i].get_text(" ", strip=True)) if i < len(snippets) else ""
        if not url or not title or url in seen:
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
        })
        if len(sources) >= max_results:
            break
    note = f"DuckDuckGo 返回 {len(sources)} 条（query={query!r}）"
    if not sources:
        note += "。注意：lite 页面结构可能变化，可检查网络或换后端。"
    return sources, note


def _dedup_key(title: str) -> str:
    return "title:" + re.sub(r"[^a-z0-9]+", "", title.lower())[:80]


def tool_search_web(ws, query: str, max_results: int | None = None):
    cfg: Config = ws.config
    max_results = max_results or cfg.web_results_per_query
    if cfg.search_mode == "mock":
        from lodestar.fixtures import topic_from_text, web_for
        sources = web_for(topic_from_text(query))
        return {"sources": [{**w, "query": query} for w in sources][:max_results],
                "note": f"mock 离线检索（query={query!r}）"}
    if cfg.web_search_backend != "duckduckgo":
        return {"sources": [], "error": f"不支持 web_search_backend={cfg.web_search_backend!r}（V0 仅 duckduckgo）"}
    try:
        sources, note = _search_duckduckgo(query, max_results, cfg.tool_timeout_s)
        return {"sources": sources, "note": note}
    except Exception as e:  # noqa: BLE001
        # Keep live research useful when the general web endpoint is unavailable.
        # arXiv is an independent, keyless source and preserves evidence provenance.
        try:
            from lodestar.tools.arxiv_search import _search_arxiv
            sources = _search_arxiv(query, max_results=max_results, timeout=min(cfg.tool_timeout_s, 12),
                                    field=cfg.arxiv_search_field)
            if sources:
                return {"sources": sources,
                        "note": "DuckDuckGo unavailable; live arXiv fallback returned " + str(len(sources)) + " sources.",
                        "fallback": "arxiv"}
        except Exception as fallback_error:  # noqa: BLE001
            return {"sources": [], "error": "web search failed: " + str(e) + "; arXiv fallback failed: " + str(fallback_error),
                    "note": "query=" + repr(query)}
        return {"sources": [], "error": "web search failed: " + str(e), "note": "query=" + repr(query)}


register(
    name="search_web",
    description="按 query 检索网页，返回 {sources:[{title,url,snippet,dedup_key}]}",
    fn=tool_search_web,
    parameters={"query": {"type": "string", "required": True}, "max_results": {"type": "integer"}},
)
