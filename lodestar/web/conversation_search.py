"""Human-facing transcript lookup; model history scoping remains separate."""
from datetime import datetime, timedelta, timezone
import re

def search(ws, *, query='', project_id=None, item_id='', role='', days='', page=1):
    if not isinstance(query,str) or len(query)>200 or role not in ('','user','assistant') or str(days) not in ('','7','30') or type(page) is not int or not 1<=page<=10000:
        raise ValueError('搜索条件无效')
    query=query.strip()
    where=["i.owner='local'","m.role IN ('user','assistant')","coalesce(m.kind,'')<>'superseded'"]
    args=[]
    if query:
        literal=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        where.append("m.content LIKE ? ESCAPE '\\'");args.append('%'+literal+'%')
    if project_id:where.append('i.project_id=?');args.append(int(project_id))
    if item_id:where.append('i.id=?');args.append(item_id)
    if role:where.append('m.role=?');args.append(role)
    if days:
        where.append('julianday(m.created_at)>=julianday(?)');args.append((datetime.now(timezone.utc)-timedelta(days=int(days))).isoformat())
    base=' FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE '+' AND '.join(where)
    # With no query, show the latest message in each matching conversation.
    if not query:
        base+=' AND m.id=(SELECT MAX(m2.id) FROM messages m2 WHERE m2.conversation_id=m.conversation_id AND m2.role IN (\'user\',\'assistant\') AND coalesce(m2.kind,\'\')<>\'superseded\''+(' AND m2.role=?' if role else '')+')'
        if role:args.append(role)
    total=ws.conn.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
    rows=ws.conn.execute('SELECT m.id,m.role,m.content,m.created_at,i.id AS item_id,i.name AS item_name,i.project_id'+base+' ORDER BY m.id DESC LIMIT 20 OFFSET ?',[*args,(page-1)*20]).fetchall()
    hits=[]
    for r in rows:
        value=dict(r);raw=value.pop('content')
        text=re.sub(r'\[([^]\n]+)\]\(https?://[^)]+\)',r'\1',raw)
        text=re.sub(r'(?m)^\s*#{1,6}\s*','',text).replace('**','').replace('```','').replace('`','')
        text=re.sub(r'\s+',' ',text).strip()
        if query and query.casefold() not in text.casefold(): text=re.sub(r'\s+',' ',raw).strip()
        at=text.casefold().find(query.casefold()) if query else 0
        start=max(0,at-65);end=start+240
        value['excerpt']=('…' if start else '')+text[start:end]+('…' if len(text)>end else '')
        hits.append(value)
    return {'matches':hits,'total':total,'page':page,'recent':not query}

def context(ws,item_id,message_id):
    item=ws.conn.execute("SELECT * FROM web_items WHERE id=? AND owner='local'",(item_id,)).fetchone()
    if not item:raise LookupError('找不到对话')
    target=ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND id=? AND role IN ('user','assistant') AND coalesce(kind,'')<>'superseded'",(item['conversation_id'],message_id)).fetchone()
    if not target:raise LookupError('找不到消息')
    sql="SELECT id,role,content,created_at FROM messages WHERE conversation_id=? AND role IN ('user','assistant') AND coalesce(kind,'')<>'superseded' AND id"
    before=ws.conn.execute(sql+'<? ORDER BY id DESC LIMIT 2',(item['conversation_id'],message_id)).fetchall()
    after=ws.conn.execute(sql+'>=? ORDER BY id LIMIT 3',(item['conversation_id'],message_id)).fetchall()
    messages=[]
    for r in [*reversed(before),*after]:
        v=dict(r);v['truncated']=len(v['content'])>12000;v['content']=v['content'][:12000];messages.append(v)
    return {'item_id':item_id,'item_name':item['name'],'target':message_id,'messages':messages}
