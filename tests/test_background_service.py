"""Owned local service lifecycle, no provider, external network or OS task installation."""
import json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from lodestar.web import background

class BackgroundServiceTests(unittest.TestCase):
 def test_stale_or_foreign_instance_is_never_stopped(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);background.marker(root).write_text(json.dumps({'instance_id':'stale','token':'PRIVATE_CANARY','port':32123}),encoding='utf-8')
   response=Mock();response.json.return_value={'instance_id':'different','pid':123}
   session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False);session.get.return_value=response
   with patch('requests.Session',return_value=session):
    self.assertFalse(background.status(root)['running']);self.assertFalse(background.stop(root)['running']);session.post.assert_not_called()
   self.assertNotIn('PRIVATE_CANARY',json.dumps(background.status(root)))
 def test_real_offline_background_process_status_and_graceful_stop(self):
  with tempfile.TemporaryDirectory(prefix='lodestar-service-') as tmp:
   root=Path(tmp)
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   # Avoid Windows venv redirector: own and wait on the real interpreter process.
   env=dict(os.environ,PYTHONPATH=os.pathsep.join(sys.path),PYTHONIOENCODING='utf-8')
   executable=getattr(sys,'_base_executable',sys.executable)
   child=subprocess.Popen([executable,'-X','utf8','-m','lodestar.web.background','run','--data-dir',str(root),'--port',str(port),'--offline'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   try:
    deadline=time.monotonic()+20;value={'running':False}
    while time.monotonic()<deadline:
     value=background.status(root)
     if value['running']:break
     if child.poll() is not None:break
     time.sleep(.05)
    self.assertTrue(value['running'],'isolated process did not become ready')
    self.assertEqual(value['pid'],child.pid)
    self.assertNotIn('token',value)
    self.assertTrue(background.stop(root)['stopping'])
    out,err=child.communicate(timeout=15)
    self.assertEqual(child.returncode,0,err.decode('utf-8','replace'))
    self.assertFalse(background.marker(root).exists())
    self.assertFalse(background.status(root)['running'])
   finally:
    if child.poll() is None:
     try:background.stop(root);child.communicate(timeout=15)
     except Exception:child.terminate();child.communicate(timeout=5)
 def test_controller_uses_hidden_detached_process_without_shell_or_credentials(self):
  with tempfile.TemporaryDirectory() as tmp:
   process=Mock();process.pid=123;process.poll.return_value=None
   with patch.object(background,'status',side_effect=[{'running':False},{'running':False},{'running':True,'pid':123}]),patch.object(background.subprocess,'Popen',return_value=process) as popen:
    self.assertTrue(background.start(tmp,8137,offline=True)['running'])
    args,kwargs=popen.call_args;self.assertIsInstance(args[0],list);self.assertNotIn('shell',kwargs)
    if os.name=='nt':self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
 def test_scheduler_notices_deduplicate(self):
  import sqlite3
  with sqlite3.connect(':memory:') as conn:
   conn.executescript(background.SCHEMA)
   background.notify(conn,'same-run','task','owned','error');background.notify(conn,'same-run','task','owned','error')
   self.assertEqual(conn.execute('SELECT COUNT(*) FROM web_notifications').fetchone()[0],1)

 def test_login_task_xml_is_scoped_escaped_and_contains_no_key(self):
  from xml.etree import ElementTree as ET
  xml=background.task_xml(Path('workspace/space & unicode 目录'),8137)
  root=ET.fromstring(xml)
  ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
  self.assertEqual(root.find('t:Principals/t:Principal/t:LogonType',ns).text,'InteractiveToken')
  self.assertEqual(root.find('t:Triggers/t:LogonTrigger/t:UserId',ns).text,root.find('t:Principals/t:Principal/t:UserId',ns).text)
  self.assertEqual(root.find('t:Settings/t:MultipleInstancesPolicy',ns).text,'IgnoreNew')
  self.assertIn('background',root.find('t:Actions/t:Exec/t:Arguments',ns).text)
