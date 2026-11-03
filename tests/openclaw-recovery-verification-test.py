"""Native restore proof cannot be supplied by a boot prompt or another VM."""
import base64
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    s = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m

m = module('native_restore', 'scripts/verify-openclaw-recovery.py')
probe = module('guest_probe', 'deploy/openclaw/recovery/inspect-restored-state.py')

class NativeRestoreTests(unittest.TestCase):
    def test_private_startup_diagnostics_bound_and_redact_health_errors(self):
        from types import SimpleNamespace
        with patch.object(probe.subprocess,'run',return_value=SimpleNamespace(stdout=b'active')):
            details=probe.private_diagnostics({'returncode':1,'stderr':'Bearer synthetic-secret token=secret https://private.example/path'})
        self.assertNotIn('synthetic-secret',details['native_stderr'])
        self.assertNotIn('token=secret',details['native_stderr'])
        self.assertNotIn('private.example',details['native_stderr'])
        self.assertEqual(details['native_exit_code'],1)
        self.assertLessEqual(len(details['native_stderr']),2048)

    def test_larger_limits_are_selected_before_engine_drops_privileges(self):
        dropped=False
        def drop(*args,**kwargs):
            nonlocal dropped
            dropped=True
        def limit(*args,**kwargs):
            if dropped:raise PermissionError('cannot raise hard limits after setuid')
        with patch.object(m.ENGINE,'vm_child',side_effect=drop) as prepare,patch.object(m.resource,'setrlimit',side_effect=limit):
            m.child(65534,65534,300)
        self.assertEqual(prepare.call_args.kwargs,{'memory_limit':12*1024**3,'cpu_seconds':900,'file_limit':2*1024**3})

    def test_native_sqlite_inspection_reads_adjacent_wal_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'agents/main/agent/openclaw-agent.sqlite';path.parent.mkdir(parents=True)
            (root/'workspace').mkdir()
            config={'agents':{'entries':{name:{} for name in ['main',*[f'role{i}' for i in range(11)]]}},
                    'cron':{'enabled':False}}
            (root/'openclaw.json').write_text(json.dumps(config))
            with sqlite3.connect(path) as db,patch.object(probe,'STATE',root):
                db.execute('PRAGMA journal_mode=WAL')
                for table in ('session_nodes','session_windows','transcript_events'):
                    db.execute('CREATE TABLE '+table+' (id TEXT)')
                    db.execute('INSERT INTO '+table+' VALUES (?)',('synthetic retained row',))
                db.commit()
                self.assertTrue(path.with_name(path.name+'-wal').exists())
                result=probe.inspect(probe.policy_digest(config))
                self.assertTrue(result['native_conversations_verified'])
                self.assertEqual(result['native_conversation_counts']['transcript_events'],1)
                (root/'corrupt.sqlite').write_bytes(b'SQLite format 3\0'+b'broken'*100)
                with self.assertRaises(sqlite3.DatabaseError):probe.inspect(probe.policy_digest(config))

    def test_seed_has_public_probe_and_hash_but_no_credentials_or_network_configuration(self):
        value = json.loads(m.seed('synthetic-nonce', 'a' * 64).split('\n', 1)[1])
        self.assertEqual(value['network'], {'config': 'disabled'})
        self.assertFalse(value['package_update'])
        self.assertNotIn('users', value)
        body = base64.b64decode(value['write_files'][0]['content'])
        self.assertEqual(body, (ROOT / m.PROBE).read_bytes())
        self.assertEqual(value['runcmd'][0][-2:], ['--expected-policy-sha256', 'a' * 64])

    def test_cow_vm_has_no_network_or_host_write_handle(self):
        args = m.command('/nix/store/synthetic/bin/qemu-system-x86_64', [21, 22])
        self.assertEqual(args[args.index('-nic') + 1], 'none')
        self.assertEqual(args[args.index('-m') + 1], '8192')
        self.assertIn('file=/dev/fdset/1,if=virtio,format=qcow2,snapshot=on', args)
        self.assertIn('file=/dev/fdset/2,if=virtio,format=raw,readonly=on', args)
        self.assertNotIn('-netdev', args)
        self.assertNotIn('-qmp', args)
        self.assertNotIn('-virtfs', args)

    def test_only_one_matching_nonce_receipt_is_accepted(self):
        line = b'HC_OPENCLAW_RESTORE_selected {"native_policy_verified":true}\n'
        self.assertEqual(m.result_from_log(line, 'selected'), {'native_policy_verified': True})
        self.assertIsNone(m.result_from_log(line, 'other'))
        self.assertIsNone(m.result_from_log(line + line, 'selected'))
        self.assertIsNone(m.result_from_log(b'agents login:\n', 'selected'))

    def test_native_integrity_identity_plugins_and_authenticated_gateway_are_required(self):
        result = {key: True for key in ('native_policy_verified', 'native_sqlite_integrity',
                                       'authenticated_gateway_verified', 'required_plugins_loaded',
                                       'native_conversations_verified','runtime_version_verified')}
        result.update(roles=12, native_sqlite_databases=1, gateway_plugin_errors=0,
                      openclaw_version='2026.9.1', image=probe.IMAGE)
        self.assertTrue(m.accepted(result))
        for key in ('native_policy_verified', 'native_sqlite_integrity', 'authenticated_gateway_verified',
                    'required_plugins_loaded', 'roles', 'native_sqlite_databases', 'image', 'openclaw_version'):
            self.assertFalse(m.accepted({**result, key: None}))
        self.assertFalse(m.accepted({**result, 'gateway_plugin_errors': 1}))
        self.assertFalse(m.accepted({**result, 'native_conversations_verified': False}))
        self.assertFalse(m.accepted({**result, 'runtime_version_verified': False}))

    def test_policy_hash_retains_authority_and_sharing_but_allows_native_auth_rotation(self):
        original = {key: {} for key in probe.SECTIONS}
        original['channels'] = {'mattermost': {'enabled': True, 'accounts': {'private': {'token': 'synthetic'}}}}
        expected = probe.policy_digest(original)
        self.assertEqual(expected, probe.policy_digest({**original, 'gateway': {'auth': {'token': 'synthetic-new'}}}))
        for key in probe.SECTIONS:
            self.assertNotEqual(expected, probe.policy_digest({**original, key: {'changed': True}}))

if __name__ == '__main__':
    unittest.main()
