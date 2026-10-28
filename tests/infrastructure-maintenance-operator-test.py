"""Operator review/reconciliation and deployment pin preservation."""
import contextlib
import hashlib
import json
from pathlib import Path
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

    def test_full_deployment_cannot_silently_replace_active_image_override(self):
        source=(ROOT/'scripts/deploy-home-core.sh').read_text()
        guard=source.split("python3 - /var/lib/homecompute-infrastructure-maintenance/plans.sqlite <<'PY'\n",1)[1].split('\nPY\n',1)[0]
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'plans.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE plans(plan TEXT,status TEXT)')
                db.execute('INSERT INTO plans VALUES(?,?)',(json.dumps(self.plan),'registered'))
            self.assertEqual(subprocess.run([sys.executable,'-',str(database)],input=guard,text=True,capture_output=True).returncode,0)
            for status in ('executing','uncertain','succeeded'):
                with sqlite3.connect(database) as db: db.execute('UPDATE plans SET status=?',(status,))
                result=subprocess.run([sys.executable,'-',str(database)],input=guard,text=True,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Reconcile infrastructure image overrides',result.stderr)
            with sqlite3.connect(database) as db: db.execute("UPDATE plans SET status='reconciled'")
            self.assertEqual(subprocess.run([sys.executable,'-',str(database)],input=guard,text=True,capture_output=True).returncode,0)


if __name__ == '__main__': unittest.main()
