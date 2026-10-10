"""Worker transport, sandbox and checkout security tests; no paid APIs."""
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
import tomllib
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

PROJECT = {"repository": "example/synthetic", "base_sha": "a" * 40,
           "base_branch": "main", "classification": "cloud_allowed",
           "tests": ["python3", "-m", "unittest", "discover", "-s", "tests"],
           "write_prefixes": ["src/", "tests/"]}
BODY = {"project": "demo", "issue_key": "synthetic:arithmetic:1", "summary": "simulated defect", "context": "2+3 must be 5"}
FILES = [{"path": "src/calc.py", "content": base64.b64encode(b"def add(a,b): return a+b\n").decode()}]




class WorkerTransportTests(unittest.TestCase):
    def test_only_fixed_compose_or_https_root_broker_origins_are_accepted(self):
        self.assertEqual(broker_origin("http://broker:8080"), "http://broker:8080")
        self.assertEqual(broker_origin("https://codex-broker.home.arpa:8443/"), "https://codex-broker.home.arpa:8443")
        for origin in ("http://other:8080", "https://user:secret@broker", "https://@broker", "https://broker/tasks",
                       "https://broker?token=x", "https://broker?", "https://broker#", "https://broker\n",
                       "https://", "https://broker:0", "https://broker:65536", "file:///tmp/broker"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                broker_origin(origin)

    def test_broker_redirects_never_forward_token_or_replay_submission(self):
        reached = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                if self.path == "/target":
                    reached.append(self.command)
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(int(self.path.strip("/")))
                self.send_header("Location", "/target")
                self.send_header("Content-Length", "0")
                self.end_headers()
            def do_GET(self):
                reached.append(self.command)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        worker = Worker(Path("unused"), "synthetic-control-token", {})
        # The origin validator is tested separately. Local plaintext here
        # exercises actual urllib redirect behavior without TLS credentials.
        worker.broker = "http://127.0.0.1:" + str(server.server_port)
        try:
            for code in (301, 302, 303, 307, 308):
                with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as error:
                    worker.request("/" + str(code), {})
                self.assertEqual(error.exception.code, code)
                error.exception.close()
            self.assertEqual(reached, [])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_broker_response_is_bounded_before_json_parsing(self):
        class Oversized:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, size):
                self.size = size
                return b"x" * size
        worker = Worker(Path("unused"), "synthetic-control-token", {})
        response = Oversized()
        with patch.object(worker.opener, "open", return_value=response), \
                patch("worker.json.loads") as loads, self.assertRaisesRegex(ValueError, "exceeds budget"):
            worker.request("/tasks")
        self.assertEqual(response.size, MAX_BROKER_RESPONSE_BYTES + 1)
        loads.assert_not_called()


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
            self.assertEqual(worker.error_code, "sandbox_unavailable")
            probe = argv[-1]
            self.assertIn("sandbox write boundary failed", probe)
            self.assertIn("private evidence read boundary failed", probe)
            self.assertIn("sandbox network boundary failed", probe)
            self.assertEqual((root / task_id / "preflight/.git/config").read_text(), "synthetic protected git")

    def test_codex_execution_preserves_preflight_temp_and_sandbox_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = Worker(root, "synthetic-unused", {"demo": PROJECT})
            task = {"id": "d9f9dfe9-b1b4-42f3-b895-6281189b690b", "project": "demo",
                    "policy": PROJECT, "body": BODY}
            def git(directory, *args):
                if args[0] == "worktree":
                    tree = Path(args[3]); tree.mkdir()
                    (tree / ".git").write_text("gitdir: ../clone/.git/worktrees/worktree\n")
                    return b""
                return b'{"schema_version":1,"classification":"cloud_allowed"}'
            def clone(repository, target, evidence):
                (target / ".git").mkdir(parents=True)
                (target / ".git/config").write_text("synthetic Git config")
            def run(argv, cwd, evidence, *args, **kwargs):
                if kwargs.get("codex"):
                    evidence.write_text('{"type":"thread.started","thread_id":"synthetic-session"}\n')
                return 0
            with patch("worker.os.chown") as chown, patch.object(worker, "clone", side_effect=clone), patch.object(worker, "git", side_effect=git), \
                    patch.object(worker, "run", side_effect=run) as calls, patch.object(worker, "running", return_value=True), \
                    patch.object(worker, "collect", return_value=FILES):
                result = worker.execute(task)
                self.assertEqual(result["session_id"], "synthetic-session")
                self.assertTrue(result["tests_passed"])
                self.assertEqual(worker.phase, "collect")
            job = root / task["id"]
            self.assertEqual((job / "clone/.git").stat().st_mode & 0o777, 0o750)
            self.assertEqual((job / "clone/.git/config").stat().st_mode & 0o777, 0o640)
            self.assertEqual((job / "worktree/.git").stat().st_mode & 0o777, 0o640)
            job_owners = [call.args[1:] for call in chown.call_args_list if call.args[0] == job]
            self.assertEqual(job_owners, [(worker.job_uid, worker.job_uid), (os.getuid(), os.getgid()),
                                         (worker.job_uid, worker.job_uid)])
            invocations = [call for call in calls.call_args_list if call.args[0][0] == "codex"]
            self.assertEqual(len(invocations), 2)
            test_calls = [call for call in calls.call_args_list if call.args[0] == PROJECT["tests"]]
            self.assertEqual(len(test_calls), 2)
            for invocation in test_calls:
                self.assertEqual(invocation.kwargs["file_budget"], "tests")
            for invocation in invocations:
                self.assertNotIn("file_budget", invocation.kwargs)
                argv = invocation.args[0]
                self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
                self.assertEqual(argv[argv.index("-a") + 1], "never")
                self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
                self.assertNotIn("dangerously-bypass", " ".join(argv))
                self.assertNotIn("use_legacy_landlock", " ".join(argv))
                if invocation.kwargs.get("codex"):
                    overrides = [argv[index + 1] for index, argument in enumerate(argv) if argument == "-c"]
                    config = tomllib.loads("\n".join(overrides))
                    shell = config["shell_environment_policy"]
                    self.assertEqual(shell["exclude"], ["CODEX_API_KEY", "OPENAI_API_KEY"])
                    self.assertEqual(shell["set"], {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
                                                   "GIT_CONFIG_VALUE_0": str(job / "worktree")})
                    self.assertNotIn("inherit", shell)
                    self.assertNotIn("ignore_default_excludes", shell)

    def test_model_cannot_receive_the_larger_test_file_budget(self):
        worker = Worker(Path("unused"), "unused", {})
        with patch.object(worker, "running", side_effect=AssertionError("subprocess admission attempted")):
            with self.assertRaisesRegex(ValueError, "invalid subprocess file-size budget"):
                worker.run(["codex"], Path("unused"), Path("unused"), "synthetic", 1,
                           codex=True, file_budget="tests")

    def test_heartbeat_sends_phase_and_stops_after_cancellation(self):
        worker = Worker(Path("unused"), "unused", {})
        worker.phase = "codex"
        with patch.object(worker, "request", side_effect=[{"state": "running"}, {}]) as request:
            self.assertTrue(worker.running("synthetic"))
            self.assertEqual(request.call_args.args, ("/tasks/synthetic/heartbeat", {"phase": "codex"}))
        with patch.object(worker, "request", return_value={"state": "cancelled"}) as request:
            self.assertFalse(worker.running("synthetic"))
            request.assert_called_once_with("/tasks/synthetic")

    def test_worker_failure_returns_only_categorical_metadata_without_retry(self):
        worker = Worker(Path("unused"), "unused", {})
        worker.phase = "codex"
        worker.error_code = "codex_failed"
        with patch.object(worker, "request", side_effect=[{"id": "synthetic"}, {}]) as request, \
                patch.object(worker, "execute", side_effect=RuntimeError("secret private failure")):
            worker.loop()
        result = request.call_args.args[1]
        self.assertEqual(result, {"ok": False, "session_id": None, "files": [], "error_code": "codex_failed"})
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(request.call_count, 2)

    def test_native_controller_seals_previous_job_without_fowner_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "previous"
            previous.mkdir(mode=0o755)
            worker = Worker(root, "synthetic-unused", {"demo": PROJECT})
            task = {"id": "d9f9dfe9-b1b4-42f3-b895-6281189b690b", "project": "demo",
                    "policy": PROJECT, "body": BODY}
            with patch("worker.os.chown") as chown, patch("worker.os.getuid", return_value=991), \
                    patch("worker.os.getgid", return_value=991), \
                    patch.object(worker, "sandbox_preflight", side_effect=RuntimeError("stop before credentials")):
                with self.assertRaises(RuntimeError):
                    worker.execute(task)
            self.assertTrue(any(call.args == (previous, 991, 991) for call in chown.call_args_list))
            self.assertEqual(previous.stat().st_mode & 0o777, 0o700)

    def test_default_denies_all_and_placeholder_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "projects.json"
            empty.write_text(json.dumps({"schema_version": 1, "projects": {}}))
            self.assertEqual(load_projects(empty), {})
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

    def test_public_checkout_missing_credential_never_uses_user_auth_or_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory), "unused", {})
            with patch.object(Path, "read_text", side_effect=FileNotFoundError), patch.object(subprocess, "run") as run:
                worker.clone("example/synthetic", Path(directory) / "clone", Path(directory) / "clone.log")
            environment = run.call_args.kwargs["env"]
            self.assertNotIn("GIT_CONFIG_VALUE_0", environment)
            self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
            self.assertIn("credential.helper=", run.call_args.args[0])

    def test_trusted_bundle_clones_expected_commit_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, bundles = root / "repository", root / "bundles"
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            (repository / "fixture.txt").write_text("trusted bundle contents\n")
            subprocess.run(["git", "-C", str(repository), "add", "fixture.txt"], check=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.name=Synthetic",
                            "-c", "user.email=synthetic@example.invalid", "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"]).strip()
            (bundles / "example").mkdir(parents=True)
            bundle = bundles / "example/synthetic.bundle"
            subprocess.run(["git", "-C", str(repository), "bundle", "create", str(bundle), "--all"], check=True)
            original_lstat = Path.lstat
            def root_owned(path, *args, **kwargs):
                info = list(original_lstat(path, *args, **kwargs)); info[4] = 0
                return os.stat_result(info)
            target = root / "clone"
            with patch("worker.BUNDLES", bundles), patch.object(Path, "lstat", root_owned), \
                    patch.object(Path, "read_text", side_effect=AssertionError("credential read attempted")) as read:
                Worker(root, "unused", {}).clone("example/synthetic", target, root / "clone.log")
            read.assert_not_called()
            bundle.unlink()  # The clone retains its own objects after removing the source.
            self.assertFalse((target / ".git/objects/info/alternates").exists())
            subprocess.run(["git", "-C", str(target), "checkout", "-q", "--detach", commit.decode()], check=True)
            self.assertEqual(subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"]).strip(), commit)
            self.assertEqual((target / "fixture.txt").read_text(), "trusted bundle contents\n")

    def test_bundle_rejects_unsafe_ownership_permissions_symlinks_and_file_types(self):
        for component in ("root", "owner", "file"):
            for defect in ("owner", "group_write", "other_write", "symlink", "wrong_type"):
                with self.subTest(component=component, defect=defect), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    bundles = root / "bundles"
                    (bundles / "example").mkdir(parents=True)
                    bundle = bundles / "example/synthetic.bundle"
                    bundle.write_bytes(b"synthetic")
                    path = {"root": bundles, "owner": bundle.parent, "file": bundle}[component]
                    if defect in {"group_write", "other_write"}:
                        path.chmod(path.stat().st_mode | (0o020 if defect == "group_write" else 0o002))
                    elif defect == "symlink":
                        replacement = path.with_name(path.name + "-real")
                        path.rename(replacement)
                        path.symlink_to(replacement, target_is_directory=component != "file")
                    elif defect == "wrong_type":
                        if component == "file":
                            path.unlink(); path.mkdir()
                        else:
                            # An existing file below a parent with the wrong type is
                            # impossible; simulate its inode while retaining lookup.
                            pass
                    original_lstat = Path.lstat
                    def root_owned(candidate, *args, **kwargs):
                        info = list(original_lstat(candidate, *args, **kwargs))
                        info[4] = 1000 if candidate == path and defect == "owner" else 0
                        if candidate == path and defect == "wrong_type" and component != "file":
                            info[0] = 0o100644
                        return os.stat_result(info)
                    with patch("worker.BUNDLES", bundles), patch.object(Path, "lstat", root_owned), \
                            patch.object(Path, "read_text") as read, patch.object(subprocess, "run") as run, \
                            self.assertRaisesRegex(ValueError, "root-owned"):
                        Worker(root, "unused", {}).clone("example/synthetic", root / "clone", root / "clone.log")
                    read.assert_not_called()
                    run.assert_not_called()

    def test_checkout_repository_rejects_traversal_and_non_repository_identifiers(self):
        for repository in ("../synthetic", "example/..", "./synthetic", "example/.", "/example/synthetic",
                           "example/synthetic/extra", "example//synthetic", "example\\synthetic",
                           "https://github.com/example/synthetic", "example/synthetic\n", "example/evil\x00"):
            with self.subTest(repository=repository), patch.object(Path, "lstat") as lstat, \
                    patch.object(Path, "read_text") as read, patch.object(subprocess, "run") as run, \
                    self.assertRaisesRegex(ValueError, "unsafe checkout repository"):
                Worker(Path("unused"), "unused", {}).clone(repository, Path("unused"), Path("unused"))
            lstat.assert_not_called()
            read.assert_not_called()
            run.assert_not_called()

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



if __name__ == "__main__":
    unittest.main()
