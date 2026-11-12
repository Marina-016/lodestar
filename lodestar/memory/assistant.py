"""Owned facts with explicit scopes; transcripts remain in their tasks."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_memories (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, scope TEXT NOT NULL,
 project_id INTEGER, item_id TEXT, content TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
 source TEXT NOT NULL DEFAULT 'user', source_message_id INTEGER,
 tier TEXT NOT NULL DEFAULT 'durable', priority TEXT NOT NULL DEFAULT 'P1',
 kind TEXT NOT NULL DEFAULT 'note', stale_after TEXT, archive_after TEXT,
 expires_at TEXT, last_accessed_at TEXT, reference_date TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS assistant_memory_owner ON assistant_memories(user_id,scope,status);
CREATE TABLE IF NOT EXISTS assistant_memory_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
 revision INTEGER NOT NULL, action TEXT NOT NULL, content TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS assistant_memory_sources (
 memory_id TEXT NOT NULL REFERENCES assistant_memories(id),
 message_id INTEGER NOT NULL REFERENCES messages(id),
 relation TEXT NOT NULL DEFAULT 'supporting',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(memory_id,message_id)
);
CREATE INDEX IF NOT EXISTS assistant_memory_sources_message ON assistant_memory_sources(message_id,memory_id);
"""

TIERS = {'session', 'durable'}
PRIORITIES = {'P0', 'P1', 'P2'}
KINDS = {'preference', 'fact', 'claim', 'reference', 'note'}
STATUSES = {'active', 'disabled', 'stale', 'archived'}
REFERENCE_DATE = re.compile(r'(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)')
REFERENCE_URL = re.compile(r'https?://[^\s<>\])}]+', re.I)
REFERENCE_SUMMARY = re.compile(r'^(?:核心思想|摘要|结论|说明|要点|分析|abstract|summary|conclusion)\s*[:：]', re.I)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _when(value):
    return value.isoformat()


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def reference_date_from(content):
    match = REFERENCE_DATE.search(content or '')
    return match.group(1) if match else None


def is_reference_content(content):
    """Only detect unmistakable academic references; ordinary links stay notes."""
    text = str(content or '')
    return bool(re.search(r'\barxiv\s*:', text, re.I) or re.search(r'arxiv\.org/(?:abs|pdf)/', text, re.I))


def reference_locator(content, reference_date=None):
    """Extract an inspectable locator without treating a saved summary as evidence."""
    text = str(content or '').strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    url = next((match.group(0).rstrip('.,;') for match in REFERENCE_URL.finditer(text)), None)
    arxiv = re.search(r'(?:arXiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)', text, re.I)
    locator = {}
    if lines:
        locator['title'] = lines[0][:240]
    if url:
        locator['url'] = url
    if arxiv:
        locator['arxiv_id'] = arxiv.group(1)
    if reference_date:
        locator['reference_date'] = reference_date
    return locator


