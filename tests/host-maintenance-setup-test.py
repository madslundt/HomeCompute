import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_setup', ROOT / 'scripts/setup-host-maintenance.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class HostMaintenanceSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o700)
        for name in setup.FILES:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{}\n' if name.endswith('.json') else 'VALUE = 1\n')
            target.chmod(0o644)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_bundle_hash_binds_all_code_and_tracking(self):
        digest = setup.bundle(self.source)[1]
        self.assertEqual(len(setup.FILES), 5)
        for name in setup.FILES:
            target = self.source / name
            before = target.read_bytes()
            target.write_bytes(before + b'\n')
            self.assertNotEqual(setup.bundle(self.source)[1], digest)
            target.write_bytes(before)
        self.assertFalse(any('env' in name or 'credentials' in name for name in setup.FILES))

    def test_source_symlinks_hardlinks_writable_files_and_oversize_refused(self):
        target = self.source / setup.FILES[0]
        target.unlink(); target.symlink_to(self.source / setup.FILES[1])
        with self.assertRaises(ValueError):
            setup.bundle(self.source)
        target.unlink(); target.write_text('VALUE = 1\n')
        os.link(target, self.source / 'link')
        with self.assertRaises(ValueError):
            setup.bundle(self.source)
        (self.source / 'link').unlink()
        target.chmod(0o666)
        with self.assertRaises(ValueError):
            setup.bundle(self.source)
        target.chmod(0o644); target.write_bytes(b' ' * 524289)
        with self.assertRaises(ValueError):
            setup.bundle(self.source)

    def test_installed_paths_check_all_ancestors_and_never_resolve_symlinks(self):
        parent = self.root / 'writable'
        parent.mkdir(); parent.chmod(0o777)
        target = parent / 'runtime.py'
        target.write_text('VALUE = 1\n')
        real_lstat = Path.lstat
        def privileged_lstat(path):
            info = list(real_lstat(path)); info[4] = 0
            if path != parent:
                info[0] &= ~0o022
            return os.stat_result(info)
        with patch.object(Path, 'lstat', privileged_lstat):
            with self.assertRaises(ValueError):
                setup.safe(target)
        target.unlink(); target.symlink_to(self.source / setup.FILES[0])
        with self.assertRaises(ValueError):
            setup.safe(target, root=False)

    def test_dry_run_needs_no_root_and_starts_nothing(self):
        with patch.object(setup, 'install') as install, patch.object(setup, 'run') as run, \
             patch.object(setup.subprocess, 'run') as command, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.main(['--source', str(self.source), '--host', 'home-spark']), 0)
        self.assertFalse(json.loads(output.getvalue())['installed'])
        install.assert_not_called(); run.assert_not_called(); command.assert_not_called()

    def test_install_host_root_and_exact_hash_are_mandatory(self):
        for uid, actual, selected in ((1000, 'home-core', 'home-core'), (0, 'home-core', 'home-spark'),
                                       (0, 'other', 'home-core')):
            with patch.object(setup.os, 'geteuid', return_value=uid), \
                 patch.object(setup.socket, 'gethostname', return_value=actual):
                with self.assertRaises(ValueError):
                    setup.require_host(selected)
        with patch.object(setup, 'require_host'), patch.object(setup, 'directory') as directory:
            with self.assertRaises(ValueError):
                setup.install(self.source, '0' * 64, 'home-core')
            directory.assert_not_called()

    def test_daily_bounded_timer_uses_fixed_host_python_and_no_retry(self):
        self.assertIn('OnCalendar=*-*-* 05:55:00 Europe/Copenhagen\n', setup.timer())
        self.assertIn('Persistent=true\n', setup.timer())
        for host, python in (('home-core', '/run/current-system/sw/bin/python3'), ('home-spark', '/usr/bin/python3')):
            unit = setup.service(host)
            self.assertIn(f'ExecStart={python} -I {setup.DEST}/setup-host-maintenance.py --run --host {host}\n', unit)
            self.assertIn('Type=oneshot\n', unit)
            self.assertIn('User=root\n', unit)
            self.assertIn('TimeoutStartSec=7200\n', unit)
            self.assertIn('Restart=no\n', unit)
        self.assertEqual(setup.layout('home-core')[0], Path('/etc/systemd/system.attached'))
        self.assertEqual(setup.layout('home-spark')[0], Path('/etc/systemd/system'))

    def test_installation_lock_excludes_bundle_replacement_during_execution(self):
        state = self.root / 'lock-state'
        state.mkdir(mode=0o700)
        real_fstat = os.fstat
        def privileged_fstat(fd):
            info = list(real_fstat(fd)); info[4] = 0
            return os.stat_result(info)
        with patch.object(setup, 'STATE', state), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)), \
             patch.object(setup.os, 'fstat', privileged_fstat):
            with setup.installation_lock():
                with self.assertRaises(BlockingIOError):
                    with setup.installation_lock(exclusive=True):
                        self.fail('replacement must not overlap runtime')
            with setup.installation_lock(exclusive=True):
                with self.assertRaises(BlockingIOError):
                    with setup.installation_lock():
                        self.fail('runtime must not import a partially replaced bundle')

    def test_run_requires_fixed_installed_runtime_and_matching_host(self):
        runner = Mock(return_value={'summary': {'current': 1}})
        with patch.object(setup, 'require_host') as required, patch.object(setup, 'safe') as safe, \
             patch.object(setup, 'load_runtime', return_value=SimpleNamespace(run=runner)), \
             patch.object(setup, 'installation_lock', return_value=contextlib.nullcontext()):
            result = setup.run('home-spark')
        required.assert_called_once_with('home-spark')
        runner.assert_called_once_with('home-spark')
        self.assertEqual(result['summary']['current'], 1)
        self.assertEqual(safe.call_count, 2)
        with patch.object(setup, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--run', '--source', str(self.source)]), 2)
            self.assertEqual(setup.main(['--run', '--bundle-sha256', '0' * 64]), 2)
            run.assert_not_called()

    def test_active_runner_and_unmanaged_timer_are_refused(self):
        with patch.object(setup, 'require_host'), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(setup, 'directory') as directory:
            with self.assertRaises(ValueError):
                setup.install(self.source, setup.bundle(self.source)[1], 'home-core')
            directory.assert_not_called()
        link = self.root / 'wrong.timer'
        link.symlink_to('/unreviewed/timer')
        with self.assertRaises(ValueError):
            setup.dependency(link, self.root / 'right.timer')

    def test_install_backs_up_code_tracking_units_preserves_state_and_starts_only_timer(self):
        dest, state, public, units = (self.root / name for name in ('dest', 'state', 'public', 'units'))
        for path in (dest, state, public, units):
            path.mkdir()
        for name in setup.FILES:
            (dest / Path(name).name).write_text('previous reviewed bytes')
        marker = state / 'attempts.json'
        marker.write_text('{"failed":true}')
        report = public / 'report.json'
        report.write_text('{"generation":42}')
        old_unit = units / (setup.NAME + '.service')
        old_unit.write_text('previous reviewed unit')
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 3 if 'is-active' in argv else 0)
        with patch.object(setup, 'DEST', dest), patch.object(setup, 'STATE', state), \
             patch.object(setup, 'PUBLIC', public), patch.object(setup, 'require_host'), \
             patch.object(setup, 'layout', return_value=(units, '/usr/bin/python3')), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)), \
             patch.object(setup.subprocess, 'run', side_effect=command), \
             patch.object(setup, 'installation_lock', return_value=contextlib.nullcontext()) as locked:
            result = setup.install(self.source, setup.bundle(self.source)[1], 'home-spark')
        locked.assert_called_once_with(exclusive=True)
        self.assertEqual(marker.read_text(), '{"failed":true}')
        self.assertEqual(report.read_text(), '{"generation":42}')
        backup = Path(result['backup'])
        for name in setup.FILES:
            self.assertEqual((backup / Path(name).name).read_text(), 'previous reviewed bytes')
            self.assertEqual((dest / Path(name).name).read_bytes(), (self.source / name).read_bytes())
        self.assertEqual((backup / old_unit.name).read_text(), 'previous reviewed unit')
        self.assertEqual(result['updates_run'], 0)
        self.assertTrue(any(c[1:] == ['restart', setup.NAME + '.timer'] for c in commands))
        self.assertFalse(any('start' in c or ('restart' in c and c[-1].endswith('.service')) for c in commands))


if __name__ == '__main__':
    unittest.main()
