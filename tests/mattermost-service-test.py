#!/usr/bin/env python3
"""Exercise retained-credential safety and the isolated Mattermost lifecycle."""
import importlib.util
from contextlib import nullcontext
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mattermost_setup', ROOT / 'scripts/setup-mattermost.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MattermostServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS /var is a symlink. The deployment deliberately requires real paths.
        self.state = Path(self.temporary.name).resolve() / 'state'
        self.site = 'https://home-core.tail479ad.ts.net:9443'

    def settings(self):
        return {**setup.DEFAULTS, 'MATTERMOST_STATE_ROOT': str(self.state),
                'MATTERMOST_SITE_URL': self.site, 'MATTERMOST_FQDN': 'home-core.tail479ad.ts.net',
                'MATTERMOST_TAILSCALE_ADDRESS': '100.110.248.102'}

    def user_directory(self, path, uid=0, gid=0):
        setup.no_links(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)

    def user_read(self, path, uid=0, mode=0o600):
        setup.no_links(path)
        if stat.S_IMODE(path.stat().st_mode) != mode:
            raise setup.SetupError('private mode differs')
        return path.read_text()

    def initialize_as_test_user(self):
        # Exercise real files, atomic writes and mode checks without creating
        # Linux UIDs 70/2000 on the workstation running this test.
        with patch.object(setup, 'private_dir', self.user_directory), \
                patch.object(setup, 'private_read', self.user_read), patch.object(os, 'fchown'):
            return setup.initialize(self.state, self.site, '100.110.248.102')

    def test_reviewed_primary_or_transport_url_and_official_digest_required(self):
        setup.validate_settings(self.settings(), self.state)
        setup.validate_settings({**self.settings(), 'MATTERMOST_SITE_URL': setup.PRIMARY_SITE}, self.state)
        for field, value in [
            ('MATTERMOST_SITE_URL', 'http://home-core.tail479ad.ts.net:9443'),
            ('MATTERMOST_SITE_URL', 'https://user:password@home-core.tail479ad.ts.net:9443'),
            ('MATTERMOST_SITE_URL', self.site + '/other'),
            ('MATTERMOST_SITE_URL', 'https://other.home.arpa'),
            ('MATTERMOST_FQDN', 'mattermost.home.arpa'),
            ('MATTERMOST_TAILSCALE_ADDRESS', '0.0.0.0'),
            ('MATTERMOST_TAILSCALE_ADDRESS', '192.168.30.122'),
            ('MATTERMOST_IMAGE', 'mattermost/mattermost-enterprise-edition:latest'),
            ('POSTGRES_IMAGE', 'postgres:16.15-alpine'),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises((ValueError, setup.SetupError)):
                setup.validate_settings({**self.settings(), field: value}, self.state)

    def test_initialization_preserves_credentials_and_operator_configuration(self):
        self.initialize_as_test_user()
        password = (self.state / 'secrets/database-password').read_text()
        configpath = self.state / 'config/config.json'
        config = json.loads(configpath.read_text())
        self.assertFalse(config['TeamSettings']['EnableOpenServer'])
        self.assertFalse(config['TeamSettings']['EnableUserCreation'])
        self.assertTrue(config['ServiceSettings']['EnableLocalMode'])
        self.assertEqual(config['ServiceSettings']['AllowCorsFrom'], setup.PRIMARY_SITE + ' ' + self.site)
        config['TeamSettings']['SiteName'] = 'Retained operator customization'
        config['ServiceSettings']['AllowCorsFrom'] = 'https://retained.home.arpa'
        configpath.write_text(json.dumps(config))
        self.initialize_as_test_user()
        self.assertEqual((self.state / 'secrets/database-password').read_text(), password)
        self.assertEqual(json.loads(configpath.read_text())['TeamSettings']['SiteName'],
                         'Retained operator customization')
        self.assertEqual(json.loads(configpath.read_text())['ServiceSettings']['AllowCorsFrom'],
                         'https://retained.home.arpa')
        self.assertNotIn(password.strip(), (self.state / 'runtime.env').read_text())

    def test_canonical_url_reconciliation_preserves_transport_secrets_and_customization(self):
        self.initialize_as_test_user()
        directory = self.state / 'private'
        directory.mkdir(mode=0o700)
        retained = {'site_url': self.site, 'password': 'private password', 'bot_token': 'private token'}
        for name in ['credentials.json', 'operator-login.json']:
            path = directory / name
            path.write_text(json.dumps(retained))
            path.chmod(0o600)
        configpath = self.state / 'config/config.json'
        original = json.loads(configpath.read_text())

        def mmctl(command, **kwargs):
            self.assertEqual(command[:5], ['docker', 'exec', '--user', '2000:2000',
                                          'homecompute-mattermost-mattermost-1'])
            self.assertIn('--local', command)
            value = json.loads(configpath.read_text())
            value['ServiceSettings'][command[-2].split('.')[-1]] = command[-1]
            configpath.write_text(json.dumps(value))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(setup, 'private_dir', self.user_directory), \
                patch.object(setup, 'private_read', self.user_read), patch.object(os, 'fchown'), \
                patch.object(setup, 'run', side_effect=mmctl) as commands:
            value = setup.set_site_url(self.state, setup.PRIMARY_SITE)
            setup.set_site_url(self.state, setup.PRIMARY_SITE)
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(value['MATTERMOST_FQDN'], 'home-core.tail479ad.ts.net')
        self.assertEqual(value['MATTERMOST_SITE_URL'], setup.PRIMARY_SITE)
        expected = json.loads(json.dumps(original))
        expected['ServiceSettings']['SiteURL'] = setup.PRIMARY_SITE
        self.assertEqual(json.loads(configpath.read_text()), expected)
        for name in ['credentials.json', 'operator-login.json']:
            path = directory / name
            self.assertEqual(json.loads(path.read_text()), {**retained, 'site_url': setup.PRIMARY_SITE})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(len(list(directory.glob('site-url-before-*'))), 1)
        self.assertNotIn('private token', (self.state / 'runtime.env').read_text())

    def test_url_reconciliation_refuses_unreviewed_cors_origin(self):
        self.initialize_as_test_user()
        path = self.state / 'config/config.json'
        value = json.loads(path.read_text())
        value['ServiceSettings']['AllowCorsFrom'] = '*'
        path.write_text(json.dumps(value))
        with patch.object(setup, 'private_read', self.user_read), patch.object(setup, 'run') as commands:
            with self.assertRaises(setup.SetupError):
                setup.set_site_url(self.state, setup.PRIMARY_SITE)
        commands.assert_not_called()

    def test_retained_database_without_secret_is_never_reinitialized(self):
        database = self.state / 'postgres'
        database.mkdir(parents=True)
        (database / 'PG_VERSION').write_text('16')
        with self.assertRaises(setup.SetupError):
            self.initialize_as_test_user()
        self.assertFalse((self.state / 'secrets/database-password').exists())
        self.assertEqual((database / 'PG_VERSION').read_text(), '16')

    def test_retained_wrong_credential_is_refused(self):
        self.initialize_as_test_user()
        path = self.state / 'config/config.json'
        value = json.loads(path.read_text())
        value['SqlSettings']['DataSource'] = 'postgres://other:secret@postgres/mattermost'
        path.write_text(json.dumps(value))
        with self.assertRaises(setup.SetupError):
            self.initialize_as_test_user()

    def test_symlink_state_cannot_escape(self):
        self.state.symlink_to(self.state.parent, target_is_directory=True)
        with self.assertRaises(setup.SetupError):
            setup.no_links(self.state)

    def test_subprocess_failure_never_exposes_credentials(self):
        fake = subprocess.CompletedProcess(['docker'], 1, 'private token', 'private password')
        with patch.object(subprocess, 'run', return_value=fake):
            with self.assertRaises(setup.SetupError) as error:
                setup.run(['docker'])
        self.assertNotIn('private', str(error.exception))

    def test_failure_diagnostics_are_retained_privately(self):
        self.state.mkdir()
        fake = subprocess.CompletedProcess(['docker'], 1, 'private token', 'private password')
        with patch.object(subprocess, 'run', return_value=fake), patch.object(os, 'fchown'):
            with self.assertRaises(setup.SetupError):
                setup.run(['docker', 'compose', 'up'], diagnostics=self.state)
        path = self.state / 'last-operation-failure.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())['stderr'], 'private password')

    def test_attached_boot_dependencies_are_idempotent_and_preserve_foreign_links(self):
        units = self.state
        units.mkdir()
        (units / 'test.service').write_text('[Service]\n')
        setup.boot_dependency(units, 'multi-user.target', 'test.service')
        setup.boot_dependency(units, 'multi-user.target', 'test.service')
        link = units / 'multi-user.target.wants/test.service'
        self.assertEqual(link.resolve(), units / 'test.service')
        link.unlink()
        link.symlink_to('../other.service')
        with self.assertRaises(setup.SetupError):
            setup.boot_dependency(units, 'multi-user.target', 'test.service')
        self.assertEqual(os.readlink(link), '../other.service')

    def test_dedicated_compose_and_tailnet_proxy(self):
        compose = (ROOT / 'deploy/mattermost/compose.yaml').read_text()
        caddy = (ROOT / 'deploy/mattermost/Caddyfile').read_text()
        self.assertIn('name: homecompute-mattermost', compose)
        self.assertIn('127.0.0.1:18065:8065', compose)
        self.assertNotIn('/var/run/docker.sock', compose)
        self.assertIn('database: {internal: true}', compose)
        self.assertIn('bind {$MATTERMOST_TAILSCALE_ADDRESS}', caddy)
        self.assertIn('admin unix//tmp/caddy-admin.sock', caddy)
        self.assertNotIn('20199', caddy)
        self.assertNotIn('127.0.0.1:20199', (ROOT / 'scripts/setup-mattermost.py').read_text())

    def test_install_includes_complete_local_account_helper_dependencies(self):
        install = self.state.parent / 'installed'
        units = self.state.parent / 'units'
        with patch.object(setup, 'settings', return_value=self.settings()), \
                patch.object(setup, 'private_dir', self.user_directory), \
                patch.object(os, 'fchown'), patch.object(setup, 'run'):
            setup.install_files(self.state, install, unitdir=units)
        for name in setup.HELPERS:
            self.assertEqual((install / name).read_bytes(), (ROOT / 'scripts' / name).read_bytes())
            self.assertEqual(stat.S_IMODE((install / name).stat().st_mode), 0o700)
        self.assertIn('sync-mattermost-atlas.py', setup.HELPERS)
        self.assertTrue((units / 'timers.target.wants/homecompute-mattermost-atlas-membership.timer').is_symlink())

    def test_automatic_membership_units_are_bounded_persistent_and_separate_from_app(self):
        body = setup.atlas_unit(self.state, self.state.parent / 'installed')
        timer = setup.atlas_timer()
        self.assertIn('Type=oneshot', body); self.assertIn('TimeoutStartSec=45s', body)
        self.assertIn('sync-mattermost-atlas.py --private-root ' + str(self.state) + '/private', body)
        self.assertIn('ProtectSystem=strict', body); self.assertIn('CapabilityBoundingSet=', body)
        self.assertIn('OnBootSec=30s', timer); self.assertIn('OnUnitInactiveSec=30s', timer)
        self.assertIn('AccuracySec=1s', timer); self.assertNotIn('docker', body)
        module = (ROOT / 'modules/nixos/mattermost.nix').read_text()
        self.assertIn('systemd.services.homecompute-mattermost-atlas-membership', module)
        self.assertIn('systemd.timers.homecompute-mattermost-atlas-membership', module)
        self.assertIn('OnUnitInactiveSec = "30s"', module)
        self.assertIn('TimeoutStartSec = "45s"', module)

    def test_start_activates_installed_membership_timer_without_an_extra_manual_command(self):
        install = self.state.parent / 'installed'
        argv = ['setup-mattermost.py', '--state-root', str(self.state), '--install-root', str(install), 'start']
        with patch.object(setup.sys, 'argv', argv), patch.object(setup.os, 'geteuid', return_value=0), \
                patch.object(setup, 'lifecycle_lock', return_value=nullcontext()), \
                patch.object(setup, 'compose', return_value=['docker', 'compose']), \
                patch.object(setup, 'run') as commands, patch('builtins.print'):
            self.assertEqual(setup.main(), 0)
        self.assertEqual(commands.call_args_list[-1].args[0],
                         ['systemctl', 'start', 'homecompute-mattermost-atlas-membership.timer'])
        self.assertEqual(len(commands.call_args_list), 3)

    def test_compose_tmpfs_is_a_single_absolute_mount(self):
        result = subprocess.run(['docker', 'compose', '--env-file', str(ROOT / 'config/mattermost.env.example'),
                                 '-f', str(ROOT / 'deploy/mattermost/compose.yaml'),
                                 'config', '--format', 'json'], capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(value['services']['proxy']['tmpfs'], ['/tmp:rw,nosuid,nodev,noexec,size=32m'])
        for service in value['services'].values():
            for mount in service.get('tmpfs', []):
                self.assertTrue(mount.startswith('/'), mount)

    def test_firewall_is_idempotent_and_only_adds_exact_proxy_source(self):
        existing = set()

        def iptables(command, **kwargs):
            self.assertEqual(command[:2], ['iptables', '-w'])
            if command[2] == '-C':
                rule = tuple(command[3:])
                return subprocess.CompletedProcess(command, int(rule not in existing))
            self.assertEqual(command[2:5], ['-I', 'INPUT', '1'])
            existing.add(('INPUT', *command[5:]))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(setup, 'run', side_effect=iptables) as commands:
            setup.firewall(self.settings())
            setup.firewall(self.settings())
        self.assertEqual(len(existing), 3)
        self.assertEqual(sum(call.args[0][2] == '-I' for call in commands.call_args_list), 3)
        proxy = next(rule for rule in existing if 'br-hc-ctrl' in rule)
        self.assertEqual(proxy, ('INPUT', '-i', 'br-hc-ctrl', '-s', '172.28.200.2/32',
                         '-d', '100.110.248.102/32', '-p', 'tcp', '--dport', '9443',
                         '-m', 'conntrack', '--ctstate', 'NEW,ESTABLISHED', '-m', 'comment',
                         '--comment', 'homecompute-mattermost-private', '-j', 'ACCEPT'))

    def test_nixos_proxy_firewall_addition_has_exact_removal(self):
        module = (ROOT / 'modules/nixos/mattermost.nix').read_text()
        exact = 'INPUT -i br-hc-ctrl -s 172.28.200.2/32 -d 100.110.248.102/32'
        self.assertEqual(module.count(exact), 3)  # check, check-on-stop and removal
        self.assertIn('-I INPUT 1 -i br-hc-ctrl -s 172.28.200.2/32', module)
        self.assertNotIn('interfaces.br-hc-ctrl.allowedTCPPorts', module)


if __name__ == '__main__':
    unittest.main()
