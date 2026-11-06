#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from datetime import date
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import assistant_context_sources as sources
sync_spec = importlib.util.spec_from_file_location("assistant_context_sync", ROOT / "scripts/assistant-context-sync.py")
sync_module = importlib.util.module_from_spec(sync_spec)
sync_spec.loader.exec_module(sync_module)


class EventContractTests(unittest.TestCase):
    def test_envelope_is_exact_and_scope_is_not_source_data(self):
        event = sources.normalize_event("aula", "calendar:42", 1, "calendar.snapshot", "upsert",
            "2026-11-06T10:15:00+01:00", "aula:calendar:42",
            {"title": "Pickup", "principal": "ignored", "payload": {"visibility": "secret", "ok": True}})
        self.assertEqual(set(event), {"schema_version", "source", "object_id", "revision", "event_type",
                                      "operation", "occurred_at", "source_ref", "payload"})
        self.assertEqual(event["occurred_at"], "2026-11-06T09:15:00Z")
        self.assertNotIn("principal", event["payload"])
        self.assertNotIn("visibility", event["payload"]["payload"])
        with self.assertRaises(ValueError):
            sources.normalize_event("aula", "x", 0, "aula.snapshot", "upsert",
                                    "2026-11-06T10:00:00", "aula:x", {})

    def test_multibyte_daythread_payload_stays_within_core_limit(self):
        event = sources.normalize_event("daythread", "diary:2026-11-06", 1, "daythread.summary", "upsert",
            "2026-11-06T10:00:00Z", "daythread:diary/2026-11-06", {"summary": "æøå résumé " * 5000})
        self.assertLessEqual(len(json.dumps(event, ensure_ascii=False).encode("utf-8")), 8192)
        self.assertLessEqual(len(event["payload"]["summary"].encode("utf-8")), sources.MAX_TEXT)
        self.assertNotIn("�", event["payload"]["summary"])

    def test_daythread_projection_excludes_audio_and_keeps_stable_source_identity(self):
        snapshot = sources.daythread_snapshot([{
            "object_id": "diary:2026-11-06", "event_type": "daythread.summary",
            "occurred_at": "2026-11-06T08:00:00Z", "source_ref": "daythread:diary/2026-11-06",
            "payload": {"summary": "Synthetic summary: æøå", "version_ref": "diary:v1"},
        }], observed_at="2026-11-06T09:00:00Z", coverage_key="diary:2026-11-06", complete=False)
        self.assertEqual(snapshot.source, "daythread")
        obj = snapshot.objects[0]
        self.assertEqual(obj.object_id, "diary:2026-11-06")
        self.assertEqual(obj.event_type, "daythread.summary")
        self.assertIn("æøå", obj.payload["summary"])
        self.assertFalse(snapshot.complete)
        with self.assertRaises(ValueError):
            sources.daythread_snapshot([], observed_at="2026-11-06T09:00:00Z",
                coverage_key="recent:30d", complete=True)

    def test_rolling_calendar_window_tracks_copenhagen_days_across_dst(self):
        selection = {"rolling": {"past_days": 1, "future_days": 7}}
        before = sync_module._calendar_bounds(selection,
            now=sync_module.datetime.fromisoformat("2026-03-28T12:00:00+01:00"))
        after = sync_module._calendar_bounds(selection,
            now=sync_module.datetime.fromisoformat("2026-03-29T12:00:00+02:00"))
        self.assertEqual(before, ("2026-03-27T00:00:00+01:00", "2026-04-04T00:00:00+02:00"))
        self.assertEqual(after, ("2026-03-28T00:00:00+01:00", "2026-04-05T00:00:00+02:00"))
        self.assertNotEqual(before, after)
        maximum = sync_module._calendar_bounds({"rolling": {"past_days": 15, "future_days": 15}},
            now=sync_module.datetime.fromisoformat("2026-11-01T12:00:00+01:00"))
        self.assertEqual(maximum, ("2026-10-17T00:00:00+02:00", "2026-11-16T00:00:00+01:00"))
        sync_module.openclaw_calendar.times(*maximum, False)
        with self.assertRaisesRegex(ValueError, "at most 30 local days"):
            sync_module._calendar_bounds({"rolling": {"past_days": 15, "future_days": 16}},
                now=sync_module.datetime.fromisoformat("2026-11-01T12:00:00+01:00"))

    def test_calendar_rolling_config_and_coverage_use_exact_resolved_window(self):
        config = {"schema_version": 1, "aula": {"enabled": False},
                  "calendar": {"enabled": True, "aliases": ["family"],
                               "rolling": {"past_days": 1, "future_days": 7}}}
        with patch.object(sync_module, "private_bytes", return_value=json.dumps(config).encode()):
            self.assertEqual(sync_module.load_config(Path("unused")), config | {"daythread": {"enabled": False, "host": ""}})

        class Facade:
            def __init__(self, *_args): self.requests = []
            def request(self, _operation, params):
                self.requests.append(params)
                return {"events": [], "truncated": False}

        facade = Facade()
        instant = sync_module.datetime.fromisoformat("2026-03-29T12:00:00+02:00")
        with patch.object(sync_module.openclaw_calendar, "Calendar", return_value=facade):
            snapshots = sync_module.read_calendar(config, Path("/tmp/source-state"), now=instant)
        expected = ("2026-03-28T00:00:00+01:00", "2026-04-05T00:00:00+02:00")
        self.assertEqual(facade.requests, [{"calendar": "family", "start": expected[0], "end": expected[1]}])
        self.assertEqual(snapshots[0].coverage_key, f"family:{expected[0]}:{expected[1]}")

        invalid = dict(config, calendar={"enabled": True, "aliases": ["family"], "start": expected[0],
                                         "end": expected[1], "rolling": {"past_days": 1, "future_days": 7}})
        with patch.object(sync_module, "private_bytes", return_value=json.dumps(invalid).encode()):
            with self.assertRaisesRegex(ValueError, "exactly one calendar window mode"):
                sync_module.load_config(Path("unused"))
        over_limit = dict(config, calendar={"enabled": True, "aliases": ["family"],
            "rolling": {"past_days": 15, "future_days": 16}})
        with patch.object(sync_module, "private_bytes", return_value=json.dumps(over_limit).encode()):
            with self.assertRaisesRegex(ValueError, "Rolling calendar window refused"):
                sync_module.load_config(Path("unused"))


