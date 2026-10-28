#!/usr/bin/env python3
"""Synthetic exact-origin access bridge, MCP composition and native role policy."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_access_bridge as bridge
import openclaw_team_policy as policy
from openclaw_notifications import Outbox


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMUNICATION = load('access_communication_fixture', ROOT / 'scripts/openclaw-communication.py')
REFERENCE = '00000000-0000-4000-8000-000000000001'
REQUEST = {'account': 'hotels.com', 'scope': 'search', 'minutes': 30, 'request_key': 'synthetic-access-request-0001'}


def receipt(**changes):
    item = {'request_id': REFERENCE, 'actor': 'openclaw', 'account': 'hotels.com', 'scope': 'search',
            'requested_minutes': 30, 'requested_at': 1000, 'request_digest': 'a' * 64, 'status': 'pending',
            'approved_at': None, 'expires_at': None, 'account_execution_available': False,
            'transaction_execution_available': False, 'notification_delivery': 'submitted'}
    item.update(changes)
    return item


class AccessBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.token = self.root / 'access-request-token'
        self.secret = 'synthetic-request-token-' + 'a' * 40
        self.token.write_text(self.secret); self.token.chmod(0o600)
        self.config = self.root / 'access-requests.json'
        self.settings = {'schema_version': 1, 'principal': 'openclaw', 'endpoint': bridge.ENDPOINT,
                         'token_file': str(self.token)}
        self.config.write_text(json.dumps(self.settings)); self.config.chmod(0o600)
        self.token_patch = patch.object(bridge, 'TOKEN_PATH', self.token)
        self.token_patch.start(); self.addCleanup(self.token_patch.stop)
        self.client = bridge.AccessBridge(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def connection(self, value=None, status=200):
        response = Mock(status=status)
        response.read.return_value = json.dumps(value if value is not None else receipt()).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        return connection

    def test_fixed_endpoint_authentication_and_allowlisted_projection(self):
        connection = self.connection(receipt(password='synthetic-private', headers={'secret': self.secret}))
        with patch.object(bridge, 'HTTPConnection', return_value=connection) as factory:
            result = self.client.request('homecompute_access_request', REQUEST)
        factory.assert_called_once_with('127.0.0.1', 18812, timeout=10)
        method, path, raw, headers = connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/access/request'))
        self.assertEqual(json.loads(raw), REQUEST)
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.secret)
        self.assertNotIn('password', result)
        self.assertNotIn('headers', result)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertFalse(result['account_execution_available'])
        connection.close.assert_called_once()
        with patch.object(bridge, 'HTTPConnection', return_value=self.connection(receipt(notification_delivery='delivered'))):
            result = self.client.request('homecompute_access_request', REQUEST)
        self.assertEqual(result['notification_delivery'], 'delivered')

    def test_actor_urls_operator_methods_and_invalid_durations_never_reach_transport(self):
        with patch.object(bridge, 'HTTPConnection') as factory:
            for values in [dict(REQUEST, actor='operator'), dict(REQUEST, url='http://elsewhere'),
                           dict(REQUEST, token=self.secret), dict(REQUEST, scope='book'),
                           dict(REQUEST, account='https://expedia.com'), dict(REQUEST, minutes=True),
                           dict(REQUEST, minutes=0), dict(REQUEST, minutes=1441)]:
                with self.assertRaises(ValueError):
                    self.client.request('homecompute_access_request', values)
            for operation in ['homecompute_access_approve', 'homecompute_access_revoke']:
                with self.assertRaises(ValueError):
                    self.client.request(operation, {})
            with self.assertRaises(ValueError):
                self.client.request('homecompute_access_status', {'request_id': '../../operator'})
        factory.assert_not_called()

    def test_wrong_identity_reference_execution_flags_and_secret_bearing_errors_are_redacted(self):
        for changes in [{'actor': 'travel'}, {'scope': 'prepare_booking'}, {'account_execution_available': 'yes'},
                        {'transaction_execution_available': True}, {'request_digest': 'private diagnostic'},
                        {'notification_delivery': self.secret}, {'requested_at': True},
                        {'status': 'active', 'approved_at': 1200, 'expires_at': 1200}]:
            with patch.object(bridge, 'HTTPConnection', return_value=self.connection(receipt(**changes))):
                with self.assertRaises(ValueError) as caught:
                    self.client.request('homecompute_access_request', REQUEST)
                self.assertNotIn(self.secret, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
        connection = self.connection()
        connection.getresponse.side_effect = TimeoutError(self.secret)
        with patch.object(bridge, 'HTTPConnection', return_value=connection):
            with self.assertRaises(ValueError) as caught:
                self.client.request('homecompute_access_request', REQUEST)
        self.assertNotIn(self.secret, str(caught.exception))
        with patch.object(bridge, 'HTTPConnection', return_value=self.connection(receipt(request_id='00000000-0000-4000-8000-000000000002'))):
            with self.assertRaises(ValueError):
                self.client.request('homecompute_access_status', {'request_id': REFERENCE})

    def test_configuration_cannot_choose_another_principal_origin_or_token_file(self):
        for changes in [{'principal': 'travel'}, {'endpoint': 'http://127.0.0.1:18813'},
                        {'endpoint': 'https://attacker.invalid'}, {'token_file': '/operator-token'},
                        {'operator_token': self.secret}]:
            self.config.write_text(json.dumps(dict(self.settings, **changes)))
            with self.assertRaisesRegex(ValueError, '^Private account-access configuration refused$'):
                bridge.AccessBridge(self.config)

    def test_composite_preserves_other_services_and_routes_only_two_access_tools(self):
        existing = SimpleNamespace(tools=[{'name': 'homecompute_web_search'}], validate=Mock(return_value={}),
                                   request=Mock(return_value={'source': 'existing'}))
        composed = bridge.CompositeServices(existing, self.client)
        self.assertEqual({x['name'] for x in composed.tools}, bridge.NAMES | {'homecompute_web_search'})
        self.assertEqual(composed.request('homecompute_web_search', {}), {'source': 'existing'})
        self.assertEqual(composed.validate('homecompute_access_request', REQUEST), REQUEST)
        existing.validate.assert_not_called()

    def test_core_staging_and_command_only_opt_in_with_private_native_configuration(self):
        setup = load('access_core_setup_fixture', ROOT / 'scripts/setup-openclaw-core-communication.py')
        for name in ('openclaw_access_bridge.py', 'openclaw_site_registry.py', 'openclaw_bitwarden.py'):
            self.assertIn('scripts/' + name, setup.FILES)
        with patch.object(setup, 'CONFIG', self.root):
            command = setup.commands()['adapter']
            self.assertNotIn('--access-config', command)
            (self.root / 'access-requests.json').write_text('{}')
            self.assertNotIn('--access-config', setup.commands()['adapter'])
            (self.root / 'native-mcp-tokens.json').write_text('{}')
            command = setup.commands()['adapter']
            self.assertNotIn('--site-config', command)
            (self.root / 'site-operations.json').write_text('{}')
            command = setup.commands()['adapter']
            self.assertEqual(command[command.index('--site-config') + 1], str(self.root / 'site-operations.json'))
        self.assertEqual(command[command.index('--access-config') + 1], str(self.root / 'access-requests.json'))

    def test_authenticated_native_mcp_inventory_and_calls_do_not_expose_operator_authority(self):
        settings = {'schema_version': 1, 'destination': 'operator-private', 'conversations': ['operator'],
                    'projects': [], 'actions': {}, 'timezone': 'Europe/Copenhagen',
                    'quiet_start_hour': 22, 'quiet_end_hour': 7, 'cooldown_seconds': 1800}
        transport = {name: name + 'x' * 40 for name in ('collector', 'conversation', 'delivery')}
        native = {'openclaw': 'synthetic-native-' + 'y' * 40}
        tasks = SimpleNamespace(tokens={'assistant': 'synthetic-broker-assistant'}, projects=set())
        store = Outbox(self.root / 'transport.sqlite3', settings)
        with self.assertRaises(ValueError):
            COMMUNICATION.serve(store, transport, {}, Mock(), tasks=tasks, access=self.client)
        server = COMMUNICATION.serve(store, transport, {}, Mock(side_effect=AssertionError('model recursion')),
                                     ('127.0.0.1', 0), tasks=tasks, mcp_tokens=native, access=self.client)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def rpc(method, params=None):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/mcp',
                data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode(),
                headers={'Authorization': 'Bearer ' + native['openclaw'], 'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream'})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        try:
            inventory = rpc('tools/list')['result']['tools']
            self.assertTrue(bridge.NAMES <= {item['name'] for item in inventory})
            self.assertFalse(any('access_approve' in item['name'] for item in inventory))
            with patch.object(bridge, 'HTTPConnection', return_value=self.connection()):
                result = rpc('tools/call', {'name': 'homecompute_access_request', 'arguments': REQUEST})
            self.assertFalse(result['result']['isError'])
            self.assertNotIn(self.secret, json.dumps(result))
            with patch.object(bridge, 'HTTPConnection') as factory:
                result = rpc('tools/call', {'name': 'homecompute_access_request', 'arguments': dict(REQUEST, actor='operator')})
            self.assertEqual(result['error']['code'], -32602)
            factory.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(); store.db.close()


class SiteBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve(); self.root.chmod(0o700)
        self.secret = 'synthetic-site-operation-' + 'z' * 40
        self.token = self.root / 'site-operation-token'
        self.token.write_text(self.secret); self.token.chmod(0o600)
        self.config = self.root / 'site-operations.json'
        self.settings = {'schema_version': 1, 'principal': bridge.PRINCIPAL,
                         'endpoint': bridge.SITE_ENDPOINT, 'token_file': str(self.token)}
        self.config.write_text(json.dumps(self.settings)); self.config.chmod(0o600)
        patcher = patch.object(bridge, 'SITE_TOKEN_PATH', self.token)
        patcher.start(); self.addCleanup(patcher.stop)
        self.client = bridge.SiteBridge(self.config)
        self.arguments = {'request_id': REFERENCE, 'operation': 'search', 'parameters': {'query': 'coffee'}}

    def tearDown(self):
        self.temporary.cleanup()

    def receipt(self, **changes):
        return dict({'request_id': REFERENCE, 'operation_id': '00000000-0000-4000-8000-000000000002',
                     'account': 'shop.example', 'operation': 'search', 'status': 'completed', 'results_count': 3,
                     'account_execution_available': True, 'transaction_execution_available': False}, **changes)

    def connection(self, **changes):
        response = Mock(status=200)
        response.read.return_value = json.dumps(self.receipt(**changes)).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        return connection

    def test_fixed_executor_and_finite_projection_never_expose_browser_or_credentials(self):
        connection = self.connection(password='synthetic-private', cookies={'token': self.secret}, html='<private>')
        with patch.object(bridge, 'HTTPConnection', return_value=connection) as factory:
            result = self.client.request('homecompute_site_run', self.arguments)
        factory.assert_called_once_with('127.0.0.1', 18816, timeout=10)
        method, path, body, headers = connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/site/run'))
        self.assertEqual(json.loads(body), self.arguments)
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.secret)
        self.assertEqual(result['results_count'], 3)
        self.assertEqual(set(result), {'request_id', 'operation_id', 'account', 'operation', 'status', 'results_count',
                                       'account_execution_available', 'transaction_execution_available'})
        self.assertNotIn(self.secret, json.dumps(result))
        with patch.object(bridge, 'HTTPConnection', return_value=self.connection()):
            self.client.request('homecompute_site_status', {'request_id': REFERENCE})
        connection.close.assert_called_once()

    def test_only_registered_operation_parameters_may_reach_backend_schema_authority(self):
        invalid = [dict(self.arguments, operation='book'), dict(self.arguments, operation='pay'),
                   dict(self.arguments, url='https://elsewhere.example/'), dict(self.arguments, account='shop.example'),
                   dict(self.arguments, request_id='../../operator')]
        for parameters in [{'password': 'synthetic'}, {'selector': '#pay'}, {'url': 'https://elsewhere.example/'},
                           {'login_mode': 'owner'},
                           {'credential_id': 'synthetic'}, {'token': self.secret}, {'query': {'nested': 'value'}},
                           {'query': ['value']}, {'query': 'https://elsewhere.example/'}, {'query': 'javascript:alert(1)'}]:
            invalid.append(dict(self.arguments, parameters=parameters))
        with patch.object(bridge, 'HTTPConnection') as factory:
            for value in invalid:
                with self.assertRaises(ValueError):
                    self.client.request('homecompute_site_run', value)
            factory.assert_not_called()

    def test_bad_receipts_and_raw_upstream_errors_fail_closed_without_values(self):
        changes = [{'transaction_execution_available': True}, {'results_count': True}, {'results_count': 101},
                   {'operation': 'read'}, {'request_id': '00000000-0000-4000-8000-000000000003'},
                   {'status': 'running', 'operation_id': None}, {'status': 'private-diagnostic'},
                   {'account': 'https://elsewhere.example'}, {'operation_id': 'private-cookie-value'}]
        for change in changes:
            with patch.object(bridge, 'HTTPConnection', return_value=self.connection(**change)):
                with self.assertRaises(ValueError) as error:
                    self.client.request('homecompute_site_run', self.arguments)
                self.assertEqual(str(error.exception), 'Site-operation result unavailable; inspect site status before requesting another operation')
                self.assertTrue(error.exception.__suppress_context__)
        connection = self.connection()
        connection.getresponse.side_effect = RuntimeError(self.secret)
        with patch.object(bridge, 'HTTPConnection', return_value=connection):
            with self.assertRaises(ValueError) as error:
                self.client.request('homecompute_site_run', self.arguments)
            self.assertNotIn(self.secret, str(error.exception))

    def test_site_config_cannot_reuse_other_origin_token_or_principal(self):
        for change in [{'endpoint': bridge.ENDPOINT}, {'principal': 'operator'}, {'token_file': '/operator-token'},
                       {'endpoint': 'https://elsewhere.example/'}, {'password': 'synthetic'}]:
            self.config.write_text(json.dumps(dict(self.settings, **change)))
            with self.assertRaisesRegex(ValueError, '^Private site-operation configuration refused$'):
                bridge.SiteBridge(self.config)

    def test_tools_are_registered_only_with_explicit_executor_configuration(self):
        access = SimpleNamespace(tools=[], validate=Mock(), request=Mock())
        without = bridge.CompositeServices(None, access)
        self.assertFalse(bridge.SITE_NAMES & {item['name'] for item in without.tools})
        with self.assertRaises(ValueError):
            without.request('homecompute_site_run', self.arguments)
        with_site = bridge.CompositeServices(None, access, self.client)
        self.assertEqual({item['name'] for item in with_site.tools}, bridge.SITE_NAMES)
        self.assertEqual(with_site.validate('homecompute_site_run', self.arguments), self.arguments)
        run = next(item for item in with_site.tools if item['name'] == 'homecompute_site_run')
        self.assertFalse(run['annotations']['idempotentHint'])

    def test_access_envelope_accepts_hostname_but_backend_registry_decides_registration(self):
        values = dict(REQUEST, account='shop.example', scope='prepare_purchase')
        self.assertEqual(bridge.validate('homecompute_access_request', values), values)
        result = bridge.project(receipt(account='shop.example', scope='prepare_purchase', account_execution_available=True))
        self.assertEqual(result['account'], 'shop.example')
        self.assertTrue(result['account_execution_available'])

    def test_owner_sign_in_required_receipt_stays_metadata_only_and_private_actions_are_unavailable(self):
        with patch.object(bridge, 'HTTPConnection', return_value=self.connection(
                status='human_sign_in_required', results_count=None, account='hotels.com',
                session_url='https://private.example/owner', screenshot='synthetic-private-image',
                login_mode='owner', vnc_password=self.secret, cookies=self.secret)):
            result = self.client.request('homecompute_site_run', self.arguments)
        self.assertEqual(result['status'], 'human_sign_in_required')
        self.assertEqual(result['operation'], 'search')
        self.assertIsNotNone(result['operation_id'])
        self.assertIsNone(result['results_count'])
        self.assertFalse({'session_url', 'screenshot', 'login_mode', 'vnc_password', 'cookies'} & set(result))
        self.assertNotIn(self.secret, json.dumps(result))
        for change in ({'operation_id': None}, {'operation': None}):
            with self.assertRaises(ValueError): bridge.project_site(self.receipt(status='human_sign_in_required', **change))
        for action in ('homecompute_site_owner_fill', 'homecompute_site_screen', 'homecompute_site_login'):
            with self.assertRaises(ValueError): self.client.validate(action, {'request_id': REFERENCE})
        descriptions = ' '.join(item['description'] for item in bridge.SITE_TOOLS)
        self.assertIn('do not request credentials or verification codes in chat', descriptions)
        self.assertIn('private sign-in page', descriptions)

    def test_authenticated_mcp_registers_site_tools_only_for_native_openclaw(self):
        settings = {'schema_version': 1, 'destination': 'operator-private', 'conversations': ['operator'],
                    'projects': [], 'actions': {}, 'timezone': 'Europe/Copenhagen',
                    'quiet_start_hour': 22, 'quiet_end_hour': 7, 'cooldown_seconds': 1800}
        transport = {name: name + 'x' * 40 for name in ('collector', 'conversation', 'delivery')}
        native = {'openclaw': 'synthetic-native-' + 'y' * 40, 'hermes': 'synthetic-retired-' + 'h' * 40}
        tasks = SimpleNamespace(tokens={'assistant': 'synthetic-broker-assistant'}, projects=set())
        access = SimpleNamespace(tools=bridge.TOOLS, validate=bridge.validate, request=Mock())
        store = Outbox(self.root / 'transport.sqlite3', settings)
        for options in ({'site': self.client}, {'site': self.client, 'mcp_tokens': native}):
            with self.assertRaises(ValueError):
                COMMUNICATION.serve(store, transport, {}, Mock(), tasks=tasks, **options)
        server = COMMUNICATION.serve(store, transport, {}, Mock(), ('127.0.0.1', 0), tasks=tasks,
                                     mcp_tokens=native, access=access, site=self.client)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def rpc(method, params=None, token=None):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/mcp',
                data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode(),
                headers={'Authorization': 'Bearer ' + (token or native['openclaw']),
                         'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        try:
            names = {item['name'] for item in rpc('tools/list')['result']['tools']}
            self.assertTrue(bridge.NAMES | bridge.SITE_NAMES <= names)
            self.assertFalse(any('approve' in name or 'cookie' in name or 'password' in name for name in names))
            with patch.object(bridge, 'HTTPConnection', return_value=self.connection()):
                result = rpc('tools/call', {'name': 'homecompute_site_run', 'arguments': self.arguments})
            self.assertFalse(result['result']['isError'])
            self.assertNotIn(self.secret, json.dumps(result))
            with patch.object(bridge, 'HTTPConnection') as factory:
                invalid = dict(self.arguments, parameters={'password': self.secret})
                result = rpc('tools/call', {'name': 'homecompute_site_run', 'arguments': invalid})
            self.assertEqual(result['error']['code'], -32602)
            self.assertNotIn(self.secret, json.dumps(result))
            factory.assert_not_called()
            for token in (transport['conversation'], native['hermes']):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    rpc('tools/list', token=token)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(); store.db.close()


class NativeAccessPolicyTest(unittest.TestCase):
    def before(self):
        return {'tools': {'allow': ['browser', 'read', 'session_status', 'sessions_spawn'],
                          'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False},
                          'subagents': {'tools': {'allow': ['browser', 'read'], 'deny': ['exec']}}},
                'agents': {'entries': {role: {'tools': {'allow': ['read'], 'deny': ['exec']}}
                                       for role in ['main', 'travel', 'shopper', 'operator', 'family']}}}

    def test_initial_grant_requires_review_and_only_travel_and_shopper_can_call(self):
        before = self.before()
        with self.assertRaises(policy.TeamRefused):
            policy.access_candidate(before)
        granted = policy.access_candidate(before, enable_access_upgrade=True)
        self.assertEqual(before, self.before())
        self.assertTrue(policy.ACCESS_TOOLS <= set(granted['tools']['allow']))
        self.assertTrue(policy.ACCESS_TOOLS <= set(granted['tools']['subagents']['tools']['allow']))
        for role in ['main', 'travel', 'shopper', 'operator', 'family']:
            tools = granted['agents']['entries'][role]['tools']
            self.assertEqual(policy.ACCESS_TOOLS <= set(tools['allow']), role in policy.ACCESS_ROLES)
            self.assertEqual(policy.ACCESS_TOOLS <= set(tools['deny']), role not in policy.ACCESS_ROLES)
        self.assertEqual(policy.access_candidate(granted), granted)

    def test_partial_revoked_or_broadened_grants_are_never_restored(self):
        original = policy.access_candidate(self.before(), enable_access_upgrade=True)
        name = sorted(policy.ACCESS_TOOLS)[0]
        for scope in ['child', 'travel', 'shopper', 'global', 'deny', 'other', 'family', 'main', 'shopper_deny', 'target_both']:
            changed = copy.deepcopy(original)
            if scope == 'child': changed['tools']['subagents']['tools']['allow'].remove(name)
            if scope == 'travel': changed['agents']['entries']['travel']['tools']['allow'].remove(name)
            if scope == 'shopper': changed['agents']['entries']['shopper']['tools']['allow'].remove(name)
            if scope == 'shopper_deny': changed['agents']['entries']['shopper']['tools']['deny'].append(name)
            if scope in {'family', 'main'}: changed['agents']['entries'][scope]['tools']['allow'].append(name)
            if scope == 'target_both':
                for role in policy.ACCESS_ROLES: changed['agents']['entries'][role]['tools']['allow'].remove(name)
            if scope == 'global': changed['tools']['allow'].remove(name)
            if scope == 'deny': changed['agents']['entries']['travel']['tools']['deny'].append(name)
            if scope == 'other': changed['agents']['entries']['operator']['tools']['allow'].append(name)
            with self.assertRaises(policy.TeamRefused):
                policy.access_candidate(changed, enable_access_upgrade=True)

    def legacy(self):
        value = self.before()
        names = policy.LEGACY_ACCESS_TOOLS
        value['tools']['allow'] += sorted(names)
        value['tools']['subagents']['tools']['allow'] += sorted(names)
        for role, entry in value['agents']['entries'].items():
            entry['tools']['allow' if role == 'travel' else 'deny'] += sorted(names)
        return value

    def test_exact_legacy_grant_needs_explicit_upgrade_then_both_roles_get_four_tools(self):
        legacy = self.legacy()
        with self.assertRaises(policy.TeamRefused):
            policy.access_candidate(legacy)
        upgraded = policy.access_candidate(legacy, enable_access_upgrade=True)
        self.assertEqual(policy.access_candidate(upgraded), upgraded)
        for role, entry in upgraded['agents']['entries'].items():
            self.assertEqual(policy.ACCESS_TOOLS & set(entry['tools']['allow']),
                             policy.ACCESS_TOOLS if role in policy.ACCESS_ROLES else set())
            self.assertEqual(policy.ACCESS_TOOLS & set(entry['tools']['deny']),
                             set() if role in policy.ACCESS_ROLES else policy.ACCESS_TOOLS)
        self.assertEqual(legacy, self.legacy())

    def test_removing_every_allow_does_not_turn_a_revoked_grant_into_a_fresh_upgrade(self):
        for state in (self.legacy(), policy.access_candidate(self.before(), enable_access_upgrade=True)):
            for tools in [state['tools'], state['tools']['subagents']['tools'],
                          *[entry['tools'] for entry in state['agents']['entries'].values()]]:
                tools['allow'] = sorted(set(tools['allow']) - policy.ACCESS_TOOLS)
            with self.assertRaises(policy.TeamRefused):
                policy.access_candidate(state, enable_access_upgrade=True)

    def test_legacy_partial_denied_broadened_and_unreviewed_mixes_are_refused(self):
        name = sorted(policy.LEGACY_ACCESS_TOOLS)[0]
        extra = sorted(policy.ACCESS_TOOLS - policy.LEGACY_ACCESS_TOOLS)[0]
        for mutation in ('global', 'child', 'travel', 'shopper', 'family', 'main', 'denial', 'new_tool', 'no_deny'):
            state = self.legacy()
            entries = state['agents']['entries']
            if mutation == 'global': state['tools']['allow'].remove(name)
            if mutation == 'child': state['tools']['subagents']['tools']['allow'].remove(name)
            if mutation == 'travel': entries['travel']['tools']['allow'].remove(name)
            if mutation in {'shopper', 'family', 'main'}: entries[mutation]['tools']['allow'].append(name)
            if mutation == 'denial': entries['travel']['tools']['deny'].append(name)
            if mutation == 'new_tool': state['tools']['allow'].append(extra)
            if mutation == 'no_deny': entries['shopper']['tools']['deny'].remove(name)
            with self.assertRaises(policy.TeamRefused):
                policy.access_candidate(state, enable_access_upgrade=True)

    def test_regular_team_reapply_preserves_an_intact_reviewed_access_grant(self):
        fixture_module = load('access_team_fixture', ROOT / 'tests/openclaw-team-test.py')
        fixture = fixture_module.TeamSetupTest('test_role_capabilities_are_finite_and_keep_separate_workspaces')
        fixture.setUp()
        try:
            baseline = fixture_module.reviewed_candidate(fixture.before, fixture.team)
            granted = policy.access_candidate(baseline, enable_access_upgrade=True)
            reapplied = fixture_module.reviewed_candidate(granted, fixture.team)
            for role in policy.ACCESS_ROLES:
                self.assertTrue(policy.ACCESS_TOOLS <= set(reapplied['agents']['entries'][role]['tools']['allow']))
            self.assertFalse(policy.ACCESS_TOOLS & set(reapplied['agents']['entries']['main']['tools']['allow']))
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
