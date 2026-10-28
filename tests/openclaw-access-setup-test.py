#!/usr/bin/env python3
"""Validate the real Compose rendering and isolated attached-unit boot links."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('access_setup_fixture', ROOT / 'scripts/setup-openclaw-access.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)
IMAGE = 'caddy:2.11.7-alpine@sha256:' + '0' * 64


class SetupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose renderer required')
    def test_real_compose_renderer_keeps_one_complete_tmpfs_and_only_certificate_config_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'compose.yaml'
            config.write_text(SETUP.compose(IMAGE))
            result = subprocess.run(['docker', 'compose', '-f', str(config), 'config', '--format', 'json'],
                                    capture_output=True, text=True, check=True)
            proxy = json.loads(result.stdout)['services']['proxy']
            self.assertEqual(len(proxy['tmpfs']), 1, 'Comma-separated tmpfs options must remain one YAML string')
            self.assertTrue(proxy['tmpfs'][0].startswith('/tmp:'))
            self.assertIn('nodev', proxy['tmpfs'][0])
            self.assertTrue(proxy['read_only'])
            self.assertEqual(proxy['cap_drop'], ['ALL'])
            self.assertEqual(proxy['logging']['driver'], 'none')
            self.assertEqual({value['target'] for value in proxy['volumes']},
                             {'/etc/caddy/Caddyfile', '/certificates', '/lan-certificates'})
            self.assertTrue(all(value['read_only'] for value in proxy['volumes']))

    def test_public_ips_unpinned_images_and_command_overrides_are_refused(self):
        for address in ['192.168.1.1', '0.0.0.0', '100.1.1.1', '8.8.8.8']:
            with self.assertRaises(ValueError):
                SETUP.caddyfile(address)
        for image in ['caddy:latest', IMAGE + '\nextra: value', 'untrusted/caddy@sha256:' + 'a' * 64]:
            with self.assertRaises(ValueError):
                SETUP.compose(image)
        with self.assertRaises(ValueError):
            SETUP.command(['python3', '-c', 'anything'])
        for network in ['0.0.0.0/0', '172.30.0.0/16', '8.8.8.0/24', '::/0']:
            with self.assertRaises(ValueError):
                SETUP.service_unit(network)

    def test_boot_dependency_uses_attached_namespace_and_refuses_foreign_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            units = Path(temporary).resolve()
            name = 'homecompute-openclaw-access.service'
            (units / name).write_text('[Service]\n')
            with patch.object(SETUP, 'UNITS', units):
                SETUP.boot_dependency(name)
                SETUP.boot_dependency(name)
                link = units / 'multi-user.target.wants' / name
                self.assertEqual(link.resolve(), units / name)
                link.unlink()
                link.symlink_to('/unrelated.service')
                with self.assertRaises(ValueError):
                    SETUP.boot_dependency(name)

    def test_request_service_blocks_vault_and_bootstrap_admin_state(self):
        unit = SETUP.service_unit()
        protected = next(line for line in unit.splitlines() if line.startswith('InaccessiblePaths='))
        self.assertIn('/var/lib/homecompute-credential-secrets', protected)
        self.assertIn('/srv/state/mattermost/private', protected)
        self.assertIn('IPAddressAllow=localhost', unit)
        self.assertNotIn('--operator-token-file', unit)
        self.assertIn('--owner-config', unit)
        self.assertIn('StandardError=journal', unit)
        self.assertIn('SyslogIdentifier=homecompute-openclaw-access', unit)

    def test_same_home_dns_name_is_reachable_through_approved_tailnet_subnet_route(self):
        calls = []
        def command(arguments, **_kwargs):
            calls.append(arguments)
            return SimpleNamespace(returncode=1)
        with patch.object(SETUP, 'settings', return_value={'MATTERMOST_TAILSCALE_ADDRESS': '100.110.248.102'}), \
                patch.object(SETUP, 'command', side_effect=command):
            SETUP.firewall()
        suffix = ['-d', '192.168.30.122/32', '-p', 'tcp', '--dport', '18814', '-m', 'comment',
                  '--comment', 'homecompute-openclaw-access-lan']
        prefix = ['iptables', '-w', '-I', 'INPUT', '1']
        self.assertIn(prefix + ['-i', 'tailscale0', '-s', '100.64.0.0/10', *suffix, '-j', 'ACCEPT'], calls)
        self.assertIn(prefix + [*suffix, '-j', 'DROP'], calls)
        allowed = [call for call in calls if call[:5] == prefix and call[-1] == 'ACCEPT'
                   and '192.168.30.122/32' in call]
        self.assertEqual(len(allowed), 4, 'Only household LAN, HA, loopback and Tailscale may enter')


if __name__ == '__main__':
    unittest.main()
