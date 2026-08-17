"""Golden Cases：Eval 的数据真值（PRD §21）。JSON 文件位于 eval/cases/。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Expected:
    min_sources: int = 3
    must_cover_concepts: list[str] = field(default_factory=list)  # brief 中必须覆盖的概念（子串匹配，大小写不敏感）
    key_insights: list[str] = field(default_factory=list)         # 期望洞察 checklist（供人工复核，不自动打分）


@dataclass
class GoldenCase:
    id: str
    title: str
    goal: str
    expected: Expected
    seed_knowledge: list[dict] = field(default_factory=list)   # 运行前预置的 Knowledge State 基线
    tags: list[str] = field(default_factory=list)
    thresholds: dict = field(default_factory=lambda: {"coverage": 0.6, "task_success": 3, "faithfulness": 3})
    metadata: dict = field(default_factory=dict)


DEFAULT_THRESHOLDS = {"coverage": 0.6, "task_success": 3, "faithfulness": 3}


def load_case(path: Path) -> GoldenCase:
    raw = json.loads(path.read_text(encoding="utf-8"))
    exp = Expected(**(raw.get("expected") or {}))
    return GoldenCase(
        id=raw["id"], title=raw.get("title", raw["id"]), goal=raw["goal"], expected=exp,
        seed_knowledge=raw.get("seed_knowledge", []), tags=raw.get("tags", []),
        thresholds=raw.get("thresholds", DEFAULT_THRESHOLDS),
        metadata=raw.get("metadata", {}),
    )


def load_cases(cases_dir: Path) -> list[GoldenCase]:
    cases_dir = Path(cases_dir)
    paths = sorted(cases_dir.glob("*.json")) if cases_dir.exists() else []
    return [load_case(p) for p in paths]
