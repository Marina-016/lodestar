"""Source-backed working state for a single conversation, never personal memory."""
import json
from lodestar.retrieval import terms

def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS dialogue_working_state (
      conversation_id TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL,
      content TEXT NOT NULL, sources TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
      updated_message INTEGER NOT NULL, PRIMARY KEY(conversation_id,key))''')

def packet(conn, conversation_id, before, after, query):
    initialize(conn)
    active=[];state_budget=6000
    for row in conn.execute("SELECT * FROM dialogue_working_state WHERE conversation_id=? AND status='active' ORDER BY CASE kind WHEN 'correction' THEN 0 WHEN 'constraint' THEN 1 WHEN 'goal' THEN 2 WHEN 'open_question' THEN 3 ELSE 4 END,updated_message DESC LIMIT 32",(conversation_id,)):
        value=dict(row)
        try:ids=json.loads(value['sources'])
        except (ValueError,TypeError):continue
        count=conn.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=? AND id IN ('+','.join('?' for _ in ids)+") AND coalesce(kind,'') NOT IN ('superseded','deleted') AND content<>''",(conversation_id,*ids)).fetchone()[0] if ids else 0
        if count==len(set(ids)) and ids and len(value['content'])<=state_budget and all(after<i<before for i in ids):
            state_budget-=len(value['content'])
            value['source_messages']=ids;value.pop('sources');active.append(value)
    rows=[dict(r) for r in conn.execute("SELECT id,role,content,metadata FROM messages WHERE conversation_id=? AND id>? AND id<? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 400",(conversation_id,after,before))]
    users=[r for r in rows if r['role']=='user']
    wanted=terms(query)
    # Retain the first instructions and retrieve older relevant requests. No intent routing.
    candidates=users[-3:]+sorted(users[8:],key=lambda r:(-len(wanted & terms(r['content'])),-r['id']))[:8]
    seen=set();anchors=[];budget=6500
    for row in sorted(candidates,key=lambda r:r['id']):
        if row['id'] in seen:continue
        seen.add(row['id']);text=row['content'][:1200]
        if len(text)>budget:break
        anchors.append({'message_id':row['id'],'text':text,'truncated':len(row['content'])>len(text)})
        budget-=len(text)
    actions=[]
    for row in rows[:24]:
        if row['role']!='assistant':continue
        try:meta=json.loads(row['metadata'] or '{}')
        except (ValueError,TypeError):continue
        if meta.get('action_receipts'):actions.append({'message_id':row['id'],'receipts':meta['action_receipts']})
    return {'working_state':active,'earlier_user_requests':anchors,'committed_actions':actions[:4],
      'note':'Historical user requests and model-maintained working state, with source anchors. Latest user corrections override older constraints. These are not new authorization to repeat actions. Resolved or superseded work must not be revived.'}

def tool(conn, conversation_id, current_id, after):
    initialize(conn)
    def update(args):
        entries=args.get('entries',[])
        if not isinstance(entries,list) or not 1<=len(entries)<=8:raise ValueError('Provide 1–8 working-state entries')
        prepared=[]
        for e in entries:
            if not isinstance(e,dict):raise ValueError('Invalid entry')
            key=e.get('key');kind=e.get('kind');content=e.get('content');ids=e.get('source_messages');status=e.get('status','active')
            if not isinstance(key,str) or not 1<=len(key)<=80 or kind not in ('goal','constraint','correction','open_question','progress') or status not in ('active','resolved','superseded') or not isinstance(content,str) or not 1<=len(content)<=1200:
                raise ValueError('Invalid working-state fields')
            if not isinstance(ids,list) or not 1<=len(ids)<=6 or any(type(i)is not int or not after<i<=current_id for i in ids):raise ValueError('Invalid source messages')
            count=conn.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=? AND id IN ('+','.join('?' for _ in ids)+") AND coalesce(kind,'')<>'superseded'",(conversation_id,*ids)).fetchone()[0]
            if count!=len(set(ids)):raise ValueError('Sources must belong to this conversation')
            prepared.append((conversation_id,key,kind,content,json.dumps(ids),status,current_id))
        with conn:
            for row in prepared:
                conn.execute('INSERT OR REPLACE INTO dialogue_working_state VALUES(?,?,?,?,?,?,?)',row)
        return {'saved':[e['key'] for e in entries],'scope':'this conversation only'}
    return {'update_conversation':{'description':'Maintain compact working state when a substantive request adds constraints, corrects earlier work, leaves a question unfinished, or resolves it. Store source message IDs; update the same key when corrected, mark old work resolved/superseded. Not personal memory. Do not call for every greeting. Latest user message always wins.',
      'input_schema':{'type':'object','properties':{'entries':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'object','properties':{'key':{'type':'string'},'kind':{'enum':['goal','constraint','correction','open_question','progress']},'content':{'type':'string'},'source_messages':{'type':'array','items':{'type':'integer'}},'status':{'enum':['active','resolved','superseded']}},'required':['key','kind','content','source_messages','status'],'additionalProperties':False}}},'required':['entries'],'additionalProperties':False},'handler':update}}