class ReconciliationTests(unittest.TestCase):
    def snapshot(self, rows, complete):
        return sources.aula_snapshot(rows, observed_at="2026-11-06T09:00:00Z", coverage_key="week:2026-W45",
                                     complete=complete, family="calendar")

    def test_changes_use_monotonic_revisions_and_unchanged_replays_do_not_ingest(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = sources.RevisionLedger(str(Path(directory) / "revision.db"))
            sent = []
            row = {"id": "event-1", "title": "Class", "startDateTime": "2026-11-06T10:00:00+01:00"}
            first = self.snapshot([row], True)
            self.assertEqual(ledger.apply(first, sent.append), {"upserted": 1, "unchanged": 0, "deleted": 0})
            self.assertEqual(ledger.apply(first, sent.append), {"upserted": 0, "unchanged": 1, "deleted": 0})
            changed = self.snapshot([dict(row, title="Updated")], True)
            ledger.apply(changed, sent.append)
            self.assertEqual([event["revision"] for event in sent], [1, 2])
            self.assertEqual(len({event["revision"] for event in sent}), 2)
            ledger.close()

    def test_uncertain_ingest_receipt_replays_identical_event_before_advancing_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = sources.RevisionLedger(str(Path(directory) / "revision.db"))
            sent = []
            fail_once = [True]

            def uncertain(event):
                sent.append(dict(event))
                if fail_once[0]:
                    fail_once[0] = False
                    raise OSError("synthetic lost receipt")

            first = self.snapshot([{"id": "event-1", "title": "Original"}], True)
            with self.assertRaises(OSError):
                ledger.apply(first, uncertain)
            changed = self.snapshot([{"id": "event-1", "title": "Corrected"}], True)
            ledger.apply(changed, uncertain)
            self.assertEqual(sent[0], sent[1])
            self.assertEqual(sent[0]["revision"], 1)
            self.assertEqual(sent[-1]["revision"], 2)
            self.assertEqual(sent[-1]["payload"]["title"], "Corrected")
            ledger.close()

    def test_partial_scan_never_deletes_and_complete_scan_is_coverage_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = sources.RevisionLedger(str(Path(directory) / "revision.db"))
            sent = []
            row = {"id": "event-1", "title": "Class"}
            ledger.apply(self.snapshot([row], True), sent.append)
            self.assertEqual(ledger.apply(self.snapshot([], False), sent.append)["deleted"], 0)
            self.assertEqual(ledger.apply(sources.aula_snapshot([], observed_at="2026-11-06T09:00:00Z",
                coverage_key="week:2026-W46", complete=True), sent.append)["deleted"], 0)
            deleted = ledger.apply(self.snapshot([], True), sent.append)
            self.assertEqual(deleted["deleted"], 1)
            self.assertEqual(sent[-1]["operation"], "delete")
            self.assertEqual(sent[-1]["revision"], 2)
            ledger.close()

    def test_daythread_incomplete_scan_never_tombstones(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = sources.RevisionLedger(str(Path(directory) / "revision.db"))
            sent = []
            row = {"object_id": "diary:2026-11-06", "event_type": "daythread.summary",
                   "occurred_at": "2026-11-06T09:00:00Z", "source_ref": "daythread:diary/2026-11-06",
                   "payload": {"summary": "One"}}
            initial = sources.daythread_snapshot([row], observed_at="2026-11-06T09:00:00Z",
                coverage_key="recent:30d", complete=False)
            ledger.apply(initial, sent.append)
            empty = sources.daythread_snapshot([], observed_at="2026-11-06T09:05:00Z",
                coverage_key="recent:30d", complete=False)
            self.assertEqual(ledger.apply(empty, sent.append)["deleted"], 0)
            self.assertEqual(len(sent), 1)
            ledger.close()

    def test_daythread_known_id_resolver_tombstones_monotonically_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "revision.db")
            ledger = sources.RevisionLedger(path)
            sent = []
            row = {"object_id": "diary:2026-11-06", "event_type": "daythread.summary",
                   "occurred_at": "2026-11-06T09:00:00Z", "source_ref": "daythread:diary/2026-11-06",
                   "payload": {"summary": "Original", "version_ref": "v1"}}
            first = sources.daythread_snapshot([row], observed_at="2026-11-06T09:00:00Z",
                coverage_key="profile:owner", complete=False)
            ledger.apply(first, sent.append)
            self.assertEqual(ledger.known_ids("daythread"), ("diary:2026-11-06",))
            ledger.close()
            ledger = sources.RevisionLedger(path)  # Restart retains IDs and revision state.
            self.assertEqual(ledger.apply(sources.daythread_snapshot([], observed_at="2026-11-06T09:05:00Z",
                coverage_key="profile:owner", complete=False), sent.append)["deleted"], 0)
            deleted = sources.daythread_snapshot([], observed_at="2026-11-06T09:10:00Z", coverage_key="profile:owner",
                complete=False, resolved=((row["object_id"], "deleted", None),))
            self.assertEqual(ledger.apply(deleted, sent.append)["deleted"], 1)
            self.assertEqual(sent[-1]["revision"], 2)
            self.assertEqual(sent[-1]["operation"], "delete")
            # A missed page or stale replay cannot resurrect the deleted version.
            self.assertEqual(ledger.apply(first, sent.append)["upserted"], 0)
            self.assertEqual(len(sent), 2)
            newer = {**row, "payload": {"summary": "Recreated newer", "version_ref": "v2"}}
            self.assertEqual(ledger.apply(sources.daythread_snapshot([newer], observed_at="2026-11-06T09:20:00Z",
                coverage_key="profile:owner", complete=False), sent.append)["upserted"], 1)
            self.assertEqual(sent[-1]["revision"], 3)
            self.assertEqual(sent[-1]["payload"]["summary"], "Recreated newer")
            ledger.close()

    def test_known_daythread_resolution_batch_rotates_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "revision.db")
            ledger = sources.RevisionLedger(path)
            objects = [{"object_id": f"diary:2026-11-0{day}", "event_type": "daythread.summary",
                "occurred_at": "2026-11-06T09:00:00Z", "source_ref": f"daythread:diary/2026-11-0{day}",
                "payload": {"summary": f"synthetic {day}"}} for day in (1, 2, 3)]
            snapshot = sources.daythread_snapshot(objects, observed_at="2026-11-06T09:00:00Z",
                coverage_key="profile:owner", complete=False)
            ledger.apply(snapshot, lambda _event: None)
            first = ledger.known_ids("daythread", limit=2)
            self.assertEqual(len(first), 2)
            ledger.close()
            ledger = sources.RevisionLedger(path)
            second = ledger.known_ids("daythread", limit=2)
            self.assertEqual(len(second), 2)
            self.assertNotEqual(set(first), set(second))
            ledger.close()

    def test_daythread_reader_uses_authenticated_export_pages_and_never_tombstones(self):
        class Transport:
            def __init__(self):
                self.calls = []

            def get(self, path, params):
                self.calls.append((path, dict(params)))
                common = {"schema_version": 1, "source": "daythread", "snapshot_at": "2026-11-06T09:00:00Z",
                          "window": {"start": "2026-10-07T09:00:00Z", "end": "2026-11-06T09:00:00Z", "days": 30},
                          "coverage": {"window_complete": False, "deletion_complete": False,
                                       "snapshot_consistent": True, "record_limit_reached": False,
                                       "record_count": 1, "returned_records": 1}}
                row = {"object_id": "diary:2026-11-06", "revision": 1, "event_type": "daythread.summary",
                       "source_timestamp": "2026-11-06T08:00:00Z", "modified_at": "2026-11-06T08:00:00Z",
                       "version_ref": "diary:version-1", "source_ref": "daythread:diary/2026-11-06",
                       "payload": {"day": "2026-11-06", "summary": "Synthetic résumé æøå",
                                   "source_refs": [{"id": "source-1", "version": "v1"}],
                                   "pending_chunks": 0}}
                if params.get("cursor"):
                    return dict(common, items=[], next_cursor=None,
                                coverage=dict(common["coverage"], returned_records=0))
                return dict(common, items=[row], next_cursor="next-page")

        transport = Transport()
        client = sources.DaythreadClient(transport)
        snapshot = client.read()
        self.assertEqual({path for path, _ in transport.calls}, {"/v1/integrations/assistant-context"})
        self.assertEqual(len(transport.calls), 2)
        self.assertFalse(snapshot.complete)
        self.assertEqual({row.event_type for row in snapshot.objects}, {"daythread.summary"})
        self.assertIn("æøå", snapshot.objects[0].payload["summary"])

        class FixedPage:
            def __init__(self, page): self.page = page
            def get(self, _path, _params): return self.page

        first_page = transport.get("/v1/integrations/assistant-context", {"limit": 20})
        self.assertEqual(len(first_page["items"]), 1)
        no_deletion_claim = dict(first_page, next_cursor=None,
            coverage=dict(first_page["coverage"], deletion_complete=True))
        with self.assertRaises(ValueError):
            sources.DaythreadClient(FixedPage(no_deletion_claim)).read()
        extra_payload = dict(first_page["items"][0], payload={**first_page["items"][0]["payload"],
                                                               "transcript": "must not pass"})
        unsafe_payload = dict(first_page, next_cursor=None, items=[extra_payload])
        with self.assertRaises(ValueError):
            sources.DaythreadClient(FixedPage(unsafe_payload)).read()

    def test_daythread_reader_resolves_known_statuses_and_refreshes_only_valid_versions(self):
        class Transport:
            def get(self, _path, params):
                return {"schema_version": 1, "source": "daythread", "snapshot_at": "2026-11-06T09:00:00Z",
                    "window": {"start": "2026-10-07T09:00:00Z", "end": "2026-11-06T09:00:00Z", "days": 30},
                    "coverage": {"window_complete": True, "deletion_complete": False,
                        "snapshot_consistent": True, "record_limit_reached": False,
                        "record_count": 0, "returned_records": 0}, "items": [], "next_cursor": None}
            def post(self, path, body):
                self.path, self.body = path, body
                results = []
                for object_id in body["object_ids"]:
                    if object_id == "diary:2026-11-06":
                        item = {"object_id": object_id, "revision": 1, "event_type": "daythread.summary",
                            "source_timestamp": "2026-11-06T08:00:00Z", "modified_at": "2026-11-06T08:00:00Z",
                            "version_ref": "diary:version-2", "source_ref": "daythread:diary/2026-11-06",
                            "payload": {"day": "2026-11-06", "summary": "Refreshed", "source_refs": [{"id": "recording-1", "version": "v2"}], "pending_chunks": 0}}
                        results.append({"object_id": object_id, "status": "valid", "item": item})
                    else:
                        results.append({"object_id": object_id, "status": "invalidated"})
                return {"schema_version": 1, "source": "daythread", "results": results}

        transport = Transport()
        snapshot = sources.DaythreadClient(transport).read(["diary:2026-11-06", "diary:2026-11-05"])
        self.assertEqual(transport.path, "/v1/integrations/assistant-context/resolve")
        self.assertEqual(transport.body, {"object_ids": ["diary:2026-11-06", "diary:2026-11-05"]})
        self.assertEqual({obj.object_id for obj in snapshot.objects}, {"diary:2026-11-06"})
        self.assertEqual(snapshot.objects[0].payload["version_ref"], "diary:version-2")
        self.assertEqual(snapshot.resolved, (("diary:2026-11-05", "invalidated", None),))

    def test_moving_aula_relative_window_uses_distinct_reconciliation_coverage(self):
        first = sources.aula_calendar_coverage("this_week", [12], date(2026, 11, 5))
        following = sources.aula_calendar_coverage("this_week", [12], date(2026, 11, 6))
        self.assertNotEqual(first, following)

    def test_ingest_uses_fixed_api_routes_and_reports_coverage_separately(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b'{"accepted":true}'

        class Opener:
            def __init__(self): self.requests = []
            def open(self, request, timeout):
                self.requests.append((request, timeout))
                return Response()

        client = sync_module.Ingest("synthetic-collector-token-value-long-enough")
        opener = Opener()
        client.opener = opener
        event = sources.normalize_event("calendar", "mads:event-1", 1, "calendar.event", "upsert",
            "2026-11-06T10:00:00Z", "google-calendar:mads:event-1", {"summary": "Synthetic"})
        client(event)
        client.coverage("calendar", "2026-11-06T10:05:00Z", True)
        client.failure("daythread", "2026-11-06T10:06:00Z")
        self.assertEqual([row[0].full_url for row in opener.requests], [
            "http://127.0.0.1:18816/v1/events", "http://127.0.0.1:18816/v1/coverage",
            "http://127.0.0.1:18816/v1/coverage/failure"])
        self.assertTrue(all(row[0].get_header("Authorization") == "Bearer synthetic-collector-token-value-long-enough"
                            for row in opener.requests))
        coverage = json.loads(opener.requests[1][0].data)
        self.assertEqual(coverage, {"source": "calendar", "observed_at": "2026-11-06T10:05:00Z", "complete": True})
        failure = json.loads(opener.requests[2][0].data)
        self.assertEqual(failure, {"source": "daythread", "observed_at": "2026-11-06T10:06:00Z"})

    def test_daythread_outage_does_not_block_homeassistant_or_calendar_snapshots(self):
        observed = "2026-11-06T10:05:00Z"
        def snapshot(source, object_id):
            obj = sources.SourceObject(object_id, "test.snapshot", observed, f"{source}:{object_id}",
                                       {"summary": "Synthetic source fixture"})
            return sources.Snapshot(source, "fixture-window", (obj,), False, observed, None, "")

        class FakeIngest:
            instances = []
            def __init__(self, _token):
                self.events, self.coverage_rows, self.failures = [], [], []
                self.instances.append(self)
            def __call__(self, event): self.events.append(event)
            def coverage(self, *row): self.coverage_rows.append(row)
            def failure(self, *row): self.failures.append(row)

        class BrokenDaythread:
            def __init__(self, *_args): pass
            def read(self, _known_ids): raise ValueError("synthetic provider outage")

        config = {"daythread": {"enabled": True, "host": "daythread.home.arpa"}}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "revision.sqlite3"
            with (patch.object(sync_module, "load_config", return_value=config),
                  patch.object(sync_module, "private_bytes", return_value=b"synthetic-ingest-token-long-enough-0123456789"),
                  patch.object(sync_module, "read_ha", return_value=snapshot("home-assistant", "metadata")),
                  patch.object(sync_module, "read_aula", return_value=[]),
                  patch.object(sync_module, "read_calendar", return_value=[snapshot("calendar", "fixture-event")]),
                  patch.object(sync_module, "DaythreadClient", BrokenDaythread),
                  patch.object(sync_module, "Ingest", FakeIngest)):
                result = sync_module.sync(Path(directory) / "unused-config", state)

        self.assertEqual(result["home-assistant"]["upserted"], 1)
        self.assertEqual(result["calendar"]["upserted"], 1)
        self.assertEqual(result["errors"], [{"source": "daythread", "failed": True}])
        ingest = FakeIngest.instances[-1]
        self.assertEqual({row[0] for row in ingest.coverage_rows}, {"home-assistant", "calendar"})
        self.assertEqual([row[0] for row in ingest.failures], ["daythread"])

    def test_daythread_http_transport_pins_https_host_and_export_path(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b'{"schema_version":1,"source":"daythread"}'

        class Opener:
            def __init__(self): self.request = None
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        transport = sync_module.DaythreadHTTPS(
            "daythread.home.arpa", "synthetic-token-that-is-long-enough-for-tests-12345")
        opener = Opener()
        transport.opener = opener
        result = transport.get("/v1/integrations/assistant-context", {"limit": 20})
        self.assertEqual(result["source"], "daythread")
        self.assertEqual(opener.request.full_url,
            "https://daythread.home.arpa/v1/integrations/assistant-context?limit=20")
        self.assertEqual(opener.request.get_header("Authorization"),
            "Bearer synthetic-token-that-is-long-enough-for-tests-12345")
        self.assertEqual(opener.timeout, 8)
        class ResolveResponse(Response):
            def read(self, _): return b'{"schema_version":1,"source":"daythread","results":[]}'
        opener.open = lambda request, timeout: (setattr(opener, "request", request) or
            setattr(opener, "timeout", timeout) or ResolveResponse())
        resolved = transport.post("/v1/integrations/assistant-context/resolve", {"object_ids": []})
        self.assertEqual(resolved["results"], [])
        self.assertEqual(opener.request.full_url, "https://daythread.home.arpa/v1/integrations/assistant-context/resolve")
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertIn('"object_ids":[]', opener.request.data.decode())
        with self.assertRaises(ValueError):
            sync_module.DaythreadHTTPS("https://evil.example", "token")
        with self.assertRaises(ValueError):
            transport.get("/v1/transcripts", {})
        with self.assertRaises(ValueError):
            transport.post("/v1/transcripts", {"object_ids": []})

    def test_revision_database_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.db"
            target.write_bytes(b"safe")
            link = Path(directory) / "revision.db"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                sources.RevisionLedger(str(link))


if __name__ == "__main__":
    unittest.main()
