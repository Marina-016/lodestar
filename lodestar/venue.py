"""V1-R1：期刊 / venue 元数据回填（免费无 Key 数据源，provider 顺序回退）。

定位：确定性 code（PRD §26③），不是 Agent 工具——在检索去重后由编排器调用一次。

provider 链（config.venue_providers，默认顺序）：
  semanticscholar（按 arXiv id）→ openalex（按 arXiv id）→
  dblp（按 title）→ crossref（按 title）
- 前一个**成功**即停；明确未收录（404/空结果）→ 继续下一个 provider。
- 遇 429 把该 provider 标记为「本批次限流」不再硬闯；瞬时错误（断连/超时）退避重试一次。
- 基于 title 的 provider 带**近精确相似度守卫 ≥0.8**（实测 Dblp 的 title 搜索对短/常见短语标题
  会把相似但不是同一篇的论文排前面——0.5 阈值会错挂 venue，故收紧到近精确；达不到就诚实 None，
  绝不把别的论文的 venue 标到本篇头上，这是 Faithfulness 红线）。
- 礼貌约束：请求带 mailto UA、批间 sleep 1.2s（dblp 要求 ~1 req/s，超打会被断连/429）。
- 全失败该篇 venue=None，任务照常完成，绝不阻断。
"""
from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Optional

import requests

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
OPENALEX_URL = "https://api.openalex.org/works?filter=ids.arxiv:{arxiv_id}"
DBLP_URL = "https://dblp.org/search/publ/api"
CROSSREF_URL = "https://api.crossref.org/works"

MISSING_VENUE = None
PREPRINT_VENUE = "arXiv preprint"
TITLE_SIM_THRESHOLD = 0.8  # title 型源只信近精确匹配，防错挂别的论文 venue
DBLP_PREPRINT_TYPE = {"Informal and Other Publications", "Reference"}
CROSSREF_PREPRINT_TYPE = {"posted-content", "report", "standard"}


def _extract_arxiv_id(source: dict) -> Optional[str]:
    dk = source.get("dedup_key") or ""
    if dk.startswith("arxiv:"):
        return dk.split("arxiv:", 1)[1]
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.]+)", source.get("url") or "")
    return m.group(1) if m else None


def _title_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _pick_best(hits: list[tuple[dict, float, bool]]) -> Optional[tuple[dict, bool]]:
    """hits: [(info/raw, sim, is_published)]。

    只信近精确匹配（sim >= TITLE_SIM_THRESHOLD），否则返回 None（诚实缺失，不错挂）。
    同相似度档（0.1）内优先已发表条目（dblp 常并列 CoRR 与正式发表两条）。
    """
    best, best_score = None, -1.0
    for raw, sim, is_published in hits:
        if sim < TITLE_SIM_THRESHOLD:
            continue
        score = round(sim, 1) * 10 + (1 if is_published else 0)  # 同档内 published 胜出
        if score > best_score:
            best, best_score = (raw, is_published), score
    return best


# ----------------------------------------------------------------------
# provider fetch + parse（成功/未收录 → dict|None；429 → {"_rate_limited":True}；异常抛出）
# ----------------------------------------------------------------------
def _fetch_semanticscholar(arxiv_id: str, title: str, timeout: float, ua: str) -> Optional[dict]:
    resp = requests.get(S2_URL.format(arxiv_id=arxiv_id),
                        params={"fields": "title,venue,publicationVenue,externalIds,year,publicationTypes"},
                        timeout=timeout, headers={"User-Agent": ua})
    if resp.status_code == 429:
        return {"_rate_limited": True}
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    d = resp.json()
    pub_types = d.get("publicationTypes") or []
    venue = d.get("venue") or (d.get("publicationVenue") or {}).get("name")
    return _result(venue, bool(pub_types), {"ArXiv": arxiv_id}, "Semantic Scholar")


def _fetch_openalex(arxiv_id: str, title: str, timeout: float, ua: str) -> Optional[dict]:
    resp = requests.get(OPENALEX_URL.format(arxiv_id=arxiv_id), timeout=timeout, headers={"User-Agent": ua})
    if resp.status_code == 429:
        return {"_rate_limited": True}
    resp.raise_for_status()
    works = (resp.json().get("results")) or []
    if not works:
        return None
    w = works[0]
    src = ((w.get("primary_location") or {}).get("source") or {})
    is_published = bool(src.get("id")) and src.get("type") not in ("repository",)
    ext = {"OpenAlex": w.get("id"), "DOI": (w.get("ids") or {}).get("doi")}
    return _result(src.get("display_name"), is_published, ext | {"ArXiv": arxiv_id}, "OpenAlex")


def _sanitize_dblp_query(title: str) -> str:
    """Dblp q 是词级 AND 匹配且 `:` 有字段前缀语义，去掉标点只留词。"""
    return re.sub(r"[^A-Za-z0-9 ]+", " ", title or "").strip()


def _dblp_query_variants(title: str) -> list[str]:
    """多查询回退：完整标题 → 冒号前短名（短/带冒号标题用短查询命中率高）。"""
    full = _sanitize_dblp_query(title)
    variants = [full] if full else []
    head = (title or "").split(":", 1)[0].strip()
    if head and head not in variants:
        variants.append(head)  # 冒号前短名（含单词缩写，如 "MRMMIA"），短查询对带冒号标题命中率高
    return variants[:2]


