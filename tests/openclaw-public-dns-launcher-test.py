"""Exact namespace custody, drift refusal, and real Linux privilege dropping."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dns = load('public_dns_controller', ROOT / 'scripts/openclaw-public-dns.py')
setup = load('public_dns_setup', ROOT / 'scripts/setup-openclaw-public-dns.py')
IDENTITY = {'sandbox_id': setup.SANDBOX_ID, 'generation': setup.GENERATION, 'module_sha256': 'b' * 64}


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / 'registry.json'
        self.entry = {'name': 'agent-openclaw', 'agent': 'openclaw', 'gatewayName': 'nemoclaw-9123',
                      'model': 'automation-moe', 'lifecycleGeneration': IDENTITY['generation'],
                      'workload': {'reference': 'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@' + dns.IMAGE}}
        self.write_registry()
        self.labels = {'openshell.ai/sandbox-id': IDENTITY['sandbox_id'], 'openshell.ai/sandbox-name': 'agent-openclaw',
                       'openshell.ai/sandbox-namespace': dns.NAMESPACE, 'openshell.ai/sandbox-workspace': 'default',
                       'openshell.ai/managed-by': 'openshell', 'io.nvidia.nemoclaw.agent': 'openclaw'}
        self.records = ['a' * 64, dns.IMAGE, {'Running': True, 'Pid': 50}, self.labels,
                        [{'Destination': '/sandbox/.openclaw', 'Name': 'nemoclaw-openclaw-state-v1-agent-openclaw'}]]
        self.commands = []
        self.top = b'PID UID COMMAND\n50 0 openshell-sandb\n100 998 openclaw-gatewa\n'
        self.env = ('OPENCLAW_EXPECTED_SANDBOX_ID=' + IDENTITY['sandbox_id'] + '\n'
                    'OPENCLAW_EXPECTED_GENERATION=' + IDENTITY['generation'] + '\n').encode()
        self.patches = [patch.object(dns, 'REGISTRY', self.registry),
                        patch.object(dns, 'trusted_file', return_value=self.env),
                        patch.object(dns, 'SUPERVISOR_IDENTITY', self.root / 'supervisor.env'),
                        patch.object(dns, 'process_identity', return_value=(99, 200, 300, 400)),
                        patch.object(dns, 'command', side_effect=self.command),
                        patch.object(Path, 'read_text', return_value='head\n0: 00000000:4967 x 0A\n1: 0100007F:4970 x 0A\n')]
        for item in self.patches: item.start()
        dns.SUPERVISOR_IDENTITY.write_bytes(self.env)

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temporary.cleanup()

    def write_registry(self):
        self.registry.write_text(json.dumps({'sandboxes': {'agent-openclaw': self.entry}}))

    def command(self, args):
        self.commands.append(args)
        if args[0] == 'image':
            return (json.dumps(dns.IMAGE) + '\n' + json.dumps(['ghcr.io/nvidia/nemoclaw/openclaw-sandbox@' + dns.IMAGE]) + '\n').encode()
        return (('\n'.join(json.dumps(row) for row in self.records) + '\n').encode()
                if args[0] == 'inspect' else self.top)

    def test_exact_current_namespace_observed_without_environment_or_arbitrary_command(self):
        observed = dns.observe(IDENTITY)
        self.assertEqual(observed, dns.Snapshot('a' * 64, 50, 100, (99, 200, 300, 400)))
        self.assertTrue(all('openshell-default--agent-openclaw-' + setup.SANDBOX_ID in args for args in self.commands if args[0] != 'image'))
        template = self.commands[0][2]
        self.assertNotIn('Env', template)
        self.assertNotIn('exec', [args[0] for args in self.commands])

    def test_uuid_image_namespace_volume_and_generation_drift_refused(self):
        for slot, value in [(1, 'sha256:' + 'f' * 64), (3, {**self.labels, 'openshell.ai/sandbox-id': 'other'}),
                            (3, {**self.labels, 'openshell.ai/sandbox-namespace': 'other'}), (4, [])]:
            before = self.records[slot]
            self.records[slot] = value
            with self.assertRaises(dns.Refused): dns.observe(IDENTITY)
            self.records[slot] = before
        self.entry['lifecycleGeneration'] = 'other'
        self.write_registry()
        with self.assertRaises(dns.Refused): dns.observe(IDENTITY)

    def test_ambiguous_gateway_or_missing_auth_relay_refused(self):
        self.top += b'101 998 openclaw-gatewa\n'
        with self.assertRaises(dns.Refused): dns.observe(IDENTITY)
        self.top = b'PID UID COMMAND\n100 998 openclaw-gatewa\n'
        with patch.object(Path, 'read_text', return_value='head\n0: 00000000:4967 x 0A\n'):
            with self.assertRaises(dns.Refused): dns.observe(IDENTITY)

    def test_manifest_allows_only_canonical_operator_identity_and_digest(self):
        for value in ({**IDENTITY, 'url': 'http://elsewhere'}, {**IDENTITY, 'generation': 'bad'},
                      {**IDENTITY, 'module_sha256': '../module'}):
            with patch.object(dns, 'trusted_file', return_value=json.dumps(value).encode()):
                with self.assertRaises((dns.Refused, ValueError)): dns.manifest()

    def test_metadata_oversize_symlinks_and_fifo_never_authorize_or_block(self):
        path = self.root / 'metadata'
        path.write_bytes(b'x' * 17)
        with self.assertRaises(dns.Refused): dns.bounded_observation(path, 16)
        path.unlink(); path.symlink_to(self.registry)
        with self.assertRaises(OSError): dns.bounded_observation(path, 16)
        path.unlink(); os.mkfifo(path)
        with self.assertRaises(dns.Refused): dns.bounded_observation(path, 16)

    def test_prelaunch_pid_or_netns_drift_never_forks(self):
        expected = dns.Snapshot('a' * 64, 50, 100, (99, 200, 300, 400))
        with patch.object(dns.sys, 'platform', 'linux'), patch.object(dns.os, 'geteuid', return_value=0), \
             patch.object(dns.socket, 'gethostname', return_value='agents'), \
             patch.object(dns, 'manifest', return_value=IDENTITY), \
             patch.object(dns, 'observe', side_effect=[expected, replace(expected, gateway_pid=101)]), \
             patch.object(dns.os, 'pidfd_open', return_value=10, create=True), \
             patch.object(dns.select, 'poll', return_value=Mock(poll=Mock(return_value=[]))), \
             patch.object(dns.os, 'open', side_effect=[11, 12]), patch.object(dns.os, 'close'), \
             patch.object(dns.os, 'fstat', side_effect=[Mock(st_dev=300, st_ino=400), Mock(st_ino=200)]), \
             patch.object(dns.os, 'fork') as fork:
            with self.assertRaises(dns.Refused): dns.run()
            fork.assert_not_called()

    def test_runtime_drift_terminates_only_own_child(self):
        expected = dns.Snapshot('a' * 64, 50, 100, (99, 200, 300, 400))
        with patch.object(dns.sys, 'platform', 'linux'), patch.object(dns.os, 'geteuid', return_value=0), \
             patch.object(dns.socket, 'gethostname', return_value='agents'), \
             patch.object(dns, 'manifest', return_value=IDENTITY), \
             patch.object(dns, 'observe', side_effect=[expected, expected, replace(expected, process=(99, 201, 300, 400))]), \
             patch.object(dns.os, 'pidfd_open', return_value=10, create=True), \
             patch.object(dns.select, 'poll', return_value=Mock(poll=Mock(return_value=[]))), \
             patch.object(dns.os, 'open', side_effect=[11, 12]), patch.object(dns.os, 'close'), \
             patch.object(dns.os, 'fstat', side_effect=[Mock(st_dev=300, st_ino=400), Mock(st_ino=200)]), \
             patch.object(dns.os, 'fork', return_value=77), patch.object(dns.os, 'waitpid', return_value=(0, 0)), \
             patch.object(dns.signal, 'signal'), patch.object(dns, 'terminate') as terminate, patch('builtins.print'):
            with self.assertRaises(dns.Refused): dns.run()
            terminate.assert_called_once_with(77)


class InstallerTest(unittest.TestCase):
    def test_fixed_staging_idempotent_and_backs_up_without_launch_or_dns_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'config'
            unit = Path(temporary) / 'unit'
            with patch.object(setup, 'DIRECTORY', directory), patch.object(setup, 'UNIT', unit), \
                 patch.object(setup, 'protected'), patch.object(setup.os, 'geteuid', return_value=0), \
                 patch.object(setup.sys, 'platform', 'linux'), patch.object(setup.socket, 'gethostname', return_value='agents'):
                first = setup.install()
                setup.install()
                self.assertTrue(first['staged']); self.assertFalse(first['started'])
                self.assertFalse((directory / 'backups').exists())
                self.assertEqual(json.loads((directory / 'identity.json').read_bytes())['sandbox_id'], setup.SANDBOX_ID)
                unit.write_text('previous reviewed unit')
                setup.install()
                backups = list((directory / 'backups').iterdir())
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(), 'previous reviewed unit')
                self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_root_guard_and_no_target_cli(self):
        with patch.object(setup.os, 'geteuid', return_value=998):
            with self.assertRaises(ValueError): setup.install()
        with patch.object(setup.sys, 'argv', ['setup', '--pid', '123']), patch('sys.stderr'):
            with self.assertRaises(SystemExit): setup.main()
        with patch.object(dns.Path, 'lstat', return_value=Mock(st_uid=998, st_mode=stat.S_IFREG | 0o555)):
            with self.assertRaises(dns.Refused): dns.trusted_path(Path('/root/module'))

    def test_pinned_code_rejects_content_drift_or_writable_guard(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'guard.mjs'
            path.write_bytes(b'approved-code'); path.chmod(0o444)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(dns, 'trusted_path'), \
                 patch.object(dns.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o444)):
                self.assertEqual(dns.trusted_file(path, digest=digest), b'approved-code')
                path.chmod(0o644); path.write_bytes(b'drifted-code')
                with self.assertRaises(dns.Refused): dns.trusted_file(path, digest=digest)
            with patch.object(dns, 'trusted_path'), \
                 patch.object(dns.os, 'fstat', return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)):
                with self.assertRaises(dns.Refused): dns.trusted_file(path, digest=digest)

    def test_explicit_modes_survive_private_operator_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'controller.py'
            mask = os.umask(0o077)
            try:
                with patch.object(setup, 'protected'):
                    setup.write(path, b'fixed-controller', 0o555)
                self.assertEqual(path.stat().st_mode & 0o777, 0o555)
            finally:
                os.umask(mask)

    def test_persisted_unit_exposes_no_socket_docker_or_namespace_to_child(self):
        unit = (ROOT / 'deploy/openclaw/nemoclaw/homecompute-public-dns.service').read_text()
        for field in ('User=root', 'NoNewPrivileges=true', 'ProtectSystem=strict', 'ProtectHome=read-only',
                      'KillMode=control-group', 'RestrictNamespaces=net', 'ConditionPathExists=/etc/homecompute-public-dns/identity.json'):
            self.assertIn(field, unit)
        self.assertIn('AmbientCapabilities=CAP_SETUID\n', unit)
        self.assertEqual([line for line in unit.splitlines() if line.startswith('AmbientCapabilities=')], ['AmbientCapabilities=CAP_SETUID'])
        self.assertNotIn('docker exec', unit)

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'real Linux root privilege check')
    def test_real_linux_child_cannot_regain_root_or_read_root_secret(self):
        pid = os.fork()
        if pid == 0:
            try:
                dns.drop_privileges()
                try:
                    os.setuid(0)
                    os._exit(1)
                except PermissionError:
                    pass
                try:
                    fd = os.open('/etc/shadow', os.O_RDONLY)
                    os.close(fd)
                    os._exit(2)
                except PermissionError:
                    pass
                os._exit(0)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)


if __name__ == '__main__':
    unittest.main()
