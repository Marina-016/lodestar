import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import requests
from lodestar.desktop import options, status, build
from lodestar.web.server import make_server, SCHEMA
from tests import test_web as fixtures


class CompanionTests(unittest.TestCase):
    def test_desktop_options_never_accept_commands_paths_or_unbounded_dimensions(self):
        self.assertEqual(options(), {'skin':'pajamas','size':180,'motion':True})
        for value in [[], {'skin':['pajamas']}, {'skin':'../private'}, {'size':True}, {'size':321},
                      {'size':63}, {'size':'200'}, {'motion':'false'}, {'command':'anything'}, {'port':8124}]:
            with self.subTest(value=value), self.assertRaises(ValueError): options(value)

    def test_status_prioritizes_active_work_and_never_returns_chat_text(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row;conn.executescript(SCHEMA)
        self.addCleanup(conn.close)
        self.assertEqual(status(conn)['pose'],'idle')
        conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('r','main','k','running','PRIVATE_PROMPT','{}')")
        self.assertEqual(status(conn)['pose'],'thinking')
        conn.execute("INSERT INTO web_events(run_id,kind,data) VALUES('r','progress',?)",(json.dumps('read_paper'),))
        self.assertEqual(status(conn)['pose'],'reading')
        conn.execute("INSERT INTO web_events(run_id,kind,data) VALUES('r','text','PRIVATE_ANSWER')")
        packet=status(conn);self.assertEqual(packet['pose'],'writing')
        self.assertNotIn('PRIVATE',json.dumps(packet))
        conn.execute("UPDATE web_runs SET status='cancelling'")
        self.assertEqual(status(conn)['pose'],'idle')

    @unittest.skipUnless(sys.platform=='win32','Windows native compiler')
    def test_native_source_builds_and_validates_preferences_without_showing_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=build(Path(directory))
            result=subprocess.run([str(executable),'--self-test'],timeout=10)
            self.assertEqual(result.returncode,0)


class CompanionHttpTests(unittest.TestCase):
    setUp=fixtures.WebTest.setUp
    tearDown=fixtures.WebTest.tearDown

    def test_desktop_launch_is_csrf_guarded_and_status_does_not_call_models(self):
        server=make_server(self.app,0);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        client=requests.Session();client.trust_env=False
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch('lodestar.desktop.launch',return_value={'status':'opened'}) as launch:
                self.assertEqual(client.post(url+'/api/companion/desktop',json={'skin':'pajamas'}).status_code,403)
                launch.assert_not_called()
                result=client.post(url+'/api/companion/desktop',json={'skin':'pajamas','size':200,'motion':True},
                    headers={'X-Lodestar-Token':self.app.token})
                self.assertEqual(result.status_code,200)
                launch.assert_called_once_with(self.app.config.workspace_dir,port=server.server_port,
                    settings={'skin':'pajamas','size':200,'motion':True},stop=False)
            packet=client.get(url+'/api/companion/status').json()
            self.assertEqual(packet['pose'],'idle')
            self.assertEqual(set(packet),{'pose','status','desktop_supported'})
            sprite=client.get(url+'/lolo-pajamas.png')
            self.assertEqual(sprite.headers['Content-Type'],'image/png')
            self.assertTrue(sprite.content.startswith(b'\x89PNG\r\n\x1a\n'))
        finally:
            client.close();server.shutdown();server.server_close();worker.join()
