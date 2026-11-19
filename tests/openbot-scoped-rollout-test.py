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
