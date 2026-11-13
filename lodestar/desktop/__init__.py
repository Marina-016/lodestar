"""Windows desktop companion, built with the installed .NET Framework only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SOURCE = Path(__file__).with_name('LoloDesktop.cs')
ASSETS = Path(__file__).parents[1] / 'web' / 'static'
SKINS = {'maid': 'lolo-companion.png', 'pajamas': 'lolo-pajamas.png'}


def options(data=None):
    data = {} if data is None else data
    if not isinstance(data, dict) or set(data) - {'skin', 'size', 'motion'}:
        raise ValueError('桌面设置仅支持服装、大小和动画。')
    skin, size, motion = data.get('skin', 'pajamas'), data.get('size', 180), data.get('motion', True)
    if type(skin) is not str or skin not in SKINS or type(size) is not int or not 64 <= size <= 320 or type(motion) is not bool:
        raise ValueError('请选择有效服装、64–320 px 大小和动画开关。')
    return {'skin': skin, 'size': size, 'motion': motion}


def status(conn):
    """Only a pose and coarse run status; never expose prompts or answer text."""
    row = conn.execute("SELECT id,status FROM web_runs ORDER BY CASE WHEN status IN ('queued','running','cancelling') "
                       "THEN 0 ELSE 1 END,created_at DESC,rowid DESC LIMIT 1").fetchone()
    state, pose = (row['status'] if row else 'idle'), 'idle'
    if state in ('queued', 'running'):
        pose = 'thinking'
        event = conn.execute("SELECT kind,CASE WHEN kind='progress' THEN data ELSE NULL END AS phase FROM web_events "
                             "WHERE run_id=? AND kind IN ('progress','text') ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
        if event and event['kind'] == 'text':
            pose = 'writing'
        elif event:
            try:phase = json.loads(event['phase'])
            except (ValueError, TypeError):phase = ''
            if isinstance(phase, str) and phase.startswith(('search_', 'discover_', 'read_', 'verify_', 'get_', 'project_', 'manage_memory')):
                pose = 'reading'
    return {'pose': pose, 'status': state, 'desktop_supported': sys.platform == 'win32'}


def build(workspace):
    if sys.platform != 'win32':
        raise ValueError('桌面小 lolo 目前支持 Windows；页面里的小 lolo 可以照常使用。')
    framework = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319'
    if not (framework / 'csc.exe').is_file():
        framework = framework.parent.parent / 'Framework' / 'v4.0.30319'
    compiler = framework / 'csc.exe'
    if not compiler.is_file():
        raise ValueError('没有找到 Windows .NET Framework 编译器，暂时无法打开桌面小 lolo。')
    directory = Path(workspace) / 'desktop-companion'
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    output = directory / f'lolo-{digest}.exe'
    if not output.is_file():
        references = ['System.dll', 'System.Core.dll', 'System.Web.Extensions.dll',
                      'System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Xaml.dll']
        references += [str(framework / 'WPF' / name) for name in
                       ('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll')]
        result = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/codepage:65001',
            '/optimize+', '/out:' + str(output), *('/reference:' + name for name in references), str(SOURCE)],
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            # Keep compiler details local; no model requests or prompt content.
            (directory / 'build.log').write_bytes(result.stdout + result.stderr)
            raise ValueError('桌面小 lolo 没有构建成功，详细信息保存在 desktop-companion/build.log。')
    return output


def launch(workspace, *, port=8124, settings=None, stop=False):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('无效的 Lodestar 端口。')
    config = options(settings)
    output = build(workspace)
    directory = output.parent
    if not stop:
        if not all((ASSETS / name).is_file() for name in SKINS.values()):
            raise ValueError('小 lolo 的服装素材还没有准备好。')
    if not stop and settings is not None:
        temporary = directory / 'request.tmp'
        temporary.write_text(json.dumps(config), encoding='utf-8')
        temporary.replace(directory / 'request.json')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1
    process = subprocess.Popen([str(output), str(ASSETS), str(directory), str(port), 'stop' if stop else 'show'],
        startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # A second invocation signals the existing companion and exits successfully.
    time.sleep(.25)
    code = process.poll()
    if code not in (None, 0):
        raise ValueError('小 lolo 暂时没能出现在桌面，可以稍后再试。')
    return {'status': 'stopped' if stop else 'opened', 'settings': config}
