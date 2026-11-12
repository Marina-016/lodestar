"""Recover received prose after a process restart, without invoking a model."""
import json

from lodestar.agent.continuation import answer_text
from lodestar.memory import repo


def recover_latest_interrupted(conn, item_id=None):
    recovered = []
    with conn:
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        runs = conn.execute('''SELECT r.*,i.conversation_id FROM web_runs r
            JOIN web_items i ON i.id=r.item_id WHERE i.owner='local' AND r.status='interrupted'
            AND (? IS NULL OR r.item_id=?) AND NOT EXISTS
            (SELECT 1 FROM web_runs n WHERE n.item_id=r.item_id AND n.rowid>r.rowid)''',
            (item_id,item_id)).fetchall()
        for run in runs:
            last = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 1",
                                (run['conversation_id'],)).fetchone()
            if last is None:
                continue
            saved_metadata = json.loads(last['metadata'] or '{}')
            if run['continue_message_id'] and run['id'] in (saved_metadata.get('last_continue_run_id'),saved_metadata.get('run_id')):
                status = last['kind'] if last['kind'] in ('error','cancelled') else 'completed'
                conn.execute('UPDATE web_runs SET status=?,result=? WHERE id=?',
                    (status,json.dumps({'answer':last['content'],'recovered':True},ensure_ascii=False),run['id']))
                continue
            original = ''
            if run['continue_message_id']:
                if last['id'] != run['continue_message_id']:
                    continue
                original = answer_text(dict(last))
            elif last['role'] == 'assistant':
                # The answer committed before the process exited; only the run's
                # terminal status was missing. Never append its stream a second time.
                metadata = json.loads(last['metadata'] or '{}')
                if metadata.get('run_id') not in (None,run['id']):
                    continue
                status = last['kind'] if last['kind'] in ('error','cancelled') else 'completed'
                conn.execute('UPDATE web_runs SET status=?,result=? WHERE id=?',
                    (status,json.dumps({'answer':last['content'],'recovered':True},ensure_ascii=False),run['id']))
                continue
            elif last['role'] != 'user' or last['content'] != run['message']:
                continue
            chunks = [json.loads(e['data']) for e in conn.execute(
                "SELECT data FROM web_events WHERE run_id=? AND kind='text' ORDER BY id", (run['id'],))]
            partial = original + ''.join(text for text in chunks if isinstance(text,str))
            metadata = json.loads(last['metadata'] or '{}') if run['continue_message_id'] else {}
            receipts = [json.loads(e['data']) for e in conn.execute(
                "SELECT data FROM web_events WHERE run_id=? AND kind='action' ORDER BY id", (run['id'],))]
            run_ids = [*metadata.get('run_ids',[]),metadata.get('run_id'),run['id']]
            metadata.update(partial_answer=partial,model_error={'code':'stream_interrupted'},
                error_message='刚才的连接中断了，已经收到的内容给你留着。',
                run_id=run['id'],run_ids=list(dict.fromkeys(r for r in run_ids if r)),
                action_receipts=metadata.get('action_receipts',[])+receipts,
                model_configuration=json.loads(run['config']),recovered_after_restart=True)
            if run['continue_message_id']:
                conn.execute("UPDATE messages SET content=?,kind='error',metadata=? WHERE id=?",
                    (partial,json.dumps(metadata,ensure_ascii=False),last['id']))
                message_id = last['id']
            else:
                cursor = conn.execute("INSERT INTO messages(conversation_id,role,kind,content,metadata,created_at) VALUES(?,'assistant','error',?,?,?)",
                    (run['conversation_id'],partial,json.dumps(metadata,ensure_ascii=False),repo._now()))
                message_id = cursor.lastrowid
            conn.execute("UPDATE web_runs SET status='error',result=? WHERE id=?",
                (json.dumps({'answer':partial,'status':'error','model_error':{'code':'stream_interrupted'},
                             'recovered_message_id':message_id},ensure_ascii=False),run['id']))
            recovered.append(message_id)
    return recovered
