#!/usr/bin/env python3
"""Exercise the real systemd user boot transaction without starting services."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('systemd-analyze'), 'Linux systemd-analyze required')
class BootOrderingTest(unittest.TestCase):
    def test_vendor_after_default_target_does_not_cycle(self):
        with tempfile.TemporaryDirectory(prefix='hermes-boot-test-') as temporary:
            root = Path(temporary)
            for target in ('basic.target', 'shutdown.target'):
                (root / target).write_text('[Unit]\nDescription=Isolated ' + target + '\n')
            (root / 'app.slice').write_text('[Unit]\nDescription=Isolated application slice\n[Slice]\n')
            (root / 'default.target').write_text('[Unit]\nDescription=Isolated user boot\n')
            (root / 'nemoclaw-openshell-gateway.service').write_text(
                '[Unit]\nAfter=default.target\n[Service]\nExecStart=' + shutil.which('true') + '\n')
            scripts = root / 'scripts'; scripts.mkdir()
            helper = scripts / 'setup-hermes-guest.sh'
            helper.write_text('#!/bin/sh\nexit 0\n'); helper.chmod(0o755)
            source = (ROOT / 'deploy/hermes/hermes-personal.service.in').read_text()
            source = source.replace('@RELEASE@', str(root)).replace('@CONFIG@', str(root / 'config')).replace('@OPERATOR_HOME@', str(root))
            unit = root / 'homecompute-hermes-personal.service'
            wants = root / 'default.target.wants'; wants.mkdir()
            for name in (unit.name, 'nemoclaw-openshell-gateway.service'):
                (wants / name).symlink_to(root / name)
            environment = {**os.environ, 'LC_ALL': 'C',
                           'SYSTEMD_UNIT_PATH': str(root)}

            def verify(text):
                unit.write_text(text)
                return subprocess.run(['systemd-analyze', '--user', 'verify', 'default.target'],
                                      env=environment, capture_output=True, text=True, timeout=20)

            broken = verify(source.replace('After=default.target nemoclaw-openshell-gateway.service',
                                            'After=nemoclaw-openshell-gateway.service'))
            self.assertIn('ordering cycle', broken.stderr)
            fixed = verify(source)
            self.assertNotIn('ordering cycle', fixed.stderr)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)


if __name__ == '__main__':
    unittest.main()
