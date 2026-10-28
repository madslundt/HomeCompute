"""Privacy-pruned failures remain observable without workflow or execution bodies."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import n8n_observations as monitor


class WorkflowHealthTests(unittest.TestCase):
    def test_live_wal_and_soft_deleted_errors_are_counted_without_payload_reads(self):
        now = datetime(2026, 10, 28, 10, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'database.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE workflow_entity (id TEXT, active INTEGER, isArchived INTEGER)')
                db.execute('CREATE TABLE execution_entity (workflowId TEXT, status TEXT, mode TEXT, stoppedAt TEXT, deletedAt TEXT)')
                db.executemany('INSERT INTO workflow_entity VALUES (?,?,?)', [('active', 1, 0), ('retired', 0, 1)])
                db.executemany('INSERT INTO execution_entity VALUES (?,?,?,?,?)', [
                    ('active', 'error', 'trigger', now.isoformat(), 'privacy-pruned'),
                    ('active', 'crashed', 'webhook', now.isoformat(), None),
                    ('active', 'error', 'manual', now.isoformat(), None),
                    ('retired', 'error', 'trigger', now.isoformat(), None),
                    ('active', 'error', 'trigger', (now - timedelta(hours=1)).isoformat(), None),
                    ('active', 'success', 'trigger', now.isoformat(), None)])
                db.commit()
                value = monitor.collect(path, now=now)
                self.assertEqual(value['failure_count'], 2)
                self.assertEqual(value['window_seconds'], 900)
                self.assertNotIn('active', json.dumps(value))
                self.assertNotIn('privacy-pruned', json.dumps(value))
                self.assertEqual(db.execute('SELECT count(*) FROM execution_entity').fetchone()[0], 6)
                self.assertEqual(monitor.project(value, now), ('degraded', {'failed': True}))

    def test_missing_database_never_created_or_reported_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing'
            with self.assertRaises(sqlite3.Error):
                monitor.collect(path)
            self.assertFalse(path.exists())
        self.assertEqual(monitor.project(None, datetime.now(timezone.utc)),
                         ('degraded', {'reason': 'collection_failed'}))

    def test_stale_malformed_and_changing_counts_do_not_claim_recovery_or_repeat_alerts(self):
        now = datetime.now(timezone.utc)
        value = dict(schema_version=1, generated_at=now.isoformat(), window_seconds=900, failure_count=1)
        self.assertEqual(monitor.project(value, now), monitor.project(dict(value, failure_count=25), now))
        self.assertEqual(monitor.project(dict(value, failure_count=0), now), ('healthy', {'failed': False}))
        for wrong in (dict(value, failure_count=True), dict(value, failure_count=-1),
                      dict(value, generated_at=(now - timedelta(hours=1)).isoformat()),
                      dict(value, schema_version=True), dict(value, private='SECRET')):
            self.assertEqual(monitor.project(wrong, now)[0], 'degraded')


if __name__ == '__main__':
    unittest.main()
