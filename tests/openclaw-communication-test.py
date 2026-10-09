#!/usr/bin/env python3
"""Transport failure, event freshness and approval-boundary regression tests."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_notifications import Outbox, quiet
spec = importlib.util.spec_from_file_location("communication", ROOT / "scripts/openclaw-communication.py")
communication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(communication)

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()
KEY = "home-core:container.n8n"
ALLOWED = {KEY: {"category": "health", "ttl": 900}}
SETTINGS = {"schema_version": 1, "destination": "operator-private", "conversations": ["operator"],
            "projects": ["synthetic-demo"], "actions": {"synthetic.recover": "synthetic-core"}, "timezone": "Europe/Copenhagen",
            "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}
TOKENS = {role: role + "-" + "x" * 40 for role in ("collector", "conversation", "delivery")}


def iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")


def report(status="degraded", now=NOW, evidence=None):
    return {"schema_version": 1, "document_type": "system_observation_report", "generated_at": iso(now),
            "mode": "observe-only", "automatic_actions": False, "physical_device_actions": False,
            "observations": [{"system_id": "home-core", "check_id": "container.n8n", "stable_key": KEY,
                              "category": "health", "status": status,
                              "severity": "warning" if status == "degraded" else "info",
                              "evidence": evidence or {"state": "unhealthy"}, "observed_at": iso(now),
                              "expires_at": iso(now + 900)}]}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def observe(self, value):
        self.store.observations(value, ALLOWED, instant_report(value))

    def test_durable_dedup_and_fresh_envelope(self):
        self.observe(report())
        self.observe(report(now=NOW + 1200))
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.assertEqual(self.store.status(NOW)["outbox"], {"pending": 1})
        self.assertIsNotNone(self.store.claim(NOW + 1200))

    def test_unknown_and_stale_never_recover(self):
        self.observe(report())
        self.observe(report("unknown", NOW + 1))
        stale = report("healthy", NOW + 2)
        stale["observations"][0]["expires_at"] = iso(NOW + 1)
        with self.assertRaises(ValueError): self.observe(stale)
        self.assertEqual(self.store.status(NOW)["incidents"][0]["status"], "degraded")

    def test_recovery_before_delivery_is_quiet(self):
        self.observe(report())
        self.observe(report("healthy", NOW + 1))
        self.assertIsNone(self.store.claim(NOW + 1))

    def test_delivery_recovery_and_recurrence(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.observe(report("healthy", NOW + 1))
        recovery = self.store.claim(NOW + 1)
        self.assertEqual(recovery["kind"], "recovery")
        self.store.acknowledge(recovery["delivery_key"], recovery["claim"], "telegram:2", NOW + 1)
        self.observe(report(now=NOW + 1801))
        recurrence = self.store.claim(NOW + 1801)
        self.assertNotEqual(recurrence["delivery_key"], first["delivery_key"])

    def test_ambiguous_delivery_never_replays(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.assertEqual(self.store.status(NOW)["outbox"], {"uncertain": 1})
        self.assertIsNone(self.store.claim(NOW))
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        with self.assertRaises(ValueError):
            self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:2", NOW)

    def test_concurrent_claims(self):
        self.observe(report())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: self.store.claim(NOW), range(4)))
        self.assertEqual(sum(x is not None for x in values), 1)

    def test_cooldown_retains_latest_change(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.observe(report(now=NOW + 1, evidence={"state": "missing"}))
        self.assertIsNone(self.store.claim(NOW + 1))
        self.observe(report(now=NOW + 1800, evidence={"state": "missing"}))
        self.assertIsNotNone(self.store.claim(NOW + 1800))

    def test_quiet_hours_and_dst(self):
        for value in ("2026-10-09T21:00:00+00:00", "2026-10-10T04:00:00+00:00",
                      "2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"):
            self.assertTrue(quiet(datetime.fromisoformat(value).timestamp(), SETTINGS))
        self.assertFalse(quiet(NOW, SETTINGS))
        self.observe(report(now=NOW + 9 * 3600))
        self.assertIsNone(self.store.claim(NOW + 9 * 3600))

    def test_task_heartbeats_and_approval_boundary(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "pending", "updated": NOW, "context": "private", "error": "token"}
        self.store.tasks([task], NOW)
        self.store.tasks([task], NOW + 1)
        event = self.store.claim(NOW)
        self.assertEqual(event["kind"], "approval")
        self.assertIn("Chat replies do not approve", event["text"])
        self.assertNotIn("token", event["text"])
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW)
        task["state"] = "running"
        task["updated"] += 1
        self.store.tasks([task], NOW + 1)
        self.store.tasks([task], NOW + 1)
        self.assertIsNone(self.store.claim(NOW))

    def test_sensitive_fields_and_unregistered_check_rejected(self):
        data = report()
        data["observations"][0]["evidence"]["household_states"] = "private"
        with self.assertRaises(ValueError): self.observe(data)
        with self.assertRaises(ValueError): self.store.observations(report(), {}, NOW)
        data = report()
        data["observations"][0]["arbitrary_instructions"] = "private"
        self.observe(data)
        self.assertNotIn("private", self.store.claim(NOW)["text"])

    def test_action_approval_request_and_obsolete_approval(self):
        action = {"id": "11111111-1111-4111-8111-111111111111", "action_id": "synthetic.recover",
                  "target": "synthetic-core", "state": "pending", "updated": NOW, "snapshot": "private"}
        self.store.actions([action], NOW)
        self.store.actions([action], NOW)
        event = self.store.claim(NOW)
        self.assertIn("exact reviewed digest", event["text"])
        self.assertNotIn("private", event["text"])
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:action", NOW)
        action["state"] = "approved"; action["updated"] += 1
        self.store.actions([action], NOW + 1)
        self.assertIsNone(self.store.claim(NOW + 1))
        action["state"] = "pending"; action["updated"] += 1
        self.store.actions([action], NOW + 2)
        self.assertIsNotNone(self.store.claim(NOW + 2))

    def test_pending_task_approval_superseded_after_approval(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "pending", "updated": NOW}
        self.store.tasks([task], NOW)
        task["state"] = "queued"; task["updated"] += 1
        self.store.tasks([task], NOW + 1)
        self.assertIsNone(self.store.claim(NOW + 1))

    def test_stale_report_and_old_recovery(self):
        with self.assertRaises(ValueError): self.store.observations(report(), ALLOWED, NOW + 1000)
        self.observe(report())
        self.observe(report("healthy", NOW - 1))
        self.assertEqual(len(self.store.status(NOW)["incidents"]), 1)

    def test_equal_timestamp_conflicts_and_heartbeat_watermark(self):
        self.observe(report())
        with self.assertRaises(ValueError): self.observe(report("healthy"))
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "running", "updated": NOW}
        self.store.tasks([task], NOW)
        task["updated"] = NOW + 30
        self.store.tasks([task], NOW + 30)
        task["state"] = "pending"; task["updated"] = NOW + 1
        self.store.tasks([task], NOW + 30)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events WHERE kind='approval'").fetchone())
        task["updated"] = NOW + 30
        with self.assertRaises(ValueError): self.store.tasks([task], NOW + 30)

    def test_recovery_freshness_and_reopened_incident(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "test:1", NOW)
        self.observe(report("healthy", NOW + 1))
        self.assertIsNone(self.store.claim(NOW + 902))
        self.observe(report(now=NOW + 903))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events WHERE kind='recovery' AND state='pending'").fetchone())

    def test_registry_ttl_and_source_report_expiry(self):
        value = report()
        value["observations"][0]["expires_at"] = iso(NOW + 901)
        with self.assertRaises(ValueError): self.observe(value)
        value = report(evidence={"count": 0, "source_report_generated_at": iso(NOW - 1000)})
        with self.assertRaises(ValueError): self.observe(value)

    def test_repeated_ack_cannot_extend_cooldown(self):
        self.observe(report())
        event = self.store.claim(NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW + 900)
        row = self.store.db.execute("SELECT created FROM events WHERE key=?", (event["delivery_key"],)).fetchone()
        self.assertEqual(row[0], NOW)

    def test_explicit_reply_bypasses_proactive_quiet_hours(self):
        late = NOW + 9 * 3600
        body = {"conversation": "operator", "destination": "operator-private", "request_id": "quiet:1", "text": "/status"}
        communication.conversation(body, self.store, lambda *_: "unused", late)
        self.assertEqual(self.store.claim(late)["kind"], "reply")

    def test_stale_queue_cannot_starve_required_action(self):
        with self.store.db:
            for number in range(101):
                self.store.add("old:" + str(number), "old", "incident", {"observation": {"expires_at": iso(NOW - 1)}, "text": "old"}, NOW)
            self.store.add("required", "task", "approval", {"text": "Approval required"}, NOW)
        self.assertEqual(self.store.claim(NOW)["kind"], "approval")

    def test_newer_task_state_cannot_regress_and_status_is_bounded(self):
        import uuid
        tasks = [{"id": str(uuid.uuid4()), "project": "synthetic-demo", "state": "running", "updated": NOW} for _ in range(20)]
        self.store.tasks(tasks, NOW)
        regression = dict(tasks[0], state="pending", updated=NOW + 1)
        with self.assertRaises(ValueError): self.store.tasks([regression], NOW + 1)
        body = {"conversation": "operator", "destination": "operator-private", "request_id": "status:bounded", "text": "/status"}
        result = communication.conversation(body, self.store, lambda *_: "unused", NOW)
        status = json.loads(result["reply"])
        self.assertEqual(result["state"], "completed")
        self.assertTrue(status["tasks_truncated"])
        self.assertLess(len(result["reply"]), 4000)

    def test_equivalent_timestamp_formats_cannot_conflict(self):
        self.observe(report())
        value = report("healthy")
        value["observations"][0]["observed_at"] = value["observations"][0]["observed_at"].replace("Z", "+00:00")
        with self.assertRaises(ValueError): self.observe(value)
        self.assertEqual(len(self.store.status(NOW)["incidents"]), 1)

    def test_model_counter_cannot_omit_source_provenance(self):
        value = report(evidence={"count": 0})
        row = value["observations"][0]
        row.update(category="updates", check_id="model-update-report.changed", stable_key="home-core:model-update-report.changed")
        allowed = {row["stable_key"]: {"category": "updates", "ttl": 900}}
        with self.assertRaises(ValueError): self.store.observations(value, allowed, NOW)
        row["evidence"]["source_report_generated_at"] = iso(NOW)
        self.store.observations(value, allowed, NOW)

    def test_unknown_observation_watermark_survives_restart(self):
        self.observe(report())
        self.observe(report("unknown", NOW + 60))
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.observe(report("healthy", NOW + 30))
        self.assertEqual(len(self.store.status(NOW + 60)["incidents"]), 1)
        self.observe(report("healthy", NOW + 61))
        self.assertEqual(len(self.store.status(NOW + 61)["incidents"]), 0)

    def test_native_analysis_envelope_is_scoped_and_fresh(self):
        self.observe(report())
        event = self.store.claim(NOW)
        envelope = self.store.incident(event["delivery_key"], NOW)
        self.assertEqual(envelope["issue_key"], "monitor:" + envelope["observation"]["episode_key"])
        self.assertNotIn("text", envelope)
        with self.assertRaises(ValueError): self.store.incident(event["delivery_key"], NOW + 901)
        self.observe(report("healthy", NOW + 1))
        with self.assertRaises(ValueError): self.store.incident(event["delivery_key"], NOW + 1)

    def test_http_e2e_auth_dedup_status_send_ack_and_model_outage(self):
        calls = []
        def turn(name, text):
            calls.append((name, text))
            if text == "outage": raise RuntimeError("secret diagnostics")
            return "synthetic reply"
        server = communication.serve(self.store, TOKENS, ALLOWED, turn, ("127.0.0.1", 0), lambda: NOW)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def post(path, role, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + TOKENS[role], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response: return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/claim", "conversation", {})
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            post("/observations", "collector", report())
            event = post("/claim", "delivery", {})["event"]
            self.assertEqual(event["destination"], "operator-private")
            post("/ack", "delivery", {"delivery_key": event["delivery_key"], "claim": event["claim"], "receipt": "synthetic:42"})
            self.assertIsNone(post("/claim", "delivery", {})["event"])
            analysis = post("/analysis", "collector", {"delivery_key": event["delivery_key"]})
            self.assertEqual(analysis["state"], "completed")
            self.assertIn("Read-only", calls[-1][1])
            body = {"conversation": "operator", "destination": "operator-private", "request_id": "update:1", "text": "hello"}
            self.assertEqual(post("/conversation", "conversation", body)["reply"], "synthetic reply")
            post("/conversation", "conversation", body)
            self.assertEqual(len(calls), 2)
            body["request_id"] = "update:2"; body["text"] = "/approve anything"
            self.assertIn("cannot approve", post("/conversation", "conversation", body)["reply"])
            body["request_id"] = "update:3"; body["text"] = "outage"
            self.assertEqual(post("/conversation", "conversation", body)["state"], "uncertain")
            post("/conversation", "conversation", body)
            self.assertEqual(len(calls), 3)
            body["destination"] = "family"
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/conversation", "conversation", body)
            denied.exception.close()
            self.assertFalse(post("/status", "conversation", {})["actions_enabled"])
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/approve", "conversation", {})
            denied.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_fail_closed_configuration(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(SETTINGS))
        communication.load_config(path)
        with self.assertRaises(ValueError): communication.load_config(ROOT / "config/openclaw-communication.example.json")
        with self.assertRaises(ValueError): communication.serve(self.store, TOKENS, ALLOWED, lambda *_: "", ("0.0.0.0", 18793))
        tokens = dict(TOKENS); tokens["delivery"] = tokens["collector"]
        with self.assertRaises(ValueError): communication.serve(self.store, tokens, ALLOWED, lambda *_: "")
        self.assertIn("home-core:container.homecompute-automation-n8n-1", communication.registry_keys(ROOT / "config/system-monitoring.json"))
        self.assertFalse(any(x.startswith("home-assistant:") for x in communication.registry_keys(ROOT / "config/system-monitoring.json")))


def instant_report(value):
    return datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00")).timestamp()


if __name__ == "__main__":
    unittest.main()
