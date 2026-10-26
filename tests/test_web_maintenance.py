"""Backup/lock contracts use only owned temporary SQLite files and subprocesses."""
from contextlib import closing, redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from lodestar.web.maintenance import backup_database, inspect_database, main, workspace_lease


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='lodestar-backup-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / '研究 # database.db'
        self.destination = self.root / 'backups' / 'snapshot.db'

    def seed(self):
        connection = sqlite3.connect(self.source)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT)')
        connection.execute("INSERT INTO messages(content) VALUES('PRIVATE_RECORD_CANARY')")
        connection.commit()
        return connection

    def test_online_snapshot_includes_committed_wal_not_uncommitted_edits(self):
        connection = self.seed()
        try:
            connection.execute("INSERT INTO messages(content) VALUES('uncommitted')")
            self.assertTrue(Path(str(self.source) + '-wal').exists())
            report = backup_database(self.source, self.destination)
            self.assertEqual(report, {'integrity': 'ok', 'counts': {'messages': 1}})
            with closing(sqlite3.connect(self.destination)) as backup:
                self.assertEqual(backup.execute('SELECT content FROM messages').fetchall(), [('PRIVATE_RECORD_CANARY',)])
                tables = {r[0] for r in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(tables, {'messages'}, 'Backups must not migrate source schemas')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)
        finally:
            connection.close()

    def test_existing_destination_and_source_itself_are_never_overwritten(self):
        self.seed().close()
        self.destination.parent.mkdir()
        self.destination.write_bytes(b'owned-existing-file')
        for path in (self.destination, self.source):
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                backup_database(self.source, path)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_database_never_creates_source_or_empty_backup(self):
        with self.assertRaises(ValueError):
            backup_database(self.source, self.destination)
        with self.assertRaises(ValueError):
            inspect_database(self.source)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_corrupt_source_kept_and_failed_snapshot_removed(self):
        content = b'not a SQLite database - offline fixture'
        self.source.write_bytes(content)
        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(self.source, self.destination)
        self.assertEqual(self.source.read_bytes(), content)
        self.assertFalse(self.destination.exists())

    def test_expired_snapshot_deadline_keeps_source_and_cleans_partial_copy(self):
        self.seed().close()
        with self.assertRaises(TimeoutError):
            backup_database(self.source, self.destination, timeout=-1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(inspect_database(self.source)['counts'], {'messages': 1})

    def test_command_only_reports_integrity_and_counts_without_loading_config(self):
        self.seed().close()
        output = StringIO()
        with patch('lodestar.config.load_config', side_effect=AssertionError('config should not load')), \
                redirect_stdout(output):
            main(['backup', '--database', str(self.source), '--output', str(self.destination)])
        self.assertEqual(json.loads(output.getvalue()), {'integrity': 'ok', 'counts': {'messages': 1}})
        self.assertNotIn('PRIVATE_RECORD_CANARY', output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            main(['check', '--database', str(self.destination)])
        self.assertEqual(json.loads(output.getvalue())['integrity'], 'ok')

    def test_cli_failure_has_nonzero_exit_and_no_record_or_sql_diagnostics(self):
        self.source.write_bytes(b'PRIVATE_RECORD_CANARY')
        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            main(['check', '--database', str(self.source)])
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn('PRIVATE_RECORD_CANARY', errors.getvalue())

    def test_process_lease_blocks_another_process_and_releases_normally(self):
        code = '''
import sys
from lodestar.web.maintenance import workspace_lease, WorkspaceInUse
try:
    with workspace_lease(sys.argv[1]):
        print('acquired')
except WorkspaceInUse:
    sys.exit(23)
'''
        with workspace_lease(self.source):
            blocked = subprocess.run([sys.executable, '-c', code, str(self.source)],
                capture_output=True, timeout=10)
            self.assertEqual(blocked.returncode, 23, blocked.stderr.decode(errors='replace'))
        released = subprocess.run([sys.executable, '-c', code, str(self.source)],
            capture_output=True, timeout=10)
        self.assertEqual(released.returncode, 0, released.stderr.decode(errors='replace'))
        self.assertFalse(self.source.exists(), 'A lock must not initialize the database')
        self.assertTrue(self.source.with_suffix('.db.lock').exists())

    def test_abrupt_process_exit_releases_lock_without_deleting_lock_file(self):
        code = '''
import sys, threading
from lodestar.web.maintenance import workspace_lease
with workspace_lease(sys.argv[1]):
    print('locked', flush=True)
    threading.Event().wait(30)
'''
        # Avoid terminating only Windows' venv redirector and leaving its child alive.
        executable = getattr(sys, '_base_executable', sys.executable)
        child = subprocess.Popen([executable, '-c', code, str(self.source)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertEqual(ready.get(timeout=5).strip(), b'locked')
            reader.join(1)
        finally:
            child.terminate()
            child.communicate(timeout=5)
            reader.join(1)
        with workspace_lease(self.source):
            pass

    def test_independent_databases_do_not_share_a_lease(self):
        with workspace_lease(self.source), workspace_lease(self.root / 'another.db'):
            pass


if __name__ == '__main__':
    unittest.main()
