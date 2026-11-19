"""Operator review/reconciliation and deployment pin preservation."""
import contextlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from infrastructure_maintenance_operator import operate


class OperatorTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE plans(id TEXT,plan TEXT,status TEXT,reason TEXT)')
        self.plan = {'steps': [['/usr/bin/docker', 'compose', '-f',
                     '/var/lib/homecompute-infrastructure-maintenance/prepared/plan.image.json', 'up']]}
        self.db.execute('INSERT INTO plans VALUES(?,?,?,NULL)', ('a'*32,json.dumps(self.plan),'registered'))
        self.controller = SimpleNamespace(uid=0, connect=lambda: contextlib.nullcontext(self.db),
            locked=lambda: contextlib.nullcontext(),
            row=lambda db, pid: db.execute('SELECT * FROM plans WHERE id=?',(pid,)).fetchone(),
            public=lambda row: {'status':row['status']})

    def tearDown(self): self.db.close()

    def test_root_review_exposes_exact_plan_and_never_approves_or_runs(self):
        with patch('infrastructure_maintenance_operator.os.geteuid', return_value=0):
            result = operate(self.controller, 'review', 'a'*32)
            self.assertEqual(result['exact_plan'], self.plan)
            self.assertEqual(result['receipt']['status'], 'registered')
        with patch('infrastructure_maintenance_operator.os.geteuid', return_value=1000), self.assertRaises(ValueError):
            operate(self.controller, 'review', 'a'*32)

    def test_reconciliation_never_consumes_pending_approval_or_resets_attempt(self):
        with patch('infrastructure_maintenance_operator.os.geteuid', return_value=0):
            with self.assertRaises(ValueError): operate(self.controller,'reconcile','a'*32)
            self.db.execute("UPDATE plans SET status='uncertain'")
            self.assertEqual(operate(self.controller,'reconcile','a'*32)['status'],'reconciled')
            with self.assertRaises(ValueError): operate(self.controller,'reconcile','a'*32)

    def release_guard(self, plan, status, base):
        source = (ROOT / 'scripts/deploy-home-core.sh').read_text()
        invocation = re.search(
            r"python3 - /var/lib/homecompute-infrastructure-maintenance/plans\.sqlite [^\n]*<<'PY'\n(.*?)\nPY\n",
            source, re.DOTALL,
        )
        self.assertIsNotNone(invocation)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'plans.sqlite'
            with contextlib.closing(sqlite3.connect(database)) as connection, connection:
                connection.execute('CREATE TABLE plans(plan TEXT,status TEXT)')
                connection.execute('INSERT INTO plans VALUES(?,?)', (json.dumps(plan), status))
            return subprocess.run([sys.executable, '-', str(database), base],
                                  input=invocation[1], text=True, capture_output=True)

    def test_full_deployment_cannot_silently_replace_active_image_override(self):
        for base in ('', 'b' * 40):
            for status in ('registered', 'executing', 'uncertain', 'succeeded', 'reconciled'):
                with self.subTest(approved_release=bool(base), status=status):
                    result = self.release_guard(self.plan, status, base)
                    if status in {'registered', 'reconciled'}:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('Reconcile infrastructure image overrides', result.stderr)

    def test_approved_release_respects_pending_nixos_package_reconciliation(self):
        plan = {'policy': {'target': 'home-core'}, 'steps': [
            ['/run/current-system/sw/bin/nix-env', '--profile', '/nix/var/nix/profiles/system',
             '--set', '/nix/store/candidate'],
        ]}
        blocked = self.release_guard(plan, 'succeeded', 'b' * 40)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn('Reconcile the NixOS package generation', blocked.stderr)
        for status, base in (('reconciled', 'b' * 40), ('succeeded', '')):
            result = self.release_guard(plan, status, base)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
