"""Owner admission, rollback and native connection boundaries; synthetic credentials only."""
import copy
import hashlib
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


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


setup = load('hermes_mcp_setup', 'setup-hermes-mcp.py')
connect = load('hermes_mcp_connect', 'connect-hermes-mcp.py')


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.module = setup.native()
        self.patches = []
        def start(patcher):
            self.patches.append(patcher); return patcher.start()
        self.start = start
        start(patch.object(setup, 'ROOT', self.root / 'source'))
        start(patch.object(setup, 'STATE', self.root / 'state'))
        files = {}
        for index, name in enumerate(setup.FILES):
            target = self.root / ('installed-' + str(index))
            target.write_bytes(b'old source'); target.chmod(0o644)
            source = setup.ROOT / name; source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b'new source')
            files[name] = (target, setup.digest(target.read_bytes()))
        start(patch.object(setup, 'FILES', files))
        self.tokens = self.root / 'tokens.json'; self.tokens.write_text(json.dumps({'openclaw': 'x' * 48}))
        self.tokens.chmod(0o600)
        start(patch.object(self.module, 'TOKENS', self.tokens))
        start(patch.object(self.module, 'ROOT_UID', os.getuid()))
        start(patch.object(self.module.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        regular = self.module.regular
        start(patch.object(self.module, 'regular', side_effect=lambda path, owner=None, mode=None: regular(path, os.getuid(), mode)))
        start(patch.object(self.module.os, 'fchown')); start(patch.object(self.module.os, 'chown'))
        self.calls = start(patch.object(setup, 'call', return_value='active'))
        self.adapter_state = start(patch.object(setup, 'adapter_state', return_value='active'))
        self.probe = start(patch.object(setup, 'probe'))

    def tearDown(self):
        for patcher in reversed(self.patches): patcher.stop()
        self.tmp.cleanup()

    def reviewed(self):
        plan = setup.plan(self.module, 'a' * 40)
        return plan, setup.digest(json.dumps(plan, sort_keys=True).encode())

    def test_apply_is_idempotent_and_rollback_restores_source_and_credentials(self):
        before = self.tokens.read_bytes()
        plan, digest = self.reviewed()
        receipt = setup.apply(self.module, plan, digest)
        admitted = self.tokens.read_bytes()
        self.assertNotEqual(before, admitted)
        self.assertEqual(set(json.loads(admitted)), {'openclaw', 'hermes-owner'})
        self.assertNotIn('x' * 48, json.dumps(receipt))
        self.assertTrue(all(path.read_bytes() == b'new source' for path, _ in setup.FILES.values()))
        plan, digest = self.reviewed()
        self.assertEqual(setup.apply(self.module, plan, digest), receipt)
        self.assertEqual(self.tokens.read_bytes(), admitted)
        self.assertEqual(setup.rollback(self.module)['state'], 'rolled-back')
        self.assertEqual(self.tokens.read_bytes(), before)
        self.assertTrue(all(path.read_bytes() == b'old source' for path, _ in setup.FILES.values()))

    def test_wrong_plan_or_unknown_source_fails_before_stopping_adapter(self):
        plan, _ = self.reviewed()
        with self.assertRaises(ValueError): setup.apply(self.module, plan, '0' * 64)
        self.calls.assert_not_called()
        next(iter(setup.FILES.values()))[0].write_bytes(b'private unexpected drift')
        with self.assertRaises(ValueError): self.reviewed()
        self.calls.assert_not_called()

    def test_failed_live_probe_restores_original_files_and_bearers(self):
        before = self.tokens.read_bytes(); plan, digest = self.reviewed()
        self.probe.side_effect = ValueError('synthetic failure')
        with self.assertRaises(ValueError): setup.apply(self.module, plan, digest)
        self.assertEqual(self.tokens.read_bytes(), before)
        self.assertTrue(all(path.read_bytes() == b'old source' for path, _ in setup.FILES.values()))
        self.assertFalse((setup.STATE / 'receipt.json').exists())

    def test_failed_adapter_is_allowed_only_for_a_rolled_back_recovery(self):
        plan, digest = self.reviewed()
        self.adapter_state.return_value = 'failed'
        with self.assertRaises(ValueError): setup.apply(self.module, plan, digest)
        self.assertFalse((setup.STATE / 'receipt.json').exists())

        self.adapter_state.return_value = 'active'
        setup.apply(self.module, plan, digest)
        setup.rollback(self.module)
        self.adapter_state.return_value = 'failed'
        plan, digest = self.reviewed()
        receipt = setup.apply(self.module, plan, digest)
        self.assertEqual(receipt['state'], 'applied')
        self.assertEqual(set(json.loads(self.tokens.read_bytes())), {'openclaw', 'hermes-owner'})

    def test_rollback_preserves_later_source_or_credential_changes(self):
        plan, digest = self.reviewed(); setup.apply(self.module, plan, digest)
        self.tokens.write_text(json.dumps({'openclaw': 'z' * 48, 'hermes-owner': 'y' * 48}))
        self.calls.reset_mock()
        with self.assertRaises(ValueError): setup.rollback(self.module)
        self.calls.assert_not_called()

    def test_rollback_preserves_a_file_already_updated_to_the_current_published_source(self):
        plan, digest = self.reviewed(); setup.apply(self.module, plan, digest)
        name = 'scripts/openclaw-communication.py'
        target, _ = setup.FILES[name]
        newer = b'newer published source'
        target.write_bytes(newer)
        (setup.ROOT / name).write_bytes(newer)
        self.calls.reset_mock()
        receipt = setup.rollback(self.module)
        self.assertEqual(receipt['preserved_source_files'], [name])
        self.assertEqual(target.read_bytes(), newer)
        self.assertTrue(all(path.read_bytes() == b'old source'
                            for file, (path, _) in setup.FILES.items() if file != name))
        self.assertEqual([call.args[0][1] for call in self.calls.call_args_list], ['stop', 'start'])


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.token = 'synthetic-owner-' + 'x' * 48
        self.status = {'server': 'homecompute', 'url': connect.URL, 'env': {'names': [connect.KEY]},
                       'provider': {'credentialResolution': {'ok': True}}, 'adapter': {'registered': True},
                       'trustedPrivateTarget': {'state': 'match'}}
        self.digest = hashlib.sha256((connect.SKILL / 'SKILL.md').read_bytes()).hexdigest()
        self.android_digest = hashlib.sha256((connect.ANDROID_SKILL / 'SKILL.md').read_bytes()).hexdigest()

    def test_failed_native_diagnostic_redacts_the_supplied_bearer(self):
        failed = subprocess.CompletedProcess([], 2, 'synthetic failure ' + self.token, 'private endpoint refused')
        with patch.object(connect.subprocess, 'run', return_value=failed):
            with self.assertRaises(ValueError) as error:
                connect.call(['nemohermes', 'hermes-personal-v23', 'mcp', 'add'], env={connect.KEY: self.token})
        self.assertNotIn(self.token, str(error.exception))
        self.assertIn('[redacted]', str(error.exception))

    def test_native_add_passes_secret_only_in_environment_and_preserves_runtime_selection(self):
        commands = []
        def call(args, **kwargs):
            commands.append((args, kwargs))
            if args[2:4] == ['status', '--json']: return json.dumps({'agent': 'hermes', 'phase': 'Ready', 'hostMounts': []})
            if args[2:4] == ['mcp', 'list']: return json.dumps({'bridges': []})
            if args[2:4] == ['mcp', 'status']: return json.dumps(self.status)
            if args[2] == 'exec':
                program = kwargs.get('input', '')
                if 'android-gateway' in program:
                    if not hasattr(self, 'android_probe_count'):
                        self.android_probe_count = 0
                    self.android_probe_count += 1
                    return 'absent' if self.android_probe_count == 1 else self.android_digest
                return self.digest
            return ''
        with patch.object(connect, 'gates'), patch.object(connect, 'network_setup'), patch.object(connect, 'call', side_effect=call):
            result = connect.connect('hermes-personal', self.token)
        self.assertTrue(result['credential_verified'])
        self.assertNotIn(self.token, json.dumps(result))
        addition = next((args, kwargs) for args, kwargs in commands if args[2:4] == ['mcp', 'add'])
        self.assertNotIn(self.token, ' '.join(addition[0]))
        self.assertEqual(addition[1]['env'][connect.KEY], self.token)
        self.assertEqual(addition[1]['env']['OPENSHELL_GATEWAY'], 'nemoclaw')
        self.assertFalse(any('stop' in args or 'start' in args or 'recreate' in args for args, _ in commands))

    def test_existing_conflicting_registration_or_unknown_probe_does_not_remove_or_replace(self):
        for url, verified in (('https://other.example/mcp', True), (connect.URL, None)):
            state = {**self.status, 'url': url, 'provider': {'credentialResolution': {'ok': verified}}}
            with patch.object(connect, 'gates'), patch.object(connect, 'network_setup'), patch.object(connect, 'call', side_effect=[
                    json.dumps({'agent': 'hermes', 'phase': 'Ready'}), json.dumps({'bridges': [state]}),
                    json.dumps(state)]) as call:
                with self.assertRaises(ValueError): connect.connect('hermes-personal-v23', self.token)
            self.assertFalse(any(c.args[0][2:4] in (['mcp', 'add'], ['mcp', 'remove']) for c in call.call_args_list))

    def test_explicit_repair_replaces_only_exact_legacy_boundary_conflict_with_private_backup(self):
        old_list_row = {'server': 'homecompute', 'url': connect.URL,
                        'env': {'names': [], 'missing': [], 'ready': False},
                        'trustedPrivateTarget': {'state': 'match'}}
        old_status = {'server': 'homecompute', 'url': connect.URL,
                      'env': {'names': [], 'missing': [], 'ready': False},
                      'provider': {'name': 'hermes-personal-v23-mcp-homecompute', 'state': 'conflict',
                                   'present': True, 'credentialReady': False,
                                   'credentialResolution': {'ok': False}},
                      'adapter': {'registered': None},
                      'trustedPrivateTarget': {'state': 'match'},
                      'warnings': ['This agent-source MCP credential name no longer satisfies the host-only credential boundary. '
                                   'Restart and rebuild fail closed for it; remove this server, then add it again with a dedicated service credential name.']}
        with tempfile.TemporaryDirectory() as temporary:
            commands = []
            provider_present = True
            provider_attached = True
            def call(args, **kwargs):
                nonlocal provider_present, provider_attached
                commands.append((args, kwargs))
                if args[:5] == ['openshell', '-g', 'nemoclaw', 'policy', 'get']:
                    return json.dumps({'policy': {'network_policies': {}}})
                if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'list']:
                    rows = ([{'name': 'hermes-personal-v23-mcp-homecompute', 'id': 'provider-id',
                              'resource_version': 'rv1', 'type': 'nemoclaw-mcp-v1',
                              'credential_keys': [connect.KEY]}] if provider_present else [])
                    return json.dumps(rows)
                if args[:5] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'list']:
                    return 'hermes-personal-v23\n'
                if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'list']:
                    return ('NAME TYPE CREDENTIAL_KEYS CONFIG_KEYS\n'
                            + ('hermes-personal-v23-mcp-homecompute nemoclaw-mcp-v1 1 0\n'
                               if provider_attached else ''))
                if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'detach']:
                    provider_attached = False
                    return ''
                if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'delete']:
                    provider_present = False
                    return ''
                if args[2:4] == ['status', '--json']:
                    return json.dumps({'agent': 'hermes', 'phase': 'Ready', 'hostMounts': []})
                if args[2:4] == ['mcp', 'list']:
                    return json.dumps({'bridges': [old_list_row] if not any(
                        row[0][2:4] == ['mcp', 'remove'] for row in commands) else []})
                if args[2:4] == ['mcp', 'status']:
                    return json.dumps(old_status if not any(
                        row[0][2:4] == ['mcp', 'add'] for row in commands) else self.status)
                if args[2] == 'exec':
                    program = kwargs.get('input', '')
                    if 'android-gateway' in program:
                        if not hasattr(self, 'repair_android_probe_count'):
                            self.repair_android_probe_count = 0
                        self.repair_android_probe_count += 1
                        return 'absent' if self.repair_android_probe_count == 1 else self.android_digest
                    return self.digest
                return ''
            with patch.object(connect.Path, 'home', return_value=Path(temporary)), \
                 patch.object(connect, 'BACKUPS', Path(temporary) / '.local/state/homecompute/backups'), \
                 patch.object(connect, 'gates'), patch.object(connect, 'network_setup'), \
                 patch.object(connect, 'call', side_effect=call):
                result = connect.connect('hermes-personal-v23', self.token, repair_legacy_registration=True)
            self.assertTrue(result['credential_verified'])
            self.assertIn('replaced_legacy_registration_backup', result)
            backup = Path(result['replaced_legacy_registration_backup'])
            record = json.loads(backup.read_text())
            self.assertEqual(record['provider_state'], 'conflict')
            self.assertEqual(record['env_names'], [])
            self.assertNotIn(self.token, backup.read_text())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        remove_index = next(i for i, (args, _) in enumerate(commands) if args[2:4] == ['mcp', 'remove'])
        add_index = next(i for i, (args, _) in enumerate(commands) if args[2:4] == ['mcp', 'add'])
        self.assertLess(remove_index, add_index)
        remove_args, remove_kwargs = commands[remove_index]
        self.assertNotIn(self.token, ' '.join(remove_args))
        self.assertEqual(remove_args[-1], '--force')
        self.assertNotIn(connect.KEY, remove_kwargs['env'])
        detach_index = next(i for i, (args, _) in enumerate(commands)
                            if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'detach'])
        delete_index = next(i for i, (args, _) in enumerate(commands)
                            if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'delete'])
        self.assertLess(remove_index, detach_index)
        self.assertLess(detach_index, delete_index)
        self.assertLess(delete_index, add_index)
        add_args, add_kwargs = commands[add_index]
        self.assertNotIn(self.token, ' '.join(add_args))
        self.assertEqual(add_kwargs['env'][connect.KEY], self.token)

    def test_repair_flag_does_not_accept_an_unrelated_provider_warning(self):
        old_list_row = {'server': 'homecompute', 'url': connect.URL,
                        'env': {'names': []}, 'trustedPrivateTarget': {'state': 'match'}}
        old_status = {'server': 'homecompute', 'url': connect.URL,
                      'env': {'names': [], 'ready': False},
                      'provider': {'name': 'hermes-personal-v23-mcp-homecompute', 'state': 'conflict',
                                   'present': True, 'credentialReady': False},
                      'adapter': {'registered': None}, 'trustedPrivateTarget': {'state': 'match'},
                      'warnings': ['unrelated conflict']}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(connect.Path, 'home', return_value=Path(temporary)), \
             patch.object(connect, 'BACKUPS', Path(temporary) / '.local/state/homecompute/backups'), \
             patch.object(connect, 'gates'), patch.object(connect, 'network_setup'), \
             patch.object(connect, 'call', side_effect=[
                 json.dumps({'agent': 'hermes', 'phase': 'Ready'}),
                 json.dumps({'bridges': [old_list_row]}), json.dumps(old_status)]) as call:
            with self.assertRaises(ValueError):
                connect.connect('hermes-personal-v23', self.token, repair_legacy_registration=True)
            self.assertFalse(any(row.args[0][2:4] in (['mcp', 'add'], ['mcp', 'remove'])
                                 for row in call.call_args_list))
            self.assertFalse((Path(temporary) / '.local/state/homecompute/backups').exists())

    def test_repair_recovery_requires_private_backup_from_exact_legacy_boundary_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            backup_dir = Path(temporary) / '.local/state/homecompute/backups'
            backup_dir.mkdir(mode=0o700, parents=True)
            path = backup_dir / '123-homecompute.json'
            path.write_text(json.dumps({
                'schema_version': 1,
                'sandbox': 'hermes-personal-v23',
                'server': 'homecompute',
                'url': connect.URL,
                'env_names': [],
                'provider_state': 'conflict',
                'private_target_state': 'match',
                'warnings': ['This agent-source MCP credential name no longer satisfies the host-only credential boundary. '
                             'Restart and rebuild fail closed for it; remove this server, then add it again with a dedicated service credential name.'],
            }))
            path.chmod(0o600)
            with patch.object(connect.Path, 'home', return_value=Path(temporary)), \
                 patch.object(connect, 'BACKUPS', backup_dir):
                self.assertEqual(connect.private_legacy_backup('hermes-personal-v23'), str(path))
                self.assertIsNone(connect.private_legacy_backup('hermes-personal'))
                path.chmod(0o644)
                self.assertIsNone(connect.private_legacy_backup('hermes-personal-v23'))

    def test_stale_provider_cleanup_refuses_attachment_to_another_sandbox_before_mutation(self):
        calls = []
        def call(args, **kwargs):
            calls.append(args)
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'policy', 'get']:
                return json.dumps({'policy': {'network_policies': {}}})
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'list']:
                return json.dumps([{'name': 'hermes-personal-v23-mcp-homecompute', 'id': 'id',
                                    'resource_version': 'rv', 'type': 'nemoclaw-mcp-v1',
                                    'credential_keys': [connect.KEY]}])
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'list']:
                return 'hermes-personal-v23\nhermes-personal\n'
            if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'list']:
                return ('NAME TYPE CREDENTIAL_KEYS CONFIG_KEYS\n'
                        + ('hermes-personal-v23-mcp-homecompute nemoclaw-mcp-v1 1 0\n'
                           if args[6] == 'hermes-personal' else ''))
            return ''
        with patch.object(connect, 'call', side_effect=call):
            with self.assertRaisesRegex(ValueError, 'attached outside its owner sandbox'):
                connect.clear_stale_provider('hermes-personal-v23', {})
        self.assertFalse(any(args[3:5] == ['provider', 'delete'] or args[5] == 'detach'
                             for args in calls))

    def test_stale_provider_cleanup_accepts_only_exact_binding_only_detach_diagnostic(self):
        calls = []
        provider_present = True
        def call(args, **kwargs):
            nonlocal provider_present
            calls.append(args)
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'policy', 'get']:
                return json.dumps({'policy': {'network_policies': {}}})
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'list']:
                rows = ([{'name': 'hermes-personal-v23-mcp-homecompute', 'id': 'id',
                          'resource_version': 'rv', 'type': 'nemoclaw-mcp-v1',
                          'credential_keys': [connect.KEY]}] if provider_present else [])
                return json.dumps(rows)
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'list']:
                return 'hermes-personal-v23\n'
            if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'list']:
                return 'NAME TYPE CREDENTIAL_KEYS CONFIG_KEYS\nhermes-personal-v23-mcp-homecompute nemoclaw-mcp-v1 1 0\n'
            if args[5] == 'detach':
                raise ValueError("native openshell -g nemoclaw sandbox failed: Error:   × code: 'The system is not in a state required for the operation's\n"
                                 "  │ execution', message: \"credential_binding references provider 'hermes-\n"
                                 "  │ personal-v23-mcp-homecompute', but that provider is not attached to the\n"
                                 "  │ sandbox\"")
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'delete']:
                provider_present = False
                return ''
            return ''
        with patch.object(connect, 'call', side_effect=call):
            connect.clear_stale_provider('hermes-personal-v23', {})
        self.assertIn(['openshell', '-g', 'nemoclaw', 'provider', 'delete',
                       'hermes-personal-v23-mcp-homecompute'], calls)

    def test_stale_provider_cleanup_does_not_accept_other_detach_errors(self):
        calls = []
        def call(args, **kwargs):
            calls.append(args)
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'policy', 'get']:
                return json.dumps({'policy': {'network_policies': {}}})
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'provider', 'list']:
                return json.dumps([{'name': 'hermes-personal-v23-mcp-homecompute', 'id': 'id',
                                    'resource_version': 'rv', 'type': 'nemoclaw-mcp-v1',
                                    'credential_keys': [connect.KEY]}])
            if args[:5] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'list']:
                return 'hermes-personal-v23\n'
            if args[:6] == ['openshell', '-g', 'nemoclaw', 'sandbox', 'provider', 'list']:
                return 'NAME TYPE CREDENTIAL_KEYS CONFIG_KEYS\nhermes-personal-v23-mcp-homecompute nemoclaw-mcp-v1 1 0\n'
            if args[5] == 'detach':
                raise ValueError('permission denied for provider detach')
            return ''
        with patch.object(connect, 'call', side_effect=call):
            with self.assertRaisesRegex(ValueError, 'permission denied'):
                connect.clear_stale_provider('hermes-personal-v23', {})
        self.assertFalse(any(args[3:5] == ['provider', 'delete'] for args in calls))

    def test_gate_requires_private_fresh_owner_records(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as temporary, patch.object(connect, 'GATES', Path(temporary)):
            for name, gate in (('backup-readiness.json', 'off-host-backup'), ('agents-network.json', 'agents-network')):
                p = connect.GATES / name
                p.write_text(json.dumps({'schema_version': 1, 'gate': gate, 'scope': 'hermes-household',
                                        'status': 'ready', 'evidence': 'synthetic',
                                        'observed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}))
                p.chmod(0o600)
            connect.gates()
            p.chmod(0o644)
            with self.assertRaises(ValueError): connect.gates()
            p.chmod(0o600)
            row = json.loads(p.read_bytes()); row['observed_at'] = '2000-01-01T00:00:00Z'
            p.write_text(json.dumps(row))
            with self.assertRaises(ValueError): connect.gates()


class ProbeTests(unittest.TestCase):
    def test_waits_for_type_simple_listener_without_retrying_authentication(self):
        names = ('homecompute_school_discover', 'homecompute_shopping_search',
                 'homecompute_system_read', 'homecompute_ha_propose',
                 'homecompute_android_status', 'homecompute_android_screen', 'homecompute_android_apps',
                 'homecompute_android_visible_text', 'homecompute_android_screenshot')
        response = Mock(status=200)
        response.read.return_value = json.dumps({'result': {'tools': [{'name': name} for name in names]}}).encode()
        connection = Mock()
        connection.connect.side_effect = [ConnectionRefusedError(), ConnectionRefusedError(), None]
        connection.getresponse.return_value = response
        module = SimpleNamespace(TOKENS=SimpleNamespace(read_bytes=lambda: b'{"hermes-owner":"synthetic"}'))
        with patch.object(setup.http.client, 'HTTPConnection', return_value=connection), patch.object(setup.time, 'sleep'):
            setup.probe(module)
        self.assertEqual(connection.connect.call_count, 3)
        connection.request.assert_called_once()
        response.status = 401
        connection.connect.side_effect = None
        connection.request.reset_mock()
        with patch.object(setup.http.client, 'HTTPConnection', return_value=connection), patch.object(setup.time, 'sleep'):
            with self.assertRaises(ValueError): setup.probe(module)
        connection.request.assert_called_once()


class NetworkTests(unittest.TestCase):
    def policy(self):
        return {'unrelated': {'preserve': True}, 'network_policies': {'mcp_bridge_homecompute': {
            'binaries': [{'path': '/usr/local/bin/hermes'}, {'path': '/usr/bin/python3*'},
                         {'path': '/opt/hermes/.venv/bin/python*'}],
            'endpoints': [{'host': 'homecompute-capabilities.home.arpa', 'port': 19445, 'path': '/mcp',
                           'protocol': 'mcp', 'enforcement': 'enforce', 'allowed_ips': ['10.77.20.1'],
                           'credential_binding': {'provider': 'hermes-personal-v23-mcp-homecompute'},
                           'mcp': {'max_body_bytes': 131072, 'strict_tool_names': True,
                                   'allow_all_known_mcp_methods': False},
                           'rules': [{'allow': {'method': 'initialize'}}]}]}}}

    def test_only_exact_real_interpreter_is_added_and_repeat_is_idempotent(self):
        before = self.policy(); raw = json.dumps(before, sort_keys=True)
        after = connect.network_candidate(before, 'hermes-personal-v23')
        self.assertEqual(json.dumps(before, sort_keys=True), raw)
        self.assertEqual(after, connect.network_candidate(after, 'hermes-personal-v23'))
        after['network_policies']['mcp_bridge_homecompute']['binaries'].pop()
        self.assertEqual(after, before)

    def test_unknown_endpoint_binding_pins_or_binary_grants_are_preserved(self):
        for key, value in (('host', 'other.example'), ('path', '/admin'), ('allowed_ips', ['10.77.20.2']),
                           ('credential_binding', {'provider': 'openclaw'}), ('protocol', 'rest')):
            before = self.policy()
            before['network_policies']['mcp_bridge_homecompute']['endpoints'][0][key] = value
            with self.assertRaises(ValueError): connect.network_candidate(before, 'hermes-personal-v23')
        before = self.policy(); before['network_policies']['mcp_bridge_homecompute']['binaries'].append({'path': '/**'})
        with self.assertRaises(ValueError): connect.network_candidate(before, 'hermes-personal-v23')

    def test_stale_provider_policy_cleanup_selects_only_exact_speech_routes(self):
        sandbox = 'hermes-personal-v23'
        network = {}
        for key, path in (('homecompute_transcription', '/v1/audio/transcriptions'),
                          ('homecompute_speech', '/v1/audio/speech')):
            network[key] = {'name': key, 'binaries': [{'path': connect.PYTHON}], 'endpoints': [{
                'host': 'homecompute-capabilities.home.arpa', 'port': 19445, 'path': path,
                'allowed_ips': ['10.77.20.1'],
                'credential_binding': {'provider': sandbox + '-mcp-homecompute'},
                'protocol': 'rest', 'enforcement': 'enforce',
                'rules': [{'allow': {'method': 'POST', 'path': path}}],
            }]}
        policy = {'unrelated': {'preserve': True}, 'network_policies': network}
        entries = connect.speech_policy_entries(policy, sandbox)
        self.assertEqual(set(entries), set(network))
        invalid = copy.deepcopy(policy)
        invalid['network_policies']['homecompute_speech']['endpoints'][0]['rules'] = [
            {'allow': {'method': 'POST', 'path': '/**'}}]
        with self.assertRaisesRegex(ValueError, 'differs'):
            connect.speech_policy_entries(invalid, sandbox)
        invalid = copy.deepcopy(policy)
        invalid['network_policies']['other'] = {'endpoints': [{
            'credential_binding': {'provider': sandbox + '-mcp-homecompute'}}]}
        with self.assertRaisesRegex(ValueError, 'unexpected policy'):
            connect.speech_policy_entries(invalid, sandbox)


if __name__ == '__main__':
    unittest.main()
