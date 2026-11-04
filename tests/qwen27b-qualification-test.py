from __future__ import annotations

import importlib.util
import fcntl
import json
import os
import stat
import tempfile
import types
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/qualify-qwen27b-fallback.py"
SPEC = importlib.util.spec_from_file_location("qwen27b_qualification", MODULE_PATH)
assert SPEC and SPEC.loader
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)
MANAGER_PATH = Path(__file__).resolve().parents[1] / "scripts/sparkrun-model-manager.py"
MANAGER_SPEC = importlib.util.spec_from_file_location("sparkrun_model_manager", MANAGER_PATH)
assert MANAGER_SPEC and MANAGER_SPEC.loader
manager = importlib.util.module_from_spec(MANAGER_SPEC)
MANAGER_SPEC.loader.exec_module(manager)


class QualificationContractTests(unittest.TestCase):
    def test_frozen_request_schema_and_hash(self) -> None:
        raw = json.dumps({"schema_version": 1, "request": {
            "messages": [{"role": "user", "content": "SYNTHETIC_PRIVATE_PROMPT"}],
            "stream": True, "max_tokens": 12,
        }}).encode()
        body, digest = qualification.validate_request(raw)
        self.assertEqual(body["messages"][0]["content"], "SYNTHETIC_PRIVATE_PROMPT")
        self.assertEqual(digest, qualification.sha256(raw))
        with self.assertRaises(qualification.QualificationError):
            qualification.validate_request(raw[:-1] + b", \"model\":\"oracle\"}")

    def test_request_schema_passes_bounded_local_json_schema_and_requires_finite_explicit_caps(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "synthetic"}],
            "stream": True, "max_completion_tokens": 64,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "synthetic", "strict": True,
                "schema": {"type": "object", "properties": {"ok": {"$ref": "#/$defs/boolean"}},
                           "$defs": {"boolean": {"type": "boolean"}}},
            }},
        }
        raw = json.dumps({"schema_version": 1, "request": body}).encode()
        self.assertEqual(qualification.validate_request(raw)[0]["response_format"], body["response_format"])
        for changed in (
            {k: v for k, v in body.items() if k != "max_completion_tokens"},
            body | {"max_tokens": 64},
            body | {"temperature": float("inf")},
            body | {"top_p": 1.1},
            body | {"response_format": {"type": "json_schema", "json_schema": {
                "name": "bad", "schema": {"$ref": "https://example.invalid/schema"}}}},
        ):
            with self.subTest(changed=changed):
                invalid = json.dumps({"schema_version": 1, "request": changed}).encode()
                with self.assertRaises(qualification.QualificationError):
                    qualification.validate_request(invalid)

    def test_stream_requires_done_complete_choices_visible_text_and_nontruncated_finish(self) -> None:
        good = b"""data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

"""
        self.assertEqual(qualification.validate_sse_response(good), {
            "choices": 1, "finish_reasons": ["stop"], "visible_chars": 5, "complete": True,
        })
        invalid_streams = (
            good.replace(b"data: [DONE]\n\n", b""),
            good.replace(b'"finish_reason":"stop"', b'"finish_reason":"length"'),
            b'data: {"error":{"message":"synthetic"}}\n\ndata: [DONE]\n\n',
            good.replace(b'"content":"Hello"', b'"content":"  "'),
        )
        for payload in invalid_streams:
            with self.subTest(payload=payload):
                with self.assertRaises(qualification.QualificationError):
                    qualification.validate_sse_response(payload)

    def test_private_http_client_blocks_redirects_ignores_proxies_and_bounds_total_time(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                seen.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/target")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/slow-headers":
                    for piece in (b"HTTP/1.1 200 OK\r\n", b"X-Slow: ", b"header\r\n",
                                  b"Content-Length: 0\r\n", b"\r\n"):
                        try:
                            self.wfile.write(piece)
                            self.wfile.flush()
                        except OSError:
                            return
                        time.sleep(0.04)
                    return
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"5\r\nfirst\r\n")
                self.wfile.flush()
                time.sleep(0.25)
                try:
                    self.wfile.write(b"6\r\nsecond\r\n0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 1)
        base = f"http://127.0.0.1:{server.server_port}"
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://203.0.113.1:9",
                                          "HTTPS_PROXY": "http://203.0.113.1:9"}):
            status, _redirect_body = qualification.http_request(base + "/redirect", timeout=1)
            self.assertEqual(status, 302)
            started = time.monotonic()
            status, partial = qualification.http_request(base + "/slow", timeout=0.08)
            elapsed = time.monotonic() - started
        self.assertEqual(status, 0)
        self.assertLess(elapsed, 0.2)
        self.assertNotIn(b"second", partial)
        started = time.monotonic()
        status, _headers = qualification.http_request(base + "/slow-headers", timeout=0.08)
        elapsed = time.monotonic() - started
        self.assertEqual(status, 0)
        self.assertLess(elapsed, 0.2)
        self.assertNotIn("/target", seen)

    def test_manifest_must_be_regular_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            manifest = root / "request.json"
            manifest.write_bytes(b"{}")
            manifest.chmod(0o600)
            allowed = {os.getuid()}
            self.assertEqual(qualification.verify_regular_private(
                manifest, allowed_uids=allowed, exact_mode=0o600, max_bytes=10), b"{}")
            manifest.chmod(0o644)
            with self.assertRaises(qualification.QualificationError):
                qualification.verify_regular_private(
                    manifest, allowed_uids=allowed, exact_mode=0o600, max_bytes=10)
            manifest.unlink()
            manifest.symlink_to(root / "target")
            with self.assertRaises(qualification.QualificationError):
                qualification.verify_regular_private(
                    manifest, allowed_uids=allowed, exact_mode=0o600, max_bytes=10)

    def test_export_directory_must_be_preexisting_owned_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            good = root / "results"
            good.mkdir(mode=0o700)
            fd = qualification.open_export_directory(good, expected_uid=os.getuid())
            os.close(fd)
            with self.assertRaises(qualification.QualificationError):
                qualification.open_export_directory(good, expected_uid=os.getuid() + 1)
            good.chmod(0o755)
            with self.assertRaises(qualification.QualificationError):
                qualification.open_export_directory(good, expected_uid=os.getuid())
            link = root / "linked-results"
            link.symlink_to(good, target_is_directory=True)
            with self.assertRaises(qualification.QualificationError):
                qualification.open_export_directory(link, expected_uid=os.getuid())

    def test_invalid_export_destination_refuses_before_taking_runtime_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            export = root / "unsafe"
            export.mkdir(mode=0o755)
            args = types.SimpleNamespace(action="run", maintenance_window=True,
                                         env=str(root / "unused"), manifest=str(root / "unused"),
                                         export_dir=str(export))
            with mock.patch.object(qualification.os, "geteuid", return_value=0), \
                    mock.patch.object(qualification.os, "uname",
                                      return_value=types.SimpleNamespace(nodename="home-spark")), \
                    mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}), \
                    mock.patch.object(qualification, "acquire_deployment_lock",
                                     side_effect=AssertionError("must reject before locking or model changes")):
                with self.assertRaises(qualification.QualificationError) as caught:
                    qualification.perform(args)
            self.assertEqual(caught.exception.classification, "export-directory-permissions-invalid")

    def test_export_copies_only_allowlisted_private_artifacts_and_cleans_partial_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            evidence = root / "evidence"
            results = root / "results"
            evidence.mkdir(mode=0o700)
            results.mkdir(mode=0o700)
            for name, data in (("flash.response", b"flash response"),
                               ("qwen27b.response", b"fallback response"),
                               ("request.json", b"PRIVATE REQUEST MUST NOT EXPORT"),
                               ("api-key", b"PRIVATE KEY MUST NOT EXPORT")):
                target = evidence / name
                target.write_bytes(data)
                target.chmod(0o600)
            receipt_path = evidence / "receipt.json"
            receipt = {"receipt_id": "synthetic", "outcome": "qualified-output-captured"}
            qualification.write_receipt(receipt_path, receipt)
            export_fd = qualification.open_export_directory(results, expected_uid=os.getuid())
            try:
                with mock.patch.object(qualification, "file_fingerprint",
                                       side_effect=lambda path, **_kwargs: qualification.sha256(path.read_bytes())):
                    self.assertTrue(qualification.export_private_outputs(
                        export_fd, evidence, receipt_path, receipt, owner_uid=os.getuid()))
            finally:
                os.close(export_fd)
            self.assertEqual(sorted(path.name for path in results.iterdir()),
                             ["flash.response", "qwen27b.response", "receipt.json"])
            for path in results.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual((results / "flash.response").read_bytes(), b"flash response")
            self.assertEqual(json.loads((results / "receipt.json").read_text())["export_status"], "complete")

            collision = root / "collision"
            collision.mkdir(mode=0o700)
            (collision / "qwen27b.response").write_bytes(b"do not replace")
            (collision / "qwen27b.response").chmod(0o600)
            collision_fd = qualification.open_export_directory(collision, expected_uid=os.getuid())
            try:
                with mock.patch.object(qualification, "file_fingerprint",
                                       side_effect=lambda path, **_kwargs: qualification.sha256(path.read_bytes())):
                    self.assertFalse(qualification.export_private_outputs(
                        collision_fd, evidence, receipt_path, receipt, owner_uid=os.getuid()))
            finally:
                os.close(collision_fd)
            self.assertEqual((collision / "qwen27b.response").read_bytes(), b"do not replace")
            self.assertFalse((collision / "flash.response").exists())
            self.assertFalse((collision / "receipt.json").exists())
            self.assertEqual(json.loads(receipt_path.read_text())["export_status"], "failed")

    def test_idle_metric_parser_requires_both_known_series(self) -> None:
        good = b"""# HELP vllm:num_requests_running Requests running.\nvllm:num_requests_running{model_name=\"flash\"} 0\nvllm:num_requests_waiting{model_name=\"flash\"} 0\n"""
        busy = good.replace(b"running{model_name=\"flash\"} 0", b"running{model_name=\"flash\"} 1")
        self.assertTrue(qualification.metrics_idle(good))
        self.assertFalse(qualification.metrics_idle(busy))
        with self.assertRaises(qualification.QualificationError):
            qualification.metrics_idle(b"vllm:num_requests_running 0\n")
        with self.assertRaises(qualification.QualificationError):
            qualification.metrics_idle(good.replace(b" 0", b" NaN", 1))

    def test_api_key_reader_checks_mode_without_exposing_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "key"
            path.write_bytes(b"FAKE_SECRET_VALUE_123456\n")
            path.chmod(0o440)
            self.assertEqual(qualification.read_api_key(path, expected_uid=os.getuid()),
                             b"FAKE_SECRET_VALUE_123456")
            path.chmod(0o444)
            with self.assertRaises(qualification.QualificationError):
                qualification.read_api_key(path, expected_uid=os.getuid())

    def test_pinned_model_tuple_mismatch_refuses_before_runtime_inspection(self) -> None:
        env = {
            "MODEL_DEPLOYMENT_ID": qualification.DEPLOYMENT_ID,
            "MODEL_ID": qualification.MODEL_ID,
            "MODEL_REVISION": "0" * 40,
            "TOKENIZER_REVISION": qualification.MODEL_REVISION,
            "CODE_REVISION": qualification.MODEL_REVISION,
        }
        with mock.patch.object(qualification, "run", side_effect=AssertionError("must not inspect or fetch")):
            with self.assertRaises(qualification.QualificationError) as caught:
                qualification.verify_model_cache(env, Path(__file__).resolve().parents[1])
        self.assertEqual(caught.exception.classification, "configured-fallback-tuple-mismatch")

    def test_missing_candidate_or_active_cache_manifest_has_role_specific_preflight_failure(self) -> None:
        catalog = json.loads((Path(__file__).resolve().parents[1] / "config/model-catalog.json").read_text())
        deployment = catalog["deployments"][qualification.DEPLOYMENT_ID]
        runtime = catalog["runtime_profiles"][deployment["runtime_profile"]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifests = root / "manifests"
            manifests.mkdir(mode=0o700)
            tuple_data = (f"schema=1\nmodel={qualification.MODEL_REVISION}\n"
                          f"tokenizer={qualification.MODEL_REVISION}\ncode={qualification.MODEL_REVISION}\n"
                          f"repo={qualification.MODEL_ID}\n").encode()
            candidate_manifest = manifests / (
                f"accepted-model-cache-{qualification.sha256(tuple_data)}.json")
            env = {
                "MODEL_DEPLOYMENT_ID": qualification.DEPLOYMENT_ID,
                "MODEL_ID": qualification.MODEL_ID,
                "MODEL_REVISION": qualification.MODEL_REVISION,
                "TOKENIZER_REVISION": qualification.MODEL_REVISION,
                "CODE_REVISION": qualification.MODEL_REVISION,
                "VLLM_IMAGE": runtime["image"], "GB10_ROOT": str(root),
            }
            image_id = "sha256:" + "a" * 64
            with mock.patch.object(qualification, "run", return_value=image_id) as managed:
                with self.assertRaises(qualification.QualificationError) as caught:
                    qualification.verify_model_cache(env, Path(__file__).resolve().parents[1])
            self.assertEqual(caught.exception.stage, "cache")
            self.assertEqual(caught.exception.classification, "candidate-cache-manifest-file-missing")
            self.assertEqual(managed.call_count, 1)
            self.assertEqual(managed.call_args.args[0][:3], ["docker", "image", "inspect"])

            candidate_manifest.write_bytes(tuple_data)
            candidate_manifest.chmod(0o600)
            real_fingerprint = qualification.file_fingerprint

            def current_user_fingerprint(path: Path, **kwargs: object) -> str:
                return real_fingerprint(path, owner=os.getuid(), **kwargs)

            with mock.patch.object(qualification, "file_fingerprint", side_effect=current_user_fingerprint), \
                    mock.patch.object(qualification, "run", return_value=image_id) as managed:
                with self.assertRaises(qualification.QualificationError) as caught:
                    qualification.verify_model_cache(env, Path(__file__).resolve().parents[1])
            self.assertEqual(caught.exception.stage, "cache")
            self.assertEqual(caught.exception.classification, "active-cache-manifest-file-missing")
            self.assertEqual(managed.call_count, 1)
            self.assertTrue(all(call.args[0][:3] == ["docker", "image", "inspect"]
                                for call in managed.call_args_list))

    def test_missing_flash_owner_record_is_distinct_from_missing_cache_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_record = Path(directory) / "runtime.env"
            with self.assertRaises(qualification.QualificationError) as caught:
                qualification.parse_record(missing_record, {"PROFILE"})
        self.assertEqual(caught.exception.stage, "owner-state")
        self.assertEqual(caught.exception.classification, "flash-owner-record-file-missing")

    def test_qualifier_requires_source_under_the_active_immutable_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            releases = root / "releases"
            active = releases / "current-release"
            other = releases / "other-release"
            active.mkdir(parents=True)
            other.mkdir()
            link = root / "current"
            link.symlink_to(active, target_is_directory=True)
            qualification.verify_active_release(active, current_link=link, releases_root=releases,
                                                expected_uid=os.getuid())
            link.unlink()
            link.symlink_to(other, target_is_directory=True)
            with self.assertRaises(qualification.QualificationError) as caught:
                qualification.verify_active_release(active, current_link=link, releases_root=releases,
                                                    expected_uid=os.getuid())
            self.assertEqual(caught.exception.classification, "helper-not-from-active-release")

    def test_container_inspection_is_metadata_only_and_unknown_errors_fail_closed(self) -> None:
        output = "|".join([
            "sha256:" + "a" * 64, "/qwen38-flash-ultrafast", "sha256:" + "b" * 64,
            "flash:image", "running", "healthy", "unless-stopped", "0", "flash-next",
        ]) + "\n"
        with mock.patch.object(qualification.subprocess, "run", return_value=types.SimpleNamespace(
                returncode=0, stdout=output, stderr="")) as run:
            row = qualification.inspect_container("qwen38-flash-ultrafast")
        self.assertEqual(row["name"], "qwen38-flash-ultrafast")
        self.assertNotIn("Env", row)
        self.assertIn("--format", run.call_args.args[0])
        with mock.patch.object(qualification.subprocess, "run", return_value=types.SimpleNamespace(
                returncode=1, stdout="", stderr="permission denied")):
            with self.assertRaises(qualification.QualificationError):
                qualification.inspect_container("qwen38-flash-ultrafast")
        with mock.patch.object(qualification.subprocess, "run", return_value=types.SimpleNamespace(
                returncode=1, stdout="", stderr="Error: No such object: qwen38-flash-ultrafast\n")):
            self.assertIsNone(qualification.inspect_container("qwen38-flash-ultrafast"))

    def test_sparkrun_mutation_lock_uses_the_shared_file_and_rejects_contention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            parent.chmod(0o700)
            lock_path = parent / "text-model.lock"
            fd = manager.acquire_text_runtime_lock(lock_path, expected_uid=os.getuid())
            second = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(manager.RequestError) as caught:
                    manager.acquire_text_runtime_lock(lock_path, expected_uid=os.getuid())
                self.assertEqual(caught.exception.code, "lifecycle_busy")
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(second)
                os.close(fd)

    def test_sparkrun_lock_refuses_world_writable_parent_and_pending_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            unsafe = root / "unsafe"
            unsafe.mkdir()
            unsafe.chmod(0o1777)
            with self.assertRaises(manager.RequestError) as caught:
                manager.acquire_text_runtime_lock(unsafe / "lock", expected_uid=os.getuid())
            self.assertEqual(caught.exception.code, "lifecycle_lock_untrusted")

            safe = root / "safe"
            safe.mkdir(mode=0o700)
            lock_path = safe / "lock"
            pending = root / "pending.json"
            pending.write_text("{}")
            with mock.patch.object(manager, "QWEN_QUALIFICATION_PENDING", pending):
                with self.assertRaises(manager.RequestError) as caught:
                    manager.acquire_text_runtime_lock(lock_path, expected_uid=os.getuid())
            self.assertEqual(caught.exception.code, "qualification_recovery_pending")
            self.assertFalse(lock_path.exists())

    def test_pending_marker_created_at_lock_acquisition_boundary_releases_both_python_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            pending = root / "pending.json"
            manager_parent, qualifier_parent = root / "manager", root / "qualifier"
            manager_parent.mkdir(mode=0o700)
            qualifier_parent.mkdir(mode=0o700)
            actual_flock = fcntl.flock

            def acquire_then_create_marker(fd: int, operation: int) -> None:
                actual_flock(fd, operation)
                if operation & fcntl.LOCK_EX and not pending.exists():
                    pending.write_text("{}")

            manager_path = manager_parent / "lock"
            with mock.patch.object(manager, "QWEN_QUALIFICATION_PENDING", pending), \
                    mock.patch.object(manager.fcntl, "flock", side_effect=acquire_then_create_marker):
                with self.assertRaises(manager.RequestError) as caught:
                    manager.acquire_text_runtime_lock(manager_path, expected_uid=os.getuid())
            self.assertEqual(caught.exception.code, "qualification_recovery_pending")
            manager_probe = os.open(manager_path, os.O_RDWR)
            try:
                actual_flock(manager_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                actual_flock(manager_probe, fcntl.LOCK_UN)
                os.close(manager_probe)

            pending.unlink()
            qualifier_path = qualifier_parent / "lock"
            with mock.patch.object(qualification.fcntl, "flock", side_effect=acquire_then_create_marker):
                with self.assertRaises(qualification.QualificationError) as caught:
                    qualification.acquire_lock(qualifier_path, expected_uid=os.getuid(), pending_path=pending)
            self.assertEqual(caught.exception.classification, "qwen-qualification-recovery-pending")
            qualifier_probe = os.open(qualifier_path, os.O_RDWR)
            try:
                actual_flock(qualifier_probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                actual_flock(qualifier_probe, fcntl.LOCK_UN)
                os.close(qualifier_probe)

    def test_deployment_lock_refuses_contention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            parent.chmod(0o700)
            path = parent / "home-spark-deploy.lock"
            fd = qualification.acquire_deployment_lock(path, expected_uid=os.getuid())
            try:
                with self.assertRaises(qualification.QualificationError) as caught:
                    qualification.acquire_deployment_lock(path, expected_uid=os.getuid())
                self.assertEqual(caught.exception.classification, "deployment-in-progress")
            finally:
                os.close(fd)

    def test_qualification_lock_allows_only_explicit_recovery_past_pending_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            parent.chmod(0o700)
            lock_path = parent / "text-model.lock"
            pending = parent / "pending.json"
            pending.write_text("{}")
            with self.assertRaises(qualification.QualificationError) as caught:
                qualification.acquire_lock(lock_path, expected_uid=os.getuid(), pending_path=pending)
            self.assertEqual(caught.exception.classification, "qwen-qualification-recovery-pending")
            fd = qualification.acquire_lock(lock_path, allow_pending=True, expected_uid=os.getuid(), pending_path=pending)
            os.close(fd)


class QualificationTransactionTests(unittest.TestCase):
    def run_transaction(self, *, baseline_status: int = 200, fallback_status: int = 200,
                        rollback_failure: bool = False, interrupt_during_fallback: bool = False,
                        interrupt_signal: int | None = None,
                        idle_guard_failure: bool = False, restore_drift: str | None = None,
                        export_results: bool = False
                        ) -> tuple[dict[str, object], str, str, dict[str, dict[str, object]]]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        root.chmod(0o700)
        state = root / "state"
        state.mkdir(mode=0o700)
        manifest_dir = root / "input"
        manifest_dir.mkdir(mode=0o700)
        manifest = manifest_dir / "request.json"
        raw_request = json.dumps({"schema_version": 1, "request": {
            "messages": [{"role": "user", "content": "SYNTHETIC_PRIVATE_PROMPT"}],
            "stream": True, "max_tokens": 12,
        }}).encode()
        manifest.write_bytes(raw_request)
        manifest.chmod(0o600)
        export_dir = root / "export"
        if export_results:
            export_dir.mkdir(mode=0o700)
        api_key = root / "api-key"
        api_key.write_bytes(b"FAKE_SECRET_VALUE_123456\n")
        api_key.chmod(0o440)
        env_file = root / "env"
        env_file.write_text("SYNTHETIC_TEST_CONFIG=1\n")
        env_file.chmod(0o600)
        flash_dir = root / "flash"
        flash_dir.mkdir(mode=0o700)
        runtime_file = flash_dir / "runtime.env"
        runtime_file.write_text("PROFILE=ultrafast\nIMAGE_ID=sha256:" + "a" * 64 +
                                "\nMODEL_ID=model\nMODEL_REVISION=revision\nSERVED_NAME=automation-qualification\n")
        runtime_file.chmod(0o440)
        next_dir = root / "next"
        next_dir.mkdir(mode=0o700)
        active_profile = next_dir / "active-profile"
        active_profile.write_text("ultrafast\n")
        active_profile.chmod(0o600)
        previous = next_dir / "previous-text-containers"
        previous.write_text("gb10-text-primary\n")
        previous.chmod(0o600)
        image = "local/qwen:pin@sha256:" + "c" * 64
        env = {
            "FLASH_ULTRAFAST_NAME": "qwen38-flash-ultrafast",
            "FLASH_ULTRAFAST_STATE_DIR": str(flash_dir),
            "FLASH_ULTRAFAST_IMAGE_ID": "sha256:" + "a" * 64,
            "FLASH_ULTRAFAST_MODEL_ID": "model", "FLASH_ULTRAFAST_MODEL_REVISION": "revision",
            "FLASH_ULTRAFAST_SERVED_MODEL_NAME": "automation-qualification",
            "FLASH_ULTRAFAST_IMAGE": "flash:image", "FLASH_ULTRAFAST_HOST_BIND": "127.0.0.1",
            "FLASH_ULTRAFAST_HOST_PORT": "18300", "FLASH_NEXT_STATE_DIR": str(next_dir),
            "GB10_ROOT": "/srv/gb10-ai", "GB10_BIND_ADDRESS": "127.0.0.1", "VLLM_HOST_PORT": "8000",
            "VLLM_API_KEY_FILE": str(api_key), "VLLM_IMAGE": image,
        }
        flash_id = "sha256:" + "a" * 64
        original = {"id": flash_id, "name": "qwen38-flash-ultrafast", "image_id": flash_id,
                    "image_ref": "flash:image", "status": "running", "health": "healthy",
                    "restart": {"Name": "unless-stopped", "MaximumRetryCount": 0}, "managed": "flash-next"}
        containers: dict[str, dict[str, object]] = {"qwen38-flash-ultrafast": dict(original)}
        stdout = __import__("io").StringIO()
        stderr = __import__("io").StringIO()
        handlers: dict[int, object] = {}
        request_bodies: list[bytes] = []
        managed_commands: list[list[str]] = []
        source_reads = 0
        failure_state = {"rollback": rollback_failure, "baseline": baseline_status, "fallback": fallback_status}

        def inspect(name: str) -> dict[str, object] | None:
            row = containers.get(name)
            return dict(row) if row is not None else None

        def fake_run(argv: list[str], *, timeout: int = 60, stage: str = "preflight") -> str:
            nonlocal source_reads
            managed_commands.append(list(argv))
            if argv[:3] == ["docker", "image", "inspect"]:
                return "sha256:" + ("a" if argv[-1] == env["FLASH_ULTRAFAST_IMAGE"] else "d") * 64
            if argv[:2] == ["docker", "update"] and len(argv) >= 5:
                row = containers[argv[-1]]
                policy = argv[3].split(":", 1)
                row["restart"] = {"Name": policy[0], "MaximumRetryCount": int(policy[1]) if len(policy) > 1 else 0}
                return ""
            if argv[:2] == ["docker", "stop"]:
                if argv[-1] in containers:
                    containers[argv[-1]]["status"] = "exited"
                return ""
            if argv[:2] == ["docker", "rm"]:
                containers.pop(argv[-1], None)
                return ""
            if argv[:2] == ["docker", "start"]:
                if failure_state["rollback"] and argv[-1] == "qwen38-flash-ultrafast":
                    qualification.fail("restore", "synthetic-restore-failure")
                containers[argv[-1]]["status"] = "running"
                return ""
            if "compose" in argv and "up" in argv:
                for name in ("gb10-text-edge", "gb10-text-primary"):
                    containers[name] = {"id": "sha256:" + ("e" if name.endswith("edge") else "f") * 64,
                                        "name": name, "image_id": "sha256:" + "d" * 64,
                                        "image_ref": image, "status": "running", "health": None,
                                        "restart": {"Name": "no", "MaximumRetryCount": 0}, "managed": None}
                return ""
            if argv[:2] == ["git", "-C"]:
                source_reads += 1
                if restore_drift == "source" and source_reads > 1:
                    return "f" * 40
                return "e" * 40
            return ""

        def fake_http(url: str, body: bytes | None = None, *, key: bytes | None = None,
                      timeout: int = 15, max_bytes: int = qualification.MAX_RESPONSE_BYTES
                      ) -> tuple[int, bytes]:
            def stream(text: str) -> bytes:
                rows = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                return ("".join("data: " + json.dumps(row, separators=(",", ":")) + "\n\n" for row in rows)
                        + "data: [DONE]\n\n").encode()
            if url.endswith("/metrics"):
                return 200, (b"vllm:num_requests_running{model_name=\"flash\"} 0\n"
                             b"vllm:num_requests_waiting{model_name=\"flash\"} 0\n")
            if url.endswith("/health"):
                return 200, b"ok"
            if body is None:
                return 200, b"ok"
            request_bodies.append(body)
            if key:
                if interrupt_during_fallback:
                    signum = interrupt_signal or qualification.signal.SIGTERM
                    handlers[signum](signum, None)
                if restore_drift == "environment":
                    env_file.write_text("SYNTHETIC_TEST_CONFIG=changed\n")
                return failure_state["fallback"], stream("Qwen synthetic")
            return failure_state["baseline"], stream("Flash synthetic")

        def fake_signal(signum: int, handler: object) -> object | None:
            previous = handlers.get(signum)
            handlers[signum] = handler
            return previous

        def test_fingerprint(path: Path, *, owner: int = 0, mode_mask: int = 0o022,
                             exact_mode: int | None = None, role: str = "owner-state") -> str:
            st = path.lstat()
            if (not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)
                    or st.st_mode & mode_mask
                    or (exact_mode is not None and stat.S_IMODE(st.st_mode) != exact_mode)):
                qualification.fail("owner-state", "owner-state-file-untrusted")
            return qualification.sha256(path.read_bytes())

        args = types.SimpleNamespace(action="run", maintenance_window=True, env=str(env_file),
                                     manifest=str(manifest),
                                     export_dir=str(export_dir) if export_results else None)
        lock_fd = os.open(os.devnull, os.O_RDONLY)
        deployment_lock_fd = os.open(os.devnull, os.O_RDONLY)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(qualification.os, "geteuid", return_value=0))
            stack.enter_context(mock.patch.object(qualification.os, "uname", return_value=types.SimpleNamespace(nodename="home-spark")))
            stack.enter_context(mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}))
            stack.enter_context(mock.patch.object(qualification, "LOCK", root / "lock"))
            stack.enter_context(mock.patch.object(qualification, "acquire_deployment_lock",
                                                  return_value=deployment_lock_fd))
            stack.enter_context(mock.patch.object(qualification, "STATE_ROOT", state))
            stack.enter_context(mock.patch.object(qualification, "acquire_lock", return_value=lock_fd))
            stack.enter_context(mock.patch.object(qualification, "ROOT", Path(__file__).resolve().parents[1]))
            stack.enter_context(mock.patch.object(qualification, "verify_active_release", return_value=None))
            stack.enter_context(mock.patch.object(qualification, "read_env", return_value=env))
            stack.enter_context(mock.patch.object(qualification, "read_api_key", return_value=b"FAKE_SECRET_VALUE_123456"))
            stack.enter_context(mock.patch.object(qualification, "ensure_private_dir", side_effect=lambda p: (p.mkdir(parents=True, exist_ok=True), p.chmod(0o700))))
            stack.enter_context(mock.patch.object(qualification, "verify_model_cache", return_value={"model_id": qualification.MODEL_ID,
                "model_revision": qualification.MODEL_REVISION, "runtime_image": image,
                "runtime_image_id": "sha256:" + "d" * 64, "cache_manifest_sha256": "e" * 64}))
            stack.enter_context(mock.patch.object(qualification, "validate_whisper_preflight", return_value={"whisper": {"id": "w", "image_id": "wi", "health": "healthy"}}))
            stack.enter_context(mock.patch.object(qualification, "verify_whisper", return_value={"whisper": {"id": "w", "image_id": "wi", "health": "healthy"}}))
            stack.enter_context(mock.patch.object(qualification, "check_whisper_same", return_value=None))
            stack.enter_context(mock.patch.object(qualification, "memory_gate", return_value={"available_bytes": 16, "total_bytes": 20}))
            stack.enter_context(mock.patch.object(qualification, "inspect_container", side_effect=inspect))
            stack.enter_context(mock.patch.object(qualification, "running_container_names", side_effect=lambda: {
                name for name, row in containers.items() if row["status"] == "running"}))
            stack.enter_context(mock.patch.object(qualification, "run", side_effect=fake_run))
            stack.enter_context(mock.patch.object(qualification, "http_request", side_effect=fake_http))
            stack.enter_context(mock.patch.object(qualification, "wait_health", return_value=True))
            if idle_guard_failure:
                stack.enter_context(mock.patch.object(qualification, "wait_model_idle",
                    side_effect=qualification.QualificationError("restore", "text-model-idle-timeout")))
            stack.enter_context(mock.patch.object(qualification, "file_fingerprint", side_effect=test_fingerprint))
            stack.enter_context(mock.patch.object(qualification.signal, "signal", side_effect=fake_signal))
            stack.enter_context(mock.patch.object(qualification.time, "sleep", return_value=None))
            stack.enter_context(mock.patch("sys.stdout", stdout))
            stack.enter_context(mock.patch("sys.stderr", stderr))
            if (baseline_status != 200 or fallback_status != 200 or interrupt_during_fallback
                    or idle_guard_failure or restore_drift):
                expected_exception = KeyboardInterrupt if interrupt_during_fallback else qualification.QualificationError
                with self.assertRaises(expected_exception):
                    qualification.perform(args)
            else:
                self.assertEqual(qualification.perform(args), 0)
        result = json.loads(stdout.getvalue())
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        self.assertNotIn("SYNTHETIC_PRIVATE_PROMPT", stdout.getvalue())
        self.assertNotIn("FAKE_SECRET_VALUE", stdout.getvalue())
        self.assertEqual(len(request_bodies), 2)
        first_request, second_request = (json.loads(body) for body in request_bodies)
        self.assertNotEqual(first_request["model"], second_request["model"])
        first_request.pop("model")
        second_request.pop("model")
        self.assertEqual(first_request, second_request)
        evidence_dir = Path(result["receipt_path"]).parent
        self.assertTrue((evidence_dir / "flash.response").is_file())
        if not interrupt_during_fallback:
            self.assertTrue((evidence_dir / "qwen27b.response").is_file())
        if receipt["outcome"] == "qualified-output-captured":
            self.assertIs(receipt["request_hashes_match_except_model_alias"], True)
        if export_results:
            self.assertEqual(result["export_status"], "complete")
            expected_exports = ["flash.response", "receipt.json"]
            if not interrupt_during_fallback:
                expected_exports.insert(1, "qwen27b.response")
            self.assertEqual(sorted(path.name for path in export_dir.iterdir()),
                             expected_exports)
            self.assertEqual(json.loads((export_dir / "receipt.json").read_text())["export_status"], "complete")
            for path in export_dir.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertNotIn(b"SYNTHETIC_PRIVATE_PROMPT", b"".join(
                path.read_bytes() for path in export_dir.iterdir()))
        return result, stdout.getvalue(), stderr.getvalue(), {
            **{k: dict(v) for k, v in containers.items()}, "__commands__": managed_commands}

    def test_success_captures_both_outputs_and_restores_exact_flash(self) -> None:
        result, _stdout, _stderr, containers = self.run_transaction()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(containers["qwen38-flash-ultrafast"]["status"], "running")
        self.assertEqual(containers["qwen38-flash-ultrafast"]["restart"]["Name"], "unless-stopped")
        self.assertNotIn("gb10-text-primary", containers)

    def test_successful_transaction_exports_only_response_artifacts_to_invoker_directory(self) -> None:
        result, _stdout, _stderr, _containers = self.run_transaction(export_results=True)
        self.assertEqual(result["export_status"], "complete")

    def test_fallback_failure_still_restores_and_records_original_stage(self) -> None:
        result, _stdout, _stderr, _containers = self.run_transaction(fallback_status=502)
        self.assertEqual(result["status"], "restored")
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        self.assertEqual(receipt["failure_stage"], "fallback-request")
        self.assertTrue(receipt["restored"])

    def test_sigterm_during_inference_runs_unconditional_restore(self) -> None:
        result, _stdout, _stderr, containers = self.run_transaction(interrupt_during_fallback=True)
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        self.assertEqual(result["status"], "restored")
        self.assertEqual(receipt["interrupted_by_signal"], qualification.signal.SIGTERM)
        self.assertEqual(receipt["failure_class"], "KeyboardInterrupt")
        self.assertEqual(containers["qwen38-flash-ultrafast"]["status"], "running")

    def test_sighup_during_inference_restores_and_exports_partial_receipt(self) -> None:
        result, _stdout, _stderr, containers = self.run_transaction(
            interrupt_during_fallback=True, interrupt_signal=qualification.signal.SIGHUP,
            export_results=True)
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        self.assertEqual(result["status"], "restored")
        self.assertEqual(receipt["interrupted_by_signal"], qualification.signal.SIGHUP)
        self.assertEqual(containers["qwen38-flash-ultrafast"]["status"], "running")
        self.assertEqual(result["export_status"], "complete")

    def test_rollback_failure_keeps_pending_journal_and_distinct_cause(self) -> None:
        result, _stdout, _stderr, _containers = self.run_transaction(fallback_status=502, rollback_failure=True)
        self.assertEqual(result["status"], "rollback-pending")
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        pending = Path(result["receipt_path"]).parent.parent / "pending.json"
        journal = json.loads(pending.read_text())
        self.assertEqual(receipt["failure_stage"], "fallback-request")
        self.assertEqual(receipt["rollback_failure_stage"], "restore")
        self.assertEqual(journal["initial_failure_stage"], "fallback-request")
        self.assertEqual(journal["rollback_failure_class"], "synthetic-restore-failure")

    def test_unknown_fallback_idle_keeps_services_held_without_stopping_inference(self) -> None:
        result, _stdout, _stderr, containers = self.run_transaction(idle_guard_failure=True)
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        commands = containers["__commands__"]
        self.assertEqual(result["status"], "rollback-pending")
        self.assertFalse(any(command[:2] == ["docker", "stop"] and command[-1] == "gb10-text-primary"
                             for command in commands))
        self.assertEqual(containers["gb10-text-primary"]["status"], "running")
        self.assertEqual(containers["qwen38-flash-ultrafast"]["status"], "exited")
        self.assertEqual(receipt["rollback_failure_class"], "text-model-idle-timeout")

    def test_recovery_refuses_environment_or_source_drift_before_container_mutation(self) -> None:
        for drift in ("environment", "source"):
            with self.subTest(drift=drift):
                result, _stdout, _stderr, containers = self.run_transaction(restore_drift=drift)
                receipt = json.loads(Path(result["receipt_path"]).read_text())
                commands = containers["__commands__"]
                self.assertEqual(result["status"], "rollback-pending")
                self.assertTrue(containers["gb10-text-primary"]["status"] == "running")
                self.assertEqual(containers["qwen38-flash-ultrafast"]["status"], "exited")
                self.assertFalse(any(command[:2] == ["docker", "stop"] and command[-1] == "gb10-text-primary"
                                     for command in commands))
                self.assertFalse(any(command[:2] == ["docker", "start"]
                                     and command[-1] == "qwen38-flash-ultrafast" for command in commands))
                self.assertIn(receipt["rollback_failure_class"], {
                    "compute-config-changed", "source-revision-changed",
                })


if __name__ == "__main__":
    unittest.main()
