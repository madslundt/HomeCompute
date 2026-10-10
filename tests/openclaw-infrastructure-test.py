#!/usr/bin/env python3
"""Finite read selection, redaction, deadlines, native replay and authority tests."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_infrastructure import Reader
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


if __name__ == "__main__":
    unittest.main()
