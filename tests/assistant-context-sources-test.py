#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from datetime import date
import unittest

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
        self.assertEqual([row[0].full_url for row in opener.requests], [
            "http://127.0.0.1:18816/v1/events", "http://127.0.0.1:18816/v1/coverage"])
        self.assertTrue(all(row[0].get_header("Authorization") == "Bearer synthetic-collector-token-value-long-enough"
                            for row in opener.requests))
        coverage = json.loads(opener.requests[1][0].data)
        self.assertEqual(coverage, {"source": "calendar", "observed_at": "2026-11-06T10:05:00Z", "complete": True})

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
        with self.assertRaises(ValueError):
            sync_module.DaythreadHTTPS("https://evil.example", "token")
        with self.assertRaises(ValueError):
            transport.get("/v1/transcripts", {})

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
