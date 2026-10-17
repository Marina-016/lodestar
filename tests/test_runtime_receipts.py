import json
import platform
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from lodestar.eval.environment import capture, verify
from lodestar.runtime_info import runtime_info


class RuntimeReceiptTests(unittest.TestCase):
    def test_windows_receipt_does_not_query_platform_or_wmi(self):
        version = SimpleNamespace(major=10, minor=0, build=26100)
        with patch('lodestar.runtime_info.sys.platform', 'win32'), \
                patch('lodestar.runtime_info.sys.getwindowsversion', return_value=version, create=True), \
                patch('lodestar.runtime_info.sysconfig.get_platform', return_value='win-amd64'), \
                patch.object(platform, 'uname', side_effect=AssertionError('WMI must not run')):
            info = runtime_info()
            with tempfile.TemporaryDirectory() as folder:
                hashes = capture(folder)
                receipt = json.loads((Path(folder) / 'environment.json').read_text())
                self.assertEqual(verify(folder, hashes), [])
        self.assertEqual(info['system'], 'Windows')
        self.assertEqual(info['release'], '10.0.26100')
        self.assertEqual(info['machine'], 'win-amd64')
        self.assertEqual(receipt['platform_source'], info['platform_source'])
        self.assertIn('Python build target', receipt['platform_source'])
        self.assertEqual(receipt['machine'], info['machine'])

    def test_unix_receipt_uses_native_uname(self):
        version = SimpleNamespace(sysname='Linux', release='6.8.0', machine='aarch64')
        with patch('lodestar.runtime_info.sys.platform', 'linux'), \
                patch('lodestar.runtime_info.os.uname', return_value=version, create=True):
            info = runtime_info()
        self.assertEqual(info['system'], 'Linux')
        self.assertEqual(info['machine'], 'aarch64')
        self.assertEqual(info['platform_source'], 'os.uname')


if __name__ == '__main__':
    unittest.main()
