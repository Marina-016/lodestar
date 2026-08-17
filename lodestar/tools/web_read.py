"""read_webpage：网页正文抽取（HTML → 干净文本），带硬截断（PRD 缺口 A2）。"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from lodestar.config import Config
from lodestar.tools.registry import register

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _pick_container(soup: BeautifulSoup) -> BeautifulSoup | None:
    for sel in ("article", "main", "[role='main']"):
        node = soup.select_one(sel)
        if node and len(node.get_text(" ", strip=True)) > 200:
            return node
    body = soup.find("body")
    return body


def _clean_text(node) -> str:
    text = node.get_text("\n", strip=True) if node else ""
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def tool_read_webpage(ws, url: str, char_budget: int | None = None):
    cfg: Config = ws.config
    char_budget = char_budget or cfg.read_char_budget
    if cfg.search_mode == "mock":
        from lodestar.fixtures import mock_web_text
        return mock_web_text(url)
    try:
        resp = requests.get(url, timeout=cfg.tool_timeout_s, headers={"User-Agent": UA}, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)) if soup.title else url
        body = _pick_container(soup)
        text = _clean_text(body)
        truncated = len(text) > char_budget
        if truncated:
            text = text[:char_budget] + "\n…[已截断，预算={char_budget}字符]"
        return {"title": title[:300], "url": url, "text": text, "truncated": truncated,
                "note": f"读取成功（{len(text)} 字符，含截断）"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"网页读取失败: {e}", "url": url}


register(
    name="read_webpage",
    description="读取网页正文为干净文本（硬截断到 char_budget），返回 {title,url,text,truncated}",
    fn=tool_read_webpage,
    parameters={"url": {"type": "string", "required": True}, "char_budget": {"type": "integer"}},
)
