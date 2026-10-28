#!/usr/bin/env python3
"""Operator-only native upgrade fences and truthful restart/report metadata."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('access_native_setup', ROOT / 'scripts/setup-openclaw-access-native.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class NativeSetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / 'openclaw.json'
        self.before = {'tools': {'allow': ['read'], 'fs': {'workspaceOnly': True},
                                  'elevated': {'enabled': False}, 'subagents': {'tools': {'allow': ['read']}}},
                       'agents': {'entries': {role: {'tools': {'allow': ['read'], 'deny': []}}
                                              for role in ('main', 'travel', 'shopper', 'family')}},
                       'gateway': {'mode': 'synthetic-private'}}
        self.config.write_text(json.dumps(self.before)); self.config.chmod(0o600)
        patcher = patch.object(setup, 'CONFIG', self.config)
        patcher.start(); self.addCleanup(patcher.stop)

    def digest(self):
        return hashlib.sha256(self.config.read_bytes()).hexdigest()

    def run_setup(self, *args):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['setup', *args]), contextlib.redirect_stdout(output):
            setup.main()
        return json.loads(output.getvalue())

    def apply_batch(self, batch, apply):
        if apply:
            value = json.loads(self.config.read_bytes())
            value.update({entry['path']: entry['value'] for entry in batch})
            self.config.write_text(json.dumps(value))

    def test_review_is_private_side_effect_free_and_reports_exact_roles_and_tools(self):
        with patch.object(setup, 'native') as native:
            result = self.run_setup()
        self.assertEqual(result['roles'], ['shopper', 'travel'])
        self.assertEqual(set(result['grant']), setup.ACCESS_TOOLS)
        self.assertEqual(len(result['grant']), 4)
        self.assertTrue(result['changed'])
        self.assertFalse(result['restart_required'])
        self.assertFalse(result['applied'])
        self.assertEqual(json.loads(self.config.read_bytes()), self.before)
        self.assertFalse((self.root / 'access-setup-backups').exists())
        self.assertEqual(native.call_args.args[1], False)

    def test_apply_uses_review_hash_private_backup_readback_and_truthful_restart(self):
        raw = self.config.read_bytes()
        with patch.object(setup, 'native', side_effect=self.apply_batch):
            result = self.run_setup('--apply', '--expected-config-sha256', self.digest())
            repeated = self.run_setup('--apply', '--expected-config-sha256', self.digest())
        self.assertTrue(result['restart_required'])
        self.assertFalse(repeated['restart_required'])
        self.assertFalse(repeated['changed'])
        backups = list((self.root / 'access-setup-backups').glob('*/before.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(backups[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(self.config.read_bytes())['gateway'], self.before['gateway'])

    def test_wrong_or_missing_hash_refuses_before_native_calls(self):
        for args in (('--apply',), ('--apply', '--expected-config-sha256', 'a' * 64)):
            with patch.object(setup, 'native') as native, self.assertRaisesRegex(ValueError, 'source changed'):
                self.run_setup(*args)
            native.assert_not_called()
        self.assertEqual(json.loads(self.config.read_bytes()), self.before)

    def test_edit_during_native_validation_refuses_before_backup_or_apply(self):
        def change_source(batch, apply):
            self.assertFalse(apply)
            self.config.write_text(json.dumps(dict(self.before, changed=True)))
        with patch.object(setup, 'native', side_effect=change_source) as native:
            with self.assertRaisesRegex(ValueError, 'changed during validation'):
                self.run_setup('--apply', '--expected-config-sha256', self.digest())
        self.assertEqual(native.call_count, 1)
        self.assertFalse((self.root / 'access-setup-backups').exists())

    def test_protected_readback_mutation_is_detected_and_backup_retained(self):
        def mutate(batch, apply):
            self.apply_batch(batch, apply)
            if apply:
                value = json.loads(self.config.read_bytes())
                value['gateway'] = {'mode': 'changed'}
                self.config.write_text(json.dumps(value))
        with patch.object(setup, 'native', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'Protected configuration readback'):
                self.run_setup('--apply', '--expected-config-sha256', self.digest())
        self.assertEqual(len(list((self.root / 'access-setup-backups').glob('*/before.json'))), 1)


if __name__ == '__main__':
    unittest.main()
