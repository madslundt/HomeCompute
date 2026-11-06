from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from assistant_context_briefings import (BriefingBuilder, LocalReasoner, OccurrenceLedger, load_config,
    call_briefing_tool, n8n_patch, occurrence_key_for, scheduled_occurrence, valid_scheduled_for, validate_candidate)
from assistant_context_store import ContextStore, Scope
from assistant_context_delivery import load_delivery_auth

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
OWNER = Scope("owner", "personal", "private")


class BriefingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ContextStore(self.root / "context.sqlite3")
        self.coverage = {s: {"state": "fresh", "last_success_at": NOW.isoformat()}
                         for s in ("home-assistant", "daythread", "aula", "calendar")}

    def tearDown(self):
        self.temp.cleanup()

    def event(self, source, object_id, payload, *, event_type="snapshot", when="2026-11-06T10:00:00+01:00", ref=None):
        return {"schema_version": 1, "source": source, "object_id": object_id, "revision": 1,
                "event_type": event_type, "operation": "upsert", "occurred_at": when,
                "source_ref": ref or f"{source}:{object_id}", "payload": payload}

    def test_four_source_summary_links_collisions_and_candidate_followups(self):
        records = [
            self.event("home-assistant", "heat", {"summary": "Heating checked before family arrival"}),
            self.event("daythread", "promise", {"summary": "Remember to check heating before family arrival"}, event_type="daythread.summary"),
            self.event("aula", "class", {"summary": "Class meeting before family arrival"}, when="2026-11-06T09:00:00+01:00"),
            self.event("calendar", "event-a", {"title": "Class meeting", "start": "2026-11-06T10:00:00+01:00", "end": "2026-11-06T11:00:00+01:00"}),
            self.event("calendar", "event-b", {"title": "Heating visit", "start": "2026-11-06T10:30:00+01:00", "end": "2026-11-06T11:30:00+01:00"}),
        ]
        for event in records:
            self.store.ingest(OWNER, event)
        result = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW,
            adaptation={"minimum_relevance": 0.35, "minimum_repeat_evidence": 3}).build()
        self.assertEqual({x["ref"]["source"] for x in result["source_items"]}, {"home-assistant", "daythread", "aula", "calendar"})
        self.assertEqual(len(result["calendar_collisions"]), 1)
        self.assertEqual(result["daythread_followups"][0]["uncertainty"], "unconfirmed_source_follow_up")
        self.assertTrue(all(item["source_refs"] for item in result["cross_source_correlations"]))
        self.assertTrue(all(item["source_refs"] for item in result["cross_source_summary"]["items"]))

    def test_partial_and_stale_coverage_remain_distinct(self):
        for src, oid in (("home-assistant", "h"), ("daythread", "d")):
            self.store.ingest(OWNER, self.event(src, oid, {"summary": "ordinary context"}))
        coverage = dict(self.coverage)
        coverage["home-assistant"] = {"state": "partial", "last_success_at": NOW.isoformat()}
        coverage["daythread"] = {"state": "stale", "last_success_at": "2026-10-01T00:00:00Z"}
        result = BriefingBuilder(self.store, scope=OWNER, coverage=coverage, clock=lambda: NOW).build()
        self.assertEqual(result["coverage"]["home-assistant"], "partial")
        self.assertEqual(result["coverage"]["daythread"], "stale")
        self.assertEqual(result["coverage"]["aula"], "fresh")
        self.assertEqual(result["coverage"]["calendar"], "fresh")

    def test_tomorrow_summary_excludes_today_calendar_and_midnight_end_boundary(self):
        for object_id, start, end in (
                ('today', '2026-11-06T10:00:00+01:00', '2026-11-06T11:00:00+01:00'),
                ('ends-at-midnight', '2026-11-06T23:00:00+01:00', '2026-11-07T00:00:00+01:00'),
                ('tomorrow', '2026-11-07T10:00:00+01:00', '2026-11-07T11:00:00+01:00')):
            self.store.ingest(OWNER, self.event('calendar', object_id, {'title': object_id, 'start': start, 'end': end}))
        brief = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW).build('tomorrow')
        self.assertEqual([row['ref']['object_id'] for row in brief['source_items']], ['tomorrow'])
        self.assertEqual(brief['cross_source_summary']['items'][0]['source_refs'][0]['object_id'], 'tomorrow')

    def test_stale_context_is_labeled_but_not_actionable_and_nested_calendar_times_parse(self):
        self.store.ingest(OWNER, self.event("daythread", "stale-promise", {"summary": "Remember to call school about trip"}))
        self.store.ingest(OWNER, self.event("calendar", "nested", {"summary": "Nested event", "start": {"dateTime": "2026-11-06T10:00:00+01:00"}, "end": {"dateTime": "2026-11-06T11:00:00+01:00"}}))
        coverage = dict(self.coverage)
        coverage["daythread"] = {"state": "stale", "last_success_at": "2026-10-01T00:00:00Z"}
        result = BriefingBuilder(self.store, scope=OWNER, coverage=coverage, clock=lambda: NOW,
            adaptation={"minimum_relevance": 0.35, "minimum_repeat_evidence": 2}).build()
        self.assertEqual(result["daythread_followups"], [])
        self.assertEqual(result["cross_source_correlations"], [])
        self.assertEqual(next(x for x in result["source_items"] if x["ref"]["source"] == "daythread")["freshness"], "stale")
        self.assertEqual(next(x for x in result["source_items"] if x["ref"]["object_id"] == "nested")["freshness"], "fresh")
        self.assertEqual(result["action_authority"], "none")

    def test_bad_semantic_candidate_or_inference_outage_uses_deterministic_grounded_fallback(self):
        self.store.ingest(OWNER, self.event("aula", "one", {"summary": "Term starts soon"}))
        valid_ref = {"source": "aula", "object_id": "one", "revision": 1}
        bad = {"summary": "unsupported", "items": [{"text": "invented claim", "source_refs": [{**valid_ref, "revision": 9}]}]}
        with self.assertRaises(ValueError):
            validate_candidate(bad, {"source_items": [{"ref": valid_ref}]})
        for failure in (lambda _: (_ for _ in ()).throw(TimeoutError()), lambda _: bad):
            result = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW, reasoner=failure).build()
            self.assertEqual(result["reasoning"], "deterministic-fallback")
            self.assertEqual(result["cross_source_summary"]["items"][0]["source_refs"], [valid_ref])

    def test_bounded_local_reasoner_uses_non_thinking_json_completion(self):
        seen = []
        expected = {"summary": "Synthetic qualification", "items": []}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _):
                return json.dumps({"choices": [{"message": {"content": json.dumps(expected)}}]}).encode()

        class Opener:
            def open(self, request, timeout):
                payload = json.loads(request.data)
                if payload.get("chat_template_kwargs", {}).get("enable_thinking") is not False:
                    raise TimeoutError("reasoning tokens exhaust the bounded JSON completion")
                seen.append((request.full_url, payload, timeout))
                return Response()

        with patch("assistant_context_briefings.build_opener", return_value=Opener()):
            result = LocalReasoner("sk-" + "a" * 22)({"source_items": []})
        self.assertEqual(result, expected)
        self.assertEqual(seen[0][0], "https://ai.home.arpa/v1/chat/completions")
        self.assertEqual(seen[0][1]["response_format"], {"type": "json_object"})
        self.assertLessEqual(seen[0][1]["max_tokens"], 500)
        self.assertLessEqual(seen[0][2], 8)

    def test_occurrence_admission_is_unique_concurrent_and_restart_safe(self):
        ledger = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        scheduled = "2026-11-06T07:30:00+01:00"
        key = occurrence_key_for("morning-overview", scheduled)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: ledger.admit(OWNER, "morning-overview", key, scheduled), range(20)))
        self.assertEqual({row["status"] for row in rows}, {"accepted"})
        self.assertEqual({row["revision"] for row in rows}, {1})
        self.assertEqual(ledger.admit(OWNER, "morning-overview", key, "2026-11-06T05:01:00+01:00")["revision"], 1)
        with self.assertRaises(ValueError):
            ledger.admit(OWNER, "morning-overview", "fresh-random-key", scheduled)
        recovered = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        transition_id = str(uuid.uuid4())
        result = recovered.transition(OWNER, "morning-overview", key, "uncertain", expected_revision=1, request_id=transition_id)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(recovered.transition(OWNER, "morning-overview", key, "uncertain", expected_revision=1, request_id=transition_id), result)
        self.assertEqual(recovered.admit(OWNER, "morning-overview", key, scheduled)["status"], "uncertain")
        self.assertEqual(recovered.status(OWNER, "morning-overview")["occurrences"][0]["status"], "uncertain")

    def test_pause_cancel_snooze_controls_are_revision_bound_and_scope_local(self):
        ledger = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        request_id = str(uuid.uuid4())
        state = ledger.control(OWNER, "tomorrow-preparation", "pause", expected_revision=0, request_id=request_id)
        self.assertEqual(state["revision"], 1)
        instant = "2026-11-06T20:30:00+01:00"
        key = occurrence_key_for("tomorrow-preparation", instant)
        self.assertEqual(ledger.admit(OWNER, "tomorrow-preparation", key, instant)["status"], "suppressed")
        with self.assertRaises(ValueError):
            ledger.control(OWNER, "tomorrow-preparation", "resume", expected_revision=0, request_id=str(uuid.uuid4()))
        state = ledger.control(OWNER, "tomorrow-preparation", "resume", expected_revision=1, request_id=str(uuid.uuid4()))
        self.assertEqual(state["operation"], "resume")
        family = Scope("family", "household", "shared")
        self.assertIsNone(ledger.status(family, "tomorrow-preparation")["control"])

    def test_tool_dispatch_excludes_run_and_supports_scoped_revision_bound_controls(self):
        ledger = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        builder = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW)
        self.assertEqual(call_briefing_tool(builder, ledger, OWNER, "homecompute_context_routine",
            {"routine_id": "morning-overview", "operation": "status"})["occurrences"], [])
        with self.assertRaises(ValueError):
            call_briefing_tool(builder, ledger, OWNER, "homecompute_context_routine",
                {"routine_id": "morning-overview", "operation": "run"})
        request_id = str(uuid.uuid4())
        args = {"routine_id": "morning-overview", "operation": "pause", "expected_revision": 0, "request_id": request_id}
        first = call_briefing_tool(builder, ledger, OWNER, "homecompute_context_routine", args)
        self.assertEqual(call_briefing_tool(builder, ledger, OWNER, "homecompute_context_routine", args), first)

    def test_routine_sender_defaults_to_shadow_and_proves_delivery_once(self):
        event = self.event("aula", "delivery-source", {"summary": "School closes early"})
        self.store.ingest(OWNER, event)
        builder = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW)
        brief = builder.build()
        ledger = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        scheduled = "2026-11-06T05:00:00+01:00"
        key = occurrence_key_for("morning-overview", scheduled)
        ledger.admit(OWNER, "morning-overview", key, scheduled)
        auth_file = self.root / "delivery-auth.json"
        auth_file.write_text(json.dumps({
            "schema_version": 1, "route_name": "homecompute-context-owner", "route_registered": True,
            "target": "mattermost", "route_config_sha256": "a" * 64, "standing_grant_id": "b" * 32,
            "standing_grant_status": "active", "owner_user_id": "owner", "bot_user_id": "bot",
            "channel_type": "D", "owner_destination_id": "fixed-dm", "allowed_sources": ["aula"],
            "allowed_policy_versions": ["policy-v1"], "grant_issued_at": "2026-11-06T00:00:00Z",
            "grant_expires_at": "2026-12-01T00:00:00Z", "qualification_evidence_ref": "synthetic-test",
            "route_secret": "s" * 48,
        }))
        auth_file.chmod(0o600)
        auth = load_delivery_auth(auth_file, now=NOW)
        shadow = ledger.send_once(OWNER, "morning-overview", key, scheduled, brief, auth, "policy-v1",
            store=self.store, coverage=self.coverage, enabled=False, now=NOW)
        self.assertEqual(shadow["status"], "accepted")
        self.assertEqual(shadow["delivery_status"], "shadow")
        self.assertFalse(shadow["completed"])
        calls = []
        def delivered(body, headers, timeout):
            calls.append((body, headers, timeout))
            receipt = {"status": "delivered", "route": "homecompute-context-owner", "target": "mattermost", "delivery_id": headers["X-Request-ID"]}
            return 200, json.dumps(receipt).encode()
        ledger.control(OWNER, 'morning-overview', 'pause', expected_revision=0, request_id=str(uuid.uuid4()))
        held = ledger.send_once(OWNER, 'morning-overview', key, scheduled, brief, auth, 'policy-v1',
            store=self.store, coverage=self.coverage, enabled=True, now=NOW, transport=delivered)
        self.assertEqual(held['status'], 'suppressed')
        self.assertEqual(calls, [])
        ledger.control(OWNER, 'morning-overview', 'resume', expected_revision=1, request_id=str(uuid.uuid4()))
        sent = ledger.send_once(OWNER, "morning-overview", key, scheduled, brief, auth, "policy-v1",
            store=self.store, coverage=self.coverage, enabled=True, now=NOW, transport=delivered)
        self.assertEqual(sent["status"], "completed")
        self.assertTrue(sent["completed"])
        retry = ledger.send_once(OWNER, "morning-overview", key, scheduled, brief, auth, "policy-v1",
            store=self.store, coverage=self.coverage, enabled=True, now=NOW, transport=delivered)
        self.assertEqual(retry["status"], "completed")
        self.assertEqual(len(calls), 1)

    def test_routine_sender_lost_response_is_uncertain_and_never_retried(self):
        self.store.ingest(OWNER, self.event("calendar", "uncertain-source", {"summary": "Test event",
            "start": "2026-11-06T10:00:00+01:00", "end": "2026-11-06T11:00:00+01:00"}))
        brief = BriefingBuilder(self.store, scope=OWNER, coverage=self.coverage, clock=lambda: NOW).build()
        ledger = OccurrenceLedger(self.root / "routines.sqlite3", clock=lambda: NOW)
        scheduled = "2026-11-06T05:00:00+01:00"
        key = occurrence_key_for("morning-overview", scheduled)
        ledger.admit(OWNER, "morning-overview", key, scheduled)
        auth_file = self.root / "delivery-auth.json"
        value = {"schema_version": 1, "route_name": "homecompute-context-owner", "route_registered": True,
            "target": "mattermost", "route_config_sha256": "a" * 64, "standing_grant_id": "b" * 32,
            "standing_grant_status": "active", "owner_user_id": "owner", "bot_user_id": "bot", "channel_type": "D",
            "owner_destination_id": "fixed-dm", "allowed_sources": ["calendar"], "allowed_policy_versions": ["policy-v1"],
            "grant_issued_at": "2026-11-06T00:00:00Z", "grant_expires_at": "2026-12-01T00:00:00Z",
            "qualification_evidence_ref": "synthetic-test", "route_secret": "s" * 48}
        auth_file.write_text(json.dumps(value)); auth_file.chmod(0o600)
        auth = load_delivery_auth(auth_file, now=NOW)
        calls = []
        def lost(*args, **kwargs): calls.append(1); raise TimeoutError()
        result = ledger.send_once(OWNER, "morning-overview", key, scheduled, brief, auth, "policy-v1",
            store=self.store, coverage=self.coverage, enabled=True, now=NOW, transport=lost)
        self.assertEqual(result["status"], "uncertain")
        again = ledger.send_once(OWNER, "morning-overview", key, scheduled, brief, auth, "policy-v1",
            store=self.store, coverage=self.coverage, enabled=True, now=NOW, transport=lost)
        self.assertEqual(again["status"], "uncertain")
        self.assertEqual(len(calls), 1)

    def test_controls_apply_to_admitted_occurrences_and_snooze_expires_on_next_owned_trigger(self):
        clock = [NOW]
        ledger = OccurrenceLedger(self.root / 'routines.sqlite3', clock=lambda: clock[0])
        scheduled = NOW.isoformat()
        key = occurrence_key_for('morning-overview', scheduled)
        first = ledger.admit(OWNER, 'morning-overview', key, scheduled)
        ledger.control(OWNER, 'morning-overview', 'pause', expected_revision=0, request_id=str(uuid.uuid4()))
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'suppressed')
        ledger.control(OWNER, 'morning-overview', 'resume', expected_revision=1, request_id=str(uuid.uuid4()))
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'accepted')
        ledger.transition(OWNER, 'morning-overview', key, 'snooze', expected_revision=first['revision'],
            request_id=str(uuid.uuid4()), until='2026-11-06T09:10:00Z')
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'suppressed')
        clock[0] = datetime(2026, 11, 6, 9, 11, tzinfo=timezone.utc)
        resumed = ledger.admit(OWNER, 'morning-overview', key, scheduled)
        self.assertEqual(resumed['status'], 'accepted')
        ledger.transition(OWNER, 'morning-overview', key, 'cancel', expected_revision=resumed['revision'],
            request_id=str(uuid.uuid4()))
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'cancelled')

    def test_control_suppressed_first_attempt_can_resume_without_a_new_occurrence(self):
        ledger = OccurrenceLedger(self.root / 'routines.sqlite3', clock=lambda: NOW)
        scheduled = NOW.isoformat()
        key = occurrence_key_for('morning-overview', scheduled)
        ledger.control(OWNER, 'morning-overview', 'pause', expected_revision=0, request_id=str(uuid.uuid4()))
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'suppressed')
        ledger.control(OWNER, 'morning-overview', 'resume', expected_revision=1, request_id=str(uuid.uuid4()))
        self.assertEqual(ledger.admit(OWNER, 'morning-overview', key, scheduled)['status'], 'accepted')
        self.assertEqual(len(ledger.status(OWNER, 'morning-overview')['occurrences']), 1)

    def test_copenhagen_gap_and_fold_are_deterministic(self):
        self.assertIsNone(scheduled_occurrence(date(2026, 3, 29), "02:30"))
        folded = scheduled_occurrence(date(2026, 10, 25), "02:30")
        self.assertEqual(folded.fold, 0)
        self.assertEqual(folded.utcoffset().total_seconds(), 7200)
        self.assertEqual(occurrence_key_for("morning-overview", "2026-10-25T02:30:00+02:00"),
                         occurrence_key_for("morning-overview", "2026-10-25T02:30:00+01:00"))
        self.assertTrue(valid_scheduled_for("tomorrow-preparation", "2026-11-06T20:30:00+01:00"))
        self.assertFalse(valid_scheduled_for("tomorrow-preparation", "2026-11-06T20:31:00+01:00"))

    def test_existing_schedule_owners_are_retained_and_patch_preserves_graph(self):
        config = load_config(ROOT / "config/assistant-briefings.json")
        self.assertEqual([row["owner"] for row in config["routines"]], ["WxRovre260928abc", "WKu7Zv4dE4ztnaRY"])
        graph = {"id": "WxRovre260928abc", "nodes": [
            {"id": "trigger", "type": "n8n-nodes-base.scheduleTrigger", "parameters": {"schedule": "old"}},
            {"id": "send", "type": "n8n-nodes-base.httpRequest", "parameters": {"path": "/existing"}},
        ], "connections": {"trigger": {"main": [[{"node": "send"}]]}}, "settings": {"timezone": "Europe/Copenhagen"}}
        digest = hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        patch = n8n_patch(graph, workflow_id=graph["id"], expected_sha256=digest, node_id="send", replacement_parameters={"body": "brief"})
        self.assertEqual(patch["workflow"]["nodes"][0], graph["nodes"][0])
        self.assertEqual(patch["workflow"]["connections"], graph["connections"])
        self.assertEqual(patch["workflow"]["nodes"][1]["parameters"], {"path": "/existing", "body": "brief"})
        with self.assertRaises(ValueError):
            n8n_patch(graph, workflow_id=graph["id"], expected_sha256="0" * 64, node_id="send", replacement_parameters={})
        with self.assertRaises(ValueError):
            n8n_patch(graph, workflow_id=graph["id"], expected_sha256=digest, node_id="trigger", replacement_parameters={})


if __name__ == "__main__":
    unittest.main()
