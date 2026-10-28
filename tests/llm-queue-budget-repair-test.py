#!/usr/bin/env python3
"""Paused-snapshot repair changes code only and rejects state/code races."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('queue_budget_repair', ROOT / 'scripts/repair-llm-queue-budget.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def legacy_code():
    code = repair.pause_restore_code()
    start, end = code.index('  if (Number.isFinite(deadline)) {'), code.index('  return {json,')
    old = '''  if (paused >= 120000 && Number.isFinite(deadline)) {
    const key = `aulaCollectorRetry:${$execution.id}`;
    if (json.__retryKey !== key || !state[key]?.context || state[key].context.processingDeadline !== json.processingDeadline) throw new Error('Aula retry context changed while waiting');
    json.processingDeadline = new Date(deadline + paused).toISOString();
    state[key].context.processingDeadline = json.processingDeadline;
  }
'''
    return code[:start] + old + code[end:]


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'database.sqlite'
        self.plan = self.root / 'plan'
        self.graph = {'nodes': [{'name': name, 'type': 'n8n-nodes-base.code',
                       'notes': repair.MARKER, 'parameters': {'jsCode': legacy_code()}} for name in sorted(repair.NAMES)],
                      'connections': {'unchanged': {'main': []}}, 'staticData': {}}
        self.graph['nodes'].append({'name': 'Existing effect', 'type': 'n8n-nodes-base.httpRequest',
                                    'parameters': {'url': 'https://example.test/existing'}})
        with sqlite3.connect(self.db) as db:
            db.executescript('''CREATE TABLE execution_entity(id INTEGER,status TEXT,workflowId TEXT,waitTill TEXT);
                CREATE TABLE execution_data(executionId INTEGER,workflowData TEXT,data TEXT);
                CREATE TABLE workflow_entity(id TEXT,nodes TEXT,versionId TEXT);''')
            db.execute('INSERT INTO execution_entity VALUES(?,?,?,?)', (123, 'waiting', repair.WORKFLOW, '2026-10-26 17:00:00'))
            db.execute('INSERT INTO execution_data VALUES(?,?,?)', (123, json.dumps(self.graph), 'private unchanged held input'))
            db.execute('INSERT INTO workflow_entity VALUES(?,?,?)', (repair.WORKFLOW, json.dumps(self.graph['nodes']), 'version'))

    def tearDown(self):
        self.tmp.cleanup()

    def staged(self):
        repair.stage(self.db, self.plan, 123)
        return repair.sha((self.plan / 'manifest.json').read_text())

    def apply(self, digest):
        return repair.apply(self.db, self.plan, digest, lambda: datetime(2026, 10, 26, 16, tzinfo=timezone.utc))

    def test_legacy_fixture_matches_exact_source_pin(self):
        self.assertEqual(repair.sha(legacy_code()), repair.LEGACY_SHA)

    def test_only_budget_code_changes_and_all_execution_inputs_and_state_survive(self):
        digest = self.staged()
        result = self.apply(digest)
        with sqlite3.connect(self.db) as db:
            workflow, data = db.execute('SELECT workflowData,data FROM execution_data').fetchone()
            self.assertEqual(data, 'private unchanged held input')
            self.assertEqual(db.execute('SELECT status,waitTill FROM execution_entity').fetchone(), ('waiting', '2026-10-26 17:00:00'))
        expected, _ = repair.patch_graph(self.graph)
        self.assertEqual(json.loads(workflow), expected)
        self.assertTrue(result['execution_data_unchanged'])
        self.assertEqual((self.plan / 'execution.workflow.before.json').stat().st_mode & 0o777, 0o600)

    def test_running_or_near_resume_execution_is_rejected(self):
        digest = self.staged()
        for status, wake in [('running', '2026-10-26 17:00:00'), ('waiting', '2026-10-26 16:00:05')]:
            with sqlite3.connect(self.db) as db:
                db.execute('UPDATE execution_entity SET status=?,waitTill=?', (status, wake))
            with self.assertRaises(ValueError):
                self.apply(digest)

    def test_changed_snapshot_or_candidate_cannot_be_applied(self):
        digest = self.staged()
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE execution_data SET workflowData=?', ('{}',))
        with self.assertRaises(ValueError):
            self.apply(digest)
        with self.assertRaises(ValueError):
            self.apply('wrong digest')


if __name__ == '__main__':
    unittest.main()
