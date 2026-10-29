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

PROPOSAL = {"problem": "Synthetic addition returns the wrong total.",
            "change": "Correct the return value in src/calc.py.",
            "validation": "The fixed project test command passed.",
            "risk": "Human review and independent CI are required before publication."}


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

    def test_automatic_preparation_has_no_execution_approval_notice_but_requires_publication_review(self):
        self.ledger.projects = {"demo": {**self.policy["demo"], "auto_prepare": True}}
        reply = self.client.respond("/code demo automatic:one Fix synthetic addition")
        self.assertIn("queued", reply)
        self.assertNotIn("execution approval is required", reply)
        task = self.ledger.list_tasks()[0]
        cursor = tasks.feed_once(self.client, 0, self.transport["collector"])
        events = self.store.db.execute("SELECT kind,body FROM events WHERE topic=?", ("task:" + task["id"],)).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "progress")
        self.assertIn("Queued for isolated preparation", json.loads(events[0][1])["text"])
        self.assertNotIn("after execution approval", json.loads(events[0][1])["text"])
        self.post("/worker/claim", "worker", {})
        self.post("/tasks/" + task["id"] + "/result", "worker", {"ok": True, "session_id": None,
            "tests_passed": True, "files": [{"path": "src/calc.py", "content": base64.b64encode(b"return 5\n").decode()}]})
        tasks.feed_once(self.client, cursor, self.transport["collector"])
        review = self.store.db.execute("SELECT body FROM events WHERE topic=? AND kind='approval'",
                                       ("task:" + task["id"],)).fetchall()
        self.assertEqual(len(review), 1)
        self.assertIn("publication requires its reviewed digest", json.loads(review[0][0])["text"])
        for action in ("approve", "publish"):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.post("/tasks/" + task["id"] + "/" + action, "assistant", {})
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()

    def test_feedback_reaches_task_read_and_review_notification_without_private_files(self):
        tid = self.submit()
        pending_review = tasks.request(tasks.BROKER, "/tasks/" + tid + "/review", self.tokens["operator"])
        self.assertEqual(pending_review["request"]["summary"], "Fix synthetic addition")
        self.assertEqual(pending_review["policy"], self.policy["demo"])
        self.assertIsNone(self.ledger.claim())
        self.post("/tasks/" + tid + "/approve", "operator", {})
        self.post("/worker/claim", "worker", {})
        self.post("/tasks/" + tid + "/result", "worker", {
            "ok": True, "session_id": "synthetic-feedback-session", "tests_passed": True,
            "files": [{"path": "src/calc.py", "content": base64.b64encode(b"private file contents\n").decode()}],
            "proposal": PROPOSAL})
        cursor = tasks.feed_once(self.client, 0, self.transport["collector"])
        self.assertEqual(tasks.feed_once(self.client, 0, self.transport["collector"]), cursor)
        snapshot = self.store.task_snapshot(tid)
        self.assertEqual(snapshot["result"]["proposal"], PROPOSAL)
        result = communication.conversation({"conversation": "operator", "destination": "private",
            "request_id": "phone:feedback", "text": "/task " + tid}, self.store,
            lambda *_: self.fail("model invoked"), time.time())
        for text in PROPOSAL.values():
            self.assertIn(text, result["reply"])
        notifications = [json.loads(row[0]) for row in self.store.db.execute(
            "SELECT body FROM events WHERE topic=? AND kind='approval'", ("task:" + tid,))]
        review = [event for event in notifications if event["task"]["state"] == "review"]
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["task"]["result"]["proposal"], PROPOSAL)
        for text in PROPOSAL.values():
            self.assertIn(text, review[0]["text"])
        self.assertIn("Chat replies do not approve", review[0]["text"])
        self.assertIn('\n\n**Codex feedback (untrusted)**\n\n**Problem**\n\n> ', review[0]['text'])
        self.assertNotIn('{"problem":', review[0]['text'])
        self.assertLessEqual(len(review[0]['text'].splitlines()[0]), 220)
        for exposed in (result["reply"], json.dumps(review)):
            self.assertNotIn("private file contents", exposed)
            self.assertNotIn("content", exposed)

    def test_readonly_and_assistant_permission_denials(self):
        tid = self.submit()
        for role, action in (("snapshot", "cancel"), ("snapshot", "approve"), ("snapshot", "publish"),
                             ("assistant", "approve"), ("assistant", "publish")):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.post("/tasks/" + tid + "/" + action, role, {})
            self.assertEqual(denied.exception.code, 403); denied.exception.close()
        self.assertIn("reviewed", self.client.respond("/code unreviewed issue:one bad"))
        self.assertEqual(len(self.ledger.list_tasks()), 1)
        self.assertIn("Use /cancel", self.client.respond("/cancel-task malformed"))

    def test_maximum_unicode_feedback_fits_task_reply_and_delivery_budget(self):
        tid = self.submit()
        proposal = {key: "😀" * 400 for key in PROPOSAL}
        self.ledger.action(tid, "approve", {})
        self.ledger.claim()
        self.ledger.action(tid, "result", {"ok": True, "session_id": None,
            "files": [{"path": "src/calc.py", "content": base64.b64encode(b"return 5\n").decode()}],
            "proposal": proposal})
        tasks.feed_once(self.client, 0, self.transport["collector"])
        result = communication.conversation({"conversation": "operator", "destination": "private",
            "request_id": "phone:unicode-feedback", "text": "/task " + tid}, self.store,
            lambda *_: self.fail("model invoked"), time.time())
        self.assertEqual(result["state"], "completed")
        self.assertLessEqual(len(result["reply"]), 4000)
        self.assertIn("😀" * 400, result["reply"])
        review = next(json.loads(row[0]) for row in self.store.db.execute(
            "SELECT body FROM events WHERE topic=? AND kind='approval'", ("task:" + tid,))
            if json.loads(row[0])["task"]["state"] == "review")
        self.assertLessEqual(len(review["text"]), 4000)
        self.assertIn("😀" * 400, review["text"])
        with patch("openclaw_notifications.quiet", return_value=False):
            claimed_review = None
            for _ in range(10):
                delivery = self.store.claim(time.time())
                if delivery is None:
                    break
                self.assertLessEqual(len(delivery["text"]), 4000)
                if delivery["kind"] == "approval":
                    claimed_review = delivery
                self.store.acknowledge(delivery["delivery_key"], delivery["claim"],
                    "synthetic-unicode-receipt", time.time())
        self.assertIsNotNone(claimed_review)
        self.assertIn("😀" * 400, claimed_review["text"])

    def test_maximum_feedback_batch_advances_lossless_cursor_within_transport_budget(self):
        files = [{"path": "src/calc.py", "content": base64.b64encode(b"return 5\n").decode()}]
        proposal = {key: "😀" * 400 for key in PROPOSAL}
        expected = set()
        for number in range(25):
            task = self.ledger.submit({"project": "demo", "issue_key": "batch:" + str(number),
                "summary": "Synthetic feedback batch", "context": ""})
            tid = task["id"]
            expected.add(tid)
            self.ledger.action(tid, "approve", {})
            self.ledger.claim()
            self.ledger.action(tid, "result", {"ok": True, "session_id": None,
                "files": files, "proposal": proposal})
            with self.ledger.lock, self.ledger.db:
                self.ledger.transition(tid, "publishing")
                self.ledger.transition(tid, "completed")
        cursor, pages = 0, 0
        final_cursor = self.ledger.db.execute("SELECT max(seq) FROM events").fetchone()[0]
        while cursor < final_cursor and pages < 100:
            after = tasks.feed_once(self.client, cursor, self.transport["collector"])
            self.assertGreater(after, cursor)
            cursor, pages = after, pages + 1
        self.assertEqual(cursor, final_cursor)
        received = self.store.status(time.time())["tasks"]
        self.assertEqual({row["id"] for row in received}, expected)
        for row in received:
            self.assertEqual(row["state"], "completed")
            self.assertEqual(row["result"]["proposal"], proposal)

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
