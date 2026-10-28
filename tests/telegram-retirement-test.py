"""Telegram retirement pauses mail and retains outbound notifications."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from contextlib import closing
import json
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retirement', ROOT / 'scripts/retire_telegram_workflows.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RetirementTest(unittest.TestCase):
    def test_read_retries_refuse_writes_and_preserve_existing_bounded_retries(self):
        flow = {'id': 'Bp4ePUN4mEDzs7g8', 'nodes': [{'name': 'Read participation before native reconciliation',
                'type': 'n8n-nodes-base.homeAssistant', 'parameters': {'resource': 'state', 'operation': 'get'}}]}
        self.assertEqual(module.read_retry_operations(flow)[0]['settings']['maxTries'], 3)
        flow['nodes'][0]['retryOnFail'] = True
        self.assertEqual(module.read_retry_operations(flow), [])
        flow['nodes'][0]['parameters']['operation'] = 'set'
        with self.assertRaises(ValueError): module.read_retry_operations(flow)

    def fixture(self):
        return {'id': module.FAMILY_CALENDAR_ID, 'name': 'Family Gmail + Telegram calendar',
                'nodes': [
                    {'name': 'New unread Gmail', 'type': 'n8n-nodes-base.gmailTrigger'},
                    {'name': 'Telegram Trigger (disabled - webhook)', 'type': 'n8n-nodes-base.telegramTrigger', 'disabled': True},
                    {'name': 'Expand Telegram updates', 'type': 'n8n-nodes-base.code'},
                    {'name': 'Telegram attachment?', 'type': 'n8n-nodes-base.switch'},
                    {'name': 'Download Telegram attachment', 'type': 'n8n-nodes-base.telegram', 'parameters': {'resource': 'file'}},
                    {'name': 'Restore Telegram message', 'type': 'n8n-nodes-base.code'},
                    {'name': 'Build multimodal extraction request', 'type': 'n8n-nodes-base.code'},
                    {'name': 'LLM queue · 36f4bb4 · Hold input', 'type': 'n8n-nodes-base.noOp'},
                ], 'connections': {
                    'New unread Gmail': {'main': [[{'node': 'LLM queue · 36f4bb4 · Hold input', 'type': 'main', 'index': 0}]]},
                    'Restore Telegram message': {'main': [[{'node': 'LLM queue · 36f4bb4 · Hold input', 'type': 'main', 'index': 0}]]},
                    'Telegram attachment?': {'main': [[{'node': 'Build multimodal extraction request', 'type': 'main', 'index': 0}]]},
                }}

    def test_disables_inputs_and_cuts_only_telegram_edges(self):
        result = module.operations(self.fixture())
        disabled = {x['nodeName'] for x in result if x['type'] == 'setNodeDisabled'}
        self.assertEqual(disabled, (set(module.INPUT_NODE_NAMES) | {'New unread Gmail'}) - {'Telegram Trigger (disabled - webhook)'})
        removed = [x for x in result if x['type'] == 'removeConnection']
        self.assertEqual({x['source'] for x in removed}, {'Restore Telegram message', 'Telegram attachment?', 'New unread Gmail'})
        self.assertTrue(any(x.get('nodeName') == 'New unread Gmail' and x['disabled'] for x in result))
        self.assertTrue(all(x['type'] not in {'removeNode', 'setNodeCredential'} for x in result))

    def test_refuses_unknown_workflow_and_missing_gmail(self):
        flow = self.fixture()
        flow['id'] = 'unreviewed'
        with self.assertRaises(ValueError): module.operations(flow)
        flow = self.fixture()
        flow['nodes'] = [n for n in flow['nodes'] if 'gmailTrigger' not in n['type']]
        with self.assertRaises(ValueError): module.operations(flow)

    def test_notification_workflows_are_not_retired(self):
        self.assertEqual(module.RETIRE_IDS, ('hcOpenClawTelegram20261009', 'p51AOjL4ggMQfsuA'))

    def test_cli_only_unpublishes_reviewed_reply_and_never_restarts(self):
        with patch.object(module.subprocess, 'run') as run:
            result = module.unpublish_retired()
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.args[0][:5] == ['docker', 'exec', 'homecompute-automation-n8n-1', 'n8n', 'unpublish:workflow'] for call in run.call_args_list))
        self.assertTrue(result['restart_required'])
        self.assertEqual(result['history_deleted'], 0)


class OrphanWaitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'database.sqlite'
        self.backups = self.root / 'backups'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE execution_entity(id INTEGER PRIMARY KEY,status TEXT,waitTill TEXT,stoppedAt TEXT)')
            db.execute('CREATE TABLE execution_data(executionId INTEGER PRIMARY KEY,data TEXT)')
            for parent, child in module.ORPHAN_PAIRS.items():
                data = [{'resultData': '1'}, {'runData': '2'}, {'Call Aula collector': '3'}, ['4'],
                        {'metadata': '5'}, {'subExecution': '6'}, {'executionId': '7'}, str(child)]
                db.execute('INSERT INTO execution_entity VALUES (?,?,?,NULL)', (parent, 'waiting', '3000-01-01 00:00:00.000'))
                db.execute('INSERT INTO execution_data VALUES (?,?)', (parent, json.dumps(data)))

    def run_repair(self):
        with patch.object(module, 'container_running', return_value=False):
            return module.cancel_orphaned_waits(self.db, self.backups)

    def statuses(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            return db.execute('SELECT status FROM execution_entity ORDER BY id').fetchall()

    def test_cancel_preserves_private_backup_and_execution_history(self):
        result = self.run_repair()
        self.assertEqual(self.statuses(), [('canceled',), ('canceled',)])
        saved = Path(result['backup'])
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(saved.read_text())), 2)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_data').fetchone()[0], 2)

    def test_refuses_live_container(self):
        with patch.object(module, 'container_running', return_value=True):
            with self.assertRaises(ValueError): module.cancel_orphaned_waits(self.db, self.backups)
        self.assertEqual(self.statuses(), [('waiting',), ('waiting',)])

    def test_refuses_changed_child_reference_and_existing_child(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO execution_entity VALUES (1258,'success',NULL,NULL)")
        with self.assertRaises(ValueError): self.run_repair()
        self.assertFalse(self.backups.exists())

    def test_concurrent_change_rolls_back_all_parents(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TRIGGER concurrent_change BEFORE UPDATE ON execution_entity WHEN OLD.id=1889 BEGIN SELECT RAISE(IGNORE); END')
        with self.assertRaisesRegex(ValueError, 'Concurrent'): self.run_repair()
        self.assertEqual(self.statuses(), [('waiting',), ('waiting',)])

    def test_refuses_changed_reference(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            row = db.execute('SELECT data FROM execution_data WHERE executionId=1889').fetchone()[0]
            db.execute('UPDATE execution_data SET data=? WHERE executionId=1889', (row.replace('1890', '9999'),))
        with self.assertRaises(ValueError): self.run_repair()
        self.assertEqual(self.statuses(), [('waiting',), ('waiting',)])


if __name__ == '__main__': unittest.main()
