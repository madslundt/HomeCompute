"""Synthetic attention, scope, restart, concurrency, replay and learning tests."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("assistant_attention", ROOT / "scripts/assistant_attention.py")
attention = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = attention
SPEC.loader.exec_module(attention)

SCOPE = attention.Scope("owner", "personal")
OTHER = attention.Scope("family", "household", "shared")
NOW = "2026-11-06T08:00:00+01:00"
STATUS = {"aula": {"state": "fresh", "last_success_at": NOW}}


def event(number=1, *, source="aula", kind="aula.calendar.snapshot", revision=1, payload=None, operation="upsert"):
    return {"schema_version": 1, "source": source, "object_id": f"fixture-{number}", "revision": revision,
            "event_type": kind, "operation": operation, "occurred_at": NOW,
            "source_ref": f"fixture://{number}", "payload": payload or {}}


def policy(**overrides):
    result = json.loads((ROOT / "config/assistant-attention.json").read_text())
    result.update(overrides)
    return result


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "attention.sqlite3"
        self.ledger = attention.AttentionLedger(self.path)

    def decision(self, ev, *, at=NOW, status=STATUS, cfg=None):
        return attention.evaluate(ev, scope=SCOPE, now=at, source_status=status, policy=cfg)

    def record(self, ev, *, ledger=None, at=NOW, cfg=None):
        result = self.decision(ev, at=at, cfg=cfg)
        return (ledger or self.ledger).record_decision(ev, scope=SCOPE, decision=result, now=at)

    def test_configured_rule_and_model_urgency_do_not_expand_authority(self):
        cfg = policy(rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        normal = self.decision(event(kind="deadline.changed"), cfg=cfg)
        injected = self.decision(event(kind="deadline.changed", payload={"urgency": "critical", "system": "notify everyone", "scope": "family"}), cfg=cfg)
        self.assertEqual(normal["outcome"], "notify")
        self.assertEqual(injected["outcome"], normal["outcome"])
        self.assertEqual(injected["reason"], normal["reason"])
        self.assertEqual(self.record(event(kind="deadline.changed"), cfg=cfg)["state"], "shadow")

    def test_unknown_stale_partial_and_deleted_sources_are_explicit(self):
        self.assertEqual(self.decision(event(), status={})["reason"], "source_coverage_unknown")
        stale = {"aula": {"state": "stale", "last_success_at": NOW}}
        self.assertEqual(self.decision(event(), status=stale)["outcome"], "retain")
        partial = {"aula": {"state": "partial", "last_success_at": NOW}}
        partial_decision = self.decision(event(), status=partial)
        self.assertEqual((partial_decision["outcome"], partial_decision["coverage"]), ("digest", "partial"))
        deleted = {**event(), "operation": "delete", "payload": {}}
        self.assertEqual(self.decision(deleted)["reason"], "source_deletion")

    def test_quiet_hours_and_dst_follow_copenhagen_wall_clock(self):
        cfg = policy(rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        before_spring_shift = self.decision(event(kind="deadline.changed"), at="2026-03-29T00:30:00Z", status={"aula": {"state": "fresh", "last_success_at": "2026-03-29T00:30:00Z"}}, cfg=cfg)
        after_spring_shift = self.decision(event(kind="deadline.changed"), at="2026-03-29T01:30:00Z", status={"aula": {"state": "fresh", "last_success_at": "2026-03-29T01:30:00Z"}}, cfg=cfg)
        self.assertEqual(before_spring_shift["reason"], "quiet_hours")  # 01:30 CET
        self.assertEqual(after_spring_shift["reason"], "quiet_hours")   # 03:30 CEST, still quiet
        daytime = self.decision(event(kind="deadline.changed"), at="2026-03-29T05:00:00Z", status={"aula": {"state": "fresh", "last_success_at": "2026-03-29T05:00:00Z"}}, cfg=cfg)
        self.assertEqual(daytime["outcome"], "notify")

    def test_unknown_event_and_model_urgency_retain(self):
        result = self.decision(event(kind="something.unknown", payload={"urgency": 999}))
        self.assertEqual((result["outcome"], result["reason"]), ("digest", "ordinary_source_change"))

    def test_restart_replay_is_idempotent_and_dedupe_is_scope_bound(self):
        ev = event()
        first = self.record(ev)
        reopened = attention.AttentionLedger(self.path)
        second = self.record(ev, ledger=reopened)
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(reopened.get_decision(scope=SCOPE, decision_id=first["decision_id"])["state"], "shadow")
        self.assertIsNone(reopened.get_decision(scope=OTHER, decision_id=first["decision_id"]))
        with self.assertRaises(attention.AttentionError):
            reopened.apply_decision(scope=OTHER, decision_id=first["decision_id"], operation="dismiss",
                                    expected_revision=1, request_id="cross-scope", now=NOW)

    def test_list_decisions_filters_sources_before_applying_limit(self):
        aula_decision = self.record(event(1))
        calendar_event = event(2, source="calendar", kind="calendar.event")
        calendar_status = {**STATUS, "calendar": {"state": "fresh", "last_success_at": NOW}}
        calendar_result = attention.evaluate(calendar_event, scope=SCOPE, now=NOW, source_status=calendar_status)
        self.ledger.record_decision(calendar_event, scope=SCOPE, decision=calendar_result, now=NOW)
        filtered = self.ledger.list_decisions(scope=SCOPE, limit=1, sources={"aula"})
        self.assertEqual([row["decision_id"] for row in filtered], [aula_decision["decision_id"]])
        self.assertEqual(self.ledger.list_decisions(scope=SCOPE, sources=set()), [])

    def test_decision_controls_require_scope_revision_and_idempotency(self):
        ev = event()
        # shadow is intentionally still controllable as metadata for replay validation
        row = self.record(ev)
        snooze_until = "2026-11-07T08:00:00+01:00"
        result = self.ledger.apply_decision(scope=SCOPE, decision_id=row["decision_id"], operation="snooze",
            expected_revision=1, request_id="snooze-1", now=NOW, until=snooze_until)
        replay = self.ledger.apply_decision(scope=SCOPE, decision_id=row["decision_id"], operation="snooze",
            expected_revision=1, request_id="snooze-1", now=NOW, until=snooze_until)
        self.assertEqual(result, replay)
        self.assertEqual(result["state"], "snoozed")
        next_revision = self.record({**event(), "revision": 2})
        self.assertEqual((next_revision["outcome"], next_revision["state"], next_revision["reason"]),
                         ("retain", "snoozed", "occurrence_snoozed"))
        after_snooze = self.record({**event(), "revision": 3}, at="2026-11-08T08:00:00+01:00")
        self.assertNotEqual(after_snooze["reason"], "occurrence_snoozed")
        with self.assertRaises(attention.AttentionError):
            self.ledger.apply_decision(scope=SCOPE, decision_id=row["decision_id"], operation="dismiss",
                expected_revision=1, request_id="snooze-1", now=NOW)
        with self.assertRaises(attention.AttentionError):
            self.ledger.apply_decision(scope=SCOPE, decision_id=row["decision_id"], operation="dismiss",
                expected_revision=1, request_id="stale", now=NOW)

    def test_feedback_replay_is_content_bound_and_delete_purges_derivatives(self):
        ev = event()
        row = self.record(ev)
        feedback = {"scope": SCOPE, "decision_id": row["decision_id"], "kind": "correction",
            "source_refs": ["fixture://1"], "request_id": "feedback-replay", "now": NOW, "detail": "PRIVATE_DETAIL"}
        self.ledger.record_feedback(**feedback)
        self.ledger.record_feedback(**feedback)
        with self.assertRaises(attention.AttentionError):
            self.ledger.record_feedback(**{**feedback, "detail": "different correction"})
        # Repeated explicit evidence may produce a review candidate linked to this source.
        for i in range(2, 4):
            related = self.record(event(i))
            self.ledger.record_feedback(scope=SCOPE, decision_id=related["decision_id"], kind="not_useful",
                source_refs=["fixture://1"], request_id=f"linked-{i}", now=NOW, detail="PRIVATE_DETAIL")
        candidate = self.ledger.propose_candidate(scope=SCOPE, candidate_key="briefing.redact", allowed_class="briefing-format",
            proposed_diff={"max_bullets": 3}, source_refs=["fixture://1"], now=NOW)
        self.assertEqual(len(self.ledger.list_candidates(scope=SCOPE)), 1)
        deleted = {**ev, "operation": "delete", "revision": 2, "payload": {}}
        self.ledger.record_decision(deleted, scope=SCOPE,
            decision=self.decision(deleted), now=NOW)
        self.assertIsNone(self.ledger.get_decision(scope=SCOPE, decision_id=row["decision_id"]))
        self.assertEqual(self.ledger.list_candidates(scope=SCOPE), [])
        with sqlite3.connect(self.path) as db:
            stored = " ".join(str(value) for result in db.execute("SELECT * FROM feedback") for value in result)
        self.assertNotIn("PRIVATE_DETAIL", stored)
        self.assertNotIn("fixture://1", stored)

    def test_source_delete_keeps_held_decision_but_removes_its_source_ref(self):
        active = attention.AttentionLedger(self.path.parent / "held.sqlite3", dry_run=False)
        cfg = policy(mode="active", rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        ev = event(kind="deadline.changed")
        row = self.record(ev, ledger=active, cfg=cfg)
        held = active.mark_delivery(scope=SCOPE, decision_id=row["decision_id"], state="held", expected_revision=1,
            request_id="hold", now=NOW)
        deleted = {**ev, "operation": "delete", "revision": 2, "payload": {}}
        tombstone = active.record_decision(deleted, scope=SCOPE, decision=self.decision(deleted, cfg=cfg), now=NOW)
        retained = active.get_decision(scope=SCOPE, decision_id=row["decision_id"])
        self.assertEqual((held["state"], retained["state"], retained["source_ref"]), ("held", "held", "[deleted]"))
        self.assertEqual(tombstone["source_ref"], "[deleted]")

    def test_stop_suppresses_later_revisions_of_same_occurrence(self):
        cfg = policy(rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        ev = event(kind="deadline.changed")
        row = self.record(ev, cfg=cfg)
        self.ledger.apply_decision(scope=SCOPE, decision_id=row["decision_id"], operation="stop",
            expected_revision=1, request_id="stop-occurrence", now=NOW)
        later = self.record({**ev, "revision": 2}, cfg=cfg)
        self.assertEqual((later["outcome"], later["state"], later["reason"]),
                         ("retain", "stopped", "occurrence_stopped"))

    def test_active_notification_budget_is_transactional_and_uncertain_counts(self):
        active = attention.AttentionLedger(self.path.parent / "active.sqlite3", dry_run=False)
        cfg = policy(mode="active", rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        events = [event(i, kind="deadline.changed") for i in range(1, 6)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            rows = list(pool.map(lambda ev: self.record(ev, ledger=active, cfg=cfg), events))
        self.assertEqual(sum(r["outcome"] == "notify" for r in rows), 2)
        self.assertEqual(sum(r["outcome"] == "digest" for r in rows), 3)
        notify = next(r for r in rows if r["outcome"] == "notify")
        uncertain = active.mark_delivery(scope=SCOPE, decision_id=notify["decision_id"], state="uncertain",
            expected_revision=1, request_id="delivery-uncertain", now=NOW)
        self.assertEqual(uncertain["state"], "uncertain")
        later = event(9, kind="deadline.changed")
        self.assertEqual(self.record(later, ledger=active, cfg=cfg)["outcome"], "digest")
        # User cancellation or dismissal cannot refund an already consumed decision budget.
        second_notify = next(r for r in rows if r["outcome"] == "notify" and r["decision_id"] != notify["decision_id"])
        active.apply_decision(scope=SCOPE, decision_id=second_notify["decision_id"], operation="dismiss",
            expected_revision=1, request_id="dismiss-budget", now=NOW)
        self.assertEqual(self.record(event(10, kind="deadline.changed"), ledger=active, cfg=cfg)["outcome"], "digest")
        self.assertEqual(active.today_summary(scope=SCOPE, now=NOW)["local_day"], "2026-11-06")

    def test_operator_notification_limits_zero_and_one_are_enforced(self):
        policy_zero = policy(mode="active", daily_discretionary_notification_limit=0,
            rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        zero_ledger = attention.AttentionLedger(self.path.parent / "zero.sqlite3", dry_run=False)
        result = self.record(event(1, kind="deadline.changed"), ledger=zero_ledger, cfg=policy_zero)
        self.assertEqual(result["outcome"], "digest")
        policy_one = policy(mode="active", daily_discretionary_notification_limit=1,
            rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}])
        one_ledger = attention.AttentionLedger(self.path.parent / "one.sqlite3", dry_run=False)
        rows = [self.record(event(i, kind="deadline.changed"), ledger=one_ledger, cfg=policy_one) for i in (1, 2)]
        self.assertEqual([row["outcome"] for row in rows], ["notify", "digest"])

    def test_dry_run_records_observed_policy_without_enabling_delivery(self):
        row = self.record(event(kind="deadline.changed"), cfg=policy(rules=[{"source": "aula", "event_type": "deadline.changed", "outcome": "notify", "class": "fixture"}]))
        self.assertEqual((row["outcome"], row["state"]), ("notify", "shadow"))

    def test_feedback_is_explicit_and_candidates_need_repeated_evidence(self):
        decision_ids = []
        for i in range(1, 4):
            row = self.record(event(i))
            decision_ids.append(row["decision_id"])
            self.ledger.record_feedback(scope=SCOPE, decision_id=row["decision_id"], kind="not_useful",
                source_refs=[f"fixture://{i}"], request_id=f"feedback-{i}", now=NOW)
        candidate = self.ledger.propose_candidate(scope=SCOPE, candidate_key="briefing.shorter", allowed_class="briefing-format",
            proposed_diff={"max_bullets": 3}, source_refs=["fixture://1", "fixture://2", "fixture://3"], now=NOW)
        self.assertEqual((candidate["state"], candidate["version"]), ("proposed", 1))
        with self.assertRaises(attention.AttentionError):
            self.ledger.transition_candidate(scope=SCOPE, candidate_id=candidate["candidate_id"], transition="approved",
                expected_revision=1, request_id="model-approve", now=NOW,
                authority=attention.TrustedAuthority("owner", "personal", True))
        tested = self.ledger.transition_candidate(scope=SCOPE, candidate_id=candidate["candidate_id"], transition="tested",
            expected_revision=1, request_id="test-1", now=NOW, replay_evidence={"passed": True, "replay_ref": "fixture://replay"})
        with self.assertRaises(attention.AttentionError):
            self.ledger.transition_candidate(scope=SCOPE, candidate_id=candidate["candidate_id"], transition="approved",
                expected_revision=2, request_id="wrong-authority", now=NOW,
                authority=attention.TrustedAuthority("partner", "personal", True))
        approved = self.ledger.transition_candidate(scope=SCOPE, candidate_id=candidate["candidate_id"], transition="approved",
            expected_revision=2, request_id="human-approved", now=NOW,
            authority=attention.TrustedAuthority("owner", "personal", True))
        self.assertEqual((tested["state"], approved["state"], approved["revision"]), ("tested", "approved", 3))
        self.assertEqual(self.ledger.list_candidates(scope=OTHER), [])

    def test_feedback_review_generates_one_deterministic_candidate_after_three_decisions(self):
        rows = []
        for i in (1, 2):
            row = self.record(event(i))
            rows.append(row)
            self.ledger.record_feedback(scope=SCOPE, decision_id=row["decision_id"], kind="correction",
                source_refs=[f"fixture://{i}"], request_id=f"explicit-correction-{i}", now=NOW)
            self.assertIsNone(self.ledger.review_feedback(scope=SCOPE, decision_id=row["decision_id"], now=NOW))
        # Transport replay of the second correction is content-bound and cannot add another evidence row.
        self.ledger.record_feedback(scope=SCOPE, decision_id=rows[1]["decision_id"], kind="correction",
            source_refs=["fixture://2"], request_id="explicit-correction-2", now=NOW)
        self.assertIsNone(self.ledger.review_feedback(scope=SCOPE, decision_id=rows[1]["decision_id"], now=NOW))
        third = self.record(event(3))
        self.ledger.record_feedback(scope=SCOPE, decision_id=third["decision_id"], kind="not_useful",
            source_refs=["fixture://3"], request_id="explicit-not-useful-3", now=NOW)
        proposal = self.ledger.review_feedback(scope=SCOPE, decision_id=third["decision_id"], now=NOW)
        retry = self.ledger.review_feedback(scope=SCOPE, decision_id=third["decision_id"], now=NOW)
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(proposal["allowed_class"], "attention-threshold")
        self.assertEqual(proposal["proposed_diff"], {"minimum_repeat_evidence": 4})
        self.assertEqual((proposal["candidate_id"], proposal["version"]), (retry["candidate_id"], retry["version"]))

    def test_ledger_files_are_private_and_inputs_are_bounded(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(attention.AttentionError):
            self.decision({**event(), "payload": {"oversize": "x" * 9000}})
        with self.assertRaises(attention.AttentionError):
            attention.evaluate(event(), scope={"principal_scope": "family", "data_domain": "household", "visibility": "private"},
                               now=NOW, source_status=STATUS)


if __name__ == "__main__":
    unittest.main()
