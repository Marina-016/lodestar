"""Local SQLite backups and a process lease; never load config or call a model."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time


class WorkspaceInUse(RuntimeError):
    pass


@contextmanager
def workspace_lease(database):
    """Keep the lock file, not a PID sentinel: the OS releases locks after a crash."""
    path = Path(database).resolve().with_suffix('.db.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as error:
            raise WorkspaceInUse('此数据目录已由另一个 Lodestar 进程使用；请先关闭原服务。') from error
        try:
            yield
        finally:
            handle.seek(0)
            unlock()


def _readonly(database):
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError('数据库文件不存在；未创建空数据库。')
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)


def _check(connection, deadline):
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('数据库完整性检查未通过。')


def inspect_database(database, *, timeout=30):
    """Read-only integrity/counts; do not initialize schemas or expose record text."""
    connection = _readonly(database)
    try:
        _check(connection, time.monotonic() + timeout)
        present = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: connection.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('web_items', 'web_runs', 'messages', 'assistant_memories', 'learning_events')
                  if name in present}
        return {'integrity': 'ok', 'counts': counts}
    finally:
        connection.close()


def backup_database(database, destination, *, timeout=30):
    """Use SQLite's online backup API, including committed WAL; never overwrite."""
    destination = Path(destination).resolve()
    source = _readonly(database)
    target = None
    owned = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb'):
            owned = True
        target = sqlite3.connect(str(destination))
        deadline = time.monotonic() + timeout

        def progress(*_):
            if time.monotonic() >= deadline:
                raise TimeoutError('备份超时；原数据库未修改。')

        source.backup(target, pages=128, progress=progress, sleep=.01)
        _check(target, deadline)
        target.close()
        target = None
        return inspect_database(destination, timeout=timeout)
    except Exception:
        if target is not None:
            target.close()
        if owned:
            destination.unlink(missing_ok=True)
        raise
    finally:
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'backup'):
        command = commands.add_parser(name)
        command.add_argument('--database', required=True, type=Path)
        if name == 'backup':
            command.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = (backup_database(args.database, args.output) if args.command == 'backup'
                  else inspect_database(args.database))
    except (OSError, sqlite3.Error, ValueError):
        parser.exit(1, '操作失败：检查数据库路径、完整性与目录权限；备份目标必须是新文件。未迁移或覆盖原数据库。\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
