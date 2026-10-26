"""Owned transcript lookup; model reads exclude retired context and other projects."""
from urllib.parse import urlencode


def search(ws,query,*,project_id=None,item_id=None,page=1,model_item=None):
    if not isinstance(query,str) or len(query)>200 or not query.strip(): raise ValueError('请输入1–200字符的查询')
    if type(page) is not int or page<1 or page>10000: raise ValueError('页码无效')
    where=["i.owner='local'","m.content LIKE ? ESCAPE '\\'"]
    literal=query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    args=['%'+literal+'%']
    if model_item is not None:
        where.append('m.id>i.context_after')
        if model_item['project_id'] is None:
            where.append('i.id=?');args.append(model_item['id'])
        else:
            where.append('i.project_id=?');args.append(model_item['project_id'])
    else:
        if project_id is not None:where.append('i.project_id=?');args.append(project_id)
        if item_id:where.append('i.id=?');args.append(item_id)
    clause=' AND '.join(where)
    base=' FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE '+clause
    total=ws.conn.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
    rows=ws.conn.execute('SELECT m.id,m.role,m.content,m.created_at,i.id AS item_id,i.name AS item_name,i.project_id'+base+
        ' ORDER BY m.id DESC LIMIT 20 OFFSET ?',[*args,(page-1)*20]).fetchall()
    hits=[]
    for row in rows:
        value=dict(row);content=value.pop('content');at=content.lower().find(query.strip().lower());start=max(0,at-120)
        value.update(excerpt=content[start:start+1200],excerpt_start=start,original_chars=len(content),
            truncated=start>0 or len(content)>start+1200,url='/history?'+urlencode({'item':row['item_id'],'message':row['id']}))
        hits.append(value)
    return {'matches':hits,'total':total,'page':page,'page_size':20,
            'note':'字面检索；历史助手回答不是已核实来源。模型读取仅限当前任务/项目且排除已退休上下文。'}


def message(ws,item_id,message_id):
    row=ws.conn.execute("SELECT m.id,m.role,m.content,m.created_at,i.name AS item_name,i.id AS item_id "
        "FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE i.owner='local' AND i.id=? AND m.id=?",
        (item_id,message_id)).fetchone()
    if not row:raise LookupError('找不到这条消息')
    value=dict(row);value['original_chars']=len(value['content']);value['truncated']=len(value['content'])>24000
    value['content']=value['content'][:24000]
    return value
