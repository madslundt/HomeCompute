"""Synthetic one-shot Hermes delivery and durable outbox tests; no network sends."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from assistant_attention import AttentionLedger, evaluate  # noqa: E402
from assistant_context_delivery import (  # noqa: E402
    DeliveryAuth, DeliveryError, DeliveryOutbox, ENDPOINT, HermesHTTPTransport,
    ROUTE_NAME, TARGET, classify_hermes_response, hermes_v2_headers, load_delivery_auth,
    read_source_coverage,
)
from assistant_context_store import ContextStore  # noqa: E402
from assistant_context_store import Scope  # noqa: E402

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
SCOPE = Scope("owner", "personal", "private")
SECRET = "synthetic-hmac-secret-" + "x" * 32
MESSAGE = "A synthetic Aula change is ready to review."
SOURCE_STATUS = {"aula": {"state": "fresh", "last_success_at": NOW.isoformat()}}
ACTIVE_POLICY = json.loads((ROOT / "config/assistant-attention.json").read_text())
ACTIVE_POLICY.update(mode="active", daily_discretionary_notification_limit=20,
                     rules=[{"source": "aula", "event_type": "aula.calendar.snapshot",
                                             "outcome": "notify", "class": "synthetic-test"}])


def event(number: int = 1, *, revision: int = 1, operation: str = "upsert") -> dict:
    return {"schema_version": 1, "source": "aula", "object_id": f"fixture-{number}",
            "revision": revision, "event_type": "aula.calendar.snapshot", "operation": operation,
            "occurred_at": NOW.isoformat(), "source_ref": f"fixture://aula/{number}",
            "payload": {"title": f"Synthetic event {number}", "date": "2026-11-07"}}


def response(status: str, delivery_id: str, *, response_status: int = 200, extra: dict | None = None) -> tuple[int, bytes]:
    body = {"status": status, "route": ROUTE_NAME, "target": TARGET, "delivery_id": delivery_id, **(extra or {})}
    return response_status, json.dumps(body).encode()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir(mode=0o700)
        self.store = ContextStore(self.state / "context.sqlite3")
        self.ledger = AttentionLedger(self.state / "attention.sqlite3", dry_run=False)
        self.outbox = DeliveryOutbox(self.state / "delivery.sqlite3", clock=lambda: NOW)

    def add_decision(self, number: int = 1, *, revision: int = 1, operation: str = "upsert", policy=None):
        ev = event(number, revision=revision, operation=operation)
        receipt = self.store.ingest(SCOPE, ev)
        self.assertEqual(receipt["status"], "accepted")
        cfg = policy or ACTIVE_POLICY
        decision = evaluate(ev, scope=SCOPE, now=NOW, source_status=SOURCE_STATUS, policy=cfg)
        row = self.ledger.record_decision(ev, scope=SCOPE, decision=decision, now=NOW)
        return row, ev

    def admit(self, number: int = 1, *, message=MESSAGE, decision=None, source_status=SOURCE_STATUS, policy=None,
              dry_run=False):
        if decision is None:
            decision, _ = self.add_decision(number)
        return self.outbox.admit(scope=SCOPE, decision_id=decision["decision_id"], message=message,
            ledger=self.ledger, context=self.store, source_status=source_status,
            policy=policy or ACTIVE_POLICY, now=NOW, dry_run=dry_run)

    def private_auth(self) -> DeliveryAuth:
        path = self.state / "delivery-auth.json"
        route_hash = "a" * 64
        value = {"schema_version": 1, "route_name": ROUTE_NAME, "route_registered": True,
                 "target": TARGET, "owner_user_id": "owner-user-synthetic", "bot_user_id": "hermes-bot-synthetic",
                 "channel_type": "D", "owner_destination_id": "owner-dm-synthetic",
                 "route_config_sha256": route_hash, "route_secret": SECRET,
                 "standing_grant_id": "c" * 32, "standing_grant_status": "active",
                 "allowed_sources": ["aula"], "allowed_policy_versions": ["1.0.0"],
                 "grant_issued_at": (NOW - timedelta(days=1)).isoformat(),
                 "grant_expires_at": (NOW + timedelta(days=30)).isoformat(),
                 "qualification_evidence_ref": "fixture://qualified-owner-dm"}
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return load_delivery_auth(path, now=NOW)

    def test_generic_v2_headers_match_pinned_timestamp_dot_raw_body_hmac(self):
        body = b'{"event_type":"homecompute_attention","message":"fixture"}'
        timestamp = int(NOW.timestamp())
        headers = hermes_v2_headers(SECRET, body, timestamp=timestamp, request_id="a" * 32, now=NOW)
        expected = hmac.new(SECRET.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Webhook-Signature-V2"], expected)
        self.assertEqual(headers["X-Webhook-Timestamp"], str(timestamp))
        self.assertEqual(headers["X-Request-ID"], "a" * 32)
        with self.assertRaises(DeliveryError):
            hermes_v2_headers("short", body, timestamp=timestamp, request_id="a" * 32, now=NOW)
        with self.assertRaises(DeliveryError):
            hermes_v2_headers(SECRET, body, timestamp=timestamp - 301, request_id="a" * 32, now=NOW)

    def test_active_delivery_uses_claim_before_single_fixed_route_call_and_records_only_real_receipt(self):
        row, _ = self.add_decision()
        admitted = self.admit(decision=row)
        self.assertEqual(admitted["status"], "pending")
        calls = []

        def transport(body, headers, *, timeout):
            calls.append((body, headers, timeout))
            self.assertEqual(timeout, 8.0)
            payload = json.loads(body)
            self.assertEqual(payload["decision_id"], row["decision_id"])
            self.assertEqual(payload["source"], "aula")
            self.assertEqual(payload["revision"], 1)
            self.assertEqual(payload["message"], MESSAGE)
            expected = hmac.new(SECRET.encode(), headers["X-Webhook-Timestamp"].encode() + b"." + body,
                                hashlib.sha256).hexdigest()
            self.assertEqual(headers["X-Webhook-Signature-V2"], expected)
            return response("delivered", headers["X-Request-ID"])

        final = self.outbox.send_once(scope=SCOPE, decision_id=row["decision_id"], ledger=self.ledger,
            context=self.store, source_status=SOURCE_STATUS, policy=ACTIVE_POLICY,
            auth=self.private_auth(), now=NOW, transport=transport)
        self.assertEqual((final["status"], len(calls)), ("delivered", 1))
        self.assertEqual(final["hermes_delivery_id"], final["delivery_id"])
        self.assertIsNone(final["mattermost_post_id"])
        self.assertIsNone(final["mattermost_root_id"])
        self.assertEqual(self.ledger.get_decision(scope=SCOPE, decision_id=row["decision_id"])["state"], "delivered")
        with self.assertRaises(DeliveryError):
            self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)

    def test_duplicate_admission_and_cross_scope_reads_are_isolated(self):
        row, _ = self.add_decision()
        first = self.admit(decision=row)
        second = self.admit(decision=row)
        self.assertEqual(first["delivery_id"], second["delivery_id"])
        self.assertIsNone(self.outbox.get_by_decision(scope=Scope("family", "household", "shared"), decision_id=row["decision_id"]))
        with self.assertRaises(DeliveryError):
            self.admit(decision=row, message="different content")

    def test_concurrent_claimers_admit_exactly_one_network_attempt(self):
        row, _ = self.add_decision()
        self.admit(decision=row)
        from concurrent.futures import ThreadPoolExecutor

        def claim():
            try:
                return self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)["status"]
            except DeliveryError:
                return "refused"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(results), ["claimed", "refused"])

    def test_claim_is_atomic_and_restart_recovery_never_resends(self):
        row, _ = self.add_decision()
        self.admit(decision=row)
        claimed = self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)
        self.assertEqual(claimed["status"], "claimed")
        reopened = DeliveryOutbox(self.state / "delivery.sqlite3", clock=lambda: NOW)
        self.assertEqual(reopened.recover_inflight(scope=SCOPE, now=NOW), 1)
        recovered = reopened.get_by_decision(scope=SCOPE, decision_id=row["decision_id"])
        self.assertEqual(recovered["status"], "uncertain")
        with self.assertRaises(DeliveryError):
            reopened.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)

    def test_http_202_duplicate_and_502_are_not_delivery_and_never_retry(self):
        for number, status, payload_state in ((1, 202, "accepted"), (2, 200, "duplicate"), (3, 502, "error")):
            row, _ = self.add_decision(number)
            self.admit(number, decision=row)
            target = response(payload_state, "" if status == 502 else "d" * 32, response_status=status)
            if status != 502:
                # Use the persisted stable request ID, not an arbitrary upstream delivery id.
                target = response(payload_state, self.outbox.get_by_decision(scope=SCOPE, decision_id=row["decision_id"])["delivery_id"], response_status=status)
            result = self.outbox.send_once(scope=SCOPE, decision_id=row["decision_id"], ledger=self.ledger,
                context=self.store, source_status=SOURCE_STATUS, policy=ACTIVE_POLICY, auth=self.private_auth(), now=NOW,
                transport=lambda body, headers, *, timeout, target=target: target)
            expected = "accepted" if status == 202 else "uncertain"
            self.assertEqual(result["status"], expected)
            with self.assertRaises(DeliveryError):
                self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)

    def test_only_matching_200_delivered_response_confirms_and_real_ids_are_retained(self):
        did = "f" * 32
        state, result = classify_hermes_response(200, json.dumps({"status": "delivered", "route": ROUTE_NAME,
            "target": TARGET, "delivery_id": did, "post_id": "post-real", "root_id": "root-real"}).encode(), delivery_id=did)
        self.assertEqual(state, "delivered")
        self.assertEqual((result["mattermost_post_id"], result["mattermost_root_id"]), ("post-real", "root-real"))
        self.assertEqual(classify_hermes_response(200, b'{"status":"delivered"}', delivery_id=did)[0], "held")
        self.assertEqual(classify_hermes_response(202, json.dumps({"status": "accepted", "delivery_id": did}).encode(), delivery_id=did)[0], "accepted")

    def test_default_dry_run_stays_shadow_and_never_calls_transport(self):
        row, _ = self.add_decision()
        dry = self.admit(decision=row, dry_run=True)
        self.assertEqual(dry["status"], "shadow")
        with self.assertRaises(DeliveryError):
            self.outbox.send_once(scope=SCOPE, decision_id=row["decision_id"], ledger=self.ledger,
                context=self.store, source_status=SOURCE_STATUS, policy=ACTIVE_POLICY,
                auth=self.private_auth(), now=NOW, transport=lambda *a, **k: self.fail("transport called"))

    def test_stale_source_deletion_and_stopped_decision_cannot_dispatch(self):
        row, ev = self.add_decision()
        self.admit(decision=row)
        self.store.ingest(SCOPE, {**ev, "operation": "delete", "revision": 2, "payload": {}})
        self.outbox.purge_source(scope=SCOPE, source="aula", object_id="fixture-1", now=NOW)
        removed = self.outbox.get_by_decision(scope=SCOPE, decision_id=row["decision_id"])
        self.assertEqual((removed["status"], removed["reason"]), ("cancelled", "source_deleted"))
        with closing(sqlite3.connect(self.state / "delivery.sqlite3")) as db:
            self.assertIsNone(db.execute("SELECT message FROM delivery_outbox WHERE decision_id=?", (row["decision_id"],)).fetchone()[0])
        later, ev2 = self.add_decision(2)
        self.ledger.apply_decision(scope=SCOPE, decision_id=later["decision_id"], operation="stop",
            expected_revision=1, request_id="stop-test", now=NOW)
        with self.assertRaises(DeliveryError):
            self.admit(2, decision=later)

    def test_freshness_quiet_hours_and_revision_are_rechecked_at_admission(self):
        row, _ = self.add_decision()
        with self.assertRaises(DeliveryError):
            self.admit(decision=row, source_status={"aula": {"state": "stale", "last_success_at": NOW.isoformat()}})
        with self.assertRaises(DeliveryError):
            self.admit(decision=row, source_status={"aula": {"state": "partial", "last_success_at": NOW.isoformat()}})
        self.assertEqual(self.outbox.get_by_decision(scope=SCOPE, decision_id=row["decision_id"]), None)
        ev = event(2)
        self.store.ingest(SCOPE, ev)
        stale_decision = self.ledger.record_decision(ev, scope=SCOPE,
            decision=evaluate(ev, scope=SCOPE, now=NOW, source_status=SOURCE_STATUS, policy=ACTIVE_POLICY), now=NOW)
        self.store.ingest(SCOPE, {**ev, "revision": 2, "payload": {"title": "Updated"}})
        with self.assertRaises(DeliveryError):
            self.admit(2, decision=stale_decision)
        quiet_now = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)
        with self.assertRaises(DeliveryError):
            self.outbox.admit(scope=SCOPE, decision_id=row["decision_id"], message=MESSAGE,
                ledger=self.ledger, context=self.store, source_status=SOURCE_STATUS,
                policy=ACTIVE_POLICY, now=quiet_now, dry_run=False)

    def test_coverage_change_after_claim_holds_without_network_call(self):
        row, _ = self.add_decision(3)
        self.admit(3, decision=row)
        observations = iter((SOURCE_STATUS, {"aula": {"state": "stale", "last_success_at": NOW.isoformat()}}))
        result = self.outbox.send_once(scope=SCOPE, decision_id=row["decision_id"], ledger=self.ledger,
            context=self.store, source_status=lambda: next(observations), policy=ACTIVE_POLICY,
            auth=self.private_auth(), now=NOW,
            transport=lambda *args, **kwargs: self.fail("stale coverage reached transport"))
        self.assertEqual((result["status"], result["reason"]), ("held", "fresh_dispatch_gate_suppressed"))

    def test_source_purge_during_claim_makes_effect_uncertain_and_scrubs_body(self):
        row, _ = self.add_decision()
        self.admit(decision=row)
        self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)
        self.assertEqual(self.outbox.purge_source(scope=SCOPE, source="aula", object_id="fixture-1", now=NOW), 1)
        current = self.outbox.get_by_decision(scope=SCOPE, decision_id=row["decision_id"])
        self.assertEqual(current["status"], "uncertain")
        with self.assertRaises(DeliveryError):
            self.outbox.claim(scope=SCOPE, decision_id=row["decision_id"], now=NOW)

    def test_private_auth_requires_operator_registration_and_standing_owner_dm_grant(self):
        path = self.state / "delivery-auth.json"
        self.add_decision()
        value = {"schema_version": 1, "route_name": ROUTE_NAME, "route_registered": True,
                 "target": TARGET, "owner_user_id": "owner-user-synthetic", "bot_user_id": "hermes-bot-synthetic",
                 "channel_type": "D", "owner_destination_id": "owner-dm-synthetic",
                 "route_config_sha256": "a" * 64, "route_secret": SECRET,
                 "standing_grant_id": "c" * 32, "standing_grant_status": "active",
                 "allowed_sources": ["aula"], "allowed_policy_versions": ["1.0.0"],
                 "grant_issued_at": (NOW - timedelta(days=1)).isoformat(),
                 "grant_expires_at": (NOW + timedelta(days=30)).isoformat(),
                 "qualification_evidence_ref": "fixture://qualified-owner-dm"}
        path.write_text(json.dumps(value))
        path.chmod(0o644)
        with self.assertRaises(DeliveryError):
            load_delivery_auth(path, now=NOW)
        path.chmod(0o600)
        for change in ({"route_registered": False}, {"standing_grant_status": "revoked"},
                       {"channel_type": "P"}, {"owner_destination_id": "REPLACE"},
                       {"route_secret": "x"}, {"route_config_sha256": "bad"},
                       {"allowed_sources": ["mail"]}, {"grant_expires_at": (NOW - timedelta(seconds=1)).isoformat()}):
            path.write_text(json.dumps({**value, **change}))
            path.chmod(0o600)
            with self.assertRaises(DeliveryError):
                load_delivery_auth(path, now=NOW)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        loaded = self.private_auth()
        self.assertEqual(loaded.owner_destination_id, "owner-dm-synthetic")
        with self.assertRaises(DeliveryError):
            DeliveryAuth(SECRET, "owner-user", "hermes-bot", "owner-dm", "a" * 64,
                "b" * 32, frozenset({"aula"}), frozenset({"1.0.0"}),
                (NOW + timedelta(days=1)).isoformat(), object())

    def test_one_qualified_standing_grant_covers_multiple_eligible_decisions(self):
        auth = self.private_auth()
        results = []
        for number in (10, 11):
            row, _ = self.add_decision(number)
            self.admit(number, decision=row)
            result = self.outbox.send_once(scope=SCOPE, decision_id=row["decision_id"], ledger=self.ledger,
                context=self.store, source_status=SOURCE_STATUS, policy=ACTIVE_POLICY, auth=auth, now=NOW,
                transport=lambda body, headers, *, timeout: response("delivered", headers["X-Request-ID"]))
            results.append(result["status"])
        self.assertEqual(results, ["delivered", "delivered"])

    def test_fixed_http_transport_refuses_any_endpoint_change(self):
        self.assertEqual(ENDPOINT, "http://10.77.20.2:8644/webhooks/homecompute-context-owner")
        import assistant_context_delivery as delivery_module
        original = delivery_module.ENDPOINT
        try:
            delivery_module.ENDPOINT = "http://evil.invalid/redirect"
            with self.assertRaises(DeliveryError):
                HermesHTTPTransport()(b"{}", {}, timeout=0.1)
        finally:
            delivery_module.ENDPOINT = original

    def test_current_coverage_reader_is_scope_filtered(self):
        path = self.state / "coverage.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE coverage(principal TEXT,domain TEXT,visibility TEXT,source TEXT,observed_at TEXT,complete INTEGER)")
            db.execute("INSERT INTO coverage VALUES(?,?,?,?,?,?)", ("owner", "personal", "private", "aula", NOW.isoformat(), 1))
            db.execute("INSERT INTO coverage VALUES(?,?,?,?,?,?)", ("family", "household", "shared", "calendar", NOW.isoformat(), 1))
            db.commit()
        result = read_source_coverage(path, scope=SCOPE, now=NOW)
        self.assertEqual(result["aula"]["state"], "fresh")
        self.assertEqual(result["calendar"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
