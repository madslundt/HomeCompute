#!/usr/bin/env python3
"""Protect unknown guest system ordering while installing the managed edge."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot', ROOT / 'scripts/install-hermes-system-boot.py')
boot = importlib.util.module_from_spec(spec); spec.loader.exec_module(boot)


class SystemOrderingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('systemctl'), 'requires systemctl')
    def test_enabled_dashboard_watcher_is_started_by_boot_and_owner_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            units = root / 'etc/systemd/user'
            units.mkdir(parents=True)
            name = 'homecompute-hermes-dashboard-watch.service'
            shutil.copyfile(ROOT / 'deploy/hermes/hermes-dashboard-watch.service.in', units / name)
            subprocess.run(
                ['systemctl', f'--root={root}', '--global', 'enable', name],
                check=True, capture_output=True,
            )
            for target in ('default.target', 'homecompute-hermes-personal.service'):
                self.assertTrue((units / f'{target}.wants' / name).is_symlink(), target)

    def test_idempotent_ordering_and_unknown_file_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = boot.install(root)
            self.assertIn('After=docker.service', path.read_text())
            initial = path.stat().st_mtime_ns
            native_stat = Path.stat
            def root_owned(candidate, *args, **kwargs):
                value = native_stat(candidate, *args, **kwargs)
                if candidate == path:
                    fields = list(value); fields[4] = 0
                    return os.stat_result(fields)
                return value
            with patch.object(Path, 'stat', root_owned):
                boot.install(root)
            self.assertEqual(path.stat().st_mtime_ns, initial)
            path.write_text('[Unit]\nAfter=unrelated.service\n')
            with self.assertRaisesRegex(ValueError, 'unknown system ordering'):
                boot.install(root)
            self.assertEqual(path.read_text(), '[Unit]\nAfter=unrelated.service\n')

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); parent = root / 'etc/systemd/system/user@1000.service.d'
            parent.mkdir(parents=True)
            external = root / 'unrelated'; external.write_text('preserve')
            (parent / '90-homecompute-docker-ordering.conf').symlink_to(external)
            with self.assertRaises(ValueError): boot.install(root)
            self.assertEqual(external.read_text(), 'preserve')


if __name__ == '__main__': unittest.main()
