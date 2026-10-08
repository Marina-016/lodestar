"""Scheduled, model-free public paper discovery with auditable lexical matching."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlparse

from lodestar.memory import repo
from lodestar.memory import watch as store
from lodestar.tools.discovery import discover


def _iso(now):
    return now.astimezone(timezone.utc).isoformat(timespec='seconds')


def subscribe(ws, project_id, query, terms, interval_hours=24):
    if not repo.get_project(ws.conn, project_id):
        raise ValueError('Unknown project')
    return store.subscribe(ws.conn, project_id, query, terms, interval_hours, _iso(datetime.now(timezone.utc)))


def match_terms(source, terms):
    matches = []
    for term in terms:
        pattern = re.compile(r'(?<!\w)' + re.escape(term.strip()) + r'(?!\w)', re.IGNORECASE)
        for field in ('title', 'snippet'):
            text = source.get(field) or ''
            found = pattern.search(text)
            if found:
                matches.append({'term': term, 'field': field,
                                'quote': text[max(0, found.start()-60):found.end()+100]})
                break
    return matches


def tick(ws, *, now=None, discovery=discover, limit=10):
    fixed_clock = now is not None
    now = now or datetime.now(timezone.utc)
    stamp = _iso(now)
    rows = ws.conn.execute("""SELECT w.* FROM paper_watches w JOIN projects p ON p.id=w.project_id
        WHERE w.enabled=1 AND p.status='active' AND w.next_run<=?
        AND (w.lease_until IS NULL OR w.lease_until<=?) ORDER BY w.next_run,w.id LIMIT ?""",
        (stamp, stamp, max(1,min(limit,20)))).fetchall()
    output = []
    for row in rows:
        lease_now = now if fixed_clock else datetime.now(timezone.utc)
        lease = _iso(lease_now + timedelta(minutes=10))
        claimed = ws.conn.execute("""UPDATE paper_watches SET lease_until=? WHERE id=?
            AND enabled=1 AND next_run<=? AND (lease_until IS NULL OR lease_until<=?)""",
            (lease, row['id'], stamp, stamp)).rowcount
        ws.conn.commit()
        if not claimed:
            continue
        reports, candidates, added = [], {}, 0
        try:
            for kind in ('recent', 'trending'):
                try:
                    report = discovery(ws, row['query'], days=min(30,max(1,(row['interval_hours']+23)//24+1)),
                                       kind=kind, limit=20)
                except Exception as exc:
                    report = {'status':'error','sources':[],'error':type(exc).__name__}
                reports.append({k:v for k,v in report.items() if k != 'sources'})
                if report.get('status') != 'ok':
                    continue
                for source in report.get('sources',[])[:20]:
                    url = source.get('url','')
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or parsed.hostname != 'arxiv.org' or not parsed.path.startswith('/abs/'):
                        continue
                    key = ('fixture:' if report.get('mode') == 'mock' else '') + 'arxiv:' + re.sub(r'v\d+$','',parsed.path[len('/abs/'):])
                    matched = match_terms(source, json.loads(row['terms']))
                    if not matched:
                        continue
                    if key not in candidates:
                        candidates[key] = {'source':{**source, 'discovery_mode':report.get('mode','live')},
                                           'matches':matched,'channels':[]}
                    candidates[key]['channels'].append(kind)
            for key,item in candidates.items():
                old = ws.conn.execute('SELECT channels FROM paper_recommendations WHERE project_id=? AND paper_key=?',
                                      (row['project_id'],key)).fetchone()
                channels = sorted(set(item['channels'] + (json.loads(old['channels']) if old else [])))
                ws.conn.execute("""INSERT INTO paper_recommendations(project_id,paper_key,source,matches,first_seen,last_seen,channels)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,paper_key) DO UPDATE SET
                    source=excluded.source,matches=excluded.matches,last_seen=excluded.last_seen,channels=excluded.channels""",
                    (row['project_id'],key,json.dumps(item['source'],ensure_ascii=False),json.dumps(item['matches'],ensure_ascii=False),
                     stamp,stamp,json.dumps(channels)))
                added += int(old is None)
            status = 'ok' if all(r.get('status')=='ok' for r in reports) else ('partial' if any(r.get('status')=='ok' for r in reports) else 'error')
            result = {'watch_id':row['id'],'project_id':row['project_id'],'status':status,'new_recommendations':added,
                      'matched_papers':len(candidates),'providers':reports,'model_calls':0,
                      'matching':'lexical candidate signal, not semantic applicability or mastery'}
            ws.conn.execute('INSERT INTO paper_watch_runs(watch_id,started_at,status,payload) VALUES(?,?,?,?)',
                            (row['id'],stamp,status,json.dumps(result,ensure_ascii=False)))
            delay = row['interval_hours'] if status=='ok' else min(row['interval_hours'],1)
            ws.conn.execute('UPDATE paper_watches SET next_run=?,lease_until=NULL WHERE id=?',
                            (_iso(now+timedelta(hours=delay)),row['id']))
            ws.conn.commit()
            output.append(result)
        except Exception:
            ws.conn.rollback()
            ws.conn.execute('UPDATE paper_watches SET lease_until=NULL WHERE id=?',(row['id'],))
            ws.conn.commit()
            raise
    return {'runs':output,'model_calls':0}
