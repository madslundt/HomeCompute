#!/usr/bin/env python3
"""Validate the fixed worker installer with temporary files and no real services."""
import importlib.util
import ast
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('video_api_setup', Path(__file__).resolve().parents[1] / 'scripts/setup-video-api.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source, self.root = self.base / 'source', self.base / 'installed'
        self.units, self.backups = self.base / 'units', self.base / 'backups'
        self.worker = self.base / 'worker'
        self.calls = []
        self.is_active = False
        for name, relative in SETUP.SOURCES.items():
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('NEW = True\n')
        def safe(path, *, directory=False):
            if any(item.is_symlink() for item in [*path.absolute().parents, path]):
                raise ValueError('Symlink path refused')
            if path.exists() and not (path.is_dir() if directory else path.is_file()):
                raise ValueError('Invalid deployment path')
        self.patches = [patch.object(SETUP, 'ROOT', self.root), patch.object(SETUP, 'UNITS', self.units),
                        patch.object(SETUP, 'BACKUPS', self.backups), patch.object(SETUP, 'WORKER_STATE', self.worker),
                        patch.object(SETUP, 'PRIVATE_STATE', self.base / 'private-worker'),
                        patch.object(SETUP.platform, 'system', return_value='Linux'),
                        patch.object(SETUP.socket, 'gethostname', return_value='home-core'),
                        patch.object(SETUP.os, 'geteuid', return_value=0), patch.object(SETUP.os, 'fchown'),
                        patch.object(SETUP, 'safe', side_effect=safe),
                        patch.object(SETUP, 'service_active', side_effect=lambda: self.is_active),
                        patch.object(SETUP, 'control', side_effect=self.control),
                        patch.object(SETUP, 'verify_unit', side_effect=lambda: self.calls.append('verify'))]
        for item in self.patches:
            item.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def control(self, action):
        self.calls.append(action)
        if action == 'restart':
            self.is_active = True
        elif action == 'stop':
            self.is_active = False

    def queue(self, statuses):
        self.worker.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.worker / 'jobs.sqlite3')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (status TEXT NOT NULL)')
            db.execute('DELETE FROM jobs')
            db.executemany('INSERT INTO jobs VALUES (?)', [(status,) for status in statuses])
            db.commit()

    def existing(self):
        self.root.mkdir()
        self.units.mkdir()
        for name, path in SETUP.targets().items():
            path.write_bytes(SETUP.UNIT.encode() if name == 'unit' else b'OLD = True\n')
        self.queue(['completed'])
        self.is_active = True
        return {name: path.read_bytes() for name, path in SETUP.targets().items()}

    def install(self):
        plan = SETUP.run(self.source)
        return SETUP.run(self.source, do_apply=True, expected_plan=plan['plan_sha256'])

    def test_default_plan_is_read_only_and_reviewed_hash_covers_two_sources_unit_and_boot_state(self):
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*'))
        plan = SETUP.run(self.source)
        self.assertTrue(plan['read_only'])
        self.assertEqual(set(plan['source_sha256']), {'video_api_service.py', 'homecompute_videos.py', 'unit'})
        self.assertEqual(plan['bind'], '172.28.200.1:18194')
        self.assertEqual(plan['allowed_peer'], '172.28.200.2')
        self.assertEqual(self.calls, [])
        self.assertEqual(before, sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*')))

    def test_first_install_publishes_private_backups_restricted_unit_and_only_one_service_restart(self):
        result = self.install()
        self.assertTrue(result['applied'])
        self.assertEqual(self.calls, ['verify', 'daemon-reload', 'restart'])
        self.assertTrue(SETUP.boot_link().is_symlink())
        self.assertEqual(SETUP.boot_link().resolve(), self.units / SETUP.UNIT_NAME)
        for path in SETUP.targets().values():
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        backup = Path(result['backup'])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual((backup / 'manifest.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads((backup / 'manifest.json').read_text())['was_active'])
        for setting in ('DynamicUser=yes', 'StateDirectoryMode=0700', 'NoNewPrivileges=yes',
                        'ProtectSystem=strict', 'ProtectHome=yes', 'CapabilityBoundingSet=',
                        'RestrictAddressFamilies=AF_INET AF_UNIX', 'IPAddressAllow=172.28.200.2/32'):
            self.assertIn(setting, (self.units / SETUP.UNIT_NAME).read_text())

    def test_repeat_apply_is_idempotent(self):
        self.install()
        # Production boot links are root-owned; this fixture is developer-owned.
        with patch.object(SETUP, 'enabled', return_value=True):
            self.calls.clear()
            result = self.install()
        self.assertFalse(result['changed'])
        self.assertEqual(self.calls, [])

    def test_reviewed_plan_refuses_source_or_baseline_drift(self):
        plan = SETUP.run(self.source)
        (self.source / SETUP.SOURCES['video_api_service.py']).write_text('CHANGED = True\n')
        with self.assertRaisesRegex(ValueError, 'matching --expected-plan'):
            SETUP.run(self.source, do_apply=True, expected_plan=plan['plan_sha256'])
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.calls, [])

    def test_current_or_queued_jobs_refuse_before_publication_or_restart(self):
        old = self.existing()
        for status in ('queued', 'in_progress'):
            self.queue([status])
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'jobs are queued or in progress'):
                self.install()
            self.assertEqual({name: path.read_bytes() for name, path in SETUP.targets().items()}, old)
            self.assertFalse(self.backups.exists())
            self.assertEqual(self.calls, [])

    def test_new_job_before_restart_rolls_back_without_interrupting_the_running_service(self):
        old = self.existing()
        with patch.object(SETUP, 'active_jobs', side_effect=[0, 1]), self.assertRaisesRegex(RuntimeError, 'previous files and activation restored'):
            self.install()
        self.assertEqual({name: path.read_bytes() for name, path in SETUP.targets().items()}, old)
        self.assertNotIn('restart', self.calls)
        self.assertNotIn('stop', self.calls)
        self.assertTrue(self.is_active)

    def test_partial_publication_failure_restores_existing_files_without_restarting_them(self):
        old = self.existing()
        original_publish, failed = SETUP.publish, False
        def publish(path, data, mode=0o644):
            nonlocal failed
            if path == self.root / 'homecompute_videos.py' and not failed:
                failed = True
                raise OSError('disk write failed')
            return original_publish(path, data, mode)
        with patch.object(SETUP, 'publish', side_effect=publish), self.assertRaisesRegex(RuntimeError, 'OSError'):
            self.install()
        self.assertEqual({name: path.read_bytes() for name, path in SETUP.targets().items()}, old)
        self.assertNotIn('restart', self.calls)
        self.assertTrue(list(self.backups.glob('*/manifest.json')))

    def test_restart_failure_restores_old_files_and_restarts_only_the_old_worker(self):
        old = self.existing()
        count = 0
        def control(action):
            nonlocal count
            self.calls.append(action)
            if action == 'restart':
                count += 1
                if count == 1:
                    raise OSError('restart failed')
        with patch.object(SETUP, 'control', side_effect=control), patch.object(SETUP, 'enabled', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'OSError'):
                self.install()
        self.assertEqual(count, 2)
        self.assertEqual({name: path.read_bytes() for name, path in SETUP.targets().items()}, old)

    def test_failed_first_restart_removes_new_files_boot_link_and_stops_only_new_service(self):
        def control(action):
            self.calls.append(action)
            if action == 'restart':
                raise OSError('new worker did not start')
        with (patch.object(SETUP, 'control', side_effect=control),
              patch.object(SETUP, 'enabled', side_effect=lambda: SETUP.boot_link().is_symlink()),
              self.assertRaisesRegex(RuntimeError, 'previous files and activation restored')):
            self.install()
        self.assertFalse(SETUP.boot_link().is_symlink())
        self.assertTrue(all(not path.exists() for path in SETUP.targets().values()))
        self.assertEqual(self.calls.count('restart'), 1)
        self.assertEqual(self.calls.count('stop'), 1)

    def test_unmanaged_unit_symlinks_missing_source_and_wrong_host_have_no_mutations(self):
        with patch.object(SETUP.socket, 'gethostname', return_value='home-spark'), self.assertRaisesRegex(ValueError, 'home-core'):
            SETUP.run(self.source, do_apply=True)
        self.units.mkdir()
        unit = self.units / SETUP.UNIT_NAME
        unit.write_text('[Service]\nExecStart=/unmanaged\n')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            SETUP.run(self.source)
        unit.unlink()
        outside = self.base / 'outside'
        outside.write_text('do not change')
        unit.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            SETUP.run(self.source)
        self.assertEqual(outside.read_text(), 'do not change')
        unit.unlink()
        (self.source / SETUP.SOURCES['video_api_service.py']).unlink()
        with self.assertRaises(FileNotFoundError):
            SETUP.run(self.source)
        self.assertEqual(self.calls, [])

    def test_job_inspection_is_read_only_and_unknown_or_symlinked_database_refuses(self):
        self.queue(['completed', 'failed', 'queued', 'in_progress'])
        database = self.worker / 'jobs.sqlite3'
        before = database.read_bytes()
        self.assertEqual(SETUP.active_jobs(), 2)
        self.assertEqual(database.read_bytes(), before)
        database.unlink()
        with self.assertRaisesRegex(ValueError, 'Cannot inspect'):
            SETUP.active_jobs(required=True)
        outside = self.base / 'outside.sqlite3'
        outside.write_bytes(b'outside')
        database.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Untrusted'):
            SETUP.active_jobs()
        self.assertEqual(outside.read_bytes(), b'outside')


class FixedCommandTest(unittest.TestCase):
    def test_actual_runtime_sources_compile_and_match_the_reviewed_service_contract(self):
        repo = Path(__file__).resolve().parents[1]
        for relative in SETUP.SOURCES.values():
            content = (repo / relative).read_bytes()
            compile(ast.parse(content, filename=relative), relative, 'exec')
        tree = ast.parse((repo / SETUP.SOURCES['video_api_service.py']).read_text())
        flags = [node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == 'add_argument' and node.args and isinstance(node.args[0], ast.Constant)]
        self.assertIn('--state', flags)
        self.assertIn(' --state /var/lib/homecompute-video-api', SETUP.UNIT)
        constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id in {'BIND', 'ALLOWED_PEER', 'ENGINE_BASE'}}
        self.assertEqual(constants, {'BIND': ('172.28.200.1', 18194),
                                    'ALLOWED_PEER': '172.28.200.2', 'ENGINE_BASE': 'http://172.28.200.2:8188'})

    def test_service_commands_cannot_target_other_units(self):
        with patch.object(SETUP.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            SETUP.control('restart')
            self.assertEqual(run.call_args.args[0], [SETUP.BIN + '/systemctl', 'restart', 'homecompute-video-api.service'])
            with self.assertRaises(ValueError):
                SETUP.control('restart caddy.service')


if __name__ == '__main__':
    unittest.main()
