#!/usr/bin/env python3
"""Owner-only HTTP controls and GUI deployment boundaries, using synthetic data."""
from __future__ import annotations

import copy
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import ipaddress
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load('owner_installer_fixtures', ROOT / 'tests/openclaw-sites-setup-test.py')
service = load('owner_site_service', ROOT / 'scripts/openclaw-site-service.py')
common = load('owner_common_setup', ROOT / 'scripts/setup-openclaw-access.py')
REFERENCE = '00000000-0000-4000-8000-000000000001'
OWNER = 'synthetic-owner-' + 'a' * 48
AGENT = 'synthetic-machine-' + 'b' * 48


class OwnerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.operations = SimpleNamespace(run=Mock(return_value={'status': 'running'}),
                                          status=Mock(return_value={'status': 'idle'}))
        self.sessions = SimpleNamespace(**{name: Mock(return_value={'status': name})
                                          for name in ('state', 'diagnostics', 'start', 'fill', 'ready', 'close')})
        original = ThreadingHTTPServer.__init__
        def local_init(instance, address, handler):
            original(instance, ('127.0.0.1', 0), handler)
        with patch.object(service.ThreadingHTTPServer, '__init__', local_init):
            self.server = service.Server(self.operations, {'openclaw': AGENT}, sessions=self.sessions, owner_token=OWNER)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, token=AGENT, body=None, *, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=3)
        raw = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
        fields = [('Host', '127.0.0.1:18816'), ('Authorization', 'Bearer ' + token)]
        if method == 'POST':
            fields += [('Content-Type', 'application/json'), ('Content-Length', str(len(raw or b'')))]
        fields = fields if headers is None else headers
        try:
            connection.putrequest(method, path, skip_host=True)
            for key, value in fields: connection.putheader(key, value)
            connection.endheaders(raw)
            response = connection.getresponse()
            value = response.read()
            self.assertNotIn(OWNER.encode(), value); self.assertNotIn(AGENT.encode(), value)
            return response.status, json.loads(value)
        finally:
            connection.close()

    def test_machine_key_cannot_read_canvas_assets_socket_or_control_owner_session(self):
        for suffix in ('state', 'diagnostics', 'socket', 'vendor/core/rfb.js'):
            status, value = self.request('GET', f'/owner/browser/{REFERENCE}/{suffix}')
            self.assertEqual(status, 403); self.assertEqual(value, {'error': 'website_access_refused'})
        for suffix in ('start', 'fill', 'ready', 'close'):
            status, value = self.request('POST', f'/owner/browser/{REFERENCE}/{suffix}', body={})
            self.assertIn(status, {400, 403}); self.assertEqual(set(value), {'error'})
        for method in vars(self.sessions).values(): method.assert_not_called()
        self.operations.run.assert_not_called(); self.operations.status.assert_not_called()

    def test_owner_key_alone_can_control_private_session(self):
        status, value = self.request('GET', f'/owner/browser/{REFERENCE}/state', OWNER)
        self.assertEqual((status, value), (200, {'status': 'state'}))
        status, value = self.request('GET', f'/owner/browser/{REFERENCE}/diagnostics', OWNER)
        self.assertEqual((status, value), (200, {'status': 'diagnostics'}))
        self.sessions.diagnostics.assert_called_once_with(REFERENCE)
        for suffix in ('start', 'fill', 'ready', 'close'):
            status, value = self.request('POST', f'/owner/browser/{REFERENCE}/{suffix}', OWNER, {})
            self.assertEqual((status, value), (200, {'status': suffix}))
            getattr(self.sessions, suffix).assert_called_once_with(REFERENCE)
        with patch.object(service, 'asset', side_effect=lambda handler, *_: handler.send(200, {'asset': True})) as asset:
            self.assertEqual(self.request('GET', f'/owner/browser/{REFERENCE}/vendor/core/rfb.js', OWNER)[0], 200)
            asset.assert_called_once()
        with patch.object(service, 'websocket', side_effect=lambda handler, *_: handler.send(200, {'socket': True})) as socket:
            self.assertEqual(self.request('GET', f'/owner/browser/{REFERENCE}/socket', OWNER)[0], 200)
            socket.assert_called_once()

    def test_forged_cross_origin_duplicate_auth_or_wrong_host_never_reaches_owner_methods(self):
        base = [('Host', '127.0.0.1:18816'), ('Authorization', 'Bearer ' + OWNER)]
        variants = [base + [('Origin', 'https://private-owner.example')],
                    base + [('Authorization', 'Bearer ' + AGENT)],
                    [('Host', 'localhost:18816'), base[1]],
                    [base[0], ('Authorization', 'Bearer synthetic-unknown')],
                    [base[0], ('Authorization', 'Basic synthetic')]]
        for headers in variants:
            self.assertEqual(self.request('GET', f'/owner/browser/{REFERENCE}/state', headers=headers)[0], 403)
        self.sessions.state.assert_not_called()

    def test_owner_commands_accept_only_empty_json_and_never_model_url_or_secret_parameters(self):
        for body in ({'password': 'synthetic'}, {'url': 'https://other.example/'}, {'actor': 'operator'},
                     b'{"duplicate":1,"duplicate":2}', b'[]'):
            self.assertEqual(self.request('POST', f'/owner/browser/{REFERENCE}/fill', OWNER, body)[0], 400)
        self.sessions.fill.assert_not_called()
        status, _ = self.request('POST', '/site/run', AGENT, {'request_id': REFERENCE, 'operation': 'read', 'parameters': {}})
        self.assertEqual(status, 200)
        self.operations.run.assert_called_once_with('openclaw', {'request_id': REFERENCE, 'operation': 'read', 'parameters': {}})

    def test_backend_errors_never_return_private_exception_content(self):
        self.sessions.start.side_effect = RuntimeError('synthetic-private-browser-secret')
        status, value = self.request('POST', f'/owner/browser/{REFERENCE}/start', OWNER, {})
        self.assertEqual((status, value), (400, {'error': 'website_operation_refused'}))


class OwnerInstallationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SitesSetupTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.setup = fixtures.setup

    def test_owner_key_runtime_stays_root_private_and_never_copies_into_core_or_docker(self):
        result = self.fixture.install()
        setup = self.setup
        owner = (setup.ACCESS / 'browser-owner-token').read_bytes()
        operation = (setup.CORE / 'site-operation-token').read_bytes()
        self.assertNotEqual(owner, operation)
        self.assertEqual((setup.ACCESS / 'browser-owner-token').stat().st_mode & 0o777, 0o600)
        runtime = json.loads((setup.CONFIG / 'runtime.json').read_bytes())
        self.assertEqual(runtime['owner_token_file'], str(setup.ACCESS / 'browser-owner-token'))
        self.assertEqual(runtime['sessions_directory'], str(setup.STATE / 'sessions'))
        native = json.loads((setup.CORE / 'native-mcp-tokens.json').read_bytes())['openclaw']
        self.assertIn(hashlib.sha256(native.encode()).hexdigest(), runtime['forbidden_key_digests'])
        self.assertNotIn(native, json.dumps(runtime))
        for path in setup.CORE.iterdir(): self.assertNotIn(owner, path.read_bytes())
        self.assertNotIn(owner.decode(), json.dumps(result))
        for args, _, _ in self.fixture.commands:
            self.assertNotIn(owner.decode(), json.dumps(args))
            self.assertFalse({'-p', '--publish', '--publish-all', '-P'} & set(args) if args[0] == 'docker' else set())
        self.fixture.install()
        self.assertEqual((setup.ACCESS / 'browser-owner-token').read_bytes(), owner)

    def test_retained_owner_key_must_not_alias_operation_request_or_native_mcp_identity(self):
        setup = self.setup
        self.fixture.install()
        operation = (setup.CORE / 'site-operation-token').read_text()
        metadata, native = 'synthetic-request-' + 'c' * 48, 'synthetic-native-' + 'd' * 48
        self.fixture.atomic(setup.ACCESS / 'assistant-tokens.json', json.dumps({'openclaw': metadata}).encode())
        self.fixture.atomic(setup.CORE / 'native-mcp-tokens.json', json.dumps({'openclaw': native}).encode())
        for key in (operation, metadata, native):
            self.fixture.atomic(setup.ACCESS / 'browser-owner-token', key.encode())
            with self.subTest(key_kind=('operation' if key == operation else 'other_transport')):
                with self.assertRaises(ValueError): self.fixture.install()

    def test_successful_gui_engine_migrates_only_hotels_and_never_fetches_site_credentials(self):
        setup = self.setup
        hotel = copy.deepcopy(fixtures.DEFAULT_HOTELS)
        hotel.pop('login_mode'); hotel['qualified'] = False
        automatic = copy.deepcopy(hotel)
        automatic.update(account='fixture.example', domains=['fixture.example'], driver='form',
                         scopes=['read'], actions=['navigate', 'read'], qualified=False,
                         operations={'read': {'scope': 'read', 'parameters': {}, 'required': []}},
                         driver_config={'operations': {'read': {'url': 'https://fixture.example/', 'fields': {}}}})
        reviewed = copy.deepcopy(automatic)
        reviewed.update(account='reviewed.example', domains=['reviewed.example'], qualified=True)
        reviewed['driver_config']['operations']['read']['url'] = 'https://reviewed.example/'
        self.fixture.atomic(setup.ACCESS / 'sites.json', json.dumps({'schema_version': 1, 'sites': [hotel, automatic, reviewed]}).encode())
        with patch.object(setup, 'BitwardenLogin') as provider:
            self.fixture.install()
        provider.assert_not_called()
        registry = self.fixture.registry(setup.ACCESS / 'sites.json')
        self.assertTrue(registry.profile('hotels.com')['qualified'])
        self.assertEqual(registry.profile('hotels.com')['login_mode'], 'owner')
        self.assertFalse(registry.profile('fixture.example')['qualified'])
        self.assertEqual(registry.profile('fixture.example')['login_mode'], 'automatic')
        self.assertTrue(registry.profile('reviewed.example')['qualified'])
        self.assertEqual(registry.profile('reviewed.example')['login_mode'], 'automatic')

    def test_private_owner_cli_flag_is_present_only_when_root_configuration_exists(self):
        with patch.object(common, 'CONFIG', self.setup.ACCESS):
            self.assertNotIn('--site-owner-config', common.service_unit())
            self.fixture.install()
            self.assertIn('--site-owner-config ' + str(self.setup.ACCESS / 'site-owner.json'), common.service_unit())

    def test_gui_firewall_allows_only_its_own_proxy_and_root_host_view(self):
        self.setup.firewall()
        rules = [args for args, _, _ in self.fixture.commands if args[:3] == ['iptables', '-w', '-I']]
        def value(rule, flag):
            return rule[rule.index(flag) + 1] if flag in rule else None
        for gui, proxy in (('172.30.192.20', '172.30.192.10'), ('172.30.192.21', '172.30.192.11')):
            relevant = [rule for rule in rules if value(rule, '-s') == gui]
            allows = [rule for rule in relevant if rule[-1] == 'ACCEPT']
            self.assertEqual(len(allows), 1)
            self.assertEqual((value(allows[0], '-d'), value(allows[0], '--dport')), (proxy, '3128'))
            self.assertTrue(any(rule[-1] == 'DROP' and '-d' not in rule for rule in relevant))
            host = next(rule for rule in rules if rule[3] == 'OUTPUT' and value(rule, '-d') == gui)
            self.assertEqual((value(host, '--dport'), value(host, '--uid-owner'), host[-1]), ('6080', '0', 'DROP'))
            self.assertIn('!', host)
        stateful = next(rule for rule in rules if rule[3] == 'INPUT' and rule[-1] == 'ACCEPT')
        self.assertEqual(value(stateful, '--ctstate'), 'ESTABLISHED,RELATED')

    def test_effective_rule_order_blocks_sibling_gui_and_proxy_access(self):
        self.setup.firewall()
        inserted = [args for args, _, _ in self.fixture.commands if args[:4] == ['iptables', '-w', '-I', 'DOCKER-USER']]
        def decision(source, destination, port, state='NEW'):
            # Every insertion is at position 1: the final chain reverses calls.
            for rule in reversed(inserted):
                def value(flag): return rule[rule.index(flag) + 1] if flag in rule else None
                if '-s' in rule and ipaddress.ip_address(source) not in ipaddress.ip_network(value('-s'), strict=False): continue
                if '-d' in rule and ipaddress.ip_address(destination) not in ipaddress.ip_network(value('-d'), strict=False): continue
                if '--dport' in rule and value('--dport') != str(port): continue
                if '--ctstate' in rule and state not in value('--ctstate').split(','): continue
                return rule[-1]
            return 'UNMATCHED'
        for gui, own, other in (('172.30.192.20', '172.30.192.10', '172.30.192.11'),
                                ('172.30.192.21', '172.30.192.11', '172.30.192.10')):
            self.assertEqual(decision(gui, own, 3128), 'ACCEPT')
            for destination, port in ((other, 3128), (own, 6080), ('172.30.192.2', 3128),
                                      ('172.30.192.20', 6080), ('172.30.192.21', 6080), ('8.8.8.8', 443)):
                self.assertEqual(decision(gui, destination, port), 'DROP')
            self.assertEqual(decision(own, gui, 6080), 'DROP')
            self.assertEqual(decision(own, gui, 6080, 'ESTABLISHED'), 'ACCEPT')
            self.assertEqual(decision(other, gui, 6080, 'ESTABLISHED'), 'DROP')


