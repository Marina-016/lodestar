"""Local release startup must not mutate another running workspace."""
from pathlib import Path
from contextlib import closing, redirect_stdout
from dataclasses import replace
from io import StringIO
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from lodestar.config import Config
from lodestar.web import server
from lodestar.web.maintenance import WorkspaceInUse, backup_database, inspect_database, workspace_lease


class LocalStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='lodestar-local-release-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'workspace' / 'web'
        self.config = Config(llm_mode='mock', search_mode='mock',
            workspace_dir=self.data, db_path=self.data / 'web.db')
        # Keep the old default path calculation isolated as well as the new one.
        local_path = patch('lodestar.web.server.__file__', str(self.root / 'lodestar/web/server.py'))
        local_path.start()
        self.addCleanup(local_path.stop)

    def active_app(self):
        app = server.WebApp(self.config)
        self.addCleanup(app.close)
        with app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) "
                "VALUES('active','main','active','running','offline fixture','{}')")
        return app

    def test_occupied_port_does_not_interrupt_existing_run(self):
        app = self.active_app()
        with patch('lodestar.web.server.make_server', side_effect=OSError('Port unavailable')):
            with self.assertRaises(OSError):
                server.serve(8123, open_browser=False, config=self.config)
        self.assertEqual(app.events('active', 0)['run']['status'], 'running')

    def test_occupied_port_does_not_create_or_migrate_database(self):
        with patch('lodestar.web.server.make_server', side_effect=OSError('Port unavailable')), \
                patch('lodestar.web.server.WebApp') as app_factory:
            with self.assertRaises(OSError):
                server.serve(8123, open_browser=False, config=self.config)
        app_factory.assert_not_called()
        self.assertFalse(self.config.db_path.exists())

    def test_real_listener_cannot_be_bound_twice_including_windows(self):
        occupied = server.make_server(None, 0)
        self.addCleanup(occupied.server_close)
        with self.assertRaises(OSError):
            duplicate = server.make_server(None, occupied.server_port)
            self.addCleanup(duplicate.server_close)

    def test_another_port_cannot_recover_the_same_database(self):
        app = self.active_app()
        with workspace_lease(self.config.db_path), patch('lodestar.web.server.WebApp') as factory:
            with self.assertRaises(WorkspaceInUse):
                server.serve(0, False, self.config)
        factory.assert_not_called()
        self.assertEqual(app.events('active', 0)['run']['status'], 'running')
        self.assertFalse((self.data / 'backups').exists())

    def test_startup_backs_up_before_recovery_and_stops_without_model_calls(self):
        app = self.active_app()
        factory = Mock(wraps=server.WebApp)
        with patch('lodestar.web.server.WebApp', factory), \
                patch('lodestar.web.server.LLMClient', side_effect=AssertionError('model called')), \
                patch.object(server.ThreadingHTTPServer, 'serve_forever', side_effect=KeyboardInterrupt), \
                redirect_stdout(StringIO()):
            server.serve(0, False, self.config)
        factory.assert_called_once()
        backups = list((self.data / 'backups').glob('*.db'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute('SELECT status FROM web_runs').fetchone()[0], 'running')
        self.assertEqual(app.events('active', 0)['run']['status'], 'interrupted')
        self.assertEqual(inspect_database(backups[0])['integrity'], 'ok')
        with workspace_lease(self.config.db_path):
            pass

    def test_failed_backup_aborts_before_recovery_and_releases_lease(self):
        app = self.active_app()
        with patch('lodestar.web.server.backup_database', side_effect=OSError('offline disk fixture')), \
                patch('lodestar.web.server.WebApp') as factory:
            with self.assertRaises(OSError):
                server.serve(0, False, self.config)
        factory.assert_not_called()
        self.assertEqual(app.events('active', 0)['run']['status'], 'running')
        with workspace_lease(self.config.db_path):
            pass

    def test_explicit_data_directory_does_not_touch_default_directory(self):
        separate = self.root / 'separate'
        with patch.object(server.ThreadingHTTPServer, 'serve_forever', side_effect=KeyboardInterrupt), \
                redirect_stdout(StringIO()):
            server.serve(0, False, self.config, data_dir=separate)
        self.assertTrue((separate / 'web.db').is_file())
        self.assertFalse(self.config.db_path.exists())
        self.assertFalse((separate / 'backups').exists())

    def test_lease_is_held_until_app_shutdown_finishes(self):
        app = Mock(workers={})

        def during_close():
            with self.assertRaises(WorkspaceInUse):
                with workspace_lease(self.config.db_path):
                    pass

        app.close.side_effect = during_close
        with patch('lodestar.web.server.WebApp', return_value=app), \
                patch.object(server.ThreadingHTTPServer, 'serve_forever', side_effect=KeyboardInterrupt), \
                redirect_stdout(StringIO()):
            server.serve(0, False, self.config)
        app.close.assert_called_once()
        with workspace_lease(self.config.db_path):
            pass

    def test_close_waits_for_cancelled_worker_and_rejects_new_requests(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        client = Mock()
        app = server.WebApp(self.config, llm_factory=lambda _: client)
        self.addCleanup(app.close)

        def gather(ws, llm, context, project_id, **callbacks):
            entered.set()
            release.wait(3)
            callbacks['check_cancel']()

        with patch('lodestar.agent.dialogue.gather', side_effect=gather):
            run = app.submit('main', 'offline cancellation fixture', 'shutdown')
            closer = None
            try:
                self.assertTrue(entered.wait(3))
                closer = threading.Thread(target=lambda: (app.close(), stopped.set()))
                closer.start()
                self.assertTrue(app.cancel_signals[run].wait(3))
                self.assertFalse(stopped.is_set())
                with self.assertRaises(server.Conflict):
                    app.submit('main', 'must not start', 'after-shutdown')
            finally:
                release.set()
                if closer is not None:
                    closer.join(5)
        self.assertTrue(stopped.is_set())
        self.assertFalse(app.workers)
        self.assertFalse(app.cancel_signals)
        self.assertEqual(app.events(run, 0)['run']['status'], 'cancelled')
        client.close.assert_called_once()

    def test_main_cli_forwards_config_and_explicit_directory(self):
        from lodestar.cli import main
        with patch('lodestar.cli._make_config', return_value=self.config), \
                patch('lodestar.web.server.serve') as serve:
            main(['ui', '--no-browser', '--port', '8124', '--data-dir', str(self.root / 'trial')])
        serve.assert_called_once_with(port=8124, open_browser=False, config=self.config,
            data_dir=self.root / 'trial')

    def test_shutdown_waits_for_pending_startup_failure_to_be_persisted(self):
        app = server.WebApp(self.config)
        self.addCleanup(app.close)
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def fail_start():
            entered.set()
            release.wait(3)
            raise RuntimeError('offline worker fixture')

        def submit():
            try:
                app.submit('main', 'offline startup fixture', 'failed-start')
            except RuntimeError as error:
                errors.append(type(error).__name__)

        submitter = threading.Thread(target=submit)
        closer = threading.Thread(target=lambda: (app.close(), stopped.set()))
        # Only the app's worker is replaced; these controller threads already exist.
        with patch('lodestar.web.server.threading.Thread', return_value=Mock(start=fail_start)):
            submitter.start()
            try:
                self.assertTrue(entered.wait(3))
                closer.start()
                self.assertFalse(stopped.wait(.05))
            finally:
                release.set()
                submitter.join(5)
                if closer.ident is not None:
                    closer.join(5)
        self.assertTrue(stopped.is_set())
        self.assertEqual(errors, ['RuntimeError'])
        self.assertEqual(app.state()['run']['status'], 'error')
        self.assertFalse(app.workers)
        self.assertFalse(app.cancel_signals)

    def test_snapshot_restores_tasks_memory_messages_and_runs_in_separate_directory(self):
        from lodestar.memory import repo
        app = server.WebApp(self.config)
        self.addCleanup(app.close)
        task = app.create('Offline restore fixture')
        app.note('main', {'scope': 'personal', 'content': 'PRIVATE_RESTORE_CANARY'})
        with app.workspace() as ws, ws.conn:
            repo.add_message(ws.conn, app.state()['item']['conversation_id'], 'user', 'offline message')
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) "
                "VALUES('active',?,'restore','running','offline fixture','{}')", (task,))
        backup = self.root / 'snapshot.db'
        report = backup_database(self.config.db_path, backup)
        restored = self.root / 'restore'
        backup_database(backup, restored / 'web.db')
        clone = server.WebApp(replace(self.config, db_path=restored / 'web.db', workspace_dir=restored))
        self.addCleanup(clone.close)
        self.assertEqual(report['counts'], inspect_database(clone.config.db_path)['counts'])
        self.assertEqual(clone.state()['messages'][-1]['content'], 'offline message')
        self.assertEqual(clone.state()['notes'][0]['content'], 'PRIVATE_RESTORE_CANARY')
        self.assertEqual(clone.state(task)['run']['status'], 'interrupted')
        self.assertEqual(app.state(task)['run']['status'], 'running')


if __name__ == '__main__':
    unittest.main()
