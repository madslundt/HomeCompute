"""Offline ledger/HTTP/publisher/worktree security tests; no paid APIs or PR writes."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/codex-worker"))
from broker import Ledger, serve
from policy import load_projects, validate_files
from publisher import MAX_API_RESPONSE_BYTES, Publisher
from worker import MAX_BROKER_RESPONSE_BYTES, Worker, broker_origin
import model_relay
from openclaw_worker_security import BODY, FILES, PROJECT, PolicyTests, WorkerTransportTests

PROPOSAL = {"problem": "Synthetic calculation uses an incorrect total.",
            "change": "Correct the calculation within the approved source directory.",
            "validation": "Fixed project tests passed in the isolated checkout.",
            "risk": "Review the patch and independent CI before publication."}



class FakeGitHub(Publisher):
    """Records real publisher API payloads, but performs zero external requests."""
    def __init__(self):
        super().__init__(Path("unused"))
        self.calls = []
        self.baseline = {"sha": "c" * 40, "truncated": False, "tree": [
            {"path": "src", "type": "tree", "mode": "040000"},
            {"path": "src/calc.py", "type": "blob", "mode": "100644"}]}

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/git/trees/" in path:
            return self.baseline
        if method == "GET":
            return {"sha": "a" * 40, "tree": {"sha": "c" * 40}}
        if path.endswith("/pulls"):
            return {"html_url": "https://github.com/example/synthetic/pull/1"}
        return {"sha": "b" * 40}


class AutoPreparationPolicyTests(unittest.TestCase):
    def load(self, project):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps({"schema_version": 1, "projects": {"demo": project}}))
            return load_projects(path)["demo"]

    def test_standing_preparation_permission_is_optional_and_strictly_boolean(self):
        self.assertEqual(self.load(PROJECT), PROJECT)
        for value in (False, True):
            project = {**PROJECT, "auto_prepare": value}
            self.assertEqual(self.load(project), project)
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load({**PROJECT, "auto_prepare": value})

    def test_auto_preparation_preserves_cloud_and_fixed_scope_requirements(self):
        for changes in ({"classification": "local_only"}, {"base_sha": "0" * 40},
                        {"tests": []}, {"write_prefixes": ["../"]}, {"command": "deploy"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load({**PROJECT, "auto_prepare": True, **changes})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.ledger = Ledger(self.path, {"demo": PROJECT})

    def tearDown(self):
        self.ledger.db.close()
        self.temp.cleanup()

    def task(self):
        return self.ledger.submit(BODY)

    def ready(self):
        task = self.task()
        self.ledger.action(task["id"], "approve", {})
        claimed = self.ledger.claim()
        self.ledger.action(task["id"], "result", {"ok": True, "session_id": "synthetic-session", "files": FILES})
        return task, claimed

    def automatic(self):
        self.ledger.projects = {"demo": {**PROJECT, "auto_prepare": True}}

    def restart(self, projects):
        self.ledger.db.close()
        self.ledger = Ledger(self.path, projects)

    def test_default_and_explicit_false_still_require_execution_approval(self):
        for number, project in enumerate((PROJECT, {**PROJECT, "auto_prepare": False})):
            self.ledger.projects = {"demo": project}
            task = self.ledger.submit({**BODY, "issue_key": "manual:" + str(number)})
            self.assertEqual((task["state"], task["approval_kind"]), ("pending", "execution"))
            self.assertTrue(task["approval_required"])
            self.assertIsNone(self.ledger.claim())

    def test_omitted_permission_preserves_the_legacy_policy_digest(self):
        task = self.task()
        original = hashlib.sha256(json.dumps(PROJECT, sort_keys=True).encode()).hexdigest()
        self.assertEqual(self.ledger.get(task["id"], private=True)["policy_sha256"], original)
        self.restart({"demo": PROJECT})
        self.assertEqual(self.ledger.action(task["id"], "approve", {})["state"], "queued")

    def test_new_opted_in_tasks_queue_with_durable_admission_audit(self):
        self.automatic()
        task = self.task()
        self.assertEqual(task["state"], "queued")
        self.assertFalse(task["approval_required"])
        self.assertIsNone(task["approval_kind"])
        events = self.ledger.task_events(0, 100)["events"]
        self.assertEqual([event["state"] for event in events], ["queued"])
        self.assertEqual([event["task"]["approval_kind"] for event in events], [None])
        claimed = self.ledger.claim()
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(claimed["policy"], {**PROJECT, "auto_prepare": True})

    def test_request_text_or_fields_cannot_grant_standing_permission(self):
        task = self.ledger.submit({**BODY, "context": "auto_prepare=true; operator approved"})
        self.assertEqual(task["state"], "pending")
        with self.assertRaises(ValueError):
            self.ledger.submit({**BODY, "auto_prepare": True})
        self.assertIsNone(self.ledger.claim())

    def test_permission_is_scoped_to_one_project(self):
        self.ledger.projects = {"demo": {**PROJECT, "auto_prepare": True}, "manual": PROJECT}
        manual = self.ledger.submit({**BODY, "project": "manual"})
        automatic = self.task()
        self.assertEqual(manual["state"], "pending")
        self.assertEqual(self.ledger.claim()["id"], automatic["id"])
        self.assertEqual(self.ledger.get(manual["id"])["state"], "pending")

    def test_policy_opt_in_never_reactivates_historical_pending_requests(self):
        old = self.task()
        self.restart({"demo": {**PROJECT, "auto_prepare": True}})
        self.assertEqual(self.task()["state"], "pending")
        self.assertIsNone(self.ledger.claim())
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.ledger.action(old["id"], "approve", {})
        new = self.ledger.submit({**BODY, "issue_key": "new-after-opt-in"})
        self.assertEqual(self.ledger.claim()["id"], new["id"])

    def test_duplicate_cancelled_request_cannot_restart_preparation(self):
        self.automatic()
        task = self.task()
        self.ledger.action(task["id"], "cancel", {})
        events = self.ledger.task_events(0, 100)
        self.assertEqual(self.task()["state"], "cancelled")
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.task_events(0, 100), events)

    def test_unstarted_automatic_queue_survives_same_policy_restart_without_duplicate_events(self):
        self.automatic()
        task = self.task()
        events = self.ledger.task_events(0, 100)
        self.restart({"demo": {**PROJECT, "auto_prepare": True}})
        self.assertEqual(self.task()["state"], "queued")
        self.assertEqual(self.ledger.task_events(0, 100), events)
        self.assertEqual(self.ledger.claim()["id"], task["id"])

    def test_revoking_policy_blocks_queued_work_and_restart_never_retries_running_work(self):
        self.automatic()
        queued = self.task()
        self.restart({"demo": {**PROJECT, "auto_prepare": False}})
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.get(queued["id"])["state"], "failed")
        self.automatic()
        running = self.ledger.submit({**BODY, "issue_key": "running-before-restart"})
        self.ledger.claim()
        self.restart({"demo": {**PROJECT, "auto_prepare": True}})
        self.assertEqual(self.ledger.get(running["id"])["state"], "failed")
        self.assertEqual(self.ledger.submit({**BODY, "issue_key": "running-before-restart"})["state"], "failed")
        self.assertIsNone(self.ledger.claim())

    def test_automatic_preparation_still_requires_exact_publication_approval(self):
        self.automatic()
        task = self.task()
        publisher = FakeGitHub()
        with self.assertRaises(ValueError):
            self.ledger.publish(task["id"], publisher, "")
        self.ledger.claim()
        self.ledger.action(task["id"], "result", {"ok": True, "session_id": None,
            "files": FILES, "tests_passed": True, "proposal": PROPOSAL})
        result = self.ledger.get(task["id"])
        self.assertEqual((result["state"], result["approval_kind"]), ("review", "publication"))
        self.assertTrue(result["approval_required"])
        with self.assertRaisesRegex(ValueError, "digest"):
            self.ledger.publish(task["id"], publisher, "0" * 64)
        self.assertEqual(publisher.calls, [])
        digest = self.ledger.review(task["id"])["result_sha256"]
        self.assertEqual(self.ledger.publish(task["id"], publisher, digest)["state"], "completed")

    def test_automatic_result_cannot_publish_under_a_changed_project_policy(self):
        self.automatic()
        task = self.task()
        self.ledger.claim()
        self.ledger.action(task["id"], "result", {"ok": True, "session_id": None,
            "files": FILES, "tests_passed": True, "proposal": PROPOSAL})
        digest = self.ledger.review(task["id"])["result_sha256"]
        self.ledger.projects = {"demo": {**PROJECT, "auto_prepare": False}}
        publisher = FakeGitHub()
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.ledger.publish(task["id"], publisher, digest)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(self.ledger.get(task["id"])["state"], "review")

    def test_deduplication_survives_restart(self):
        task = self.task()
        self.assertEqual(self.task()["id"], task["id"])

    def test_idempotency_key_cannot_hide_changed_content(self):
        task = self.task()
        with self.assertRaises(ValueError):
            self.ledger.submit({**BODY, "context": "different request"})
        self.assertEqual(self.task()["id"], task["id"])
        self.assertEqual(len(self.ledger.list_tasks()), 1)

    def test_review_exposes_requested_scope_before_approval_and_feedback_after_work(self):
        task = self.task()
        review = self.ledger.review(task["id"])
        self.assertEqual(review["request"], BODY)
        self.assertEqual(review["policy"], PROJECT)
        self.assertEqual(review["result"], {})
        self.assertIsNone(self.ledger.claim())
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        self.ledger.action(task["id"], "result", {"ok": True, "session_id": None,
            "files": FILES, "tests_passed": True, "proposal": PROPOSAL})
        review = self.ledger.review(task["id"])
        self.assertEqual(review["request"], BODY)
        self.assertEqual(review["policy"], PROJECT)
        self.assertEqual(review["result"]["proposal"], PROPOSAL)
        public = self.ledger.get(task["id"])
        self.assertEqual(public["result"]["proposal"], PROPOSAL)
        self.assertNotIn("request", public)
        self.assertNotIn("policy", public)
        self.assertNotIn("files", public["result"])

    def test_malformed_feedback_is_rejected_without_accepting_worker_result(self):
        task = self.task()
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        malformed = [None, [], "raw transcript", {},
            {key: value for key, value in PROPOSAL.items() if key != "risk"},
            {**PROPOSAL, "command": "arbitrary command"},
            {**PROPOSAL, "risk": 1}, {**PROPOSAL, "change": " "},
            {**PROPOSAL, "problem": "x" * 401}, {**PROPOSAL, "risk": "control\x00byte"},
            {**PROPOSAL, "risk": "Bearer " + "x" * 32}]
        before = self.ledger.task_events(0, 100)
        for proposal in malformed:
            with self.subTest(proposal=proposal):
                with self.assertRaises(ValueError):
                    self.ledger.action(task["id"], "result", {"ok": True, "session_id": None,
                        "files": FILES, "proposal": proposal})
                self.assertEqual(self.ledger.get(task["id"])["state"], "running")
                self.assertEqual(self.ledger.task_events(0, 100), before)
        with self.assertRaises(ValueError):
            self.ledger.action(task["id"], "result", {"ok": False, "session_id": None,
                "files": [], "proposal": PROPOSAL})

    def test_publication_digest_binds_feedback_as_well_as_patch_files(self):
        task = self.task()
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        result = {"ok": True, "session_id": None, "files": FILES, "proposal": PROPOSAL}
        self.ledger.action(task["id"], "result", result)
        reviewed_digest = self.ledger.review(task["id"])["result_sha256"]
        changed = {**result, "proposal": {**PROPOSAL, "risk": "The reviewed risk changed."}}
        with self.ledger.db:
            self.ledger.db.execute("UPDATE tasks SET result=? WHERE id=?", (json.dumps(changed), task["id"]))
        self.assertNotEqual(self.ledger.review(task["id"])["result_sha256"], reviewed_digest)
        publisher = FakeGitHub()
        with self.assertRaises(ValueError):
            self.ledger.publish(task["id"], publisher, reviewed_digest)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(self.ledger.get(task["id"])["state"], "review")

    def test_legacy_success_without_feedback_remains_reviewable_and_publishable(self):
        task, _ = self.ready()
        public = self.ledger.get(task["id"])
        self.assertNotIn("proposal", public["result"])
        review = self.ledger.review(task["id"])
        self.assertNotIn("proposal", review["result"])
        self.assertEqual(review["request"], BODY)
        publisher = FakeGitHub()
        self.assertEqual(self.ledger.publish(task["id"], publisher, review["result_sha256"])["state"], "completed")

    def test_unicode_feedback_event_pages_are_bounded_and_lossless(self):
        proposal = {key: "😀" * 400 for key in PROPOSAL}
        publisher = FakeGitHub()
        for number in range(25):
            task = self.ledger.submit({**BODY, "issue_key": "unicode-batch:" + str(number)})
            self.ledger.action(task["id"], "approve", {})
            self.ledger.claim()
            self.ledger.action(task["id"], "result", {"ok": True, "session_id": None,
                "files": FILES, "proposal": proposal})
            self.ledger.publish(task["id"], publisher, self.ledger.review(task["id"])["result_sha256"])
        expected = [row[0] for row in self.ledger.db.execute("SELECT seq FROM events ORDER BY seq")]
        delivered, cursor, pages = [], 0, 0
        while pages < 100:
            page = self.ledger.task_events(cursor, 100)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), 262144)
            self.assertTrue(page["events"])
            seqs = [event["seq"] for event in page["events"]]
            self.assertEqual(page["next_cursor"], seqs[-1])
            self.assertGreater(page["next_cursor"], cursor)
            delivered.extend(seqs)
            cursor, pages = page["next_cursor"], pages + 1
            if not page["has_more"]:
                break
        self.assertEqual(delivered, expected)
        self.assertGreater(pages, 1)
        self.assertFalse(self.ledger.task_events(cursor, 100)["has_more"])

    def test_baseline_schema_symlink_cannot_overwrite_controller_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "controller-owned-sentinel"
            protected.write_text("preserve controller data")
            worker = Worker(root, "synthetic-unused", {"demo": PROJECT})
            task = {"id": "d9f9dfe9-b1b4-42f3-b895-6281189b690b", "project": "demo",
                    "policy": PROJECT, "body": BODY}
            def clone(repository, target, evidence):
                (target / ".git").mkdir(parents=True)
            def git(directory, *args):
                if args[0] == "worktree":
                    tree = Path(args[3]); tree.mkdir()
                    (tree / ".git").write_text("gitdir: ../clone/.git/worktrees/worktree\n")
                    return b""
                return b'{"schema_version":1,"classification":"cloud_allowed"}'
            def baseline(argv, cwd, evidence, *args, **kwargs):
                self.assertEqual(argv, PROJECT["tests"])
                self.assertFalse(kwargs.get("codex", False))
                (worker.job_root / "proposal-schema.json").symlink_to(protected)
                return 0
            with patch("worker.os.chown"), patch.object(worker, "sandbox_preflight"), \
                    patch.object(worker, "clone", side_effect=clone), patch.object(worker, "git", side_effect=git), \
                    patch.object(worker, "allow_git_metadata_reads"), patch.object(worker, "manifest", return_value={}), \
                    patch.object(worker, "run", side_effect=baseline) as calls:
                with self.assertRaises(OSError):
                    worker.execute(task)
                calls.assert_called_once()
            self.assertEqual(protected.read_text(), "preserve controller data")

    def test_public_progress_results_events_and_unchanged_heartbeat(self):
        task = self.task()
        self.assertEqual((task["status"], task["approval_required"], task["approval_kind"]), ("queued", True, "execution"))
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        self.ledger.action(task["id"], "heartbeat", {"phase": "codex"})
        before = self.ledger.get(task["id"])
        self.assertEqual((before["status"], before["progress"]), ("running", "coding"))
        events = self.ledger.task_events(0, 100)
        self.ledger.action(task["id"], "heartbeat", {})
        self.ledger.action(task["id"], "heartbeat", {"phase": "codex"})
        self.assertEqual(self.ledger.get(task["id"]), before)
        self.assertEqual(self.ledger.task_events(0, 100), events)
        self.ledger.action(task["id"], "result", {"ok": True, "session_id": "synthetic-session", "files": FILES, "tests_passed": True})
        public = self.ledger.get(task["id"])
        self.assertEqual((public["state"], public["status"], public["approval_kind"]), ("review", "completed", "publication"))
        self.assertEqual(public["result"], {"ok": True, "session_id": "synthetic-session", "tests_passed": True, "changed_files": 1})
        self.assertNotIn("content", json.dumps(public))
        first = self.ledger.task_events(0, 2)
        self.assertTrue(first["has_more"])
        rest = self.ledger.task_events(first["next_cursor"], 100)
        self.assertEqual(rest["events"][-1]["task"], public)
        self.assertFalse(rest["has_more"])
        with self.ledger.db:
            self.ledger.db.execute("UPDATE events SET snapshot=NULL WHERE seq=1")
        legacy = self.ledger.task_events(0, 1)["events"][0]
        self.assertFalse(legacy["snapshot_available"])
        self.assertIsNone(legacy["task"])

    def test_snapshot_role_cannot_mutate_or_read_private_evidence(self):
        server = serve(self.ledger, {"assistant": "a" * 32, "worker": "w" * 32, "operator": "o" * 32,
                                    "snapshot": "s" * 32}, FakeGitHub(), ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        task = self.task()
        def request(path, value=None):
            req = urllib.request.Request("http://127.0.0.1:" + str(server.server_port) + path,
                data=None if value is None else json.dumps(value).encode(), headers={"Authorization": "Bearer " + "s" * 32})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        try:
            self.assertEqual(request("/task-events?after=0&limit=1")["events"][0]["task"]["id"], task["id"])
            self.assertEqual(request("/tasks/" + task["id"])["id"], task["id"])
            for path, body in (("/tasks", BODY), ("/worker/claim", {}), ("/tasks/" + task["id"] + "/approve", {}),
                               ("/tasks/" + task["id"] + "/publish", {}),
                               ("/tasks/" + task["id"] + "/review", None), ("/mcp", {})):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(path, body)
                self.assertEqual(error.exception.code, 403); error.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.ledger.db.close()
        self.ledger = Ledger(self.path, {"demo": PROJECT})
        self.assertEqual(self.task()["id"], task["id"])

    def test_approval_single_worker_and_cancel(self):
        task = self.task()
        self.assertIsNone(self.ledger.claim())
        self.ledger.action(task["id"], "approve", {})
        self.assertEqual(self.ledger.claim()["id"], task["id"])
        self.assertIsNone(self.ledger.claim())
        self.ledger.action(task["id"], "cancel", {})
        with self.assertRaises(ValueError):
            self.ledger.action(task["id"], "result", {"ok": False, "session_id": None, "files": []})

    def test_interruption_never_retries(self):
        task = self.task()
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        self.ledger.db.close()
        self.ledger = Ledger(self.path, {"demo": PROJECT})
        self.assertEqual(self.ledger.get(task["id"])["state"], "failed")
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.task()["id"], task["id"])

    def test_expired_lease_fails(self):
        task = self.task()
        self.ledger.action(task["id"], "approve", {})
        self.ledger.claim()
        with self.ledger.db:
            self.ledger.db.execute("UPDATE tasks SET updated=?", (time.time()-70,))
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.get(task["id"])["state"], "failed")

    def test_offline_proposal_to_draft_pr_contract(self):
        task, claimed = self.ready()
        self.assertEqual(claimed["body"], BODY)
        self.assertNotIn("context", self.ledger.get(task["id"]))
        publisher = FakeGitHub()
        with self.assertRaises(ValueError):
            self.ledger.publish(task["id"], publisher, "wrong-digest")
        self.assertEqual(publisher.calls, [])
        digest = self.ledger.review(task["id"])["result_sha256"]
        result = self.ledger.publish(task["id"], publisher, digest)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["pr_url"], "https://github.com/example/synthetic/pull/1")
        self.assertTrue(publisher.calls[-1][2]["draft"])
        self.assertEqual(publisher.calls[-2][2]["ref"], "refs/heads/codex/repair-" + task["id"])
        self.assertFalse(any("/merge" in call[1] for call in publisher.calls))
        events = self.ledger.db.execute("SELECT state FROM events WHERE task_id=? ORDER BY seq", (task["id"],)).fetchall()
        self.assertEqual([e[0] for e in events], ["pending", "queued", "running", "review", "publishing", "completed"])

    def test_queue_budget_and_unknown_project(self):
        with self.assertRaises(ValueError):
            self.ledger.submit({**BODY, "project": "arbitrary"})
        for number in range(32):
            self.ledger.submit({**BODY, "issue_key": str(number)})
        with self.assertRaises(ValueError):
            self.task()

    def test_publish_failure_no_retry(self):
        task, _ = self.ready()
        publisher = FakeGitHub()
        publisher.publish = lambda *_: (_ for _ in ()).throw(RuntimeError("secret should not appear"))
        with self.assertRaises(ValueError):
            self.ledger.publish(task["id"], publisher, self.ledger.review(task["id"])["result_sha256"])
        self.assertNotIn("secret", self.ledger.get(task["id"])["error"])
        self.assertEqual(self.ledger.get(task["id"])["state"], "failed")

    def test_http_roles_and_redaction(self):
        server = serve(self.ledger, {"assistant": "a" * 32, "worker": "w" * 32, "operator": "o" * 32}, FakeGitHub(), ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, role, value=None):
            req = urllib.request.Request("http://127.0.0.1:" + str(server.server_port) + path,
                data=None if value is None else json.dumps(value).encode(),
                headers={"Authorization": "Bearer " + role * 32})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        try:
            task = request("/tasks", "a", BODY)
            pending_review = request("/tasks/" + task["id"] + "/review", "o")
            self.assertEqual(pending_review["request"], BODY)
            self.assertEqual(pending_review["policy"], PROJECT)
            self.assertIsNone(self.ledger.claim())
            for action in ("approve", "publish", "heartbeat", "result"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request("/tasks/" + task["id"] + "/" + action, "a", {})
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:
                request("/tasks/" + task["id"] + "/review", "a")
            error.exception.close()
            self.assertEqual(len(request("/tasks", "a")), 1)
            request("/tasks/" + task["id"] + "/approve", "o", {})
            claimed = request("/worker/claim", "w", {})
            self.assertEqual(claimed["body"]["context"], BODY["context"])
            request("/tasks/" + task["id"] + "/result", "w", {"ok": True, "session_id": None, "files": FILES})
            review = request("/tasks/" + task["id"] + "/review", "o")
            self.assertEqual(review["result"]["files"], FILES)
            result = request("/tasks/" + task["id"] + "/publish", "o", {"result_sha256": review["result_sha256"]})
            self.assertEqual(result["state"], "completed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class PublisherTests(unittest.TestCase):
    def task(self, files=FILES):
        return {"id": "synthetic-id", "issue_key": BODY["issue_key"], "result": {"files": files}}

    def test_publisher_preserves_base_executable_mode_and_new_files_are_nonexecutable(self):
        publisher = FakeGitHub()
        publisher.baseline["tree"][1]["mode"] = "100755"
        files = FILES + [{**FILES[0], "path": "src/new.py"}]
        publisher.publish(self.task(files), PROJECT)
        tree_request = next(body for method, path, body in publisher.calls if method == "POST" and path.endswith("/git/trees"))
        self.assertEqual({entry["path"]: entry["mode"] for entry in tree_request["tree"]},
                         {"src/calc.py": "100755", "src/new.py": "100644"})
        self.assertEqual(publisher.calls[0][1], "/repos/example/synthetic/git/commits/" + PROJECT["base_sha"])
        self.assertEqual(publisher.calls[1][1], "/repos/example/synthetic/git/trees/" + "c" * 40 + "?recursive=1")
        self.assertEqual(tree_request["base_tree"], "c" * 40)

    def test_publisher_requires_complete_safe_tree_before_any_write(self):
        for mutation in (lambda tree: tree.update(truncated=True),
                         lambda tree: tree.pop("truncated"),
                         lambda tree: tree.update(sha="d" * 40),
                         lambda tree: tree["tree"].append({"path": "../escape", "mode": "100644", "type": "blob"}),
                         lambda tree: tree["tree"].append(tree["tree"][1]),
                         lambda tree: tree.update(tree=[{}] * 100001)):
            publisher = FakeGitHub(); mutation(publisher.baseline)
            with self.subTest(tree=publisher.baseline.get("sha")), self.assertRaises(ValueError):
                publisher.publish(self.task(), PROJECT)
            self.assertTrue(all(method == "GET" for method, _, _ in publisher.calls))

    def test_publisher_rejects_symlink_submodule_directory_and_invalid_file_modes(self):
        for object_type, mode in (("blob", "120000"), ("commit", "160000"), ("tree", "040000"), ("blob", "100777")):
            publisher = FakeGitHub()
            publisher.baseline["tree"][1].update(type=object_type, mode=mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publisher.publish(self.task(), PROJECT)
            self.assertTrue(all(method == "GET" for method, _, _ in publisher.calls))
        publisher = FakeGitHub()
        publisher.baseline["tree"][0].update(type="blob", mode="120000")
        with self.assertRaises(ValueError):
            publisher.publish(self.task(), PROJECT)
        self.assertTrue(all(method == "GET" for method, _, _ in publisher.calls))

    def test_publisher_rejects_conflicting_new_file_and_directory_before_writes(self):
        publisher = FakeGitHub()
        files = [{**FILES[0], "path": "src/new"}, {**FILES[0], "path": "src/new/child.py"}]
        with self.assertRaises(ValueError):
            publisher.publish(self.task(files), PROJECT)
        self.assertTrue(all(method == "GET" for method, _, _ in publisher.calls))

    def test_github_response_is_bounded_before_json_parsing(self):
        class Oversized:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, size):
                self.size = size
                return b"x" * size
        response = Oversized()
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"; token.write_text("synthetic-publisher-key")
            with patch.object(urllib.request, "urlopen", return_value=response), self.assertRaises(ValueError):
                Publisher(token).request("GET", "/repos/example/synthetic/git/trees/" + "c" * 40)
        self.assertEqual(response.size, MAX_API_RESPONSE_BYTES + 1)






class ModelRelayTests(unittest.TestCase):
    def test_fixed_host_routes_auth_output_limit_and_no_client_headers(self):
        calls = []
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _): return b'{"synthetic":true}'
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return Response()
        with patch.object(model_relay.urllib.request, "build_opener", return_value=Opener()):
            server = model_relay.serve("scoped-model-token", ssl.get_default_verify_paths().cafile, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, token="scoped-model-token"):
            req = urllib.request.Request("http://127.0.0.1:" + str(server.server_port) + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Host": "n8n.home.arpa", "Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)
        try:
            self.assertEqual(request("/v1/chat/completions", {"model": "automation-moe", "messages": [], "max_tokens": 128}), {"synthetic": True})
            self.assertEqual(calls[0].full_url, "https://ai.home.arpa/v1/chat/completions")
            self.assertNotIn("Host", calls[0].headers)
            for path, body, token, status in (
                ("/v1/models", None, "bad", 401),
                ("/admin", None, "scoped-model-token", 404),
                ("/v1/chat/completions", {"model": "other-model"}, "scoped-model-token", 400),
                ("/v1/chat/completions", {"model": "automation-moe", "max_tokens": 10000}, "scoped-model-token", 400),
            ):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(path, body, token)
                self.assertEqual(error.exception.code, status)
                error.exception.close()
            self.assertEqual(len(calls), 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
