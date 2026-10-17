"""Small local runtime receipts without WMI, shell commands or environment values."""
from __future__ import annotations

import os
import sys
import sysconfig


def runtime_info():
    """Record OS version and Python build architecture, not physical host hardware.

    Newer Python versions may ask WMI even for ``platform.system()`` on Windows.
    These receipts only need stable runtime facts; no service query is required.
    """
    info = {
        'python': '.'.join(str(part) for part in sys.version_info[:3]),
        'implementation': sys.implementation.name,
    }
    if sys.platform == 'win32':
        version = sys.getwindowsversion()
        info.update(system='Windows',
            release=f'{version.major}.{version.minor}.{version.build}',
            machine=sysconfig.get_platform(),
            platform_source='sys.getwindowsversion; machine is the Python build target')
    elif hasattr(os, 'uname'):
        version = os.uname()
        info.update(system=version.sysname, release=version.release,
            machine=version.machine, platform_source='os.uname')
    else:
        info.update(system=sys.platform, release='', machine='',
            platform_source='sys.platform; OS version and machine unavailable')
    return info
