"""Small per-turn paper discovery policy; history is an exclusion index, not evidence."""
import json
import re


def paper_key(url):
    match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?', str(url), re.I)
    return 'arxiv:' + match[1] if match else str(url).rstrip('/')


def paper_link_request(message):
    text = message.strip()
    if len(text) > 180 or not re.search(r'链接|网址|\b(?:url|link)\b', text, re.I):
        return False
    if re.search(r'推荐|介绍|找一篇|搜索|有哪些|有什么|实现|代码|怎么做', text):
        return False
    return bool(re.search(r'论文|上一篇|上篇|这篇|那篇', text) or
                re.fullmatch(r'(?:请|给我|发我|要|一下|呢|的|它|这个|那个|\s)*(?:链接|网址|url|link)(?:呢|吗|吧|一下|[？?。！!\s])*', text, re.I))


def mentioned_papers(text, sources):
    """Identify named/cited papers, never infer a citation from an unused hit."""
    matched = {}
    plain = text.casefold().replace('**', '')
    for source in sources:
        url, title = source.get('url', ''), source.get('title', '')
        if not url or ('arxiv.org/' not in url and source.get('source_type') != 'paper'):
            continue
        name = title.split(':', 1)[0].strip().casefold()
        identifier = paper_key(url)
        if ((len(name) >= 4 and name in plain) or url in text or
                (identifier.startswith('arxiv:') and identifier[6:] in text)):
            matched[identifier] = source
    def position(source):
        name = source.get('title', '').split(':', 1)[0].strip().casefold()
        positions = [plain.find(value) for value in (name, source['url'].casefold()) if value and plain.find(value) >= 0]
        return min(positions) if positions else len(plain)
    return sorted(matched.values(), key=position)


def paper_focus(conn, conversation_id, before, after=0, query=''):
    """Reconstruct the paper in focus, ignoring mistaken URL-only follow-ups."""
    focus, known, request = None, {}, ''
    for row in conn.execute('SELECT id,role,kind,content,metadata FROM messages WHERE conversation_id=? '
                            'AND id>? AND id<? ORDER BY id', (conversation_id, after, before)):
        if row['role'] == 'user':
            request = row['content']
            continue
        if row['role'] != 'assistant' or row['kind'] in ('error', 'cancelled'):
            continue
        try:
            metadata = json.loads(row['metadata'] or '{}')
        except ValueError:
            continue
        for source in metadata.get('source_details', []) + metadata.get('candidates', []):
            if isinstance(source, dict) and source.get('url'):
                known[paper_key(source['url'])] = source
        if paper_link_request(request):
            continue
        explicit = metadata.get('paper_focus')
        matches = mentioned_papers(row['content'], list(known.values()))
        if isinstance(explicit, dict) and explicit.get('url'):
            focus = explicit
        elif len(matches) == 1 or (matches and re.search(r'介绍|讲解|这篇|那篇', request)):
            focus = {**matches[0], 'message_id': row['id']}
    named = mentioned_papers(query, list(known.values()))
    selected = named[0] if len(named) == 1 else focus
    return ({key: selected[key] for key in ('url', 'title', 'date', 'source_type', 'read_depth', 'record_url', 'message_id')
             if key in selected} if selected else None)


def discovery_request(message, reference_text='', known=()):
    if reference_text or re.search(r'https?://|\d{4}\.\d{4,5}|《|这篇|那篇|之前|刚才|继续|复习|不要搜|不用搜|不联网', message):
        return False
    if not re.search(r'论文|\bpapers?\b', message, re.I):
        return False
    if not re.search(r'推荐|介绍|找|挑|选|换|\b(?:recommend|suggest|find|introduce)\b', message, re.I):
        return False
    for source in known:
        title = source.get('title', '')
        name = title.split(':', 1)[0].strip()
        if len(name) >= 4 and name.casefold() in message.casefold():
            return False
    return True


def introduced_papers(conn, conversation_id, before, after=0):
    """Use only papers actually named/cited in completed replies, not search hits."""
    known = {}
    for row in conn.execute("SELECT content,metadata FROM messages WHERE conversation_id=? AND role='assistant' "
                            "AND kind NOT IN ('error','cancelled') AND id>? AND id<? ORDER BY id",
                            (conversation_id, after, before)):
        try:
            metadata = json.loads(row['metadata'] or '{}')
        except ValueError:
            continue
        for source in metadata.get('source_details', []):
            url, title = source.get('url', ''), source.get('title', '')
            if 'arxiv.org/' not in url and source.get('source_type') != 'paper':
                continue
            name = title.split(':', 1)[0].strip()
            body = row['content'].casefold()
            if (url and url in row['content']) or (len(name) >= 4 and name.casefold() in body):
                known[paper_key(url)] = {'url': url, 'title': title}
    return list(known.values())