class OwnerDaemonTests(unittest.TestCase):
    def test_daemon_refuses_cross_role_owner_keys_before_sessions_providers_or_cleanup(self):
        metadata, native = 'synthetic-metadata-' + 'c' * 48, 'synthetic-native-' + 'd' * 48
        config = {'schema_version': 1, 'image': fixtures.IMAGE,
                  'sites_file': '/var/lib/homecompute-access/config/sites.json',
                  'state_directory': '/var/lib/homecompute-sites/operations',
                  'assistant_tokens_file': '/var/lib/homecompute-sites/config/operation-tokens.json',
                  'request_tokens_file': '/var/lib/homecompute-access/config/assistant-tokens.json',
                  'custody_directory': '/var/lib/homecompute-credential-secrets',
                  'bws_executable': '/nix/store/synthetic-bws/bin/bws',
                  'owner_token_file': '/var/lib/homecompute-access/config/browser-owner-token',
                  'sessions_directory': '/var/lib/homecompute-sites/sessions',
                  'forbidden_key_digests': [hashlib.sha256(native.encode()).hexdigest()]}
        for owner in (AGENT, metadata, native):
            values = {'runtime.json': json.dumps(config).encode(), 'operation-tokens.json': json.dumps({'openclaw': AGENT}).encode(),
                      'assistant-tokens.json': json.dumps({'openclaw': metadata}).encode(), 'browser-owner-token': owner.encode()}
            with patch.object(sys, 'argv', ['service', 'serve', '--config', '/synthetic/runtime.json']), \
                 patch.object(service.os, 'geteuid', return_value=0), \
                 patch.object(service, 'read_private', side_effect=lambda directory, name, limit: values[name]), \
                 patch.object(service, 'SiteSessions') as sessions, patch.object(service, 'BitwardenLogin') as provider, \
                 patch.object(service, 'cleanup') as cleanup, patch.object(service, 'GrantClient') as client:
                with self.assertRaisesRegex(ValueError, 'Distinct private owner authentication required'): service.main()
                sessions.assert_not_called(); provider.assert_not_called(); cleanup.assert_not_called(); client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
