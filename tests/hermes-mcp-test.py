"""Owner admission, rollback and native connection boundaries; synthetic credentials only."""
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

    def test_rollback_preserves_later_source_or_credential_changes(self):
        plan, digest = self.reviewed(); setup.apply(self.module, plan, digest)
        self.tokens.write_text(json.dumps({'openclaw': 'z' * 48, 'hermes-owner': 'y' * 48}))
        self.calls.reset_mock()
        with self.assertRaises(ValueError): setup.rollback(self.module)
        self.calls.assert_not_called()


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.token = 'synthetic-owner-' + 'x' * 48
        self.status = {'server': 'homecompute', 'url': connect.URL, 'env': {'names': [connect.KEY]},
                       'provider': {'credentialResolution': {'ok': True}}, 'adapter': {'registered': True},
                       'trustedPrivateTarget': {'state': 'match'}}
        self.digest = hashlib.sha256((connect.SKILL / 'SKILL.md').read_bytes()).hexdigest()

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
            if args[2] == 'exec': return self.digest
            return ''
        with patch.object(connect, 'gates'), patch.object(connect, 'call', side_effect=call):
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
            with patch.object(connect, 'gates'), patch.object(connect, 'call', side_effect=[
                    json.dumps({'agent': 'hermes', 'phase': 'Ready'}), json.dumps({'bridges': [state]}),
                    json.dumps(state)]) as call:
                with self.assertRaises(ValueError): connect.connect('hermes-personal-v23', self.token)
            self.assertFalse(any(c.args[0][2:4] in (['mcp', 'add'], ['mcp', 'remove']) for c in call.call_args_list))

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
                 'homecompute_system_read', 'homecompute_ha_propose')
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


if __name__ == '__main__':
    unittest.main()
