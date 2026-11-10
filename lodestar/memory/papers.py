"""Local structured paper library. Papers are materials, never automatic memories."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit, urlunsplit


SCHEMA = """
CREATE TABLE IF NOT EXISTS library_papers (
 id INTEGER PRIMARY KEY, paper_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 canonical_url TEXT NOT NULL, links TEXT NOT NULL DEFAULT '[]', authors TEXT NOT NULL DEFAULT '[]',
 published_year INTEGER, venue TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '[]',
 metadata_source TEXT NOT NULL DEFAULT 'manual', revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS library_paper_projects (
 paper_id INTEGER NOT NULL REFERENCES library_papers(id), project_id INTEGER NOT NULL REFERENCES projects(id),
 status TEXT NOT NULL DEFAULT 'inbox', priority TEXT NOT NULL DEFAULT 'normal',
 relevance TEXT NOT NULL DEFAULT 'unassessed', tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
 reading_depth TEXT NOT NULL DEFAULT 'metadata', source_recommendation_id INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(paper_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_library_paper_projects_project ON library_paper_projects(project_id,status,priority);
"""

STATUSES = {'inbox', 'later', 'reading', 'read', 'archived'}
PRIORITIES = {'high', 'normal', 'low'}
RELEVANCE = {'unassessed', 'high', 'medium', 'low', 'adopted', 'rejected'}
READING_DEPTHS = {'metadata', 'abstract', 'method', 'experiments', 'full'}
ARXIV = re.compile(r'^/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?$', re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _text(value, label, maximum, *, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{label}必须是文字')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError(f'{label}需要{("填写且" if required else "")}不超过 {maximum} 字')
    return value


def _values(value, label, maximum_items, maximum_length):
    if isinstance(value, str):
        value = re.split(r'[,，\n]', value)
    if not isinstance(value, list):
        raise ValueError(f'{label}必须是列表')
    values = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f'{label}必须是文字')
        item = item.strip()
        if item and item not in values:
            values.append(item)
    if len(values) > maximum_items or any(len(item) > maximum_length for item in values):
        raise ValueError(f'{label}数量或长度超出限制')
    return values


def identity(url):
    """Return one HTTPS canonical URL and a stable per-paper key without fetching it."""
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError('论文链接无效')
    parts = urlsplit(url.strip())
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('请提供不含账号信息的 HTTPS 论文链接')
    host = parts.hostname.lower()
    path = parts.path.rstrip('/')
    arxiv = ARXIV.fullmatch(path) if host in {'arxiv.org', 'www.arxiv.org'} else None
    if arxiv:
        paper_id = arxiv[1]
        return 'arxiv:' + paper_id, 'https://arxiv.org/abs/' + paper_id
    if host in {'doi.org', 'www.doi.org'}:
        doi = path.lstrip('/').lower()
        if not doi or len(doi) > 500:
            raise ValueError('DOI 链接无效')
        return 'doi:' + doi, 'https://doi.org/' + doi
    canonical = urlunsplit(('https', host, path or '/', parts.query, ''))
    return 'url:' + sha256(canonical.encode('utf-8')).hexdigest(), canonical


def _project(conn, project_id):
    if project_id is None or type(project_id) is not int:
        return None
    if not conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        raise ValueError('找不到所属项目')
    return project_id


def _association(data):
    status = data.get('status', 'inbox')
    priority = data.get('priority', 'normal')
    relevance = data.get('relevance', 'unassessed')
    depth = data.get('reading_depth', 'metadata')
    if status not in STATUSES or priority not in PRIORITIES or relevance not in RELEVANCE or depth not in READING_DEPTHS:
        raise ValueError('论文状态、优先级、相关性或阅读深度无效')
    return {'status': status, 'priority': priority, 'relevance': relevance,
            'reading_depth': depth, 'tags': _values(data.get('tags', []), '标签', 20, 80),
            'note': _text(data.get('note', ''), '笔记', 6000)}


def _row(row):
    value = dict(row)
    for field in ('links', 'authors', 'keywords', 'tags'):
        if field in value and isinstance(value[field], str):
            value[field] = json.loads(value[field])
    return value


def save(conn, data):
    """Create or edit one local paper and optionally its project-specific relation."""
    paper_id = data.get('id')
    title = _text(data.get('title', ''), '标题', 300, required=True)
    key, canonical_url = identity(data.get('url', ''))
    authors = _values(data.get('authors', []), '作者', 40, 120)
    keywords = _values(data.get('keywords', []), '关键词', 30, 80)
    links = _values(data.get('links', [canonical_url]), '链接', 8, 2000)
    if canonical_url not in links:
        links.insert(0, canonical_url)
    venue = _text(data.get('venue', ''), '会议或期刊', 240)
    year = data.get('published_year')
    if year in ('', None):
        year = None
    elif type(year) is not int or not 1000 <= year <= 3000:
        raise ValueError('年份无效')
    source = data.get('metadata_source', 'manual')
    if source not in {'manual', 'research_inbox'}:
        raise ValueError('资料来源无效')
    project_id = _project(conn, data.get('project_id'))
    now = _now()
    created = False
    if paper_id is None:
        existing = conn.execute('SELECT id FROM library_papers WHERE paper_key=?', (key,)).fetchone()
        if existing:
            paper_id = existing['id']
        else:
            paper_id = conn.execute('INSERT INTO library_papers(paper_key,title,canonical_url,links,authors,published_year,venue,keywords,metadata_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (key,title,canonical_url,json.dumps(links,ensure_ascii=False),json.dumps(authors,ensure_ascii=False),year,venue,json.dumps(keywords,ensure_ascii=False),source,now,now)).lastrowid
            created = True
    if not created:
        row = conn.execute('SELECT id,paper_key,revision FROM library_papers WHERE id=?', (paper_id,)).fetchone()
        if row is None:
            raise ValueError('找不到论文资料')
        revision = data.get('revision')
        if revision is not None and revision != row['revision']:
            raise ValueError('论文资料已更新，请刷新后再保存')
        if row['paper_key'] != key:
            conflict = conn.execute('SELECT id FROM library_papers WHERE paper_key=? AND id<>?', (key,paper_id)).fetchone()
            if conflict:
                raise ValueError('该链接已对应另一条论文资料')
        conn.execute('UPDATE library_papers SET paper_key=?,title=?,canonical_url=?,links=?,authors=?,published_year=?,venue=?,keywords=?,metadata_source=?,revision=revision+1,updated_at=? WHERE id=?',
            (key,title,canonical_url,json.dumps(links,ensure_ascii=False),json.dumps(authors,ensure_ascii=False),year,venue,json.dumps(keywords,ensure_ascii=False),source,now,paper_id))
    if project_id is not None:
        relation = _association(data)
        conn.execute('INSERT INTO library_paper_projects(paper_id,project_id,status,priority,relevance,tags,note,reading_depth,source_recommendation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(paper_id,project_id) DO UPDATE SET status=excluded.status,priority=excluded.priority,relevance=excluded.relevance,tags=excluded.tags,note=excluded.note,reading_depth=CASE WHEN library_paper_projects.reading_depth IN (\'method\',\'experiments\',\'full\') THEN library_paper_projects.reading_depth ELSE excluded.reading_depth END,updated_at=excluded.updated_at',
            (paper_id,project_id,relation['status'],relation['priority'],relation['relevance'],json.dumps(relation['tags'],ensure_ascii=False),relation['note'],relation['reading_depth'],data.get('source_recommendation_id'),now,now))
    return detail(conn, paper_id, project_id)


def attach_inbox(conn, project_id, recommendation_id):
    _project(conn, project_id)
    row = conn.execute('SELECT * FROM paper_recommendations WHERE id=? AND project_id=?', (recommendation_id,project_id)).fetchone()
    if row is None:
        raise ValueError('找不到这条研究收件箱论文')
    source = json.loads(row['source'])
    reading = conn.execute("SELECT evidence FROM paper_candidate_reads WHERE recommendation_id=? AND status='read' ORDER BY CASE WHEN json_extract(evidence,'$.read_depth')='full' THEN 0 ELSE 1 END,id DESC LIMIT 1", (recommendation_id,)).fetchone()
    evidence = json.loads(reading['evidence']) if reading else {}
    depth = evidence.get('read_depth') if evidence.get('read_depth') in READING_DEPTHS else 'metadata'
    return save(conn, {'title': source.get('title') or '未命名论文', 'url': source.get('url',''),
                       'authors': source.get('authors', []), 'published_year': _year(source.get('date')),
                       'venue': source.get('venue') or '', 'keywords': source.get('keywords', []),
                       'links': [value for value in (source.get('url'), source.get('record_url')) if isinstance(value,str)],
                       'metadata_source': 'research_inbox', 'project_id': project_id,
                       'status': 'read' if row['state']=='read' else 'inbox', 'priority': 'normal',
                       'relevance': 'unassessed', 'tags': source.get('tags', []), 'reading_depth': depth,
                       'source_recommendation_id': recommendation_id})


def _year(value):
    match = re.match(r'^(\d{4})', str(value or ''))
    return int(match[1]) if match and 1000 <= int(match[1]) <= 3000 else None


def catalog(conn, *, query='', project_id=None, status='', priority='', page=1):
    if not isinstance(query, str) or len(query) > 200 or status not in {'', *STATUSES} or priority not in {'', *PRIORITIES}:
        raise ValueError('论文筛选条件无效')
    if type(page) is not int or page < 1:
        raise ValueError('页码无效')
    if project_id not in (None, ''):
        project_id = _project(conn, int(project_id))
    relation = 'JOIN library_paper_projects rp ON rp.paper_id=p.id' if project_id is not None else 'LEFT JOIN library_paper_projects rp ON rp.paper_id=p.id'
    where, args = [], []
    if project_id is not None:
        where.append('rp.project_id=?'); args.append(project_id)
    if status:
        where.append('rp.status=?'); args.append(status)
    if priority:
        where.append('rp.priority=?'); args.append(priority)
    if query.strip():
        like = '%' + query.strip().lower() + '%'
        where.append("(lower(p.title) LIKE ? OR lower(p.authors) LIKE ? OR lower(p.keywords) LIKE ? OR lower(coalesce(rp.tags,'')) LIKE ?)")
        args.extend([like]*4)
    predicate = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute('SELECT COUNT(DISTINCT p.id) FROM library_papers p ' + relation + predicate, args).fetchone()[0]
    rows = conn.execute('SELECT p.*,rp.project_id,rp.status,rp.priority,rp.relevance,rp.tags,rp.note,rp.reading_depth,rp.source_recommendation_id FROM library_papers p ' + relation + predicate + ' GROUP BY p.id ORDER BY CASE rp.priority WHEN \'high\' THEN 0 WHEN \'normal\' THEN 1 ELSE 2 END,p.updated_at DESC,p.id DESC LIMIT 40 OFFSET ?', [*args,(page-1)*40]).fetchall()
    return {'papers': [_row(row) for row in rows], 'total': total, 'page': page}


def detail(conn, paper_id, project_id=None):
    if type(paper_id) is not int:
        raise ValueError('论文资料无效')
    paper = conn.execute('SELECT * FROM library_papers WHERE id=?', (paper_id,)).fetchone()
    if paper is None:
        raise ValueError('找不到论文资料')
    project_id = _project(conn, project_id) if project_id not in (None, '') else None
    rows = conn.execute('SELECT rp.*,p.name AS project_name FROM library_paper_projects rp JOIN projects p ON p.id=rp.project_id WHERE rp.paper_id=? ORDER BY p.name', (paper_id,)).fetchall()
    projects = [_row(row) for row in rows]
    return {'paper': _row(paper), 'projects': projects,
            'project': next((row for row in projects if row['project_id']==project_id), None)}
