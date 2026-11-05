#!/usr/bin/env python3
"""Guard and retention tests for OpenClaw recovery snapshot pruning."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'prune_openclaw_recovery', ROOT / 'scripts/prune-openclaw-recovery-backups.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RecoveryPruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'supervisor'
        self.state.mkdir()
        self.backups = []
        for index in range(8):
            backup = self.state / f'error-backup-{index:04d}-test'
            (backup / '.openclaw').mkdir(parents=True)
            (backup / '.openclaw/openclaw.json').write_text('{}')
            (backup / 'verification-ready/.openclaw').mkdir(parents=True)
            (backup / 'verification-ready/.openclaw/openclaw.json').write_text('{}')
            (backup / 'replacement-native.json').write_text('{}')
            (backup / 'restored-config.json').write_text('{}')
            self.backups.append(backup)
        for index, backup in enumerate(self.backups):
            backup.touch(exist_ok=True)
            backup_time = 1000 + index
            import os
            os.utime(backup, (backup_time, backup_time))
        self.current = self.backups[-1]
        (self.state / MOD.TRANSACTION).write_text(json.dumps({
            'status': 'complete', 'stage': 'complete-active-team',
            'backup': str(self.current),
        }))
        (self.state / MOD.LOCK).touch()
        (self.state / 'incident-20261103-pre-recovery').mkdir()

    def test_verified_active_snapshot_and_recent_rollbacks_are_preserved(self):
        current = MOD.require_verified_current(self.state)
        result = MOD.plan(self.state, current)
        self.assertEqual(result['remove_count'], 3)
        self.assertEqual(result['current_backup'], self.current.name)
        self.assertEqual(result['preserve'], [backup.name for backup in self.backups[-5:]])
        self.assertNotIn('incident-20261103-pre-recovery', result['remove'])

    def test_apply_deletes_only_selected_error_backups_and_writes_receipt(self):
        result = MOD.plan(self.state, self.current)
        receipt = MOD.apply(self.state, result)
        for backup in self.backups[-5:]:
            self.assertTrue(backup.exists())
        for backup in self.backups[:3]:
            self.assertFalse(backup.exists())
        self.assertTrue((self.state / 'incident-20261103-pre-recovery').exists())
        self.assertEqual(receipt['removed_count'], 3)
        self.assertEqual(len(list(self.state.glob('recovery-prune-*.json'))), 1)

    def test_refuses_incomplete_or_unverified_current_transaction(self):
        data = json.loads((self.state / MOD.TRANSACTION).read_text())
        data['status'] = 'failed'
        (self.state / MOD.TRANSACTION).write_text(json.dumps(data))
        with self.assertRaises(MOD.Refused):
            MOD.require_verified_current(self.state)

    def test_refuses_symlink_backup_entry(self):
        (self.state / 'error-backup-9999-link').symlink_to(self.current, target_is_directory=True)
        with self.assertRaises(MOD.Refused):
            MOD.plan(self.state, self.current)

    def test_requires_at_least_two_rollback_points(self):
        with self.assertRaises(MOD.Refused):
            MOD.plan(self.state, self.current, keep=1)


if __name__ == '__main__':
    unittest.main()
