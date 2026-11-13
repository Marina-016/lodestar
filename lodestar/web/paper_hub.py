"""Local paper index: shared reading state, explicit self assessment and source links."""
from __future__ import annotations
import json
import re
from lodestar.memory import papers

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_study_state (
 paper_id INTEGER PRIMARY KEY REFERENCES library_papers(id),
 status TEXT NOT NULL DEFAULT 'inbox', mastery TEXT NOT NULL DEFAULT 'unknown',
 note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_origins (
 paper_id INTEGER NOT NULL REFERENCES library_papers(id), kind TEXT NOT NULL, ref TEXT NOT NULL,
 item_id TEXT, message_id INTEGER, project_id INTEGER, label TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(paper_id,kind,ref)
);
CREATE TABLE IF NOT EXISTS paper_study_changes (
 id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL, revision INTEGER NOT NULL,
 status TEXT NOT NULL, mastery TEXT NOT NULL, note TEXT NOT NULL, source TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_index_messages (message_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL);
"""
MASTERY = {'unknown','aware','understand','explain','apply'}
URL = re.compile(r'https://(?:arxiv\.org/(?:abs|pdf)/\d{4}\.\d{4,5}(?:v\d+)?(?:\.pdf)?|doi\.org/[^\s<>\[\]）)"\']+)', re.I)

def initialize(ws):
    ws.conn.executescript(SCHEMA)

def ensure_state(conn, paper_id):
    old = conn.execute('SELECT status FROM library_paper_projects WHERE paper_id=? ORDER BY updated_at DESC LIMIT 1',(paper_id,)).fetchone()
    conn.execute('INSERT OR IGNORE INTO paper_study_state(paper_id,status,updated_at) VALUES(?,?,?)',
                 (paper_id, old['status'] if old else 'inbox', papers._now()))

def remember(ws, source, *, kind, ref, item_id=None, message_id=None, project_id=None, label=''):
    conn=ws.conn
    key,url=papers.identity(source.get('url',''))
    row=conn.execute('SELECT * FROM library_papers WHERE paper_key=?',(key,)).fetchone()
    title=str(source.get('title') or key).strip()[:300]
    keywords=papers._values(source.get('keywords',[]),'关键词',30,80)
    if row:
        paper_id=row['id']
        # An index may fill a missing title; it never overwrites user-curated metadata.
        if row['title'] in (key,url) and title not in (key,url):
            conn.execute('UPDATE library_papers SET title=?,revision=revision+1,updated_at=? WHERE id=?',(title,papers._now(),paper_id))
            # Evidence can introduce a URL before the actual citation supplies metadata.
            # Fill only empty fields while replacing that URL-only placeholder.
            for field,values in (('authors',source.get('authors',[])),('keywords',keywords)):
                if row[field]=='[]' and values:
                    values=papers._values(values,field,40 if field=='authors' else 30,120 if field=='authors' else 80)
                    conn.execute('UPDATE library_papers SET '+field+'=? WHERE id=?',(json.dumps(values,ensure_ascii=False),paper_id))
            year=papers._year(source.get('date'))
            if row['published_year'] is None and year:
                conn.execute('UPDATE library_papers SET published_year=? WHERE id=?',(year,paper_id))
    else:
        paper_id=papers.save(conn,{'url':url,'title':title,'authors':source.get('authors',[]),
            'keywords':keywords,'published_year':papers._year(source.get('date'))})['paper']['id']
    ensure_state(conn,paper_id)
    conn.execute('INSERT OR IGNORE INTO paper_origins(paper_id,kind,ref,item_id,message_id,project_id,label) VALUES(?,?,?,?,?,?,?)',
        (paper_id,kind,str(ref),item_id,message_id,project_id,label[:300]))
    if project_id is not None:
        now=papers._now()
        conn.execute('INSERT OR IGNORE INTO library_paper_projects(paper_id,project_id,created_at,updated_at) VALUES(?,?,?,?)',(paper_id,project_id,now,now))
    return paper_id

def sync(ws):
    """Index actual saved evidence/citations, without downloads, model calls or mastery inference."""
    from hashlib import sha256
    conn=ws.conn
    # Source removal/revocation must also remove its visible learning association.
    conn.execute("DELETE FROM paper_origins WHERE kind='learning' AND ref NOT IN (SELECT cast(id AS TEXT) FROM learning_events WHERE user_id='web:local' AND revoked_at IS NULL)")
    for r in conn.execute("SELECT * FROM learning_events WHERE user_id='web:local' AND revoked_at IS NULL AND paper_url<>''").fetchall():
        try:
            remember(ws,{'url':r['paper_url']},kind='learning',ref=r['id'],label=r['technology'])
        except ValueError:
            continue
    for r in conn.execute('SELECT * FROM paper_recommendations').fetchall():
        try:
            remember(ws,json.loads(r['source']),kind='recommendation',ref=r['id'],project_id=r['project_id'],label='项目推荐')
        except (ValueError,TypeError):
            continue
    conn.execute("DELETE FROM paper_origins WHERE kind='message' AND NOT EXISTS (SELECT 1 FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE cast(m.id AS TEXT)=paper_origins.ref AND i.owner='local' AND coalesce(m.kind,'') NOT IN ('superseded','error','cancelled'))")
    rows=conn.execute("SELECT m.*,i.id AS item_id,i.project_id FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE i.owner='local' AND m.role IN ('user','assistant') AND coalesce(m.kind,'') NOT IN ('superseded','error','cancelled')").fetchall()
    for r in rows:
        fingerprint=sha256((r['content']+str(r['metadata'])).encode()).hexdigest()
        indexed=conn.execute('SELECT fingerprint FROM paper_index_messages WHERE message_id=?',(r['id'],)).fetchone()
        if indexed and indexed[0]==fingerprint:
            continue
        conn.execute("DELETE FROM paper_origins WHERE kind='message' AND ref=?",(str(r['id']),))
        plain=re.sub(r'```.*?```','',r['content'],flags=re.S)
        try:
            meta=json.loads(r['metadata'] or '{}')
        except ValueError:
            meta={}
        sources=meta.get('source_details',[])
        sources=sources if isinstance(sources,list) else []
        known={}
        for s in sources:
            if not isinstance(s,dict) or not isinstance(s.get('url'),str): continue
            title=s.get('title','')
            name=title.split(':',1)[0].strip() if isinstance(title,str) else ''
            if s['url'] in plain or (len(name)>=4 and name.casefold() in plain.casefold()):
                try:
                    key,_=papers.identity(s['url'])
                    if key.startswith(('arxiv:','doi:')) or s.get('source_type')=='paper': known[key]=s
                except ValueError: pass
        for url in URL.findall(plain):
            url=url.rstrip('.,;。；')
            try:
                key,_=papers.identity(url)
                label=re.search(r'\[([^]\n]{2,300})\]\('+re.escape(url)+r'\)',plain)
                known.setdefault(key,{'url':url,**({'title':label[1]} if label else {})})
            except ValueError: pass
        for s in known.values():
            try:
                remember(ws,s,kind='message',ref=r['id'],item_id=r['item_id'],message_id=r['id'],project_id=r['project_id'],label='对话提及')
            except (ValueError,TypeError): pass
        conn.execute('INSERT OR REPLACE INTO paper_index_messages VALUES(?,?)',(r['id'],fingerprint))
    for r in conn.execute('SELECT id FROM library_papers').fetchall(): ensure_state(conn,r['id'])

def evidence(conn,paper_id):
    return [dict(r) for r in conn.execute("SELECT e.* FROM paper_origins o JOIN learning_events e ON cast(e.id AS TEXT)=o.ref WHERE o.paper_id=? AND o.kind='learning' AND e.user_id='web:local' AND e.revoked_at IS NULL ORDER BY e.id DESC",(paper_id,))]

def enrich(conn,paper):
    paper=dict(paper)
    state=conn.execute('SELECT * FROM paper_study_state WHERE paper_id=?',(paper['id'],)).fetchone()
    paper['study']=dict(state) if state else None
    rows=evidence(conn,paper['id'])
    paper['recorded_keywords']=list(paper.get('keywords',[]))
    paper['keywords']=list(dict.fromkeys([*paper.get('keywords',[]),*[v for r in rows for v in (r['technology'],r['method']) if v]]))[:30]
    paper['evidence_count']=len(rows)
    levels={'demonstrated_explanation':1,'demonstrated_implementation':2,'demonstrated_application':3}
    paper['evidence_mastery']=['unknown','can_explain','can_implement','can_apply'][max([levels.get(r['event'],0) for r in rows],default=0)]
    return paper

def detail(ws,paper_id,project_id=None):
    value=papers.detail(ws.conn,paper_id,project_id)
    value['paper']=enrich(ws.conn,value['paper'])
    value['evidence']=evidence(ws.conn,paper_id)[:40]
    value['origins']=[dict(r) for r in ws.conn.execute('SELECT * FROM paper_origins WHERE paper_id=? ORDER BY message_id DESC LIMIT 40',(paper_id,))]
    return value

def catalog(ws,*,query='',status='',mastery='',project_id=None,page=1):
    if not isinstance(query,str) or len(query)>200 or status not in {'',*papers.STATUSES} or mastery not in {'',*MASTERY} or type(page) is not int or not 1<=page<=100000:
        raise ValueError('论文筛选条件无效')
    # Local data only; enrich keywords from real, non-revoked observations.
    rows=[]
    for r in ws.conn.execute('SELECT * FROM library_papers ORDER BY updated_at DESC,id DESC'):
        p=enrich(ws.conn,papers._row(r))
        if project_id and not ws.conn.execute('SELECT 1 FROM library_paper_projects WHERE paper_id=? AND project_id=?',(p['id'],int(project_id))).fetchone(): continue
        if status and p['study']['status']!=status: continue
        if mastery and p['study']['mastery']!=mastery: continue
        if query.strip().casefold() not in ' '.join([p['title'],*p['authors'],*p['keywords']]).casefold(): continue
        rows.append(p)
    return {'papers':rows[(page-1)*40:page*40],'total':len(rows),'page':page}

def update(ws,data,*,source='user'):
    paper_id=data.get('id')
    if type(paper_id) is not int or not ws.conn.execute('SELECT 1 FROM library_papers WHERE id=?',(paper_id,)).fetchone(): raise ValueError('找不到论文')
    ensure_state(ws.conn,paper_id)
    old=dict(ws.conn.execute('SELECT * FROM paper_study_state WHERE paper_id=?',(paper_id,)).fetchone())
    if data.get('revision')!=old['revision']: raise ValueError('论文学习状态已更新，请刷新后保存')
    status=data.get('status',old['status']); mastery=data.get('mastery',old['mastery']); note=data.get('note',old['note'])
    if status not in papers.STATUSES or mastery not in MASTERY or not isinstance(note,str) or len(note)>3000: raise ValueError('阅读状态、掌握自评或笔记无效')
    now=papers._now();revision=old['revision']+1
    ws.conn.execute('UPDATE paper_study_state SET status=?,mastery=?,note=?,revision=?,updated_at=? WHERE paper_id=?',(status,mastery,note.strip(),revision,now,paper_id))
    ws.conn.execute('INSERT INTO paper_study_changes(paper_id,revision,status,mastery,note,source,created_at) VALUES(?,?,?,?,?,?,?)',(paper_id,revision,status,mastery,note.strip(),source,now))
    return detail(ws,paper_id)
