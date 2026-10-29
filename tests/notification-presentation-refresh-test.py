"""Guarded renderer refresh tests; temporary files and fake systemd only."""
import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('presentation_refresh', ROOT / 'scripts/setup-notification-presentation.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'scripts').mkdir()
        self.live = self.root / 'live'
        self.live.mkdir()
        self.before = b"VALUE = 'before'\n"
        self.after = b"VALUE = 'after'\n"
        names = list(MOD.TARGETS)
        self.targets = {name: (str(self.live), hashlib.sha256(self.before).hexdigest()) for name in names}
        for name in names:
            (self.source / 'scripts' / name).write_bytes(self.before)
            (self.live / name).write_bytes(self.before)
        self.git('init')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', '.')
        self.git('commit', '-m', 'baseline')
        baseline = self.git('rev-parse', 'HEAD').decode().strip()
        for name in names:
            (self.source / 'scripts' / name).write_bytes(self.after)
        (self.source / 'scripts/setup-notification-presentation.py').write_bytes(Path(MOD.__file__).read_bytes())
        self.git('add', '.')
        self.git('commit', '-m', 'reviewed renderers and refresh tool')
        self.revision = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-ref', 'refs/remotes/origin/master', self.revision)
        self.states = {name: {'active': name in {MOD.ADAPTER, MOD.ACCESS}, 'unit_sha256': 'a' * 64}
                       for name in (*MOD.STOP_ORDER, MOD.CERTIFICATE, MOD.MACHINE)}
        self.commands = []
        self.patches = [patch.object(MOD, 'OWNER', os.getuid()), patch.object(MOD, 'BASELINE', baseline),
                        patch.object(MOD, 'TARGETS', self.targets), patch.object(MOD, 'BACKUPS', self.root / 'backups'),
                        patch.object(MOD, 'LOCK', self.root / 'refresh.lock'),
                        patch.object(MOD, 'service_state', side_effect=lambda name: dict(self.states[name])),
                        patch.object(MOD, 'control', side_effect=self.control),
                        patch.object(MOD, 'in_flight', return_value={'running_turns': 0}),
                        patch.object(MOD, 'preserved_artifacts', return_value={'fixture-monitoring': {'sha256': 'b' * 64}}),
                        patch.object(MOD.os, 'geteuid', return_value=0),
                        patch.object(MOD.socket, 'gethostname', return_value='home-core')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), *args], capture_output=True, check=True).stdout

    def control(self, action, name):
        self.commands.append((action, name))
        self.states[name]['active'] = action == 'start'

    def plan(self):
        return MOD.plan(self.source, self.revision)[0]

    def apply(self):
        return MOD.apply(self.source, self.revision, self.plan()['plan_sha256'])

    def test_plan_is_read_only_and_covers_only_four_fixed_modules(self):
        report = self.plan()
        self.assertEqual(len(report['modules']), 4)
        self.assertEqual(report['restart_only_previously_active'], [MOD.ACCESS, MOD.ADAPTER])
        self.assertEqual(report['declared_start_dependencies'], [{'unit': MOD.MACHINE, 'trigger': MOD.ADAPTER,
                          'invocation': 'read-only status projection', 'expected_final_active': False}])
        self.assertFalse((self.root / 'backups').exists())
        self.assertFalse((self.root / 'refresh.lock').exists())
        self.assertEqual(self.commands, [])
        self.assertTrue(all(path.read_bytes() == self.before for path in self.live.iterdir()))

    def test_unknown_live_drift_and_symlinks_are_refused_without_service_changes(self):
        path = next(self.live.iterdir())
        path.write_bytes(b'UNREVIEWED = True\n')
        with self.assertRaisesRegex(ValueError, 'Installed renderer differs'):
            self.plan()
        path.unlink()
        path.symlink_to(self.source / 'scripts' / path.name)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.plan()
        self.assertEqual(self.commands, [])

    def test_dirty_source_wrong_revision_tool_or_baseline_are_refused(self):
        (self.source / 'untracked').write_text('fixture')
        with self.assertRaisesRegex(ValueError, 'Clean checkout'):
            self.plan()
        (self.source / 'untracked').unlink()
        with self.assertRaisesRegex(ValueError, 'Full reviewed Git'):
            MOD.plan(self.source, 'HEAD')
        with patch.object(MOD, '__file__', str(self.source / 'scripts' / next(iter(self.targets)))):
            with self.assertRaisesRegex(ValueError, 'Refresh tool must match'):
                self.plan()
        changed = dict(self.targets)
        name = next(iter(changed))
        changed[name] = (str(self.live), '0' * 64)
        with patch.object(MOD, 'TARGETS', changed):
            with self.assertRaisesRegex(ValueError, 'Baseline Git source'):
                self.plan()

    def test_active_work_certificate_reload_and_changed_plan_are_refused(self):
        with patch.object(MOD, 'in_flight', return_value={'sending_notifications': 1}):
            with self.assertRaisesRegex(ValueError, 'In-flight work'):
                self.plan()
        self.states[MOD.CERTIFICATE]['active'] = True
        with self.assertRaisesRegex(ValueError, 'certificate reload'):
            self.plan()
        self.states[MOD.CERTIFICATE]['active'] = False
        report = self.plan()
        self.states[MOD.PROXY]['active'] = True
        with self.assertRaisesRegex(ValueError, 'Plan changed'):
            MOD.apply(self.source, self.revision, report['plan_sha256'])
        self.assertEqual(self.commands, [])

    def test_apply_has_verified_backups_preserves_modes_and_never_starts_inactive_units(self):
        private = self.live / 'openclaw_access_notifications.py'
        private.chmod(0o600)
        untouched = self.live / 'unrelated-monitoring.py'
        untouched.write_bytes(b'KEEP = True\n')
        report = self.apply()
        self.assertTrue(report['applied'])
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(untouched.read_bytes(), b'KEEP = True\n')
        backup = Path(report['backup_directory'])
        for name in self.targets:
            self.assertEqual((backup / name).read_bytes(), self.before)
            self.assertEqual((self.live / name).read_bytes(), self.after)
        self.assertEqual(json.loads((backup / 'manifest.json').read_bytes())['plan_sha256'], report['plan_sha256'])
        self.assertEqual(self.commands, [('stop', MOD.ADAPTER), ('stop', MOD.ACCESS), ('start', MOD.ACCESS), ('start', MOD.ADAPTER)])
        self.commands.clear()
        self.assertTrue(self.apply()['already_current'])
        self.assertEqual(self.commands, [])

    def test_failure_after_atomic_replace_rolls_back_all_touched_files_and_activity(self):
        original = MOD.atomic
        failed = False
        def fail_once(path, raw, mode, gid):
            nonlocal failed
            original(path, raw, mode, gid)
            if path.parent == self.live and raw == self.after and not failed:
                failed = True
                raise OSError('fixture post-replace failure')
        with patch.object(MOD, 'atomic', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'source and service activity restored'):
                self.apply()
        self.assertTrue(all((self.live / name).read_bytes() == self.before for name in self.targets))
        self.assertTrue(self.states[MOD.ADAPTER]['active'])
        self.assertTrue(self.states[MOD.ACCESS]['active'])
        self.assertFalse(self.states[MOD.PROXY]['active'])

    def test_restart_failure_rolls_back_and_restores_only_previous_active_services(self):
        failed = False
        def fail_once(action, name):
            nonlocal failed
            if (action, name) == ('start', MOD.ADAPTER) and not failed:
                failed = True
                raise OSError('fixture start failure')
            self.control(action, name)
        with patch.object(MOD, 'control', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'source and service activity restored'):
                self.apply()
        self.assertTrue(all((self.live / name).read_bytes() == self.before for name in self.targets))
        self.assertTrue(self.states[MOD.ADAPTER]['active'])
        self.assertTrue(self.states[MOD.ACCESS]['active'])

    def test_keyboard_interrupt_term_and_hangup_restore_sources_services_and_handlers(self):
        original = MOD.atomic
        for interrupt in (KeyboardInterrupt, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(interrupt=interrupt):
                handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
                fired = False
                def interrupt_once(path, raw, mode, gid):
                    nonlocal fired
                    original(path, raw, mode, gid)
                    if path.parent == self.live and raw == self.after and not fired:
                        fired = True
                        if interrupt is KeyboardInterrupt:
                            raise KeyboardInterrupt()
                        signal.raise_signal(interrupt)
                with patch.object(MOD, 'atomic', side_effect=interrupt_once):
                    with self.assertRaisesRegex(RuntimeError, 'source and service activity restored'):
                        self.apply()
                self.assertTrue(all((self.live / name).read_bytes() == self.before for name in self.targets))
                self.assertTrue(self.states[MOD.ADAPTER]['active'])
                self.assertTrue(self.states[MOD.ACCESS]['active'])
                self.assertFalse(self.states[MOD.PROXY]['active'])
                self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)

    def test_concurrent_edit_is_preserved_and_services_stay_paused_if_rollback_is_unsafe(self):
        original = MOD.atomic
        def concurrent(path, raw, mode, gid):
            original(path, raw, mode, gid)
            if path.parent == self.live and raw == self.after:
                path.write_bytes(b'CONCURRENT = True\n')
                raise OSError('fixture concurrent change')
        with patch.object(MOD, 'atomic', side_effect=concurrent):
            with self.assertRaisesRegex(RuntimeError, 'manual reconciliation required'):
                self.apply()
        self.assertTrue(any(path.read_bytes() == b'CONCURRENT = True\n' for path in self.live.iterdir()))
        self.assertFalse(self.states[MOD.ADAPTER]['active'])
        self.assertFalse(self.states[MOD.ACCESS]['active'])


if __name__ == '__main__':
    unittest.main()
