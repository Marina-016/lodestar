"""search_papers：arXiv API 论文检索（免费、无 Key）。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from lodestar.config import Config
from lodestar.tools.registry import register

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"


def _clean_arxiv_text(text: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _search_arxiv(query: str, max_results: int = 6, timeout: int = 30, field: str = "abs") -> list[dict]:
    # field: all=全文(噪声多) | abs=摘要(默认，精确) | ti=标题(最严)
    params = {
        "search_query": f"{field}:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
    }
    resp = requests.get(ARXIV_API, params=params, timeout=timeout,
                        headers={"User-Agent": "Lodestar/0.1 (personal research workspace)"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
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
            "snippet": _clean_arxiv_text(entry.findtext(f"{ATOM}summary"), 600),
            "dedup_key": f"arxiv:{arxiv_id}",
        })
    return sources


def tool_search_papers(ws, query: str, max_results: int | None = None):
    cfg: Config = ws.config
    max_results = max_results or cfg.arxiv_results_per_query
    if cfg.search_mode == "mock":
        from lodestar.fixtures import papers_for, topic_from_text
        sources = papers_for(topic_from_text(query))
        return {"sources": [{**p, "query": query} for p in sources][:max_results],
                "note": f"mock 离线检索（query={query!r}）"}
    try:
        sources = _search_arxiv(query, max_results=max_results, timeout=cfg.tool_timeout_s,
                                field=cfg.arxiv_search_field)
        return {"sources": sources, "note": f"arXiv({cfg.arxiv_search_field}) 返回 {len(sources)} 条（query={query!r}）"}
    except Exception as e:  # noqa: BLE001
        return {"sources": [], "error": f"arXiv 检索失败: {e}", "note": f"query={query!r}"}


register(
    name="search_papers",
    description="按 query 检索 arXiv 论文，返回 {sources:[{title,url,authors,date,snippet,dedup_key}]}",
    fn=tool_search_papers,
    parameters={"query": {"type": "string", "required": True}, "max_results": {"type": "integer"}},
)
