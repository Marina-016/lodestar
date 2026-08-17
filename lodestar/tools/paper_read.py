"""read_paper：论文读取。

V0：arXiv 摘要级。V1-R2：扩展 PDF 全文读取——
- arXiv 全文：下载 PDF → `pdfs_cache/<id>.pdf` 缓存（gitignore，重复任务不重复下载）→ PyMuPDF 抽取
  → 按「节」递进（Abstract / Introduction / Method / Experiments…），内容受 read_char_budget 硬截断。
- 通用 PDF 链接：按 URL 下载解析。
- 优雅降级：PyMuPDF 缺失 / 下载失败 / 无文本层（扫描件）→ 回退 abstract 级并带 note，绝不阻断管道。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from lodestar.config import Config
from lodestar.tools.registry import register

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.]+(?:v\d+)?)")
_SECTION_RE = re.compile(
    r"^\s*(?:\d+[\.\)]\s*)?(Abstract|Introduction|Background|Related Work|Method|Methods|Methodology|"
    r"Approach|Experiments|Experimental (?:Setup|Results|Evaluation)|Results|Discussion|Conclusion|"
    r"References)\s*$", re.I)


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


def _truncate(body: str, char_budget: int) -> tuple[str, bool]:
    if len(body) > char_budget:
        return body[:char_budget] + f"\n…[已截断，预算={char_budget}字符]", True
    return body, False


# ----------------------------------------------------------------------
# PDF 下载与解析（PyMuPDF）
# ----------------------------------------------------------------------
def _pdf_cache_path(cfg, key: str) -> Path:
    d = Path(cfg.pdf_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^\w.\-]", "_", key) + ".pdf")


def _download_pdf(url: str, dest: Path, timeout: float) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True  # 命中缓存
    for attempt in (1, 2):  # arXiv PDF 偶发瞬时抖动，重试一次
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Lodestar/0.1"}, stream=True)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(65536):
                    f.write(chunk)
            if dest.stat().st_size > 1000:
                return True
        except Exception:  # noqa: BLE001
            try:
                dest.unlink(missing_ok=True)  # 清理半成品，避免污染缓存
            except Exception:  # noqa: BLE001
                pass
        if attempt == 1:
            import time
            time.sleep(2.0)
    return False


def _parse_sections(text: str) -> dict:
    """按节抽取：Abstract/Introduction/Method/Experiments… 轻量启发式。"""
    sections: dict = {}
    cur = None
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if _SECTION_RE.match(stripped) and len(stripped) < 60:
            cur = stripped.lower().replace(" ", "_")
            sections[cur] = []
            continue
        if cur is not None and stripped:
            sections[cur].append(stripped)
    out = {}
    for k, v in sections.items():
        body = re.sub(r"\s+", " ", " ".join(v)).strip()
        if body:
            out[k] = body
    return out


def _extract_pdf_text(path: Path) -> tuple[str | None, dict]:
    """返回 (全文文本 or None, sections)。PyMuPDF 缺失/扫描件 → (None, {})。"""
    try:
        import pymupdf as fitz  # PyMuPDF 1.24+ 推荐入口（fitz 别名已弃用）
    except ImportError:
        try:
            import fitz  # noqa: F401 旧版兼容
        except ImportError:
            return None, {}
    try:
        doc = fitz.open(path)
        pages = [p.get_text("text") for p in doc]
        doc.close()
    except Exception:  # noqa: BLE001
        return None, {}
    text = "\n".join(pages)
    if len(text.strip()) < 200:
        return None, {}
    return text, _parse_sections(text)


def _read_arxiv_full(cfg, arxiv_id, meta, base: str, url: str, char_budget: int) -> dict:
    dest = _pdf_cache_path(cfg, f"arxiv_{arxiv_id}")
    if not _download_pdf(f"https://arxiv.org/pdf/{arxiv_id}", dest, cfg.tool_timeout_s):
        return _result(base, meta["title"], url, "abstract", False, char_budget,
                       "PDF 下载失败，已回退摘要级", [])
    text, sections = _extract_pdf_text(dest)
    if text is None:
        return _result(base, meta["title"], url, "abstract", False, char_budget,
                       "PDF 无文本层（疑似扫描件），已回退摘要级", [])
    parts = [base]
    for key in ("introduction", "method", "methods", "methodology", "experiments",
                "experimental_setup", "results", "conclusion"):
        if key in sections:
            parts.append(f"\n## {key}\n{sections[key][:4000]}")
    body, truncated = _truncate("\n".join(parts), char_budget)
    return _result(body, meta["title"], url, "full", True, char_budget,
                   f"PDF 全文抽取成功（{len(text)} 字符 → 按节截断）", list(sections.keys()), truncated)


def _read_generic_pdf(cfg, url: str, char_budget: int) -> dict:
    dest = _pdf_cache_path(cfg, url.split("/")[-1])
    if not _download_pdf(url, dest, cfg.tool_timeout_s):
        return {"error": f"PDF 下载失败: {url}", "url": url}
    text, sections = _extract_pdf_text(dest)
    if text is None:
        return {"error": "PDF 无文本层（疑似扫描件）", "url": url}
    parts = ["\n".join(text.splitlines()[:40])]  # 开头多为标题+摘要
    for k, v in sections.items():
        parts.append(f"\n## {k}\n{v[:3000]}")
    body, truncated = _truncate("\n".join(parts), char_budget)
    return _result(body, url.split("/")[-1], url, "full", True, char_budget,
                   f"通用 PDF 抽取成功（{len(text)} 字符）", list(sections.keys()), truncated)


def _result(body: str, title: str, url: str, read_depth: str, full_ok: bool,
            char_budget: int, note: str, section_keys: list, truncated: bool | None = None) -> dict:
    if truncated is None:
        body, truncated = _truncate(body, char_budget)
    return {"title": title, "url": url, "text": body, "truncated": truncated,
            "read_depth": read_depth, "full_text_ok": full_ok, "note": note,
            "sections": section_keys}


# ----------------------------------------------------------------------
# 主工具
# ----------------------------------------------------------------------
def tool_read_paper(ws, url: str, char_budget: int | None = None, full_text: bool = False):
    cfg: Config = ws.config
    char_budget = char_budget or cfg.read_char_budget

    if cfg.search_mode == "mock":
        from lodestar.fixtures import mock_paper_text
        return mock_paper_text(url, full_text=full_text)

    arxiv_id = _extract_arxiv_id(url)
    if arxiv_id:
        try:
            meta = _fetch_abstract(arxiv_id, cfg.tool_timeout_s)
        except Exception as e:  # noqa: BLE001
            return {"error": f"论文读取失败: {e}", "url": url}
        base = (f"# {meta['title']}\nauthors: {', '.join(meta['authors'])}\n"
                f"published: {meta['date']}\n\n## Abstract\n{meta['abstract']}")
        if full_text and cfg.full_text_enabled:
            return _read_arxiv_full(cfg, arxiv_id, meta, base, url, char_budget)
        return _result(base, meta["title"], url, "abstract", False, char_budget,
                       "abstract 级读取", [])

    if re.search(r"\.pdf(\?|$)", (url or "").lower()):
        if full_text and cfg.full_text_enabled:
            return _read_generic_pdf(cfg, url, char_budget)
        return {"error": "PDF 链接需要 full_text 模式开启", "url": url}

    return {"error": "V0/V1-R2 的 read_paper 仅支持 arXiv 链接或 .pdf 链接", "url": url}


register(
    name="read_paper",
    description="读取论文：arXiv 摘要级（默认）；full_text=True 且开启全文时读 PDF 正文（按节递进、硬截断、优雅降级）",
    fn=tool_read_paper,
    parameters={"url": {"type": "string", "required": True}, "char_budget": {"type": "integer"},
                "full_text": {"type": "boolean"}},
)