def reference_cue(content, locator):
    """Retain title/version/URL, deliberately leave explanatory summaries out of recall."""
    useful = []
    for raw in str(content or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        if REFERENCE_SUMMARY.match(line):
            break
        if not useful or REFERENCE_URL.search(line) or REFERENCE_DATE.search(line) or re.search(r'\barxiv\s*:', line, re.I):
            useful.append(line)
    if not useful and locator.get('title'):
        useful.append(locator['title'])
    return '\n'.join(dict.fromkeys(useful))[:700]


def recommended_priority(content, kind='note'):
    """Conservative defaults: only durable identity facts become P0 automatically."""
    text = str(content or '').casefold()
    if kind == 'claim':
        return 'P2'
    if kind == 'reference':
        return 'P1'
    if re.search(r'(?:我的|你的|用户的)?名字(?:叫|是|为)|称呼(?:我|用户)|\bmy name is\b|\bcall me\b', text):
        return 'P0'
    return 'P1'


def migrate(conn):
    """Add recall metadata without invalidating existing user-owned records."""
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(assistant_memories)')}
    additions = {
        'tier': "TEXT NOT NULL DEFAULT 'durable'",
        'priority': "TEXT NOT NULL DEFAULT 'P1'",
        'kind': "TEXT NOT NULL DEFAULT 'note'",
        'stale_after': 'TEXT', 'archive_after': 'TEXT', 'expires_at': 'TEXT',
        'last_accessed_at': 'TEXT', 'reference_date': 'TEXT',
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE assistant_memories ADD COLUMN {name} {declaration}')
    conn.execute('CREATE INDEX IF NOT EXISTS assistant_memory_recall ON assistant_memories(user_id,status,tier,priority,updated_at)')
    for row in conn.execute("SELECT id,content,kind,priority,reference_date FROM assistant_memories WHERE user_id IS NOT NULL"):
        values = {}
        if not row['reference_date']:
            inferred = reference_date_from(row['content'])
            if inferred:
                values['reference_date'] = inferred
        kind = row['kind']
        if kind == 'note' and is_reference_content(row['content']):
            values['kind'] = kind = 'reference'
        if row['priority'] == 'P1' and kind == 'note':
            inferred = recommended_priority(row['content'], kind)
            if inferred == 'P0':
                values.update(priority='P0', kind='fact')
        if values:
            columns = ','.join(f'{column}=?' for column in values)
            conn.execute(f'UPDATE assistant_memories SET {columns} WHERE id=?', [*values.values(), row['id']])
    # Existing one-to-one provenance remains a primary anchor after migration.
    conn.execute("""INSERT OR IGNORE INTO assistant_memory_sources(memory_id,message_id,relation)
                    SELECT id,source_message_id,'primary' FROM assistant_memories
                    WHERE source_message_id IS NOT NULL""")


def _lifecycle(tier, priority, now):
    if tier == 'session':
        return None, None, _when(now + timedelta(days=1))
    if priority == 'P1':
        return _when(now + timedelta(days=90)), None, None
    if priority == 'P2':
        return _when(now + timedelta(days=30)), _when(now + timedelta(days=180)), None
    return None, None, None


def advance_lifecycle(conn, user_id):
    """On the next local access, promote expired session notes or archive P2 notes.

    No content is deleted. P0/P1 session notes become durable; P2 session notes
    become archived. Durable P1/P2 notes become stale first, then P2 archives.
    """
    now = _when(_now())
    promotions = conn.execute("""SELECT id,priority FROM assistant_memories
        WHERE user_id=? AND tier='session' AND status='active' AND expires_at IS NOT NULL
          AND julianday(expires_at)<=julianday(?) AND priority IN ('P0','P1')""", (user_id, now)).fetchall()
    for row in promotions:
        stale_after, archive_after, _ = _lifecycle('durable', row['priority'], _now())
        conn.execute("""UPDATE assistant_memories SET tier='durable',expires_at=NULL,stale_after=?,archive_after=?,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""", (stale_after, archive_after, row['id']))
    session_archives = [row['id'] for row in conn.execute("""SELECT id FROM assistant_memories
        WHERE user_id=? AND tier='session' AND status='active' AND expires_at IS NOT NULL
          AND julianday(expires_at)<=julianday(?) AND priority='P2'""", (user_id, now))]
    if session_archives:
        conn.execute('UPDATE assistant_memories SET status=\'archived\',updated_at=CURRENT_TIMESTAMP WHERE id IN (' + ','.join('?' * len(session_archives)) + ')', session_archives)
    archives = [row['id'] for row in conn.execute("""SELECT id FROM assistant_memories
        WHERE user_id=? AND tier='durable' AND status IN ('active','stale') AND archive_after IS NOT NULL
          AND julianday(archive_after)<=julianday(?)""", (user_id, now))]
    if archives:
        conn.execute('UPDATE assistant_memories SET status=\'archived\',updated_at=CURRENT_TIMESTAMP WHERE id IN (' + ','.join('?' * len(archives)) + ')', archives)
    stale = [row['id'] for row in conn.execute("""SELECT id FROM assistant_memories
        WHERE user_id=? AND tier='durable' AND status='active' AND stale_after IS NOT NULL
          AND julianday(stale_after)<=julianday(?)""", (user_id, now))]
    if stale:
        conn.execute('UPDATE assistant_memories SET status=\'stale\',updated_at=CURRENT_TIMESTAMP WHERE id IN (' + ','.join('?' * len(stale)) + ')', stale)


def visible(conn, user_id, *, project_id=None, item_id=None, active_only=False):
    advance_lifecycle(conn, user_id)
    predicate = "user_id=? AND (scope='personal' OR (scope='project' AND project_id=?) OR (scope='task' AND item_id=?))"
    if active_only:
        predicate += " AND status='active'"
    return [dict(row) for row in conn.execute(
        f'''SELECT * FROM assistant_memories WHERE {predicate}
            ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,updated_at DESC,id''',
        (user_id, project_id, item_id))]


def save(conn, user_id, *, content, scope='personal', project_id=None,
         item_id=None, memory_id=None, status='active', source='user', source_message_id=None,
         source_message_ids=None, tier='durable', priority=None, kind='note', reference_date=None):
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 2000:
        raise ValueError('记忆内容应为 1–2000 字')
    if scope not in {'personal', 'project', 'task'} or status not in STATUSES:
        raise ValueError('记忆范围或状态无效')
    if kind == 'note' and is_reference_content(content):
        kind = 'reference'
    inferred_priority = recommended_priority(content, kind)
    if kind == 'note' and inferred_priority == 'P0':
        kind = 'fact'
    # The UI's initial P1 value must not demote an unambiguous identity fact.
    priority = inferred_priority if priority is None or (priority == 'P1' and inferred_priority == 'P0') else priority
    if tier not in TIERS or priority not in PRIORITIES or kind not in KINDS:
        raise ValueError('记忆层级、优先级或类型无效')
    if reference_date in ('', None):
        reference_date = reference_date_from(content)
    if reference_date is not None:
        try:
            reference_date = date.fromisoformat(reference_date).isoformat()
        except (TypeError, ValueError):
            raise ValueError('资料日期无效') from None
    if (scope == 'project' and project_id is None) or (scope == 'task' and not item_id):
        raise ValueError('请先选择记忆归属')
    project_id = project_id if scope == 'project' else None
    item_id = item_id if scope == 'task' else None
    if source_message_ids is not None:
        if (not isinstance(source_message_ids, list) or len(source_message_ids) > 8
                or any(type(value) is not int or value < 1 for value in source_message_ids)):
            raise ValueError('记忆来源无效')
    source_ids = list(dict.fromkeys((source_message_ids or []) + ([source_message_id] if source_message_id is not None else [])))
    now = _now()
    stale_after, archive_after, expires_at = _lifecycle(tier, priority, now)
    if memory_id:
        old = conn.execute('SELECT * FROM assistant_memories WHERE id=? AND user_id=?',
                           (memory_id, user_id)).fetchone()
        if old is None:
            raise LookupError('找不到这条记忆')
        revision = old['revision'] + 1
        conn.execute('''UPDATE assistant_memories SET content=?,scope=?,project_id=?,item_id=?,status=?,tier=?,priority=?,kind=?,
                        stale_after=?,archive_after=?,expires_at=?,reference_date=?,revision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                     (content.strip(), scope, project_id, item_id, status, tier, priority, kind,
                      stale_after, archive_after, expires_at, reference_date, revision, memory_id))
        action = 'updated'
    else:
        memory_id, revision, action = uuid.uuid4().hex[:12], 1, 'created'
        conn.execute('''INSERT INTO assistant_memories(id,user_id,scope,project_id,item_id,content,status,source,source_message_id,
                        tier,priority,kind,stale_after,archive_after,expires_at,reference_date)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (memory_id, user_id, scope, project_id, item_id, content.strip(), status, source, source_message_id,
                      tier, priority, kind, stale_after, archive_after, expires_at, reference_date))
    if source_ids:
        existing = conn.execute('SELECT COUNT(*) FROM assistant_memory_sources WHERE memory_id=?', (memory_id,)).fetchone()[0]
        for index, message_id in enumerate(source_ids):
            relation = 'primary' if not existing and index == 0 else 'supporting'
            conn.execute('INSERT OR IGNORE INTO assistant_memory_sources(memory_id,message_id,relation) VALUES(?,?,?)',
                         (memory_id, message_id, relation))
    conn.execute('INSERT INTO assistant_memory_revisions(memory_id,revision,action,content) VALUES(?,?,?,?)',
                 (memory_id, revision, action, content.strip()))
    return dict(conn.execute('SELECT * FROM assistant_memories WHERE id=?', (memory_id,)).fetchone())


def forget(conn, user_id, memory_id):
    old = conn.execute('SELECT * FROM assistant_memories WHERE id=? AND user_id=?',
                       (memory_id, user_id)).fetchone()
    if old is None:
        raise LookupError('找不到这条记忆')
    conn.execute('DELETE FROM assistant_memories WHERE id=?', (memory_id,))
    conn.execute('UPDATE assistant_memory_revisions SET content=NULL WHERE memory_id=?', (memory_id,))
    conn.execute('INSERT INTO assistant_memory_revisions(memory_id,revision,action) VALUES(?,?,?)',
                 (memory_id, old['revision'] + 1, 'forgotten'))


def prompt_rows(rows, evidence=None, *, now=None):
    evidence = evidence or {}
    today = (now or _now()).date()
    packets = []
    for row in rows:
        row = dict(row)
        recorded = _date(row.get('created_at'))
        cited = _date(row.get('reference_date'))
        packet = {key: row[key] for key in ('id', 'scope', 'content', 'revision', 'tier', 'priority', 'kind')}
        packet['recorded_at'] = row.get('created_at')
        packet['record_age_days'] = (today-recorded).days if recorded else None
        if cited:
            packet['reference_date'] = cited.isoformat()
            packet['reference_age_days'] = (today-cited).days
            packet['freshness_note'] = (f'资料日期 {cited.isoformat()}，距今天 {packet["reference_age_days"]} 天；'
                                        '回答“当前/最新”相关问题前须核验来源。')
        if row.get('stale_after'):
            packet['review_after'] = row['stale_after']
        if row.get('status') != 'active':
            packet['freshness_status'] = row.get('status')
        if row.get('kind') == 'reference':
            locator = reference_locator(row['content'], packet.get('reference_date'))
            if locator:
                packet['content'] = reference_cue(row['content'], locator)
                packet['locator'] = locator
                packet['evidence_rule'] = ('这是检索线索而非自包含证据：架构或细节问题应回到主锚点原文或该链接核验，'
                                           '不可根据保存的摘要臆断。')
        if row['id'] in evidence:
            packet['source_context'] = evidence[row['id']]
        packets.append(json.dumps(packet, ensure_ascii=False))
    return packets


def reference_matches(row, query):
    """Require a named reference or meaningful topic overlap for automatic recall.

    Generic words such as 'paper' do not make every saved paper relevant. Chinese
    bigrams allow a topic inside a sentence to match without an extra model call.
    """
    def keys(text):
        ignored = {'paper', 'papers', 'arxiv', 'https', 'http', 'org', 'com', 'www',
                   '论文', '资料', '参考', '介绍', '推荐', '一篇', '一下', '什么', '我们', '之前',
                   '这个', '那个', '研究', '链接', '标题', '最新', '最近', '今天'}
        text = REFERENCE_URL.sub('', text)
        tokens = set(re.findall(r'[a-z][a-z0-9_-]{2,}', text.casefold()))
        for segment in re.findall(r'[\u4e00-\u9fff]+', text):
            tokens.update(segment[i:i+2] for i in range(len(segment)-1))
        return tokens - ignored
    content = row.get('content', '')
    locator = reference_locator(content) or {}
    url = locator.get('url', '')
    for match in REFERENCE_URL.finditer(query):
        if url and re.sub(r'v\d+$', '', match.group(0).rstrip('.,;')) == re.sub(r'v\d+$', '', url):
            return True
    arxiv_ids = re.findall(r'\d{4}\.\d{4,5}', content)
    if any(paper_id in query for paper_id in arxiv_ids):
        return True
    # Reference summaries and boilerplate must not expand automatic recall.
    cue = locator.get('title') or content
    return bool(keys(query) & keys(cue))


def select_rows(rows, query='', *, budget=8000, limit=20):
    """Bound automatic recall; explicit read_memory can retrieve other facts."""
    from lodestar.retrieval import terms
    rows = [row for row in rows if row.get('kind') != 'reference' or reference_matches(row, query)]
    wanted = terms(query)
    priority = {'P0': 2, 'P1': 1, 'P2': 0}
    core = [row for row in rows if row.get('priority') == 'P0']
    rest = [row for row in rows if row.get('priority') != 'P0']
    ranked = sorted(core, key=lambda row: (row.get('updated_at', ''), row['id']), reverse=True)
    ranked += sorted(rest, key=lambda row: (-len(wanted & terms(row['content'])),
                                             -priority.get(row.get('priority'), 1),
                                             row.get('updated_at', ''), row['id']))
    selected, used = [], 0
    for row in ranked:
        size = len(prompt_rows([row])[0])
        if used + size <= budget:
            selected.append(row)
            used += size
        if len(selected) == limit:
            break
    return selected


def mark_accessed(conn, rows):
    ids = [row['id'] for row in rows]
    if ids:
        conn.execute('UPDATE assistant_memories SET last_accessed_at=? WHERE id IN (' + ','.join('?' * len(ids)) + ')',
                     [_when(_now()), *ids])


def sources(conn, memory_id):
    return [dict(row) for row in conn.execute('''SELECT s.memory_id,s.message_id,s.relation,s.created_at,
            m.role,m.kind,m.created_at AS message_created_at,i.id AS item_id,i.name AS item_name,m.conversation_id
            FROM assistant_memory_sources s JOIN messages m ON m.id=s.message_id
            JOIN web_items i ON i.conversation_id=m.conversation_id AND i.owner='local'
            WHERE s.memory_id=? AND coalesce(m.kind,'')<>'superseded' ORDER BY CASE s.relation WHEN 'primary' THEN 0 ELSE 1 END,s.message_id''', (memory_id,))]


def reconstruct_context(conn, rows, query, *, char_budget=4000, max_memories=3, item_id=None):
    """Return compact, chronological source windows only for lexically matched cues."""
    from lodestar.retrieval import terms
    wanted = terms(query)
    if not wanted:
        return {}
    output, used = {}, 0
    for row in rows:
        matched = reference_matches(row, query) if row.get('kind') == 'reference' else bool(wanted & terms(row['content']))
        if len(output) >= max_memories or not matched:
            continue
        anchor = next((source for source in sources(conn, row['id']) if source['relation'] == 'primary'), None)
        if not anchor:
            continue
        # Scope grants a cue across tasks, never the raw transcript that led to it.
        if item_id is not None and anchor['item_id'] != item_id:
            continue
        before = list(reversed(list(conn.execute('''SELECT id,role,content,created_at FROM messages
            WHERE conversation_id=? AND id<=? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 2''',
            (anchor['conversation_id'], anchor['message_id'])))))
        after = list(conn.execute('''SELECT id,role,content,created_at FROM messages
            WHERE conversation_id=? AND id>? AND coalesce(kind,'')<>'superseded' ORDER BY id LIMIT 1''',
            (anchor['conversation_id'], anchor['message_id'])))
        messages = []
        for message in before + after:
            remaining = char_budget - used
            if remaining <= 0:
                break
            text = message['content'][:min(1200, remaining)]
            messages.append({'id': message['id'], 'role': message['role'], 'content': text,
                             'truncated': len(text) < len(message['content'])})
            used += len(text)
        if messages:
            output[row['id']] = {'anchor_message_id': anchor['message_id'],
                                 'item_id': anchor['item_id'], 'messages': messages}
    return output
