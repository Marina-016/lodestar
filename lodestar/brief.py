"""Research Brief 渲染（PRD §7）。V0 为 markdown；不引入 HTML UI。"""
from __future__ import annotations

import json
from pathlib import Path

NOVELTY_LABEL = {"high": "高", "medium": "中", "low": "低"}


def _venue_label(s: dict) -> str:
    """V1-R1：venue / 发表状态。未解析到则标注 preprint/未知，不臆测。"""
    venue = s.get("venue")
    if not venue:
        return "preprint/未知"
    pub = "已发表" if s.get("is_published") else "预印本"
    return f"{venue}（{pub}）"


_DEPTH_LABEL = {"full": "全文", "abstract": "摘要", "web": "网页", "none": "未读"}


def _sources_table(sources: list[dict]) -> str:
    lines = ["| # | 类型 | 标题 | 日期 | 读取 | venue / 发表状态 |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(sources, 1):
        title = s.get("title", "").replace("|", "\\|")
        depth = _DEPTH_LABEL.get(s.get("read_depth"), "未读")
        lines.append(f"| {i} | {s.get('source_type', '')} | [{title}]({s.get('url', '')}) | "
                     f"{s.get('date') or 'n/a'} | {depth} | {_venue_label(s)} |")
    return "\n".join(lines)


def render_brief(cfg, task_id: str, goal: str, plan: dict, queries: list[dict], sources: list[dict],
                 read_sources: list[dict], synthesis: str, novelty: dict,
                 knowledge_ctx: list[dict], assess: dict, metrics: dict,
                 relevance: dict | None = None) -> str:
    zh = cfg.brief_language == "zh"
    overall = NOVELTY_LABEL.get(novelty.get("overall_novelty"), novelty.get("overall_novelty"))
    claims = novelty.get("claims", [])

    lines = [f"# Research Brief — {goal}", ""]

    # 核心结论
    lines += [
        "## 核心结论", "",
    ]
    if claims:
        for c in claims[:3]:
            lines.append(f"- **{NOVELTY_LABEL.get(c['novelty'], c['novelty'])}新颖** · {c['claim']} —— {c['reason']}")
    else:
        lines.append("- （novelty 判定未产出结论）")
    lines.append("")

    # Why it matters
    lines += [
        "## Why it matters", "",
        f"本次研究的总体新颖度判定为 **{overall}**。"
        + (" 该方向正在从『Prompt 级自优化』走向『Skill/Memory 级结构化自演进』，且与 Eval/Regression 直接耦合，"
           "值得纳入自己的 Agent 项目路线图。" if novelty.get("overall_novelty") != "low"
           else " 大部分内容与已有认知重叠，建议只关注其中 novelty=high 的条目。"),
        "",
    ]

    # 执行概览
    lines += [
        "## 执行概览", "",
        f"- 检索 Query：{metrics.get('queries', 0)} 个；实际搜索 {metrics.get('searches', 0)} 次；replan {metrics.get('replans', 0)} 次",
        f"- 候选来源：{metrics.get('candidates_collected', 0)} → 去重后 {metrics.get('unique_sources', 0)} → 深度阅读 {metrics.get('sources_read', 0)}",
        "",
    ]

    # 跨来源综合分析（synthesis 原样嵌入）
    lines += ["## 跨来源综合分析", "", synthesis, ""]

    # What is actually new
    lines += ["## What is actually new", ""]
    if claims:
        for c in claims:
            repack = f"（重包装：{c.get('is_repackaging_of')}）" if c.get("is_repackaging_of") else ""
            lines.append(f"- **{NOVELTY_LABEL.get(c['novelty'], c['novelty'])}** · `{c.get('concept') or ''}` {repack} — {c['claim']}。{c['reason']}")
    else:
        lines.append("- 无判定。")
    lines.append("")

    # Key Sources
    lines += ["## Key Papers / Sources", "", _sources_table(sources), ""]

    # Technical Path
    lines += [
        "## Technical Path", "",
        "（技术链路细节见上方「跨来源综合分析 · 主要技术路线」，此处给路径骨架）",
        "Experience / Trace 收集 → Failure / Feedback → Reflection → Candidate 改进"
        "（作用于 Prompt / Skill / Memory / Policy / Tool 之一）→ Evaluation → Promotion。",
        "",
    ]

    # Connection to My Knowledge
    lines += ["## Connection to My Knowledge", ""]
    if knowledge_ctx:
        known = "、".join(c["name"] for c in knowledge_ctx)
        lines.append(f"- 本次研究前你已掌握：{known}。")
    else:
        lines.append("- 本次研究前 Knowledge State 为空（Novelty 判定为相对空库）。")
    if claims:
        lines.append("- 与已有知识的关系：")
        for c in claims:
            if c.get("is_repackaging_of"):
                lines.append(f"  - `{c.get('concept')}` 是已有概念 `{c['is_repackaging_of']}` 的延伸/重包装；")
            else:
                lines.append(f"  - `{c.get('concept')}` 是本次新增概念（进入 Knowledge State）。")
    lines.append("")

    # Open Questions
    lines += ["## Open Questions", ""]
    gaps = assess.get("gaps") or []
    if gaps:
        for g in gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- assess 未标出明显缺口。")
    lines.append("- 当前边界：可选 PDF 全文阅读与 Experiment scaffold 已支持；GitHub/项目文件深度检索、"
                 "真实实验执行与自动过期复审尚未实现。")
    lines.append("")

    # Project Opportunities
    lines += ["## Project Opportunities", ""]
    high = [c for c in claims if c.get("novelty") == "high"]
    if high:
        for c in high:
            lines.append(f"- **可验证方向**：{c['claim']}。验证方式：先固定 baseline 与 eval 指标，再比较 candidate。")
    else:
        lines.append("- 本次未产生明显的高新颖可验证方向。")
    lines.append("")

    # Project Relevance（最新技术 × 用户进行中项目 自动结合）
    lines += ["## Project Relevance", ""]
    mappings = (relevance or {}).get("mappings") or []
    if mappings:
        for m in mappings:
            idx = m.get("opportunity_index")
            opp = f"方向 #{idx + 1}" if isinstance(idx, int) else "方向"
            lines.append(f"- **{opp}** → 适用于：`{('`、`'.join(m.get('applicable') or []))}`")
            if m.get("reason"):
                lines.append(f"  - {m['reason']}")
    else:
        lines.append("- 当前无进行中项目匹配（可在「项目」中登记你的 GitHub 项目并标记进行中）。")
    lines.append("")
    lines.append(f"---\n*Lodestar · task_id={task_id} · llm_mode={cfg.llm_mode} · 生成时间见 Trace*")
    return "\n".join(lines)


def write_workspace(workspace_dir: Path, task_id: str, brief_md: str, sources: list[dict],
                    trace_jsonl: Path) -> Path:
    out_dir = workspace_dir / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brief.md").write_text(brief_md, encoding="utf-8")
    (out_dir / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
