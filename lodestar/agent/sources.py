"""Attach the actual supplied paper provenance without endorsing generated claims."""
from urllib.parse import urlsplit


def paper_link(url, title='论文'):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return None
    safe_url = url.replace(' ', '%20').replace('(', '%28').replace(')', '%29').replace('<', '%3C').replace('>', '%3E')
    if any(ord(char) < 32 for char in safe_url):
        return None
    title = str(title or '论文').replace('\n', ' ').replace('\r', ' ')
    title = title.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
    return f'[{title}]({safe_url})'


def attach_paper_sources(answer, sources):
    rows = []
    seen = set()
    for source in sources[:5]:
        url = source.get('url', '')
        if source.get('source_type') != 'paper' or not isinstance(url, str):
            continue
        link = paper_link(url, source.get('title'))
        if not link or url in seen:
            continue
        seen.add(url)
        scope = '有界正文片段，非整篇全文' if source.get('read_depth') == 'full' else '摘要或有限片段'
        rows.append(f'- {link}（{scope}）')
    if not rows:
        return answer
    return answer.rstrip() + '\n\n本轮提供的论文证据（不代表逐句事实核验）：\n\n' + '\n'.join(rows)
