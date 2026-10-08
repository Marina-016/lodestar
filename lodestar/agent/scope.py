"""Mark obvious broad extrapolations; this is not a semantic fact checker."""
from __future__ import annotations

import re

NOTE = '> 模型概括，待核对：以下表述不能仅凭本次少量论文视为领域结论。'
PATTERNS = (
    r'(?:是|成为|转向|已经|已|正在).{0,25}(?:主流|标配|唯一|极限)',
    r'(?:正在|已|已经).{0,25}取代',
)


def annotate_scope(text: str) -> str:
    """Preserve original claims and citations, visibly mark narrow known risks."""
    blocks = []
    for block in text.split('\n\n'):
        if not block.startswith(NOTE) and (not blocks or blocks[-1].strip() != NOTE):
            risky = False
            for pattern in PATTERNS:
                for match in re.finditer(pattern, block):
                    preceding = block[max(0, match.start() - 16):match.start()]
                    if not re.search(r'不能|不代表|不足以|不等于|不可', preceding):
                        risky = True
            if risky:
                block = NOTE + '\n\n' + block
        blocks.append(block)
    return '\n\n'.join(blocks)
