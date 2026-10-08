"""Attach the actual supplied paper provenance without endorsing generated claims."""
from urllib.parse import urlsplit


def attach_paper_sources(answer, sources):
    rows = []
    seen = set()
    for source in sources[:5]:
        url = source.get('url', '')
        if source.get('source_type') != 'paper' or not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            continue
        if url in seen:
            continue
        seen.add(url)
        safe_url = url.replace(' ', '%20').replace('(', '%28').replace(')', '%29').replace('<', '%3C').replace('>', '%3E')
        if any(ord(char) < 32 for char in safe_url):
            continue
        title = str(source.get('title') or '论文').replace('\n', ' ').replace('\r', ' ')
        title = title.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
        scope = '有界正文片段，非整篇全文' if source.get('read_depth') == 'full' else '摘要或有限片段'
        rows.append(f'- [{title}]({safe_url})（{scope}）')
    if not rows:
        return answer
    return answer.rstrip() + '\n\n本轮提供的论文证据（不代表逐句事实核验）：\n\n' + '\n'.join(rows)
