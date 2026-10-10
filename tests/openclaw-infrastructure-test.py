#!/usr/bin/env python3
"""Finite read selection, redaction, deadlines, native replay and authority tests."""
import importlib.util
import json
from datetime import datetime, timezone, timedelta
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_infrastructure import Reader
import openclaw_infrastructure as infrastructure
from openclaw_notifications import Outbox
SPEC = importlib.util.spec_from_file_location("communication_reads", ROOT / "scripts/openclaw-communication.py")
COMM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMM)
NOW = datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc)
SETTINGS = {"schema_version": 1, "destination": "operator-private", "conversations": ["operator"],
            "projects": [], "actions": {}, "timezone": "Europe/Copenhagen",
            "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.model_calls = []
        self.store = Outbox(Path(self.temp.name) / "outbox.sqlite3", SETTINGS)
        self.reader = Reader(ROOT / "config/system-monitoring.json", runner=self.runner, clock=lambda: NOW)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(kwargs["timeout"], 4)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        host = argv[-2]
        system = next(s for s in self.reader.registry["systems"] if s["id"] == host)
        if argv[-1] == "python3 -":
            value = {u["name"]: {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                                "Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "1"}
                     for u in system["units"]}
            value = {"unit_states": value, "maintenance_report": {"schema_version": 1, "mode": "observe-only", "host": host,
                "generated_at": NOW.isoformat(), "package_updates": {"status": "updates_available", "candidate_count": 1},
                "docker_image_updates": {"images": [{"state": "current"}]}}}
        else:
            self.assertEqual(argv[-1], "bash -s")
            value = {"schema_version": 1, "host": host, "containers": [{"name": c["name"], "status": "Up 3 hours (healthy)",
                     "secret": "PRIVATE", "ports": "PRIVATE"} for c in system["containers"]], "failed_units": [],
                     "platform_updates": "pkg/private PRIVATE [upgradable from: 1]", "gpu": "PRIVATE",
                     "credentials": "PRIVATE", "household": "PRIVATE"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "PRIVATE")

    def turn(self, name, text):
        self.model_calls.append((name, text))
        return "Read-only analysis; consequential actions require operator review."

    def request(self, text, request_id="read:1", reader=True):
        body = {"conversation": "operator", "destination": "operator-private", "request_id": request_id, "text": text}
        return COMM.conversation(body, self.store, self.turn, NOW.timestamp(), self.reader if reader else None)

    def test_selection_and_arguments_cannot_supply_shell_urls_or_services(self):
        for text in ("/health home-core;reboot", "/health https://evil", "/health home-core restart", "/investigate ../x", "/systems all"):
            result = self.request(text, "invalid:" + str(len(self.store.db.execute("SELECT * FROM turns").fetchall())))
            self.assertEqual(result["state"], "completed")
            self.assertIn("Use /systems", result["reply"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.model_calls, [])
        with self.assertRaises(ValueError): self.reader.read("home-core;reboot")

    def test_unconfigured_admission_fails_closed_without_native_turn(self):
        self.assertIn("not configured", self.request("/health all", reader=False)["reply"])
        self.assertEqual(self.model_calls, [])

    def test_single_system_reads_and_sensitive_metadata_projection(self):
        report = self.reader.read("home-spark")
        encoded = json.dumps(report)
        self.assertNotIn("PRIVATE", encoded)
        self.assertTrue(all(r["system_id"] == "home-spark" for r in report["observations"]))
        self.assertFalse(report["automatic_actions"])
        self.assertFalse(report["physical_device_actions"])
        self.assertEqual([a[-2] for a, _ in self.calls], ["home-spark", "home-spark"])
        self.assertEqual(next(r for r in report["observations"] if r["check_id"] == "package-update-report")["evidence"]["candidate_count"], 1)

    def test_health_never_uses_native_and_disabled_ha_is_unknown(self):
        result = self.request("/health all")
        self.assertEqual(result["state"], "completed")
        self.assertLess(len(result["reply"]), 4000)
        self.assertIn("home-assistant / collection: unknown", result["reply"])
        self.assertNotIn("PRIVATE", result["reply"])
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.store.status(NOW.timestamp())["incidents"], [])

    def test_investigation_context_and_duplicate_request_do_not_rerun(self):
        first = self.request("/investigate home-core")
        second = self.request("/investigate home-core")
        self.assertEqual(first, second)
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("Production executors are disabled", self.model_calls[0][1])
        self.assertIn("Do not write memory", self.model_calls[0][1])
        self.assertNotIn("PRIVATE", self.model_calls[0][1])
        self.assertNotIn("home-spark", self.model_calls[0][1])
        self.assertEqual(self.store.status(NOW.timestamp())["outbox"], {"pending": 1})

    def test_uncertain_native_turn_is_never_replayed(self):
        def failed(*args):
            self.model_calls.append(args)
            raise RuntimeError("PRIVATE replay-invalid receipt")
        self.turn = failed
        self.assertEqual(self.request("/investigate home-spark")["state"], "uncertain")
        count = len(self.calls)
        self.assertEqual(self.request("/investigate home-spark")["state"], "uncertain")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(self.model_calls), 1)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events").fetchone())

    def test_outage_is_unknown_not_cached_healthy_and_diagnostics_stay_private(self):
        def unavailable(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="PRIVATE")
        self.reader.runner = unavailable
        report = self.reader.read("home-core")
        self.assertEqual(report["observations"][0]["status"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_read_lock_bounds_concurrent_request_admission(self):
        self.reader.lock.acquire()
        try:
            self.assertIn("unavailable or busy", self.request("/investigate all")["reply"])
            self.assertEqual(self.calls, [])
            self.assertEqual(self.model_calls, [])
        finally:
            self.reader.lock.release()

    def test_chat_cannot_approve_execute_or_publish(self):
        for n, text in enumerate(("/approve yes", "/execute home-core.restart-n8n", "/publish any")):
            self.assertIn("cannot approve", self.request(text, "authority:" + str(n))["reply"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.model_calls, [])

    def test_registry_unknown_system_and_enabled_ha_without_transport_rejected(self):
        data = json.loads((ROOT / "config/system-monitoring.json").read_text())
        data["systems"][-1]["enabled"] = True
        path = Path(self.temp.name) / "registry.json"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): Reader(path)

    def test_inventory_and_production_authority_are_separate(self):
        result = self.request("/systems")
        self.assertIn("home-core: read configured", result["reply"])
        self.assertIn("home-assistant: not provisioned", result["reply"])
        self.assertIn("Production executors remain disabled", result["reply"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.model_calls, [])

    def test_HTTP_auth_investigation_and_duplicate_share_existing_outbox(self):
        tokens = {role: role + "-" + "x" * 40 for role in ("collector", "conversation", "delivery")}
        server = COMM.serve(self.store, tokens, {}, self.turn, ("127.0.0.1", 0),
                            lambda: NOW.timestamp(), infrastructure=self.reader)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = {"conversation": "operator", "destination": "operator-private", "request_id": "HTTP:1", "text": "/investigate home-spark"}
        def post(role):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/conversation", data=json.dumps(body).encode(),
                       headers={"Authorization": "Bearer " + tokens[role], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied: post("delivery")
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            self.assertEqual(self.calls, [])
            first = post("conversation")
            self.assertEqual(first, post("conversation"))
            self.assertEqual(first["state"], "completed")
            self.assertEqual(len(self.model_calls), 1)
            self.assertEqual(len(self.calls), 2)
            event = self.store.claim(NOW.timestamp())
            self.assertEqual(event["destination"], "operator-private")
            self.assertEqual(event["kind"], "reply")
            self.assertFalse(self.store.status(NOW.timestamp())["actions_enabled"])
        finally:
            server.shutdown(); server.server_close(); thread.join()


class CoreInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.reader = Reader(ROOT / "config/system-monitoring.json", core_host_mode=True, clock=lambda: NOW)
        self.raw_core = self.snapshot("home-core")
        self.raw_spark = self.snapshot("home-spark")
        self.core_patch = patch.object(infrastructure, "root_snapshot", side_effect=lambda: copy.deepcopy(self.raw_core))
        self.command_patch = patch.object(self.reader.transport, "bounded_command",
                                         side_effect=lambda *_args, **_kwargs: json.dumps(self.raw_spark).encode())
        self.core = self.core_patch.start()
        self.command = self.command_patch.start()

    def tearDown(self):
        self.command_patch.stop()
        self.core_patch.stop()
        self.temp.cleanup()

    def snapshot(self, host):
        system = next(row for row in self.reader.registry["systems"] if row["id"] == host)
        source = (NOW - timedelta(seconds=30)).isoformat()
        update_source = (NOW - timedelta(hours=1)).isoformat()
        return {"schema_version": 1, "host": host, "generated_at": source,
                "containers": [{"name": row["name"], "status": "Up 3 hours (healthy)", "secret": "PRIVATE"}
                               for row in system["containers"]],
                "unit_states": {row["name"]: {"LoadState": "loaded", "ActiveState": "active" if row["kind"] == "persistent" else "inactive",
                                "SubState": "running" if row["kind"] == "persistent" else "dead", "Result": "success",
                                "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "1"} for row in system["units"]},
                "maintenance_report": {"schema_version": 1, "host": host, "mode": "observe-only", "generated_at": update_source,
                    "package_updates": {"status": "updates_available", "candidate_count": 2, "security_count": 1,
                        "scope": "installed_packages", "reboot_required": True, "candidates": ["PRIVATE"], "provenance": {"skipped_sources": 0}},
                    "docker_image_updates": {"images": [{"state": "unknown", "image": "PRIVATE"}]}},
                "model_update_report": {"schema_version": 1, "mode": "review-only", "generated_at": update_source,
                    "summary": {"changed": 0, "pin_drift": 1, "source_errors": 0, "outperforms_active": 0}, "private": "PRIVATE"},
                "host_metrics": {"uptime_seconds": 29000.75, "memory_total_bytes": 48 * 1024**3,
                    "memory_available_bytes": 32 * 1024**3, "memory_used_bytes": 16 * 1024**3,
                    "disk_total_bytes": 100 * 1024**3, "disk_used_bytes": 80 * 1024**3,
                    "disk_free_bytes": 15 * 1024**3, "failed_unit_count": 0, "secret": "PRIVATE"}, "secret": "PRIVATE"}

    def test_core_and_spark_use_fixed_sources_and_keep_source_times(self):
        with patch.object(self.reader.observer, "collect", side_effect=AssertionError("no operator collector")):
            report = self.reader.read("all")
        self.core.assert_called_once_with()
        argv = self.command.call_args.args[0]
        self.assertEqual(argv[-2:], ["madslundt@192.168.30.126", "homecompute-machine-snapshot"])
        self.assertEqual(self.command.call_args.kwargs, {"timeout": 5})
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(argv[argv.index("-F") + 1], "/dev/null")
        self.assertNotIn("PRIVATE", json.dumps(report))
        for row in report["observations"]:
            if row["system_id"] == "home-core" and row["category"] == "health":
                self.assertEqual(row["observed_at"], "2026-10-09T19:29:30Z")
                self.assertEqual(row["expires_at"], "2026-10-09T19:44:30Z")
        package = next(row for row in report["observations"] if row["system_id"] == "home-core" and row["check_id"] == "package-update-report")
        self.assertEqual(package["observed_at"], "2026-10-09T18:30:00Z")
        model = next(row for row in report["observations"] if row["system_id"] == "home-core" and row["check_id"] == "model-update-report.pin_drift")
        self.assertEqual(model["observed_at"], "2026-10-09T18:30:00Z")
        metrics = report["host_metrics"]["home-core"]
        self.assertEqual(metrics["values"]["uptime_seconds"], 29000.75)
        self.assertEqual(set(metrics["values"]), set(infrastructure.METRICS))
        self.assertEqual(metrics["observed_at"], "2026-10-09T19:29:30Z")

    def test_stale_future_wrong_host_and_schema_reset_to_unknown(self):
        original = copy.deepcopy(self.raw_core)
        for change in ({"generated_at": (NOW - timedelta(seconds=61)).isoformat()},
                       {"generated_at": (NOW + timedelta(seconds=6)).isoformat()},
                       {"generated_at": "2026-10-09T19:30:00"}, {"host": "home-spark"},
                       {"schema_version": True}, {"schema_version": 2}, {"containers": "PRIVATE"}):
            self.raw_core = dict(copy.deepcopy(original), **change)
            report = self.reader.read("home-core")
            self.assertEqual(report["observations"][0]["status"], "unknown")
            self.assertEqual(report["host_metrics"]["home-core"]["status"], "unknown")
            self.assertIsNone(report["host_metrics"]["home-core"]["observed_at"])
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_bad_metrics_do_not_hide_valid_health_and_metadata(self):
        for field, value in (("memory_used_bytes", True), ("disk_free_bytes", -1),
                             ("uptime_seconds", float("nan")), ("uptime_seconds", float("inf")),
                             ("memory_total_bytes", 2**64), ("memory_used_bytes", 49 * 1024**3)):
            self.raw_core["host_metrics"] = copy.deepcopy(self.snapshot("home-core")["host_metrics"])
            self.raw_core["host_metrics"][field] = value
            report = self.reader.read("home-core")
            self.assertEqual(report["host_metrics"]["home-core"]["status"], "unknown")
            self.assertEqual(report["observations"][0]["status"], "healthy")
        self.raw_core["host_metrics"] = {}
        self.assertEqual(self.reader.read("home-core")["observations"][0]["status"], "healthy")

    def test_transport_failure_and_ha_selection_do_not_reuse_cached_health(self):
        self.assertEqual(self.reader.read("home-spark")["observations"][0]["status"], "healthy")
        self.command.side_effect = subprocess.TimeoutExpired("ssh", 5, output="PRIVATE")
        report = self.reader.read("home-spark")
        self.assertEqual(report["observations"][0]["status"], "unknown")
        self.assertEqual(report["host_metrics"]["home-spark"]["status"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.command.reset_mock()
        self.core.reset_mock()
        self.assertEqual(self.reader.read("home-assistant")["observations"][0]["status"], "unknown")
        self.command.assert_not_called()
        self.core.assert_not_called()
        with self.assertRaises(ValueError):
            self.reader.read("https://evil; reboot")

    def test_normal_context_redacts_private_fields_preserves_facts_and_is_bounded(self):
        text = self.reader.contextual_prompt("How much RAM is used on home-core?")
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("source_digest", text)
        for value in ("source_report_generated_at", "2026-10-09T18:30:00Z", "coverage", "unknown", "29000.75", "17179869184"):
            self.assertIn(value, text)
        self.assertIn("actual machines", text)
        self.assertIn("without a broker", text)
        self.assertIn("not your agent sandbox", text)
        self.assertIn("monitored units only", text)
        self.assertIn("native VM/runtime is not probed", text)
        self.assertIn("includes restarting, exited, or dead", text)
        self.assertIn("does not prove a failed healthcheck", text)
        self.assertIn("does not establish a container or agent restart cause", text)
        self.assertIn("catalog candidates, not confirmed installed-package updates", text)
        self.assertIn("not an exact number of missing sources", text)
        self.assertLessEqual(len(text.encode()), 16384)
        self.assertLessEqual(len(self.reader.contextual_prompt("x" * 4000).encode()), 16384)
        with self.assertRaises(ValueError):
            self.reader.contextual_prompt("😀" * 4000)

    def test_health_summary_has_one_compact_block_per_machine(self):
        text = self.reader.summary(self.reader.read("all"))
        self.assertEqual(text.count("\nhome-core\n"), 1)
        self.assertEqual(text.count("\nhome-spark\n"), 1)
        self.assertIn("Collected 30s ago", text)
        self.assertIn("root disk", text)
        self.assertIn("monitored failed units", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("source_digest", text)
        self.assertLessEqual(len(text), 4000)

    def test_available_metrics_cannot_make_a_degraded_machine_healthy(self):
        self.raw_spark["containers"][0]["status"] = "Restarting observed"
        report = self.reader.read("home-spark")
        self.assertEqual(report["host_metrics"]["home-spark"]["status"], "available")
        machine = Reader.compact(report)["machines"]["home-spark"]
        self.assertEqual(machine["monitored_health"]["status"], "degraded")
        prompt = self.reader.contextual_prompt("Machine status?")
        self.assertIn("Do not offer unavailable broker tools", prompt)

    def test_package_coverage_flag_never_becomes_a_source_count_in_model_context(self):
        self.raw_spark["maintenance_report"]["package_updates"]["provenance"]["skipped_sources"] = 3
        report = self.reader.read("home-spark")
        checks = Reader.compact(report)["machines"]["home-spark"]["checks"]
        coverage = next(row[3] for row in checks if row[0] == "package-update-report.coverage")
        self.assertEqual(coverage["coverage"], "incomplete")
        self.assertNotIn("count", coverage)
        updates = next(row[3] for row in checks if row[0] == "package-update-report")
        self.assertEqual(updates["reported_candidate_count"], 2)
        self.assertEqual(updates["reported_security_count"], 1)

    def test_snapshot_expiring_during_other_collection_becomes_unknown(self):
        self.raw_core["generated_at"] = (NOW - timedelta(seconds=59)).isoformat()
        self.reader.clock = iter((NOW, NOW + timedelta(seconds=2))).__next__
        report = self.reader.read("all")
        self.assertEqual(report["host_metrics"]["home-core"]["status"], "unknown")
        self.assertEqual(report["observations"][0]["status"], "unknown")

    def test_root_snapshot_requires_regular_root_owner_and_bounded_file(self):
        self.core_patch.stop()
        directory = Path(self.temp.name) / "reports"
        directory.mkdir()
        path = directory / "machine-status.json"
        path.write_text(json.dumps(self.raw_core))
        original_fstat = os.fstat
        def root_owned(fd):
            value = list(original_fstat(fd)); value[4] = 0
            return os.stat_result(value)
        with patch.object(infrastructure, "CORE_SNAPSHOT", path), patch.object(infrastructure.os, "fstat", side_effect=root_owned):
            self.assertEqual(infrastructure.root_snapshot()["host"], "home-core")
            path.chmod(0o666)
            with self.assertRaises(ValueError): infrastructure.root_snapshot()
            path.chmod(0o644)
            path.write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaises(ValueError): infrastructure.root_snapshot()
            path.unlink()
            path.symlink_to(directory / "missing")
            with self.assertRaises(OSError): infrastructure.root_snapshot()
        self.core_patch.start()


if __name__ == "__main__":
    unittest.main()
