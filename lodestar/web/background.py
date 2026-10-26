"""Opt-in model-free scheduling, durable notices and owned background service control."""
from datetime import datetime,timezone
from hashlib import sha256
import argparse,getpass,json,os,subprocess,sys,threading,time
from pathlib import Path

SCHEMA="""CREATE TABLE IF NOT EXISTS web_background_settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0);
 INSERT OR IGNORE INTO web_background_settings VALUES(1,0);
 CREATE TABLE IF NOT EXISTS web_notifications (id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"""


def notify(conn,key,kind,subject,status):
    conn.execute('INSERT OR IGNORE INTO web_notifications(event_key,kind,subject,status) VALUES(?,?,?,?)',(key,kind,str(subject),status))


def view(ws):
    return {'enabled':bool(ws.conn.execute('SELECT enabled FROM web_background_settings WHERE id=1').fetchone()[0]),
        'notifications':[dict(r) for r in ws.conn.execute('SELECT * FROM web_notifications ORDER BY id DESC LIMIT 50')],
        'unread':ws.conn.execute('SELECT COUNT(*) FROM web_notifications WHERE seen=0').fetchone()[0],
        'scope':'仅检查已启用的公开论文订阅，不调用模型、不执行代码；关闭检查不取消已提交操作。通知仅保存在本机。'}


class Scheduler:
    def __init__(self,app):
        self.app=app;self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self):self.thread.start()
    def run(self):
        while not self.stop.wait(30):
            try:self.tick()
            except Exception:
                # Store a bounded failure category, never provider text or credentials.
                try:
                    with self.app.workspace() as ws,ws.conn:notify(ws.conn,'scheduler-error:'+datetime.now(timezone.utc).strftime('%Y-%m-%d'),'scheduler','本地后台','error')
                except Exception: pass
    def tick(self):
        if self.app.closing:return
        with self.app.workspace() as ws:
            if not view(ws)['enabled']:return
            now=datetime.now(timezone.utc).isoformat(timespec='seconds')
            rows=ws.conn.execute("SELECT w.project_id,w.id,w.next_run FROM paper_watches w JOIN projects p ON p.id=w.project_id WHERE w.enabled=1 AND p.status='active' AND w.next_run<=? AND (w.lease_until IS NULL OR w.lease_until<=?) ORDER BY w.id LIMIT 20",(now,now)).fetchall()
            due={}
            for r in rows:due.setdefault(r['project_id'],[]).append((r['id'],r['next_run']))
        from lodestar.web.memory import MemoryBusy
        for project_id,records in due.items():
            if self.stop.is_set() or self.app.closing:return
            key='watch:'+sha256(json.dumps(records).encode()).hexdigest()
            try:self.app.research_job({'project_id':project_id,'action':'tick','key':key})
            except MemoryBusy:continue


def marker(root):return Path(root)/'.lodestar-service.json'


def _record(root):
    try:
        value=json.loads(marker(root).read_text(encoding='utf-8'))
        if type(value.get('port')) is not int or not 1<=value['port']<=65535 or not isinstance(value.get('instance_id'),str):return None
        return value
    except (OSError,ValueError,TypeError):return None


def status(root):
    import requests
    value=_record(root)
    if not value:return {'running':False,'note':'没有已登记的后台实例；前台服务可能仍在运行。'}
    try:
        with requests.Session() as session:
            session.trust_env=False
            result=session.get(f"http://127.0.0.1:{value['port']}/api/service",timeout=2).json()
        if result.get('instance_id')==value['instance_id']:
            return {'running':True,'port':value['port'],'pid':result.get('pid'),'instance_id':value['instance_id']}
    except (requests.RequestException,ValueError):pass
    return {'running':False,'note':'后台实例已停止或尚未就绪；未操作其他进程。'}


def start(root,port=8124,*,offline=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    current=status(root)
    if current['running']:return current
    # Serialize simultaneous starts without touching the DB or another process.
    from lodestar.web.maintenance import workspace_lease
    with workspace_lease(root/'background-controller.db'):
        current=status(root)
        if current['running']:return current
        command=[sys.executable,'-m','lodestar.web.background','run','--data-dir',str(root),'--port',str(port)]
        if offline:command.append('--offline')
        options={'creationflags':subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
        with (root/'background-service.log').open('ab') as log:
            child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            value=status(root)
            if value['running']:return value
            if child.poll() is not None:raise ValueError('后台启动未完成；请检查本地服务日志、端口和数据目录锁，原服务未被替换')
            time.sleep(.15)
        return {'running':False,'starting':True,'pid':child.pid,'note':'启动尚未确认，请用 status 检查；未自动重启或终止其他进程。'}


def stop(root):
    import requests
    value=_record(root);current=status(root)
    if not current['running']:return current
    with requests.Session() as session:
        session.trust_env=False
        r=session.post(f"http://127.0.0.1:{value['port']}/api/service-stop",headers={'X-Lodestar-Token':value['token']},
            json={'instance_id':value['instance_id']},timeout=5)
        r.raise_for_status()
    return {'stopping':True,'note':'已请求正常关闭；正在执行的网络请求可能仍需等待原超时。'}


def task_xml(root,port):
    from xml.sax.saxutils import escape
    root=Path(root).resolve()
    args=subprocess.list2cmdline(['-m','lodestar.web.background','run','--data-dir',str(root),'--port',str(port)])
    user=(os.environ.get('USERDOMAIN','')+'\\' if os.environ.get('USERDOMAIN') else '')+getpass.getuser()
    xml=f'''<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers><Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings><Actions Context="Author"><Exec><Command>{escape(sys.executable)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(str(Path(__file__).resolve().parents[2]))}</WorkingDirectory></Exec></Actions></Task>'''
    return xml


def autostart(root,port,enabled):
    if os.name!='nt':raise ValueError('登录自启配置目前仅支持 Windows；其它平台可使用系统服务管理器运行 background run')
    from xml.sax.saxutils import escape
    root=Path(root).resolve()
    name='Lodestar-'+sha256(str(root).lower().encode()).hexdigest()[:12]
    if not enabled:
        result=subprocess.run(['schtasks','/Delete','/TN',name,'/F'],capture_output=True,timeout=15)
    else:
        xml=task_xml(root,port)
        root.mkdir(parents=True,exist_ok=True);path=root/'background-task.xml';path.write_text(xml,encoding='utf-16')
        try:result=subprocess.run(['schtasks','/Create','/TN',name,'/XML',str(path),'/F'],capture_output=True,timeout=15)
        finally:path.unlink(missing_ok=True)
    if result.returncode:raise ValueError('Windows 登录任务配置失败；请检查当前用户的任务计划权限，未自动提权')
    return {'autostart':enabled,'task_name':name,'note':'登录任务只启动本地服务；订阅检查仍由页面独立开关控制。'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','stop','status','run','autostart-on','autostart-off'])
    p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--port',type=int,default=8124);p.add_argument('--offline',action='store_true');a=p.parse_args(argv)
    if not 1<=a.port<=65535:raise ValueError('端口无效')
    if a.action=='run':
        from lodestar.web.server import serve
        from lodestar.config import Config
        serve(a.port,False,config=Config(llm_mode='mock',search_mode='mock',model_calls_disabled=True) if a.offline else None,data_dir=a.data_dir,background_service=True)
        return
    result=start(a.data_dir,a.port,offline=a.offline) if a.action=='start' else stop(a.data_dir) if a.action=='stop' else status(a.data_dir) if a.action=='status' else autostart(a.data_dir,a.port,a.action=='autostart-on')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
