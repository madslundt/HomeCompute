#!/usr/bin/env python3
"""Published-source guards and narrow observability rollout/rollback behavior."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observability_setup', ROOT / 'scripts/setup-platform-observability.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ObservabilitySetupTest(unittest.TestCase):
    def test_creating_child_preserves_existing_private_parent_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / 'private'; parent.mkdir(mode=0o700)
            setup.directory(parent / 'child')
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual((parent / 'child').stat().st_mode & 0o777, 0o755)

    def test_bundle_is_finite_and_retains_live_workflow_health_dependency(self):
        files, digest = setup.bundle(ROOT)
        self.assertEqual(len(digest), 64)
        self.assertIn('scripts/n8n_observations.py', files)
        self.assertIn(b"result['n8n_workflow_health']", files['scripts/openclaw-machine-snapshot.py'])
        self.assertNotIn('scripts/openclaw-communication.py', files)
        self.assertNotIn('config/openclaw-team.json', files)

    def test_registry_updates_inventory_and_preserves_ha_admission(self):
        desired = (ROOT / 'config/system-monitoring.json').read_bytes()
        old = json.loads(desired)
        next(row for row in old['systems'] if row['id'] == 'home-assistant')['enabled'] = True
        old['systems'][0]['containers'] = old['systems'][0]['containers'][:6]
        result = json.loads(setup.merged_registry(desired, json.dumps(old).encode()))
        self.assertTrue(next(row for row in result['systems'] if row['id'] == 'home-assistant')['enabled'])
        self.assertGreater(len(next(row for row in result['systems'] if row['id'] == 'home-core')['containers']), 6)

    def test_mutating_registry_is_refused(self):
        value = json.loads((ROOT / 'config/system-monitoring.json').read_bytes())
        value['automatic_actions'] = True
        with self.assertRaises(ValueError): setup.registry(value)

    def test_attached_dropins_reset_exec_and_required_socket_constraints(self):
        values = setup.dropins()
        self.assertEqual(len(values), 2)
        for path, data in values.items():
            self.assertIn('/etc/systemd/system.attached/', str(path))
            self.assertIn(b'ExecStart=\nExecStart=', data)
            self.assertIn(b'AF_UNIX', data)
        health = next(data for path, data in values.items() if setup.HEALTH in str(path))
        self.assertIn(b'Environment=PATH=/run/current-system/sw/bin', health)
        self.assertIn(b'TimeoutStartSec=20s', health)
        self.assertIn(b'DOCKER_HOST=unix:///var/run/docker.sock', health)
        self.assertNotIn(b'10.77.10.10', health)

    def test_apply_rejects_unreviewed_hash_before_writes(self):
        with patch.object(setup, 'release') as release, patch.object(setup, 'directory') as directory:
            with self.assertRaisesRegex(ValueError, 'bundle hash'):
                setup.apply(ROOT, 'a' * 40, 'b' * 64)
            release.assert_not_called(); directory.assert_not_called()

    def test_release_rejects_mutable_workstation_source(self):
        with patch.object(setup, 'call') as call:
            with self.assertRaisesRegex(ValueError, 'release directory'):
                setup.release(ROOT, 'a' * 40, {})
            call.assert_not_called()

    def test_release_rejects_unpublished_file_change(self):
        revision = 'a' * 40; source = Path('/srv/homecompute/releases') / revision
        with patch.object(setup, 'protected'), \
                patch.object(setup, 'call', return_value=subprocess.CompletedProcess([], 0, revision + '\n', '')), \
                patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'committed', b'')):
            with self.assertRaisesRegex(ValueError, 'unpublished file changes'):
                setup.release(source, revision, {'scripts/platform-health.py': b'changed'})

    def lifecycle(self, root: Path, *, fail_snapshot: bool, degraded: bool = False):
        paths = {'DEST': root / 'install', 'STATE': root / 'state', 'ATTACHED': root / 'attached',
                 'PRIVATE_REGISTRY': root / 'registry.json', 'BUNDLED_REGISTRY': root / 'unused.json',
                 'PUBLIC_SNAPSHOT': root / 'snapshot.json', 'METRICS': root / 'metrics.prom', 'LOCK': root / 'deploy.lock'}
        original = json.loads((ROOT / 'config/system-monitoring.json').read_bytes())
        next(row for row in original['systems'] if row['id'] == 'home-assistant')['enabled'] = True
        paths['PRIVATE_REGISTRY'].write_text(json.dumps(original)); paths['PRIVATE_REGISTRY'].chmod(0o600)
        calls = []
        def call(arguments, *, check=True):
            calls.append(arguments)
            if arguments[:2] == ['systemctl', 'show']:
                name = arguments[2]
                script = 'platform-health.py' if name.startswith(setup.HEALTH) else 'openclaw-machine-snapshot.py'
                timeout = 20 if name.startswith(setup.HEALTH) else 12
                output = f"ExecStart={paths['DEST']}/current/scripts/{script}\nDropInPaths={paths['ATTACHED']}/{name}.d/{setup.DROP_NAME}\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nTimeoutStartUSec={timeout}s\nEnvironment=PATH=/run/current-system/sw/bin DOCKER_HOST=unix:///var/run/docker.sock\n"
                return subprocess.CompletedProcess(arguments, 0, output, '')
            if arguments[:2] == ['systemctl', 'start'] and arguments[2] == setup.HEALTH + '.service':
                paths['METRICS'].write_text('homecompute_platform_ready ' + ('0' if degraded else '1'))
                return subprocess.CompletedProcess(arguments, int(degraded), '', '')
            if arguments[:2] == ['systemctl', 'start'] and arguments[2] == setup.MACHINE + '.service':
                if fail_snapshot: raise subprocess.CalledProcessError(1, arguments)
                source = json.loads((ROOT / 'config/system-monitoring.json').read_bytes())
                names = next(row for row in source['systems'] if row['id'] == 'home-core')['containers']
                paths['PUBLIC_SNAPSHOT'].write_text(json.dumps({'containers': names}))
            return subprocess.CompletedProcess(arguments, 0, '', '')
        stack = ExitStack()
        for key, value in paths.items(): stack.enter_context(patch.object(setup, key, value))
        stack.enter_context(patch.object(setup, 'release'))
        stack.enter_context(patch.object(setup, 'protected'))
        stack.enter_context(patch.object(setup, 'call', side_effect=call))
        stack.enter_context(patch.object(setup.os, 'fchown'))
        return stack, paths, calls, paths['PRIVATE_REGISTRY'].read_bytes()

    def test_failed_rollout_restores_only_owned_registry_dropins_and_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stack, paths, calls, original = self.lifecycle(root, fail_snapshot=True)
            with stack:
                _, digest = setup.bundle(ROOT)
                with self.assertRaises(subprocess.CalledProcessError): setup.apply(ROOT, 'a' * 40, digest)
                self.assertEqual(paths['PRIVATE_REGISTRY'].read_bytes(), original)
                self.assertFalse((paths['DEST'] / 'current').exists())
                self.assertTrue(all(not path.exists() for path in setup.dropins()))
                self.assertIn(['systemctl', 'start', setup.HEALTH + '.timer', setup.MACHINE + '.timer'], calls)

    def test_real_dependency_failure_installs_truthful_monitoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            stack, paths, calls, original = self.lifecycle(Path(temporary), fail_snapshot=False, degraded=True)
            with stack:
                _, digest = setup.bundle(ROOT)
                result = setup.apply(ROOT, 'a' * 40, digest)
                self.assertFalse(result['readiness'])
                self.assertEqual(result['state'], 'applied')
                self.assertTrue(all(path.exists() for path in setup.dropins()))
                self.assertTrue((paths['STATE'] / 'receipt.json').exists())
                self.assertNotIn(['systemctl', 'restart', 'homecompute-control-plane-litellm-1'], calls)

    def test_retirement_refuses_unreconciled_base_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = (ROOT / 'config/system-monitoring.json').read_bytes()
            installed = root / 'install/current/config'; installed.mkdir(parents=True)
            (installed / 'system-monitoring.json').write_bytes(policy)
            registry = root / 'registry.json'; registry.write_bytes(policy)
            (root / 'receipt.json').write_text(json.dumps({'bundle_hash': 'a' * 64, 'registry': str(registry)}))
            base = root / 'base.service'; base.write_text('ExecStart=/old/unknown.py\n')
            with patch.object(setup, 'STATE', root), patch.object(setup, 'DEST', root / 'install'), patch.object(setup, 'protected'), \
                    patch.object(setup, 'LOCK', root / 'lock'), \
                    patch.object(setup, 'call', return_value=subprocess.CompletedProcess([], 0, str(base), '')):
                with self.assertRaisesRegex(ValueError, 'not reconciled'):
                    setup.retire('a' * 64)

    def test_explicit_rollback_restores_the_preinstallation_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            stack, paths, calls, original = self.lifecycle(Path(temporary), fail_snapshot=False)
            with stack:
                _, digest = setup.bundle(ROOT)
                setup.apply(ROOT, 'a' * 40, digest)
                result = setup.rollback(digest)
                self.assertEqual(result['state'], 'rolled-back')
                self.assertEqual(paths['PRIVATE_REGISTRY'].read_bytes(), original)
                self.assertTrue(all(not path.exists() for path in setup.dropins()))
                self.assertFalse((paths['DEST'] / 'current').exists())


if __name__ == '__main__':
    unittest.main()
