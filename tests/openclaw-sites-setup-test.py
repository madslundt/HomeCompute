#!/usr/bin/env python3
"""Installer lifecycle and isolation proofs; no Docker, systemd, or real secrets."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_access_grants import AccessGrants, GrantRefused
from openclaw_site_registry import DEFAULT_HOTELS, SiteRegistry

SPEC = importlib.util.spec_from_file_location('site_setup_fixture', ROOT / 'scripts/setup-openclaw-sites.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
IMAGE = 'sha256:' + 'a' * 64


class SitesSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)
        for name in setup.FILES:
            (self.source / 'scripts' / name).write_text('# synthetic reviewed source\n')
        sandbox = self.source / 'deploy/openclaw/site-worker/seccomp.json'
        sandbox.parent.mkdir(parents=True)
        sandbox.write_text(json.dumps({'defaultAction': 'SCMP_ACT_ERRNO', 'syscalls': []}))
        self.events, self.commands = [], []
        self.loaded, self.active, self.retained = 'loaded', 'inactive', [[], []]
        self.stop_fails, self.probe_fails, self.network = False, False, None
        self.image = {'Id': IMAGE, 'Os': 'linux', 'Architecture': 'amd64',
                      'Config': {'Labels': {'org.homecompute.site-worker': '1'}}}
        self.helper = SimpleNamespace(atomic=Mock(side_effect=self.atomic), boot_dependency=Mock(),
                                      configure=Mock())
        locations = {'INSTALL': self.root / 'install', 'STATE': self.root / 'state',
                     'CONFIG': self.root / 'state/config', 'ACCESS': self.root / 'access/config',
                     'CORE': self.root / 'core/config', 'UNITS': self.root / 'units'}
        self.patches = [patch.object(setup, name, value) for name, value in locations.items()]
        self.patches += [patch.object(setup.os, 'geteuid', return_value=0),
                         patch.object(setup, 'open_directory', side_effect=self.directory),
                         patch.object(setup, 'command', side_effect=self.command),
                         patch.object(setup, 'qualified_bws', return_value=Path('/nix/store/synthetic-bws/bin/bws')),
                         patch.object(setup.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())),
                         patch.object(setup, 'read_private', side_effect=lambda directory, name, maximum: (directory / name).read_bytes()),
                         patch.object(setup.SiteRegistry, 'from_file', side_effect=self.registry),
                         patch.object(setup.importlib.util, 'module_from_spec', return_value=self.helper),
                         patch.object(setup.importlib.util, 'spec_from_file_location',
                                      return_value=SimpleNamespace(loader=SimpleNamespace(exec_module=Mock())))]
        for patcher in self.patches:
            patcher.start(); self.addCleanup(patcher.stop)
        for path, token in ((setup.ACCESS / 'assistant-tokens.json', 'synthetic-request-' + 'r' * 48),
                            (setup.CORE / 'native-mcp-tokens.json', 'synthetic-native-' + 'n' * 48)):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps({'openclaw': token})); path.chmod(0o600)

    def registry(self, path):
        return SiteRegistry(json.loads(path.read_bytes())['sites'])

    def directory(self, path, *, create=False):
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(path, os.O_RDONLY)

    def atomic(self, path, data, **kwargs):
        self.events.append(('atomic', path))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data); path.chmod(kwargs.get('mode', 0o600))

    def command(self, args, *, check=True, timeout=30):
        self.commands.append((args, check, timeout)); self.events.append(('command', args))
        status, output = 0, ''
        if args[:3] == ['docker', 'image', 'inspect']:
            output = json.dumps([self.image])
        elif args[:2] == ['systemctl', 'show']:
            output = self.loaded
        elif args[:2] == ['systemctl', 'stop'] and self.stop_fails:
            raise ValueError('Synthetic stop failure')
        elif args[:2] == ['systemctl', 'is-active']:
            output = self.active
        elif args[:2] == ['docker', 'ps']:
            output = '\n'.join(self.retained.pop(0) if self.retained else [])
        elif args[:3] == ['docker', 'network', 'inspect']:
            status, output = (1, '') if self.network is None else (0, json.dumps([self.network]))
        elif args[:2] == ['docker', 'run'] and self.probe_fails:
            raise ValueError('Synthetic sandbox failure')
        elif args[:3] == ['iptables', '-w', '-C']:
            status = 1
        return SimpleNamespace(returncode=status, stdout=output)

    def install(self):
        return setup.install(self.source, IMAGE)

    def assert_no_publication(self):
        self.helper.atomic.assert_not_called()
        self.assertFalse(any(args[0] == 'iptables' for args, _, _ in self.commands))

    def test_loaded_runner_stops_and_cleanup_verifies_before_first_code_write(self):
        with patch.object(setup, 'invalidate_grants', side_effect=lambda p: self.events.append(('invalidate', p))), \
             patch.object(setup, 'BitwardenLogin') as provider:
            result = self.install()
        provider.assert_not_called()
        first_write = next(index for index, item in enumerate(self.events) if item[0] == 'atomic')
        before = [entry[1] for entry in self.events[:first_write] if entry[0] == 'command']
        self.assertIn(['systemctl', 'stop', 'homecompute-openclaw-sites.service'], before)
        self.assertIn(['systemctl', 'is-active', 'homecompute-openclaw-sites.service'], before)
        self.assertEqual(sum(args[:2] == ['docker', 'ps'] for args in before), 2)
        self.assertLess(self.events.index(('invalidate', setup.ACCESS.parent / 'ledger')),
                        self.events.index(('atomic', setup.ACCESS / 'sites.json')))
        self.assertGreater(self.events.index(('invalidate', setup.ACCESS.parent / 'ledger')),
                           self.events.index(('command', ['systemctl', 'is-active', 'homecompute-openclaw-sites.service'])))
        self.assertTrue(result['installed'])
        self.assertFalse(result['real_website_sign_in_verified'])
        self.assertFalse(result['transaction_execution_available'])
        profile = json.loads((setup.ACCESS / 'sites.json').read_bytes())['sites'][0]
        self.assertTrue(profile['qualified'])
        self.assertEqual(profile['login_mode'], 'owner')
        self.assertEqual(profile['driver_config']['login_url'], 'https://da.hotels.com/login')

    def test_failed_stop_or_still_active_or_unknown_lifecycle_aborts_before_publication(self):
        for variant in ('stop_failed', 'running', 'unknown'):
            with self.subTest(variant=variant):
                self.stop_fails = variant == 'stop_failed'
                self.active = 'active' if variant == 'running' else 'inactive'
                self.loaded = 'masked' if variant == 'unknown' else 'loaded'
                with self.assertRaises(ValueError): self.install()
                self.assert_no_publication()

    def test_absent_runner_needs_no_stop_but_retained_worker_must_be_removed(self):
        self.loaded = 'not-found'
        identity = 'f' * 64
        self.retained = [[identity], []]
        self.install()
        commands = [args for args, _, _ in self.commands]
        self.assertFalse(any(args[:2] == ['systemctl', 'stop'] for args in commands))
        removal = ['docker', 'rm', '--force', identity]
        self.assertIn(removal, commands)
        self.assertLess(self.events.index(('command', removal)),
                        next(i for i, item in enumerate(self.events) if item[0] == 'atomic'))

    def test_invalid_retained_workers_or_unverified_cleanup_refuse_before_code_write(self):
        for retained in ([['not-an-id']], [['b' * 64] * 17], [[], ['c' * 64]]):
            self.retained = copy.deepcopy(retained)
            with self.assertRaises(ValueError): self.install()
            self.assert_no_publication()

    def test_mutable_image_or_incorrect_artifact_is_refused_before_publication(self):
        with patch.object(setup.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError): self.install()
        for image in ('latest', 'repository:tag', 'sha256:ABC', 'sha256:' + 'b' * 63):
            with self.assertRaises(ValueError): setup.install(self.source, image)
        for field, value in (('Id', 'sha256:' + 'b' * 64), ('Os', 'windows'), ('Architecture', 'arm64'),
                             ('Config', {'Labels': {}})):
            original = self.image
            self.image = dict(original, **{field: value})
            with self.assertRaises(ValueError): self.install()
            self.image = original
        self.assert_no_publication()

    def test_real_sandbox_probe_has_no_network_privileges_or_persistent_storage(self):
        self.install()
        probe, _, timeout = next(item for item in self.commands if item[0][:2] == ['docker', 'run'])
        for option, value in (('--network', 'none'), ('--user', '1000:1000'), ('--cap-drop', 'ALL')):
            self.assertEqual(probe[probe.index(option) + 1], value)
        self.assertIn('--read-only', probe)
        self.assertIn('no-new-privileges:true', probe)
        self.assertIn('seccomp=' + str(setup.INSTALL / 'site-seccomp.json'), probe)
        self.assertIn('chromium_sandbox=True', probe[-1])
        self.assertIn('headless=False', probe[-1])
        self.assertIn('Display()', probe[-1])
        self.assertNotIn('--no-sandbox', probe[-1])
        self.assertNotIn('--mount', probe)
        self.assertEqual(timeout, 30)
        self.assertEqual(probe[probe.index(IMAGE) + 1:probe.index(IMAGE) + 3], ['python', '-c'])
        proxy = next(args for args, _, _ in self.commands if args[:2] == ['docker', 'create'])
        self.assertEqual(proxy[proxy.index('--cap-drop') + 1], 'ALL')
        self.assertEqual(proxy[proxy.index('--user') + 1], '1000:1000')
        self.assertIn('--read-only', proxy); self.assertIn('no-new-privileges:true', proxy)
        self.assertEqual(proxy[proxy.index('--log-driver') + 1], 'none')
        self.assertIn(IMAGE, proxy)
        mounts = [proxy[index + 1] for index, arg in enumerate(proxy) if arg == '--mount']
        self.assertEqual(mounts, [f'type=bind,src={setup.INSTALL}/site-hosts.json,dst=/run/site-hosts.json,readonly'])

    def test_failed_probe_does_not_publish_registry_tokens_runtime_or_units(self):
        self.probe_fails = True
        with self.assertRaises(ValueError): self.install()
        for path in (setup.ACCESS / 'sites.json', setup.CONFIG / 'operation-tokens.json',
                     setup.CORE / 'site-operation-token', setup.CONFIG / 'runtime.json', setup.UNITS):
            self.assertFalse(path.exists())
        self.helper.configure.assert_not_called()

    def test_missing_reviewed_source_or_permissive_seccomp_cannot_launch_probe_or_publish_runtime(self):
        path = self.source / 'scripts' / setup.FILES[0]
        path.unlink()
        with self.assertRaises(FileNotFoundError): self.install()
        self.assert_no_publication()
        path.write_text('# restored synthetic source\n')
        policy = self.source / 'deploy/openclaw/site-worker/seccomp.json'
        policy.write_text(json.dumps({'defaultAction': 'SCMP_ACT_ALLOW'}))
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(any(args[:2] == ['docker', 'run'] for args, _, _ in self.commands))
        self.assertFalse((setup.CONFIG / 'runtime.json').exists())
        self.assertFalse((setup.CORE / 'site-operation-token').exists())

    def test_firewall_changes_only_tagged_dedicated_network_rules_and_blocks_private_ranges(self):
        setup.firewall()
        commands = [args for args, _, _ in self.commands]
        self.assertTrue(commands)
        for args in commands:
            self.assertEqual(args[:2], ['iptables', '-w'])
            self.assertIn(args[2], {'-C', '-I'})
            self.assertIn(args[3], {'INPUT', 'OUTPUT', 'DOCKER-USER'})
            self.assertIn('homecompute-site-browser', args)
            self.assertFalse({'-F', '--flush', '-P', '--policy'} & set(args))
        inserted = [args for args in commands if args[2] == '-I']
        for subnet in ('10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16',
                       '172.16.0.0/12', '192.168.0.0/16'):
            self.assertTrue(any(subnet in args and args[-1] == 'DROP' for args in inserted))

    def test_retained_mismatched_network_refuses_before_proxy_or_token_publication(self):
        self.network = {'Internal': False, 'EnableIPv6': False,
                        'IPAM': {'Config': [{'Subnet': '172.30.192.0/24', 'Gateway': '172.30.192.1'}]}}
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(any(args[:2] == ['docker', 'create'] for args, _, _ in self.commands))
        self.assertFalse((setup.CORE / 'site-operation-token').exists())

    def test_reinstall_preserves_requester_token_and_does_not_rotate_per_site(self):
        self.install()
        tokens = (setup.CONFIG / 'operation-tokens.json').read_bytes()
        client = (setup.CORE / 'site-operation-token').read_bytes()
        self.install()
        self.assertEqual((setup.CONFIG / 'operation-tokens.json').read_bytes(), tokens)
        self.assertEqual((setup.CORE / 'site-operation-token').read_bytes(), client)
        self.assertEqual(set(json.loads(tokens)), {'openclaw'})

    def test_runner_and_proxy_units_preserve_private_service_boundaries(self):
        unit = setup.runner_unit()
        for line in ('User=root', 'UMask=0077', 'CapabilityBoundingSet=\n', 'AmbientCapabilities=\n',
                     'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=yes', 'PrivateDevices=yes',
                     'ProtectClock=yes', 'ProtectKernelTunables=yes', 'ProtectKernelModules=yes',
                     'LimitCORE=0', 'StandardOutput=null', 'StandardError=null', 'IPAddressAllow=localhost'):
            self.assertIn(line, unit)
        self.assertIn('homecompute-openclaw-access.service', unit)
        self.assertIn('openclaw-site-service.py cleanup', unit)
        self.assertIn(f'ReadWritePaths={setup.STATE}/operations {setup.STATE}/sessions /run/docker.sock', unit)
        self.assertNotIn('CAP_SYS_ADMIN', unit)
        self.assertIn('setup-openclaw-sites.py firewall', setup.proxy_unit())

    def qualification_fixture(self):
        profile = copy.deepcopy(DEFAULT_HOTELS)
        self.atomic(setup.ACCESS / 'sites.json', json.dumps({'schema_version': 1, 'sites': [profile]}).encode())
        runtime = {'image': IMAGE, 'custody_directory': '/synthetic/private-custody',
                   'bws_executable': '/nix/store/synthetic-bws/bin/bws'}
        self.atomic(setup.CONFIG / 'runtime.json', json.dumps(runtime).encode())
        self.atomic(setup.ACCESS / 'assistant-tokens.json', json.dumps({'openclaw': 'synthetic'}).encode())
        grant = {'request_id': '00000000-0000-4000-8000-000000000001', 'actor': 'openclaw',
                 'account': 'hotels.com', 'scope': 'read', 'status': 'active', 'request_digest': 'b' * 64,
                 'approved_at': 900, 'expires_at': 1300}
        self.helper.atomic.reset_mock(); self.events.clear(); self.commands.clear()
        return profile, grant

    def qualify(self, profile, grant, *, expected=None):
        return setup.qualify('hotels.com', grant['request_id'], 'read', {},
                             setup.profile_digest(profile) if expected is None else expected)

    def test_profile_digest_ignores_only_qualification_flag(self):
        profile = copy.deepcopy(DEFAULT_HOTELS)
        original = setup.profile_digest(profile)
        profile['qualified'] = True
        self.assertEqual(setup.profile_digest(profile), original)
        profile['domains'].append('another.example')
        self.assertNotEqual(setup.profile_digest(profile), original)

    def test_hash_mismatch_or_inactive_wrong_account_scope_or_time_never_fetches_credentials(self):
        profile, grant = self.qualification_fixture()
        with patch.object(setup, 'BitwardenLogin') as provider, patch.object(setup, 'GrantClient') as client:
            with self.assertRaises(ValueError): self.qualify(profile, grant, expected='c' * 64)
            client.assert_not_called(); provider.assert_not_called()
        for change in ({'status': 'pending'}, {'status': 'revoked'}, {'status': 'expired'},
                       {'account': 'another.example'}, {'scope': 'search'}, {'approved_at': 1001}, {'expires_at': 1000}):
            value = dict(grant, **change)
            with patch.object(setup.time, 'time', return_value=1000), \
                 patch.object(setup, 'GrantClient', return_value=SimpleNamespace(status=Mock(return_value=value))), \
                 patch.object(setup, 'BitwardenLogin') as provider:
                with self.assertRaises(ValueError): self.qualify(profile, grant)
                provider.assert_not_called()
        self.helper.atomic.assert_not_called()

    def test_revoke_before_private_fetch_denies_qualification_without_credentials(self):
        profile, grant = self.qualification_fixture()
        client = SimpleNamespace(status=Mock(side_effect=[grant, dict(grant, status='revoked')]))
        with patch.object(setup.time, 'time', return_value=1000), patch.object(setup.time, 'monotonic', return_value=100), \
             patch.object(setup, 'GrantClient', return_value=client), patch.object(setup, 'BitwardenLogin') as provider:
            with self.assertRaises(ValueError): self.qualify(profile, grant)
        provider.assert_not_called(); self.helper.atomic.assert_not_called()

    def test_qualification_success_requires_active_grant_then_invalidates_before_publication(self):
        profile, grant = self.qualification_fixture()
        login = SimpleNamespace(username='synthetic-user', password='synthetic-secret')
        provider = Mock(); provider.retrieve.return_value = login
        runner = Mock(); runner.run.return_value = {'status': 'ok', 'operation': 'read', 'results_count': 0}
        def invalidate(directory): self.events.append(('invalidate', directory))
        with patch.object(setup.time, 'time', return_value=1000), patch.object(setup.time, 'monotonic', return_value=100), \
             patch.object(setup, 'GrantClient', return_value=SimpleNamespace(status=Mock(return_value=grant))), \
             patch.object(setup, 'BitwardenLogin', return_value=provider), \
             patch.object(setup, 'ContainerRunner', return_value=runner), \
             patch.object(setup, 'invalidate_grants', side_effect=invalidate):
            result = self.qualify(profile, grant)
        self.assertTrue(result['qualified']); self.assertTrue(result['fresh_access_request_required'])
        self.assertFalse(result['transaction_execution_available'])
        provider.retrieve.assert_called_once()
        self.assertIs(runner.run.call_args.args[3], login)
        self.assertEqual(self.events[0], ('invalidate', setup.ACCESS.parent / 'ledger'))
        saved = json.loads((setup.ACCESS / 'sites.json').read_bytes())['sites'][0]
        self.assertTrue(saved['qualified'])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        self.assertNotIn('synthetic-secret', (setup.ACCESS / 'sites.json').read_text())

    def test_revocation_expiry_or_profile_change_during_qualification_cannot_publish(self):
        for failure in ('revoked', 'expired', 'profile_changed', 'challenge'):
            profile, grant = self.qualification_fixture()
            def execute(*args):
                if failure == 'profile_changed':
                    value = copy.deepcopy(profile); value['domains'].append('new.example')
                    (setup.ACCESS / 'sites.json').write_text(json.dumps({'schema_version': 1, 'sites': [value]}))
                return {'status': 'human_sign_in_required' if failure == 'challenge' else 'ok'}
            final = dict(grant, status=failure) if failure in {'revoked', 'expired'} else grant
            client = SimpleNamespace(status=Mock(side_effect=[grant, grant, final]))
            runner = SimpleNamespace(run=Mock(side_effect=execute))
            with patch.object(setup.time, 'time', return_value=1000), patch.object(setup.time, 'monotonic', return_value=100), \
                 patch.object(setup, 'GrantClient', return_value=client), patch.object(setup, 'BitwardenLogin'), \
                 patch.object(setup, 'ContainerRunner', return_value=runner), patch.object(setup, 'invalidate_grants') as invalidate:
                result = self.qualify(profile, grant)
            self.assertFalse(result['qualified'])
            self.helper.atomic.assert_not_called(); invalidate.assert_not_called()
            self.assertFalse(self.commands)

    def test_register_new_site_defaults_unqualified_and_invalidates_before_registry_write(self):
        self.qualification_fixture()
        candidate = {'account': 'shop.example', 'label': 'Fixture', 'driver': 'form', 'domains': ['shop.example'],
                     'scopes': ['read'], 'actions': ['navigate', 'read'],
                     'operations': {'read': {'scope': 'read', 'parameters': {}, 'required': []}},
                     'driver_config': {'operations': {'read': {'url': 'https://shop.example/account', 'fields': {}}}},
                     'qualified': True}
        path = self.root / 'new-profile.json'
        path.write_text(json.dumps({'schema_version': 1, 'sites': [candidate]})); path.chmod(0o600)
        with patch.object(setup, 'invalidate_grants', side_effect=lambda p: self.events.append(('invalidate', p))):
            result = setup.register(path)
        self.assertEqual(self.events[0][0], 'invalidate')
        self.assertFalse(result['qualified']); self.assertTrue(result['network_reconfigure_required'])
        self.assertFalse(result['credential_values_saved'])
        self.assertFalse(self.registry(setup.ACCESS / 'sites.json').profile('shop.example')['qualified'])
        self.assertEqual([call.args[0] for call in self.helper.atomic.call_args_list], [setup.ACCESS / 'sites.json'])
        self.helper.atomic.reset_mock()
        with patch.object(setup, 'invalidate_grants') as invalidate:
            with self.assertRaises(ValueError): setup.register(path)
        invalidate.assert_not_called(); self.helper.atomic.assert_not_called()

    def test_registration_refuses_zero_multiple_unknown_or_secret_bearing_profiles_before_mutation(self):
        self.qualification_fixture()
        path = self.root / 'new-profile.json'
        original = (setup.ACCESS / 'sites.json').read_bytes()
        for profiles in ([], [copy.deepcopy(DEFAULT_HOTELS)] * 2,
                         [dict(DEFAULT_HOTELS, password='synthetic-secret')],
                         [dict(DEFAULT_HOTELS, driver='arbitrary-script')]):
            path.write_text(json.dumps({'schema_version': 1, 'sites': profiles}))
            with patch.object(setup, 'invalidate_grants') as invalidate:
                with self.assertRaises(ValueError): setup.register(path)
            invalidate.assert_not_called(); self.helper.atomic.assert_not_called()
            self.assertEqual((setup.ACCESS / 'sites.json').read_bytes(), original)


class GrantInvalidationTests(unittest.TestCase):
    def test_configuration_change_revokes_active_expires_pending_and_removes_old_challenges(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'ledger'
            ledger = AccessGrants(path)
            def request(key):
                return ledger.request('openclaw', {'account': 'hotels.com', 'scope': 'read', 'minutes': 30,
                                                  'request_key': key * 20})[0]
            active, pending, denied = request('a'), request('b'), request('c')
            active = ledger.decide(active['request_id'], active['request_digest'], 'approve', 30,
                                   ledger.challenge(active['request_id'], 'approve'))
            denied = ledger.decide(denied['request_id'], denied['request_digest'], 'deny', 1,
                                   ledger.challenge(denied['request_id'], 'deny'))
            active_nonce = ledger.challenge(active['request_id'], 'revoke')
            pending_nonce = ledger.challenge(pending['request_id'], 'approve')
            setup.invalidate_grants(path)
            self.assertEqual(ledger.status(active['request_id'], actor='openclaw')['status'], 'revoked')
            self.assertEqual(ledger.status(pending['request_id'], actor='openclaw')['status'], 'expired')
            self.assertEqual(ledger.status(denied['request_id'], actor='openclaw')['status'], 'denied')
            for item, action, nonce in ((active, 'revoke', active_nonce), (pending, 'approve', pending_nonce)):
                with self.assertRaises(GrantRefused):
                    ledger.decide(item['request_id'], item['request_digest'], action, 30, nonce)
                with self.assertRaises(GrantRefused): ledger.authorize('openclaw', item['request_id'], 'read')
            with ledger.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM challenges').fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE action='configuration_changed'").fetchone()[0], 2)
                retained = db.execute('SELECT digest,account,expires FROM requests WHERE id=?', (active['request_id'],)).fetchone()
            self.assertEqual(tuple(retained), (active['request_digest'], 'hotels.com', active['expires_at']))
            setup.invalidate_grants(path)
            with ledger.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE action='configuration_changed'").fetchone()[0], 2)

    def test_new_profile_capability_cannot_reactivate_old_grant_or_reuse_its_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'ledger'
            ledger = AccessGrants(path)
            values = {'account': 'hotels.com', 'scope': 'read', 'minutes': 30, 'request_key': 'a' * 20}
            item = ledger.request('openclaw', values)[0]
            ledger.decide(item['request_id'], item['request_digest'], 'approve', 30,
                          ledger.challenge(item['request_id'], 'approve'))
            setup.invalidate_grants(path)
            changed = copy.deepcopy(DEFAULT_HOTELS); changed['qualified'] = True
            changed['domains'].append('reviewed.example')
            updated = AccessGrants(path, registry=SiteRegistry([changed]), execution_available=lambda *_: True)
            old, created = updated.request('openclaw', values)
            self.assertFalse(created); self.assertEqual(old['status'], 'revoked')
            with self.assertRaises(GrantRefused): updated.authorize('openclaw', old['request_id'], 'read')
            fresh, created = updated.request('openclaw', dict(values, request_key='b' * 20))
            self.assertTrue(created); self.assertEqual(fresh['status'], 'pending')
            with self.assertRaises(GrantRefused): updated.authorize('openclaw', fresh['request_id'], 'read')


if __name__ == '__main__':
    unittest.main()
