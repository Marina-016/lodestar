"""Durable user-authored follow-ups: cancel, settle, then start a new turn once."""
import json,uuid
from lodestar.web.memory import MemoryBusy

SCHEMA='''CREATE TABLE IF NOT EXISTS web_followups (
 id TEXT PRIMARY KEY,item_id TEXT NOT NULL,request_key TEXT NOT NULL,
 message TEXT NOT NULL,reference_text TEXT NOT NULL,supersedes_run TEXT,
 status TEXT NOT NULL DEFAULT 'pending',next_run TEXT,error TEXT NOT NULL DEFAULT '',policy TEXT NOT NULL DEFAULT '{}',
 UNIQUE(item_id,request_key));
 CREATE UNIQUE INDEX IF NOT EXISTS web_one_followup ON web_followups(item_id)
 WHERE status IN ('pending','starting');'''

def initialize(ws):
    ws.conn.executescript(SCHEMA)
    if 'policy' not in {r['name'] for r in ws.conn.execute('PRAGMA table_info(web_followups)')}:
        ws.conn.execute("ALTER TABLE web_followups ADD COLUMN policy TEXT NOT NULL DEFAULT '{}'")

def view(ws,item_id):
    row=ws.conn.execute("SELECT * FROM web_followups WHERE item_id=? ORDER BY rowid DESC LIMIT 1",(item_id,)).fetchone()
    return dict(row) if row and row['status'] in ('pending','starting','error','interrupted') else None

def accept(app,item_id,message,key,reference='',run=None):
    if not isinstance(message,str) or not message.strip() or not isinstance(reference,str) or len(message)+len(reference)>24000:raise ValueError('补充内容需为 1–24000 字')
    if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('提交标识无效')
    with app.lock:
        if app.closing:raise MemoryBusy('服务正在关闭')
        with app.workspace() as ws,ws.conn:
            item=app.item(ws,item_id)
            if item['status']!='active':raise MemoryBusy('请先恢复任务')
            existing=ws.conn.execute('SELECT * FROM web_followups WHERE item_id=? AND request_key=?',(item_id,key)).fetchone()
            if existing:return dict(existing)
            active=ws.conn.execute("SELECT id,policy FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')",(item_id,)).fetchone()
            if active:
                if run and active['id']!=run:raise MemoryBusy('回答已更新，请重新发送补充')
                if ws.conn.execute("SELECT 1 FROM web_followups WHERE item_id=? AND status IN ('pending','starting')",(item_id,)).fetchone():raise MemoryBusy('补充已收到，正在接续')
                cfg=app.config_for(item)
                if cfg.model_calls_disabled and cfg.llm_mode!='mock':raise MemoryBusy('模型调用已暂停')
                fid=uuid.uuid4().hex
                ws.conn.execute('INSERT INTO web_followups(id,item_id,request_key,message,reference_text,supersedes_run,policy) VALUES(?,?,?,?,?,?,?)',(fid,item_id,key,message.strip(),reference.strip(),active['id'],active['policy']))
                ws.conn.execute("UPDATE web_runs SET status='cancelling' WHERE id=? AND status IN ('queued','running')",(active['id'],))
                signal=app.cancel_signals.get(active['id'])
                if signal:signal.set()
                return {'id':fid,'status':'pending','supersedes_run':active['id'],'message':message.strip(),'reference_text':reference.strip()}
        # The old response may have completed between the click and this request.
        return {'id':app.submit(item_id,message,key,reference_text=reference),'status':'submitted'}

def dispatch(app,item_id):
    with app.lock:
        if app.closing:return
        with app.workspace() as ws,ws.conn:
            row=ws.conn.execute("SELECT * FROM web_followups WHERE item_id=? AND status='pending' ORDER BY rowid LIMIT 1",(item_id,)).fetchone()
            if not row or ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')",(item_id,)).fetchone():return
            row=dict(row);ws.conn.execute("UPDATE web_followups SET status='starting' WHERE id=?",(row['id'],))
        try:
            run=app._submit_locked(item_id,row['message'],'followup:'+row['id'],reference_text=row['reference_text'],background=False,policy=json.loads(row['policy']),internal_followup=True)
            with app.workspace() as ws,ws.conn:ws.conn.execute("UPDATE web_followups SET status='submitted',next_run=? WHERE id=?",(run,row['id']))
        except Exception:
            with app.workspace() as ws,ws.conn:ws.conn.execute("UPDATE web_followups SET status='error',error='补充未能启动，内容已保留，请重新发送' WHERE id=?",(row['id'],))
