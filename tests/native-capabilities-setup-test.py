"""Fixed native HTTPS relay staging and firewall isolation; no host mutations."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_setup', ROOT / 'scripts/setup-native-capabilities.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
ROOT_GUARD = setup.guard


class RelaySetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = []
        values = {'INSTALL': self.root / 'opt/native', 'TLS': self.root / 'state/native/tls',
                  'CA': self.root / 'ca', 'TOKENS': self.root / 'adapter/config/native-mcp-tokens.json',
                  'UNIT': self.root / 'units/homecompute-native-mcp-relay.service',
                  'ROOT_UID': os.getuid(), 'TLS_UID': os.getuid(), 'TLS_GID': os.getgid()}
        for name, value in values.items():
            self.start(patch.object(setup, name, value))
        self.start(patch.object(setup, 'guard'))
        self.start(patch.object(setup.os, 'chown'))
        self.start(patch.object(setup.os, 'fchown'))
        self.start(patch.object(setup.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        setup.CA.mkdir()
        (setup.CA / 'root.crt').write_text('public-test-root')
        (setup.CA / 'root.key').write_text('private-test-root')
        (setup.CA / 'root.key').chmod(0o600)
        setup.TOKENS.parent.mkdir(parents=True, mode=0o700)
        self.commands = []
        def call(args, check=True):
            self.commands.append(args)
            if args[0].endswith('/openssl') and '-out' in args:
                Path(args[args.index('-out') + 1]).write_text('synthetic-output')
            return subprocess.CompletedProcess(args, 0)
        self.start(patch.object(setup, 'call', side_effect=call))

    def start(self, patcher):
        self.patches.append(patcher); return patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches): patcher.stop()
        self.tmp.cleanup()

    def test_install_issues_leaf_and_preserves_credentials_on_repeat_without_starting(self):
        before_ca = {p.name: p.read_bytes() for p in setup.CA.iterdir()}
        first = setup.install()
        tokens = setup.TOKENS.read_bytes()
        key = (setup.TLS / 'server.key').read_bytes()
        second = setup.install()
        self.assertTrue(first['tls_issued']); self.assertFalse(second['tls_issued'])
        self.assertTrue(first['native_credentials_created']); self.assertFalse(second['native_credentials_created'])
        self.assertEqual(setup.TOKENS.read_bytes(), tokens)
        self.assertEqual((setup.TLS / 'server.key').read_bytes(), key)
        self.assertEqual({p.name: p.read_bytes() for p in setup.CA.iterdir()}, before_ca)
        self.assertNotIn(tokens.decode(), json.dumps(first))
        values = json.loads(tokens)
        self.assertEqual(set(values), {'openclaw', 'hermes'})
        self.assertNotEqual(values['openclaw'], values['hermes'])
        self.assertEqual(setup.TOKENS.stat().st_mode & 0o777, 0o600)
        self.assertEqual((setup.TLS / 'server.key').stat().st_mode & 0o777, 0o400)
        self.assertEqual(setup.TLS.stat().st_mode & 0o777, 0o750)
        self.assertFalse(any('systemctl' in ' '.join(args) or '/iptables' in args[0] for args in self.commands))
        self.assertFalse(any('run' in args for args in self.commands))
        signer = next(args for args in self.commands if '-CAkey' in args)
        self.assertIn('-set_serial', signer); self.assertNotIn('-CAcreateserial', signer)
        self.assertEqual(signer[signer.index('-days') + 1], '90')

    def test_symlink_and_malformed_token_targets_fail_closed(self):
        setup.TOKENS.symlink_to(setup.CA / 'root.key')
        with self.assertRaises(ValueError): setup.native_tokens()
        setup.TOKENS.unlink()
        setup.TOKENS.write_text(json.dumps({'openclaw': 'x' * 40, 'hermes': 'x' * 40}))
        setup.TOKENS.chmod(0o600)
        with self.assertRaises(ValueError): setup.native_tokens()
        setup.TOKENS.write_text(json.dumps({'openclaw': 'x' * 40, 'hermes': 'y' * 40, 'operator': 'z' * 40}))
        with self.assertRaises(ValueError): setup.native_tokens()
        unsafe = self.root / 'untrusted'
        unsafe.mkdir(); unsafe.chmod(0o777)
        with self.assertRaises(ValueError): setup.directory(unsafe)

    def test_existing_partial_certificate_requires_reconciliation_without_issuer_changes(self):
        setup.TLS.mkdir(parents=True)
        (setup.TLS / 'server.key').write_text('existing-key')
        (setup.TLS / 'server.key').chmod(0o400)
        with self.assertRaises(FileNotFoundError): setup.certificate()
        self.assertFalse(self.commands)
        self.assertEqual((setup.TLS / 'server.key').read_text(), 'existing-key')

    def test_fixed_caddy_boundary_rejects_other_methods_paths_queries_and_sources(self):
        config = setup.caddyfile()
        for value in ('bind 10.77.20.1', 'remote_ip 10.77.20.2', 'method POST', 'path /mcp',
                      'expression {http.request.orig_uri} == "/mcp"', 'reverse_proxy 127.0.0.1:18793',
                      'admin off', 'auto_https off', 'output discard', 'respond 404'):
            self.assertIn(value, config)
        for value in ('0.0.0.0', '/conversation', '/observations', '/approve', 'handle_path', '{$'):
            self.assertNotIn(value, config)

    def test_container_has_exact_image_and_no_broader_host_access(self):
        command = setup.container_command()
        for value in ('--pull=never', '--network=host', '--user', '1000:1000', '--read-only', '--cap-drop=ALL',
                      '--security-opt=no-new-privileges', '--cpus=0.25', '--memory=128m', '--pids-limit=32', setup.IMAGE):
            self.assertIn(value, command)
        mounts = [command[i + 1] for i, value in enumerate(command) if value == '--mount']
        self.assertEqual(len(mounts), 3)
        self.assertTrue(all(value.endswith(',readonly') for value in mounts))
        self.assertFalse(any('root.key' in value or 'native-mcp-tokens' in value for value in command))
        self.assertNotIn('--privileged', command); self.assertNotIn('--publish', command)

    def test_firewall_addition_and_removal_only_change_exact_owned_grant(self):
        with patch.object(setup, 'call', side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]) as call:
            setup.firewall(True)
            self.assertEqual(call.call_args_list[1].args[0][2:], ['-I', 'INPUT', '1', *setup.RULE[1:]])
        with patch.object(setup, 'call', side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]) as call:
            setup.firewall(False)
            self.assertEqual(call.call_args_list[1].args[0][2:], ['-D', *setup.RULE])
        with patch.object(setup, 'call', return_value=SimpleNamespace(returncode=0)) as call:
            setup.firewall(True)
            self.assertEqual(call.call_count, 1)
        self.assertIn('br-hc-agents', setup.RULE)
        self.assertIn('10.77.20.2/32', setup.RULE)
        self.assertIn('10.77.20.1/32', setup.RULE)
        self.assertIn('19445', setup.RULE)

    def test_run_failure_removes_grant_and_unit_stops_only_own_container(self):
        with patch.object(setup, 'check'), patch.object(setup, 'firewall') as firewall, \
             patch.object(setup.subprocess, 'run', side_effect=OSError('synthetic daemon unavailable')):
            with self.assertRaises(OSError): setup.run()
            self.assertEqual([call.args for call in firewall.call_args_list], [(True,), (False,)])
        with patch.object(setup, 'check'), \
             patch.object(setup, 'firewall', side_effect=[OSError('unknown grant receipt'), None]) as firewall:
            with self.assertRaises(OSError): setup.run()
            self.assertEqual([call.args for call in firewall.call_args_list], [(True,), (False,)])
        unit = setup.unit_text()
        self.assertIn('After=homecompute-agents-network.service docker.service', unit)
        self.assertIn('ExecStartPre=+', unit)
        self.assertIn('ExecStopPost=', unit)
        self.assertIn('close-firewall', unit)
        self.assertNotIn('docker stop ', unit)  # Exact named stop lives in fixed helper.

    def test_root_host_guard_and_cli_cannot_choose_endpoints_or_targets(self):
        for uid, host, platform in ((501, 'home-core', 'linux'), (0, 'home-spark', 'linux'), (0, 'home-core', 'darwin')):
            with patch.object(setup.os, 'geteuid', return_value=uid), \
                 patch.object(setup.socket, 'gethostname', return_value=host), patch.object(setup.sys, 'platform', platform):
                with self.assertRaises(ValueError): ROOT_GUARD()
        with patch.object(setup.sys, 'argv', ['setup-native-capabilities.py', 'install', '--upstream', 'http://evil']):
            with patch('sys.stderr'), self.assertRaises(SystemExit): setup.main()


if __name__ == '__main__':
    unittest.main()
