"""Deterministic bounded excerpt selection; lexical matches are not relevance proof."""
from __future__ import annotations

import re


def terms(text: str) -> set[str]:
    return {t.casefold() for t in re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}', text)
            if t.casefold() not in {'the', 'and', 'for', 'with', 'what', 'this', '有哪些', '有什么'}}


def select_excerpt(text: str, query: str, budget: int) -> dict:
    """Rank paragraph-sized windows, then retain document order and offsets."""
    budget = max(500, min(int(budget), 24000))
    wanted = terms(query)
    windows = []
    for start in range(0, len(text), 1000):
        content = text[start:start+1400]
        score = len(wanted & terms(content))
        windows.append((score, start, content))
    selected = []
    remaining = budget
    for score, start, content in sorted(windows, key=lambda w: (-w[0], w[1])):
        if remaining <= 0:
            break
        if selected and score == 0:
            break
        if any(abs(start - offset) < 1400 for _, offset, _ in selected):
            continue
        content = content[:remaining]
        selected.append((score, start, content))
        remaining -= len(content)
    selected.sort(key=lambda w: w[1])
    return {'text': '\n\n'.join(w[2] for w in selected),
            'evidence_spans': [{'start': w[1], 'end': w[1]+len(w[2]), 'term_matches': w[0]} for w in selected],
            'coverage': 'query_selected_body_excerpts' if wanted else 'raw_body_excerpt',
            'query_matched': any(w[0] > 0 for w in selected),
            'truncated': sum(len(w[2]) for w in selected) < len(text)}
