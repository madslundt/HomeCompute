#!/usr/bin/env python3
"""Scoped LiteLLM overlay and independent encrypted VM backup contract."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load('deploy-openbot-scoped-gateway')
backup = load('backup-openbot-agents-vm')


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'deploy/control-plane/litellm-config.yaml').read_text()
        self.baseline = self.source.replace(gateway.BLOCK, '').replace(
            'model_list:', '# retained live overlay\nmodel_list:')
        self.assertNotIn(gateway.ALIAS, self.baseline)

    def test_only_changes_the_exact_scoped_alias(self):
        desired = gateway.render(self.baseline, self.source)
        self.assertEqual(self.baseline, desired.replace(gateway.BLOCK, ''))
        self.assertEqual(desired, gateway.render(desired, self.source))
        with self.assertRaises(ValueError):
            gateway.render(self.baseline, self.source.replace(
                '[openbot-maintenance]', '[openbot-maintenance, all-harness-mcps]'))
        with self.assertRaises(ValueError):
            gateway.render(desired.replace('[openbot-maintenance]', '[all-harness-mcps]'), self.source)

    def test_apply_verify_and_rollback_bind_the_active_overlay(self):
        base, rev = 'a' * 40, 'b' * 40
        with tempfile.TemporaryDirectory() as work, ExitStack() as mocked:
            root = Path(work)
            state = root / 'openbot-scoped'
            before = root / 'litellm-retirement' / f'{base}-{"c" * 40}.yaml'
            before.parent.mkdir()
            before.write_text(self.baseline)
            after = state / f'{base}-{rev}.yaml'
            lock = root / 'deploy.lock'
            lock.touch()
            current = before

            def inputs(_):
                return (base, root / base, {'Id': 'container-id', 'Image': 'image-id'}, current,
                        hashlib.sha256(current.read_bytes()).hexdigest(), after)

            def change(release, previous, desired, image):
                nonlocal current
                self.assertEqual(current, previous)
                self.assertEqual('image-id', image)
                current = desired

            for item, name, value in (
                    (gateway.core, 'release', Mock()),
                    (gateway.core, 'protected', lambda path, **_: path.read_bytes()),
                    (gateway.core, 'LOCK', lock),
                    (gateway.gateway, 'STATE', root),
                    (gateway.gateway, 'change_config', change),
                    (gateway, 'STATE', state),
                    (gateway, 'inputs', inputs),
                    (gateway, 'verify_controller', Mock())):
                mocked.enter_context(patch.object(item, name, value))
            plan = gateway.run('plan', rev)
            with self.assertRaises(ValueError):
                gateway.run('apply', rev, '0' * 64)
            self.assertEqual(before, current)
            gateway.run('apply', rev, plan['plan_sha256'])
            self.assertEqual(after, current)
            self.assertEqual(self.baseline, current.read_text().replace(gateway.BLOCK, ''))
            self.assertTrue(gateway.run('verify', rev)['verified'])
            gateway.verify_controller.assert_called_once()
            gateway.run('rollback', rev)
            self.assertEqual(before, current)


class BackupTest(unittest.TestCase):
    def test_openbot_backup_validates_selected_store_when_default_store_is_retired(self):
        revision = 'a' * 40
        release = Path('/srv/homecompute/releases') / revision
        nas = Mock(REPOSITORY=Path('/mnt/homecompute-backups/restic-homecompute-agent-recovery'))
        with (patch.object(backup.core, 'protected', side_effect=[revision.encode(), b'reviewed NAS helper']),
              patch.object(Path, 'resolve', return_value=release),
              patch.object(backup.core, 'command', return_value=revision),
              patch.object(backup.subprocess, 'check_output', return_value=b'reviewed NAS helper'),
              patch.object(backup, 'load', return_value=nas)):
            self.assertIs(backup.live_nas(), nas)
        nas.root_directory.assert_called_once_with(backup.REPOSITORY)

    def run_backup_with_sync(self, timer_state, sync_states, *, upload_error=None, drain_stuck=False):
        with tempfile.TemporaryDirectory() as work, ExitStack() as mocked:
            state = Path(work) / 'state'
            calls = []
            timer = 'homecompute-assistant-context-sync.timer'
            service = 'homecompute-assistant-context-sync.service'
            current = timer_state
            pending = iter(sync_states)

            def inspect(*args):
                if args == ('systemctl', 'show', timer, '-p', 'ActiveState', '--value'):
                    return current
                self.assertEqual(args, ('systemctl', 'show', service, '-p', 'ActiveState', '-p', 'Job'))
                calls.append('inspect sync')
                return next(pending)

            def control(*args):
                nonlocal current
                self.assertEqual(args[0], 'systemctl')
                self.assertEqual(args[2:], (timer,))
                if args[1] == 'stop':
                    current = 'inactive'
                    calls.append('pause')
                else:
                    self.assertEqual(args[1], 'start')
                    self.assertFalse(any(state.glob('agents-vm-*')))
                    current = 'active'
                    calls.append('resume')

            def capture(*_):
                self.assertEqual(current, 'inactive')
                calls.append('capture')
                return {'captured_at': 'fixture'}

            def upload(_, *, repository):
                self.assertEqual(current, 'inactive')
                self.assertEqual(repository, backup.REPOSITORY)
                calls.append('upload')
                if upload_error:
                    raise upload_error
                return '{"message_type":"summary","snapshot_id":"fixture"}'

            def verify(*_):
                self.assertEqual(current, 'inactive')
                calls.append('verify')

            clock = Mock()
            clock.monotonic.return_value = 0
            if drain_stuck:
                clock.monotonic.side_effect = [0, 300]

            nas = Mock()
            nas.restic.side_effect = upload
            for target, name, value in (
                    (backup, 'STATE', state), (backup, 'RECEIPT', state / 'receipt.json'),
                    (backup, 'load', Mock()), (backup, 'capture', capture),
                    (backup, 'verify_snapshot', verify), (backup, 'command', control),
                    (backup, 'time', clock),
                    (backup.core, 'command', inspect)):
                mocked.enter_context(patch.object(target, name, value))

            if drain_stuck:
                with self.assertRaisesRegex(ValueError, 'sync did not finish'):
                    backup.backup(nas)
            elif upload_error:
                with self.assertRaises(type(upload_error)):
                    backup.backup(nas)
            else:
                backup.backup(nas)
            return calls

    def test_backup_drains_scan_and_holds_timer_through_snapshot_verification(self):
        calls = self.run_backup_with_sync('active', ['ActiveState=inactive\nJob=42',
                                                     'ActiveState=inactive\nJob='])
        self.assertEqual(calls, [
            'pause', 'inspect sync', 'inspect sync', 'capture', 'upload', 'verify', 'resume',
        ])

    def test_backup_restores_active_timer_on_upload_failure_or_interruption(self):
        for error in (ValueError('upload failed'), InterruptedError('backup interrupted')):
            with self.subTest(error=type(error)):
                calls = self.run_backup_with_sync(
                    'active', ['ActiveState=inactive\nJob='], upload_error=error)
                self.assertEqual(calls, ['pause', 'inspect sync', 'capture', 'upload', 'resume'])

    def test_backup_refuses_a_scan_that_does_not_finish_and_restores_the_timer(self):
        calls = self.run_backup_with_sync('active', ['ActiveState=activating\nJob=42'],
                                          drain_stuck=True)
        self.assertEqual(calls, ['pause', 'inspect sync', 'resume'])

    def test_backup_preserves_an_inactive_timer(self):
        calls = self.run_backup_with_sync('inactive', ['ActiveState=inactive\nJob='])
        self.assertEqual(calls, ['inspect sync', 'capture', 'upload', 'verify'])

    def test_only_a_fresh_snapshot_with_disk_seed_and_manifest_qualifies(self):
        directory = '/var/lib/homecompute/openbot-scoped/agents-vm-x1y2z3_4'
        metadata = {'captured_at': datetime.now(timezone.utc).isoformat(),
                    'disk_bytes': 1234, 'seed_sha256': 'a' * 64}
        snapshot = 'b' * 64
        value = {**metadata, 'snapshot': snapshot, 'directory': directory}
        entries = [{'path': directory + '/' + name, 'size': size, 'type': 'file'}
                   for name, size in (('manifest.json', 150), (backup.DISK.name, 1234), ('seed.iso', 45))]
        nas = Mock()
        nas.restic.side_effect = [
            json.dumps([{'id': snapshot, 'paths': [directory], 'tags': [backup.TAG]}]),
            '\n'.join(json.dumps(row) for row in entries), json.dumps(metadata),
        ]
        backup.verify_snapshot(nas, value)
        stale = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat()
        with self.assertRaisesRegex(ValueError, '36-hour'):
            backup.verify_snapshot(nas, dict(value, captured_at=stale))
        nas.restic.side_effect = [
            json.dumps([{'id': snapshot, 'paths': [directory], 'tags': [backup.TAG]}]),
            '\n'.join(json.dumps(row) for row in entries[:2]),
        ]
        with self.assertRaisesRegex(ValueError, 'complete agents VM bundle'):
            backup.verify_snapshot(nas, value)


if __name__ == '__main__':
    unittest.main()