def _fetch_dblp(arxiv_id: str, title: str, timeout: float, ua: str) -> Optional[dict]:
    for q in _dblp_query_variants(title):
        resp = requests.get(DBLP_URL, params={"q": q, "format": "json", "h": 10}, timeout=timeout,
                            headers={"User-Agent": ua})
        if resp.status_code == 429:
            return {"_rate_limited": True}
        resp.raise_for_status()
        hits = (resp.json().get("result", {}).get("hits", {}).get("hit")) or []
        cands = []
        for h in hits:
            info = h.get("info", {}) or {}
            is_pub = (info.get("type") or "") not in DBLP_PREPRINT_TYPE
            cands.append((info, _title_sim(title, info.get("title") or ""), is_pub))
        best = _pick_best(cands)
        if best is not None:
            info, is_published = best
            venue = info.get("venue")
            if (venue or "").upper() == "CORR":  # CoRR = arXiv 仓库，非正式发表 venue
                venue, is_published = PREPRINT_VENUE, False
            return _result(venue, is_published, {"Dblp": info.get("key"), "DOI": info.get("doi")}, "Dblp")
    return None


def _fetch_crossref(arxiv_id: str, title: str, timeout: float, ua: str) -> Optional[dict]:
    resp = requests.get(CROSSREF_URL, params={"query.bibliographic": title, "rows": "5"}, timeout=timeout,
                        headers={"User-Agent": ua})
    if resp.status_code == 429:
        return {"_rate_limited": True}
    resp.raise_for_status()
    items = (resp.json().get("message", {}).get("items")) or []
    cands = []
    for it in items:
        cand_title = (it.get("title") or [""])[0] or ""
        ctype = it.get("type") or ""
        is_pub = ctype not in CROSSREF_PREPRINT_TYPE
        cands.append((it, _title_sim(title, cand_title), is_pub))
    best = _pick_best(cands)
    if best is None:
        return None
    it, is_published = best
    venue = (it.get("container-title") or [""])[0] or (it.get("event") or {}).get("name")
    return _result(venue, is_published, {"DOI": it.get("DOI")}, "Crossref")


def _result(venue: Optional[str], is_published: bool, external_ids: dict, provider: str) -> dict:
    return {
        "venue": venue or (PREPRINT_VENUE if not is_published else MISSING_VENUE),
        "is_published": is_published,
        "external_ids": external_ids,
        "venue_note": provider,
    }


_FETCHERS = {
    "semanticscholar": _fetch_semanticscholar,
    "openalex": _fetch_openalex,
    "dblp": _fetch_dblp,
    "crossref": _fetch_crossref,
}

RETRY_BACKOFF_S = 2.0


def _fetch_with_retry(provider: str, arxiv_id: str, title: str, timeout: float, ua: str) -> Optional[dict]:
    """瞬时错误（断连/超时/5xx）退避重试一次；429 不重试直接上报限流。"""
    for attempt in (1, 2):
        try:
            return _FETCHERS[provider](arxiv_id, title, timeout, ua)
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if attempt == 1:
                time.sleep(RETRY_BACKOFF_S)
                continue
            raise
    raise RuntimeError("unreachable")


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def enrich_papers_venues(cfg, sources: list[dict]) -> tuple[list[dict], str]:
    papers = [s for s in sources if s.get("source_type") == "paper"]
    if not papers:
        return sources, "无论文来源，跳过 venue 回填"

    if cfg.search_mode == "mock":
        from lodestar.fixtures import all_papers
        by_key = {p["dedup_key"]: p for p in all_papers()}
        for s in papers:
            f = by_key.get(s.get("dedup_key"))
            s["venue"] = (f or {}).get("venue", PREPRINT_VENUE)
            s["is_published"] = bool((f or {}).get("is_published"))
            s["external_ids"] = (f or {}).get("external_ids", {"ArXiv": s.get("dedup_key", "")})
        return sources, f"mock venue 夹具回填 {len(papers)} 篇"

    if not cfg.enrich_venues:
        for s in papers:
            s.setdefault("venue", MISSING_VENUE)
            s.setdefault("is_published", False)
            s.setdefault("external_ids", {})
        return sources, "venue 回填已关闭（LODESTAR_ENRICH_VENUES=false）"

    providers = [p for p in cfg.venue_providers if p in _FETCHERS]
    if not providers:
        return sources, "venue 回填不可用：未配置已实现的 provider"

    rate_limited: set = set()
    resolved = 0
    degraded = 0
    notes = []
    ua = cfg.venue_user_agent
    batch = papers[: cfg.venue_enrich_limit]
    for i, s in enumerate(batch):
        arxiv_id = _extract_arxiv_id(s)
        title = s.get("title") or ""
        result = None
        for provider in providers:
            if provider in rate_limited:
                continue
            try:
                fetched = _fetch_with_retry(provider, arxiv_id, title, cfg.tool_timeout_s, ua)
            except Exception:  # noqa: BLE001 —— 单篇/provider 失败降级
                notes.append(f"{provider} 请求失败")
                continue
            if fetched is None:
                continue  # 该 provider 明确未收录，换下一个
            if fetched.get("_rate_limited"):
                rate_limited.add(provider)
                notes.append(f"{provider} 429 限流")
                continue
            result = fetched
            break  # 成功即停
        if result is not None:
            s.update(result)
            resolved += 1 if (result["venue"] and result["venue"] != PREPRINT_VENUE) else 0
        else:
            s.setdefault("venue", MISSING_VENUE)
            s.setdefault("is_published", False)
            s.setdefault("external_ids", {"ArXiv": arxiv_id} if arxiv_id else {})
            degraded += 1
        if i < len(batch) - 1:
            time.sleep(cfg.venue_request_interval_s)

    if rate_limited:
        notes.insert(0, "venue 回填受限：部分 provider 触发限流")
    summary = "；".join(dict.fromkeys(notes))
    return sources, (f"回填 {len(batch)} 篇：带 venue {resolved}，降级 {degraded}"
                     + (f"；{summary}" if summary else ""))
