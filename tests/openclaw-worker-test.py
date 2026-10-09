"""Offline ledger/HTTP/publisher/worktree security tests; no paid APIs or PR writes."""
from __future__ import annotations

import base64
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/codex-worker"))
from broker import Ledger, serve
from policy import load_projects, validate_files
from publisher import MAX_API_RESPONSE_BYTES, Publisher
from worker import Worker
import model_relay

PROJECT = {"repository": "example/synthetic", "base_sha": "a" * 40,
           "base_branch": "main", "classification": "cloud_allowed",
           "tests": ["python3", "-m", "unittest", "discover", "-s", "tests"],
           "write_prefixes": ["src/", "tests/"]}
BODY = {"project": "demo", "issue_key": "synthetic:arithmetic:1", "summary": "simulated defect", "context": "2+3 must be 5"}
FILES = [{"path": "src/calc.py", "content": base64.b64encode(b"def add(a,b): return a+b\n").decode()}]


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

    def test_deduplication_survives_restart(self):
        task = self.task()
        self.assertEqual(self.task()["id"], task["id"])
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


class PolicyTests(unittest.TestCase):
    def test_failed_sandbox_preflight_cannot_checkout_or_access_model_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = Worker(root, "synthetic-unused", {"demo": PROJECT})
            task_id = "d9f9dfe9-b1b4-42f3-b895-6281189b690b"
            task = {"id": task_id, "project": "demo", "policy": PROJECT, "body": BODY}
            with patch("worker.os.chown"), patch.object(worker, "run", return_value=1) as run, \
                    patch.object(worker, "clone") as clone, patch.object(worker, "git") as git, \
                    patch.object(Path, "read_text", side_effect=AssertionError("credential or repository read attempted")):
                with self.assertRaisesRegex(RuntimeError, "before checkout or model authentication"):
                    worker.execute(task)
            clone.assert_not_called()
            git.assert_not_called()
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs, {"codex": False})
            argv, cwd, evidence, observed_id, budget = run.call_args.args
            self.assertEqual(observed_id, task_id)
            self.assertEqual(budget, 30)
            self.assertEqual(cwd, root / task_id / "preflight")
            self.assertEqual(cwd.stat().st_mode & 0o777, 0o700)
            self.assertEqual(evidence.name, "sandbox-preflight.log")
            self.assertIn('sandbox_mode="workspace-write"', argv)
            self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
            self.assertNotIn("use_legacy_landlock", " ".join(argv))
            self.assertFalse((root / task_id / "clone").exists())

    def test_codex_execution_preserves_preflight_temp_and_sandbox_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = Worker(root, "synthetic-unused", {"demo": PROJECT})
            task = {"id": "d9f9dfe9-b1b4-42f3-b895-6281189b690b", "project": "demo",
                    "policy": PROJECT, "body": BODY}
            def git(directory, *args):
                if args[0] == "worktree":
                    Path(args[3]).mkdir()
                    return b""
                return b'{"schema_version":1,"classification":"cloud_allowed"}'
            def run(argv, cwd, evidence, *args, **kwargs):
                if kwargs.get("codex"):
                    evidence.write_text('{"type":"thread.started","thread_id":"synthetic-session"}\n')
                return 0
            with patch("worker.os.chown"), patch.object(worker, "clone"), patch.object(worker, "git", side_effect=git), \
                    patch.object(worker, "run", side_effect=run) as calls, patch.object(worker, "running", return_value=True), \
                    patch.object(worker, "collect", return_value=FILES):
                self.assertEqual(worker.execute(task)["session_id"], "synthetic-session")
            invocations = [call for call in calls.call_args_list if call.args[0][0] == "codex"]
            self.assertEqual(len(invocations), 2)
            for invocation in invocations:
                argv = invocation.args[0]
                self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
                self.assertEqual(argv[argv.index("-a") + 1], "never")
                self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
                self.assertNotIn("dangerously-bypass", " ".join(argv))
                self.assertNotIn("use_legacy_landlock", " ".join(argv))

    def test_default_denies_all_and_placeholder_rejected(self):
        self.assertEqual(load_projects(ROOT / "config/codex-projects.json"), {})
        with self.assertRaises(ValueError):
            load_projects(ROOT / "config/codex-projects.example.json")

    def test_patch_security(self):
        for name in ("../outside", "/tmp/file", "src/../evil", "src/.env", ".github/workflows/deploy.yml", "deploy/compose.yaml", "src/a\\b", "src//file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_files([{**FILES[0], "path": name}], PROJECT)
        with self.assertRaises(ValueError):
            validate_files(FILES * 2, PROJECT)

    def test_collector_never_executes_git_clean_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "src").mkdir()
            (root / "src/calc.py").write_text("broken\n")
            (root / ".gitattributes").write_text("src/* filter=unsafe\n")
            marker = root / "executed"
            subprocess.run(["git", "-C", str(root), "config", "filter.unsafe.clean", "touch " + str(marker) + "; cat"], check=True)
            worker = Worker(root, "unused", {})
            worker.baseline = worker.manifest(root)
            (root / "src/calc.py").write_text("fixed\n")
            files = worker.collect(root, PROJECT)
            self.assertEqual(files[0]["path"], "src/calc.py")
            self.assertFalse(marker.exists())

    def test_read_only_checkout_credential_never_persists_or_enters_job_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory), "unused", {})
            with patch.object(Path, "read_text", return_value="synthetic-read-token"), patch.object(subprocess, "run") as run:
                worker.clone("example/synthetic", Path(directory) / "clone", Path(directory) / "clone.log")
            argv = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertFalse(any("synthetic-read-token" in arg for arg in argv))
            self.assertEqual(environment["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
            self.assertNotIn("CODEX_API_KEY", environment)
            self.assertIn("--no-checkout", argv)

    def test_collector_rejects_symlinks_deletions_and_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/a").write_text("a")
            worker = Worker(root, "unused", {})
            worker.baseline = worker.manifest(root)
            (root / "src/a").unlink()
            with self.assertRaises(ValueError):
                worker.collect(root, PROJECT)
            (root / "src/a").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                worker.collect(root, PROJECT)

    def test_collector_rejects_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            os.mkfifo(root / "src/fifo")
            with self.assertRaises(ValueError):
                Worker(root, "unused", {}).collect(root, PROJECT)

    def test_scan_rejects_replaced_root_and_sparse_byte_exhaustion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / "tree"
            tree.mkdir()
            (root / "alias").symlink_to(tree, target_is_directory=True)
            with self.assertRaises(ValueError):
                Worker.manifest(root / "alias")
            with (tree / "large").open("wb") as output:
                output.truncate(268435457)
            with self.assertRaises(ValueError):
                Worker.manifest(tree)

    def test_scan_renews_lease_and_honors_cancel_and_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").write_bytes(b"x" * 150000)
            worker = Worker(root, "unused", {})
            worker.deadline = time.monotonic() + 30
            with patch.object(worker, "running", return_value=True) as heartbeat:
                Worker.manifest(root, worker.scan_checkpoint("synthetic"))
                heartbeat.assert_called_once_with("synthetic")
            with patch.object(worker, "running", return_value=False):
                with self.assertRaises(RuntimeError):
                    Worker.manifest(root, worker.scan_checkpoint("synthetic"))
            worker.deadline = time.monotonic() - 1
            with self.assertRaises(TimeoutError):
                Worker.manifest(root, worker.scan_checkpoint("synthetic"))

    def test_job_directory_never_chowns_a_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "private"
            target.mkdir()
            alias = root / "home"
            alias.symlink_to(target, target_is_directory=True)
            with patch("worker.os.chown") as chown:
                with self.assertRaises(ValueError):
                    Worker(root, "unused", {}).private_job_directory(alias)
                chown.assert_not_called()


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
