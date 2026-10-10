"""End-to-end bounded broker → communication projection with cursor recovery."""
from __future__ import annotations
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "deploy/codex-worker")]
from broker import Ledger, serve as broker_serve
from openclaw_notifications import Outbox
import openclaw_tasks as tasks
spec = importlib.util.spec_from_file_location("communication", ROOT / "scripts/openclaw-communication.py")
communication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(communication)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy = {"demo": {"repository": "example/synthetic", "base_sha": "a" * 40,
            "base_branch": "main", "classification": "cloud_allowed", "tests": ["python3", "-m", "unittest"],
            "write_prefixes": ["src/", "tests/"]}}
        self.tokens = {role: role + "x" * 40 for role in ("assistant", "snapshot", "worker", "operator")}
        self.transport = {role: role + "y" * 40 for role in ("collector", "conversation", "delivery")}
        self.settings = {"projects": ["demo"], "actions": {}, "conversations": ["operator"], "destination": "private",
                         "timezone": "Europe/Copenhagen", "quiet_start_hour": 0, "quiet_end_hour": 1, "cooldown_seconds": 60}
        self.ledger = Ledger(self.root / "tasks.sqlite3", self.policy)
        self.store = Outbox(self.root / "transport.sqlite3", self.settings)
        self.broker = broker_serve(self.ledger, self.tokens, None, ("127.0.0.1", 0))
        self.adapter = communication.serve(self.store, self.transport, {}, lambda *_: self.fail("model invoked"), ("127.0.0.1", 0))
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.broker, self.adapter)]
        for thread in self.threads: thread.start()
        self.patches = [patch.object(tasks, "BROKER", "http://127.0.0.1:" + str(self.broker.server_port)),
                        patch.object(tasks, "ADAPTER", "http://127.0.0.1:" + str(self.adapter.server_port))]
        for item in self.patches: item.start()
        policy = self.root / "projects.json"
        policy.write_text(json.dumps({"schema_version": 1, "projects": self.policy}))
        token_file = self.private("tokens.json", {key: self.tokens[key] for key in ("assistant", "snapshot")})
        config = self.private("transport.json", {"schema_version": 1, "broker_origin": tasks.BROKER,
                            "tokens_file": str(token_file), "project_policy": str(policy)})
        self.client = tasks.Tasks(config, ["demo"])

    def private(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return path

    def tearDown(self):
        for server in (self.broker, self.adapter): server.shutdown(); server.server_close()
        for thread in self.threads: thread.join()
        for item in self.patches: item.stop()
        self.ledger.db.close(); self.store.db.close(); self.temp.cleanup()

    def post(self, path, role, body):
        return tasks.request(tasks.BROKER, path, self.tokens[role], body)

    def submit(self):
        reply = self.client.respond("/code demo issue:one Fix synthetic addition")
        self.assertIn("Chat cannot approve", reply)
        return self.ledger.list_tasks()[0]["id"]

    def test_duplicate_progress_terminal_feed_and_result_read(self):
        tid = self.submit()
        self.submit()
        self.assertEqual(len(self.ledger.list_tasks()), 1)
        cursor = tasks.feed_once(self.client, 0, self.transport["collector"])
        self.assertEqual(cursor, 1)
        self.post("/tasks/" + tid + "/approve", "operator", {})
        self.post("/worker/claim", "worker", {})
        self.post("/tasks/" + tid + "/heartbeat", "worker", {"phase": "codex"})
        cursor = tasks.feed_once(self.client, cursor, self.transport["collector"])
        before = self.store.db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.post("/tasks/" + tid + "/heartbeat", "worker", {"phase": "codex"})
        self.assertEqual(tasks.feed_once(self.client, cursor, self.transport["collector"]), cursor)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], before)
        self.post("/tasks/" + tid + "/result", "worker", {"ok": True, "session_id": "synthetic-session", "tests_passed": True,
                  "files": [{"path": "src/calc.py", "content": base64.b64encode(b"return 5\n").decode()}]})
        after = tasks.feed_once(self.client, cursor, self.transport["collector"])
        # Crash after admission but before cursor save: repeat the same batch.
        self.assertEqual(tasks.feed_once(self.client, cursor, self.transport["collector"]), after)
        row = self.store.status(time.time())["tasks"][0]
        self.assertEqual(row["result"]["changed_files"], 1)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["approval_kind"], "publication")
        self.assertNotIn("files", row["result"])
        result = communication.conversation({"conversation": "operator", "destination": "private", "request_id": "phone:status",
                    "text": "/task " + tid}, self.store, lambda *_: self.fail("model invoked"), time.time())
        self.assertIn("synthetic-session", result["reply"])

    def test_readonly_and_assistant_permission_denials(self):
        tid = self.submit()
        for role, action in (("snapshot", "cancel"), ("assistant", "approve"), ("assistant", "publish")):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.post("/tasks/" + tid + "/" + action, role, {})
            self.assertEqual(denied.exception.code, 403); denied.exception.close()
        self.assertIn("reviewed", self.client.respond("/code unreviewed issue:one bad"))
        self.assertEqual(len(self.ledger.list_tasks()), 1)
        self.assertIn("Use /cancel", self.client.respond("/cancel-task malformed"))

    def test_historical_gap_and_lost_adapter_receipt_retain_cursor(self):
        tid = self.submit()
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET snapshot=NULL WHERE task_id=?", (tid,))
        with self.assertRaisesRegex(ValueError, "historical snapshot"):
            tasks.feed_once(self.client, 0, self.transport["collector"])
        self.assertEqual(self.store.status(time.time())["tasks"], [])

    def test_lost_admission_receipt_and_duplicate_cancel(self):
        tid = self.submit()
        original = tasks.request
        def lost(origin, path, token, body=None):
            value = original(origin, path, token, body)
            if origin == tasks.ADAPTER:
                raise TimeoutError("synthetic lost receipt after durable admission")
            return value
        with patch.object(tasks, "request", lost):
            with self.assertRaises(TimeoutError): tasks.feed_once(self.client, 0, self.transport["collector"])
        before = self.store.db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(tasks.feed_once(self.client, 0, self.transport["collector"]), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0], before)
        with self.assertRaises(urllib.error.HTTPError) as conflict:
            self.client.respond("/code demo issue:one Different summary")
        self.assertEqual(conflict.exception.code, 400); conflict.exception.close()
        self.assertIn("cancelled", self.client.respond("/cancel-task " + tid))
        self.assertIn("cancelled", self.client.respond("/cancel-task " + tid))
        self.assertEqual(len(self.ledger.list_tasks()), 1)

    def test_unconfigured_code_command_does_not_run_model(self):
        result = communication.conversation({"conversation": "operator", "destination": "private", "request_id": "phone:code",
                    "text": "/code demo issue:one summary"}, self.store, lambda *_: self.fail("model invoked"), time.time())
        self.assertEqual(result["state"], "completed")
        self.assertIn("not configured", result["reply"])
        result = communication.conversation({"conversation": "operator", "destination": "private", "request_id": "phone:badstatus",
                    "text": "/task"}, self.store, lambda *_: self.fail("model invoked"), time.time())
        self.assertEqual(result["state"], "completed")


if __name__ == "__main__": unittest.main()
