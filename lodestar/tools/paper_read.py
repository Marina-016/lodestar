"""read_paper：论文读取。V0 读 abstract 级（对应 PRD §2.2 决策），支持 arXiv 链接。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from lodestar.config import Config
from lodestar.tools.registry import register

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.]+(?:v\d+)?)")


def _extract_arxiv_id(url: str) -> str | None:
    m = _ARXIV_RE.search(url or "")
    return m.group(1) if m else None


def _fetch_abstract(arxiv_id: str, timeout: int) -> dict:
    resp = requests.get(ARXIV_API, params={"search_query": f"id:{arxiv_id}", "max_results": 1},
                        timeout=timeout, headers={"User-Agent": "Lodestar/0.1"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entry = root.find(f"{ATOM}entry")
    if entry is None:
        raise ValueError(f"arXiv 无此论文: {arxiv_id}")
    authors = [a.findtext(f"{ATOM}name") or "" for a in entry.findall(f"{ATOM}author")]
    return {
        "title": re.sub(r"\s+", " ", entry.findtext(f"{ATOM}title", "")).strip(),
        "authors": authors[:5],
        "date": (entry.findtext(f"{ATOM}published", "") or "")[:10],
        "abstract": re.sub(r"\s+", " ", entry.findtext(f"{ATOM}summary", "")).strip(),
    }


def tool_read_paper(ws, url: str, char_budget: int | None = None):
    cfg: Config = ws.config
    char_budget = char_budget or cfg.read_char_budget
    if cfg.search_mode == "mock":
        from lodestar.fixtures import mock_paper_text
        return mock_paper_text(url)
    arxiv_id = _extract_arxiv_id(url)
    if not arxiv_id:
        return {"error": "V0 的 read_paper 仅支持 arXiv 链接", "url": url}
    try:
        meta = _fetch_abstract(arxiv_id, cfg.tool_timeout_s)
        text = (
            f"# {meta['title']}\n"
            f"authors: {', '.join(meta['authors'])}\n"
            f"published: {meta['date']}\n\n"
            f"## Abstract\n{meta['abstract']}"
        )
        truncated = len(text) > char_budget
        if truncated:
            text = text[:char_budget] + "\n…[已截断，预算={char_budget}字符]"
        return {"title": meta["title"], "url": f"https://arxiv.org/abs/{arxiv_id}", "text": text,
                "truncated": truncated, "note": "V0 论文读取为 abstract 级；全文深度读取留待 V1"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"论文读取失败: {e}", "url": url}


register(
    name="read_paper",
    description="读取论文（V0：abstract 级；支持 arXiv 链接），返回 {title,url,text,truncated}",
    fn=tool_read_paper,
    parameters={"url": {"type": "string", "required": True}, "char_budget": {"type": "integer"}},
)
