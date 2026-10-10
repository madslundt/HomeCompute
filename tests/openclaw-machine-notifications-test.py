#!/usr/bin/env python3
"""Machine batching keeps individual observation ownership and delivery receipts."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from openclaw_notifications import Outbox

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp()
SETTINGS = {"destination": "operator-private", "timezone": "Europe/Copenhagen",
            "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}


def iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "outbox.sqlite3"
        self.store = Outbox(self.path, SETTINGS)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def add(self, check, system="home-core", kind="incident", expiry=NOW + 900, evidence=None):
        key = system + ":" + check
        row = {"system_id": system, "check_id": check, "stable_key": key,
               "category": "updates", "status": "healthy" if kind == "recovery" else "degraded",
               "evidence": evidence or {"count": 2}, "expires_at": iso(expiry)}
        with self.store.db:
            self.store.add(key, key, kind, {"observation": row, "text": "old individual text"}, NOW)
            self.store.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, json.dumps(row)))
        return key

    def test_one_claim_per_machine_and_shared_receipt(self):
        first = self.add("package-update-report", evidence={"candidate_count": 163, "security_count": 123})
        second = self.add("docker-image-report.coverage", evidence={"count": 7})
        self.add("package-update-report.reboot-required", evidence={"available": True})
        self.add("package-update-report", "home-spark")
        event = self.store.claim(NOW)
        self.assertIn("home-core", event["text"])
        for text in ("163", "123", "7", "Reboot required"):
            self.assertIn(text, event["text"])
        self.assertNotIn("home-spark", event["text"])
        self.assertEqual(event["text"].count("operator approval"), 1)
        self.assertNotIn('"candidate_count"', event["text"])
        rows = self.store.db.execute("SELECT key FROM events WHERE claim=?", (event["claim"],)).fetchall()
        self.assertEqual(len(rows), 3)
        self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:42", NOW)
        self.assertEqual(self.store.db.execute("SELECT receipt FROM events WHERE key=?", (second,)).fetchone()[0], "telegram:42")
        self.assertIn("home-spark", self.store.claim(NOW)["text"])

    def test_mixed_recovery_does_not_claim_machine_healthy(self):
        self.add("package-update-report")
        self.add("docker-image-report", kind="recovery")
        text = self.store.claim(NOW)["text"]
        self.assertIn("Recovered", text)
        self.assertIn("Packages", text)
        self.assertNotIn("machine is healthy", text)

    def test_stale_cooldown_and_unhealthy_recovery_excluded(self):
        self.add("stale", expiry=NOW - 1)
        cool = self.add("cool")
        with self.store.db:
            self.store.add("previous", cool, "incident", {}, NOW)
            self.store.db.execute("UPDATE events SET state='delivered' WHERE key='previous'")
        recovery = self.add("recovery", kind="recovery")
        with self.store.db:
            self.store.db.execute("UPDATE baseline SET body=? WHERE key=?", (json.dumps({"status": "degraded"}), recovery))
        self.add("package-update-report")
        event = self.store.claim(NOW)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE claim=?", (event["claim"],)).fetchone()[0], 1)

    def test_restart_or_expiry_never_resends_members(self):
        for restart in (False, True):
            self.store.db.execute("DELETE FROM events")
            self.add("one"); self.add("two")
            event = self.store.claim(NOW)
            if restart:
                self.store.db.close(); self.store = Outbox(self.path, SETTINGS)
            self.assertIsNone(self.store.claim(NOW + 301))
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE state='uncertain'").fetchone()[0], 2)
            self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:43", NOW + 302)
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE state='delivered'").fetchone()[0], 2)

    def test_ack_membership_frozen_and_duplicate_does_not_move_timestamp(self):
        self.add("one"); self.add("two")
        event = self.store.claim(NOW)
        late = self.add("three")
        self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:44", NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:44", NOW + 900)
        with self.assertRaises(ValueError):
            self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:45", NOW + 900)
        rows = self.store.db.execute("SELECT state,created FROM events WHERE claim=?", (event["claim"],)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("delivered", NOW)] * 2)
        self.assertEqual(self.store.db.execute("SELECT state FROM events WHERE key=?", (late,)).fetchone()[0], "pending")

    def test_concurrent_claims_and_reply_priority(self):
        self.add("one"); self.add("two")
        with self.store.db:
            self.store.add("reply:test", "conversation:test", "reply", {"text": "Reply"}, NOW)
        reply = self.store.claim(NOW)
        self.assertEqual(reply["text"], "Reply")
        self.store.acknowledge(reply["delivery_key"], reply["claim"], "telegram:46", NOW)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.store.claim(NOW), range(4)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE state='sending'").fetchone()[0], 2)

    def test_text_limit_keeps_omitted_members_pending(self):
        for n in range(100):
            self.add("check-" + str(n) + "x" * 90)
        event = self.store.claim(NOW)
        self.assertLessEqual(len(event["text"]), 4000)
        claimed = self.store.db.execute("SELECT count(*) FROM events WHERE claim=?", (event["claim"],)).fetchone()[0]
        self.assertGreater(claimed, 1)
        self.assertLess(claimed, 100)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE state='pending'").fetchone()[0], 100 - claimed)

    def test_catalog_disclaimer_and_provenance_are_not_raw_chat_data(self):
        self.add("package-update-report", evidence={"candidate_count": 163, "update_scope": "package_catalog",
                 "source_digest": "a" * 64, "source_report_generated_at": iso(NOW)})
        self.add("package-update-report.coverage", evidence={"count": 1})
        text = self.store.claim(NOW)["text"]
        self.assertIn("installed status unverified", text)
        self.assertIn("Package update coverage: incomplete or unverified", text)
        self.assertNotIn("source_digest", text)
        self.assertNotIn("a" * 64, text)
        self.assertNotIn(iso(NOW), text)

    def test_wrong_claim_and_member_receipt_conflict_are_atomic(self):
        self.add("one"); second = self.add("two")
        event = self.store.claim(NOW)
        with self.assertRaises(ValueError):
            self.store.acknowledge(event["delivery_key"], "wrong", "telegram:47", NOW)
        with self.store.db:
            self.store.db.execute("UPDATE events SET state='delivered',receipt='telegram:48' WHERE key=?", (second,))
        with self.assertRaises(ValueError):
            self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:47", NOW)
        self.assertEqual(self.store.db.execute("SELECT state FROM events WHERE key=?", (event["delivery_key"],)).fetchone()[0], "sending")

    def test_quiet_hours_keep_machine_batch_pending_but_reply_can_send(self):
        self.add("one", expiry=NOW + 86400); self.add("two", expiry=NOW + 86400)
        late = NOW + 9 * 3600
        self.assertIsNone(self.store.claim(late))
        with self.store.db:
            self.store.add("reply:late", "conversation:test", "reply", {"text": "Reply"}, late)
        self.assertEqual(self.store.claim(late)["kind"], "reply")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE state='pending'").fetchone()[0], 2)

    def test_grouped_delivery_retains_individual_recovery_episodes(self):
        def report(status, at):
            rows = [{"system_id": "home-core", "check_id": check, "stable_key": "home-core:" + check,
                     "category": "health", "status": status, "severity": "warning" if status == "degraded" else "info",
                     "evidence": {"state": "unhealthy" if status == "degraded" else "healthy"},
                     "observed_at": iso(at), "expires_at": iso(at + 900)} for check in ("container.one", "container.two")]
            return {"schema_version": 1, "document_type": "system_observation_report", "generated_at": iso(at),
                    "mode": "observe-only", "automatic_actions": False, "physical_device_actions": False, "observations": rows}
        allowed = {"home-core:container." + name: {"category": "health", "ttl": 900} for name in ("one", "two")}
        self.store.observations(report("degraded", NOW), allowed, NOW)
        event = self.store.claim(NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "telegram:49", NOW)
        self.store.observations(report("healthy", NOW + 1), allowed, NOW + 1)
        recovery = self.store.claim(NOW + 1)
        self.assertEqual(recovery["text"].count("Recovered:"), 2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE claim=?", (recovery["claim"],)).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
