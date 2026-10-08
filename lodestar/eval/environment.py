"""Local runtime receipts without environment variables, credentials or package URLs."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re


def capture(directory):
    packages = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', '')
        version = distribution.version
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+!-]*', version):
            packages[name.lower().replace('_', '-')] = version
    receipt = {'version': 1, 'python': platform.python_version(),
        'implementation': platform.python_implementation(), 'system': platform.system(),
        'release': platform.release(), 'machine': platform.machine(),
        'packages': dict(sorted(packages.items())),
        'scope': 'installed Python distribution versions; excludes source URLs and environment variables',
        'limits': 'No wheel hashes, OS libraries, provider model weights or deterministic model replay guarantee.'}
    files = {'environment.json': json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2),
        'requirements-frozen.txt': ''.join(f'{name}=={version}\n' for name, version in sorted(packages.items())
            if name != 'lodestar')}
    hashes = {}
    for name, content in files.items():
        data = content.encode('utf-8')
        (Path(directory) / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def verify(directory, hashes):
    """Validate receipt integrity; never installs dependencies or runs code."""
    if not isinstance(hashes, dict) or set(hashes) != {'environment.json', 'requirements-frozen.txt'}:
        return ['Invalid environment receipt manifest']
    errors = []
    for name, expected in hashes.items():
        try:
            actual = hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest()
        except OSError:
            errors.append('Missing environment receipt: ' + name)
            continue
        if actual != expected:
            errors.append('Environment receipt hash mismatch: ' + name)
    return errors
