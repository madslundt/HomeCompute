#!/usr/bin/env python3
"""Meaningful changes, freshness, safe projection and update collection boundaries."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_notifications import Outbox
from openclaw_machine_notifications import render
from maintenance_observations import ttl
spec = importlib.util.spec_from_file_location("observer", ROOT / "scripts/observe-homecompute.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
spec = importlib.util.spec_from_file_location("adapter", ROOT / "scripts/openclaw-communication.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
spec = importlib.util.spec_from_file_location("update_feed", ROOT / "scripts/openclaw-observation-feed.py")
update_feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_feed)
NOW = datetime(2026, 10, 10, 10, tzinfo=timezone.utc)


def fixture():
    registry = observer.validate_registry(json.loads((ROOT / "config/system-monitoring.json").read_text()))
    system = registry["systems"][1]
    raw = {"schema_version": 1, "host": system["id"], "containers": [], "maintenance_report": {
        "schema_version": 1, "host": system["id"], "mode": "observe-only", "generated_at": observer.iso(NOW),
        "package_updates": {"status": "updates_available", "candidate_count": 1,
                            "candidates": [{"name": "example", "installed": "1", "candidate": "2"}]},
        "docker_image_updates": {"images": [{"container": "test", "image": "example:stable",
            "state": "update_available", "remote_platform_digest": "sha256:" + "a" * 64}]}}}
    return system, raw


def report(system, raw, now):
    result = observer.make_report({"systems": [system]}, {system["id"]: raw}, now)
    result["observations"] = [x for x in result["observations"] if x["category"] == "updates"]
    return result


def mcp_fixture():
    registry = observer.validate_registry(json.loads((ROOT / "config/system-monitoring.json").read_text()))
    system = copy.deepcopy(registry["systems"][0])
    system["updates"] = ["mcp-update-report"]
    raw = {"schema_version": 1, "host": "home-core", "containers": [], "mcp_update_report": {
        "schema_version": 1, "host": "home-core", "generated_at": observer.iso(NOW), "components": [
            {"component": "aula-mcp", "status": "current"},
            {"component": "tilbudstrolden-mcp", "status": "succeeded"}]}}
    return system, raw


def host_fixture(host="home-core", status="current"):
    registry = observer.validate_registry(json.loads((ROOT / "config/system-monitoring.json").read_text()))
    system = copy.deepcopy(next(row for row in registry["systems"] if row["id"] == host))
    system["updates"] = ["host-update-report"]
    raw = {"schema_version": 1, "host": host, "containers": [], "host_update_report": {
        "schema_version": 1, "host": host, "generated_at": observer.iso(NOW),
        "items": [{"component": "private-container", "kind": "docker", "status": status}], "summary": {status: 1}}}
    return system, raw


class MaintenanceTest(unittest.TestCase):
    def test_feed_uses_observation_ack_and_retains_only_update_rows(self):
        system, raw = fixture()
        real = observer.make_report({"systems": [system]}, {system["id"]: raw}, NOW)
        fake = SimpleNamespace(validate_registry=lambda x: x, read_json=lambda p: {"systems": []},
                               make_report=lambda *args: copy.deepcopy(real))
        loader = SimpleNamespace(exec_module=lambda m: None)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "latest.json"
            with patch.object(update_feed.importlib.util, "spec_from_file_location", return_value=SimpleNamespace(loader=loader)), \
                 patch.object(update_feed.importlib.util, "module_from_spec", return_value=fake), \
                 patch.object(update_feed, "private_json", return_value={"collector": "synthetic-collector"}), \
                 patch.object(update_feed, "request", return_value={"accepted": True, "actions_enabled": False}) as send:
                update_feed.feed(Path("registry"), Path("tokens"), destination)
                self.assertTrue(all(x["category"] == "updates" for x in send.call_args.args[3]["observations"]))
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(destination.read_text())["findings"], [])
                destination.unlink()
                send.return_value = {"accepted": True, "actions_enabled": True}
                with self.assertRaises(ValueError):
                    update_feed.feed(Path("registry"), Path("tokens"), destination)
                self.assertFalse(destination.exists())

    def test_same_candidates_new_timestamp_no_repeated_event(self):
        system, raw = fixture()
        settings = {"timezone": "Europe/Copenhagen", "quiet_start_hour": 22,
                    "quiet_end_hour": 7, "cooldown_seconds": 1800}
        with tempfile.TemporaryDirectory() as directory:
            store = Outbox(Path(directory) / "state.sqlite3", settings)
            allowed = adapter.registry_keys(ROOT / "config/system-monitoring.json")
            store.observations(report(system, raw, NOW), allowed, NOW.timestamp())
            first = store.db.execute("SELECT count(*) FROM events").fetchone()[0]
            later = NOW + timedelta(days=1)
            raw["maintenance_report"]["generated_at"] = observer.iso(later)
            store.observations(report(system, raw, later), allowed, later.timestamp())
            self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], first)
            raw["maintenance_report"]["package_updates"]["candidates"][0]["candidate"] = "3"
            later += timedelta(minutes=1)
            raw["maintenance_report"]["generated_at"] = observer.iso(later)
            store.observations(report(system, raw, later), allowed, later.timestamp())
            self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], first + 1)

    def test_daily_freshness_independent_from_weekly_model_ttl(self):
        system, raw = fixture()
        system["update_ttl_seconds"] = 691200
        rows = report(system, raw, NOW + timedelta(hours=37))["observations"]
        self.assertFalse(any(x["status"] == "healthy" for x in rows))
        self.assertTrue(all(x["status"] == "degraded" for x in rows if x["check_id"].endswith(".coverage")))

    def test_weekly_mcp_freshness_does_not_extend_daily_sources(self):
        system, raw = mcp_fixture()
        rows = report(system, raw, NOW + timedelta(days=7))["observations"]
        self.assertTrue(all(row["status"] == "healthy" for row in rows))
        self.assertTrue(all(row["expires_at"] == observer.iso(NOW + timedelta(days=8)) for row in rows))
        stale = report(system, raw, NOW + timedelta(days=9))["observations"]
        self.assertEqual([row["status"] for row in stale], ["unknown", "degraded"])
        self.assertTrue(all(row["evidence"] == {"reason": "stale"} for row in stale))
        self.assertEqual(ttl("mcp-update-report.coverage"), 691200)
        self.assertEqual(ttl("package-update-report"), 129600)
        self.assertEqual(ttl("docker-image-report.coverage"), 129600)

    def test_major_mcp_notice_is_actionable_without_private_details(self):
        system, raw = mcp_fixture()
        raw["mcp_update_report"]["components"][0].update(
            status="approval_required", classification="major", installed="PRIVATE_VERSION_1",
            candidate="PRIVATE_VERSION_2", plan_id="PRIVATE_PLAN", reason="fresh_exact_plan_requires_approval",
            secret="PRIVATE_TOKEN")
        rows = report(system, raw, NOW)["observations"]
        main = next(row for row in rows if row["check_id"] == "mcp-update-report")
        coverage = next(row for row in rows if row["check_id"].endswith(".coverage"))
        self.assertEqual(main["status"], "degraded")
        self.assertEqual(main["evidence"]["candidate_count"], 1)
        self.assertEqual(coverage["status"], "healthy")
        summary = render("home-core", [("finding", {"observation": main})])
        self.assertIn("MCP update review", summary)
        self.assertIn("major updates require approval; inspect maintenance_list", summary)
        self.assertIn("maintenance_prepare a fresh exact plan before requesting approval", summary)
        self.assertNotIn("PRIVATE", json.dumps(rows) + summary)
        digest = main["evidence"]["source_digest"]
        raw["mcp_update_report"]["generated_at"] = observer.iso(NOW + timedelta(days=1))
        raw["mcp_update_report"]["components"][0]["plan_id"] = "PRIVATE_FRESH_PLAN"
        repeated = report(system, raw, NOW + timedelta(days=1))["observations"]
        self.assertEqual(repeated[0]["evidence"]["source_digest"], digest)
        raw["mcp_update_report"]["components"][0]["candidate"] = "PRIVATE_VERSION_3"
        changed = report(system, raw, NOW + timedelta(days=1))["observations"]
        self.assertNotEqual(changed[0]["evidence"]["source_digest"], digest)

    def test_core_feed_reads_only_fixed_public_mcp_report(self):
        system, raw = mcp_fixture()
        snapshot = dict(raw, generated_at=observer.iso(datetime.now(timezone.utc)), unit_states={})
        def read(path):
            if path == update_feed.MCP_REPORT:
                return raw["mcp_update_report"]
            raise FileNotFoundError()
        with patch("openclaw_infrastructure.root_snapshot", return_value=snapshot), \
             patch.object(update_feed, "read_report", side_effect=read) as reader:
            result = update_feed.core_snapshot(system)
        self.assertEqual(result["mcp_update_report"], raw["mcp_update_report"])
        self.assertEqual(reader.call_args_list[-1].args, (Path("/var/lib/homecompute-mcp-maintenance-public/report.json"),))
        with patch("openclaw_infrastructure.root_snapshot", return_value=snapshot), \
             patch.object(update_feed, "read_report", side_effect=FileNotFoundError()):
            self.assertIsNone(update_feed.core_snapshot(system)["mcp_update_report"])

    def test_mcp_errors_degrade_coverage_and_malformed_reports_cannot_claim_current(self):
        system, raw = mcp_fixture()
        for status in ("failed", "uncertain", "blocked"):
            raw["mcp_update_report"]["components"][0].update(status=status, reason="operator_reconciliation_required")
            rows = report(system, raw, NOW)["observations"]
            self.assertTrue(all(row["status"] == "degraded" for row in rows))
            self.assertIn("held for review", render("home-core", [("finding", {"observation": rows[0]})]))
        for mutation in ({"host": "home-spark"}, {"components": []}, {"schema_version": True},
                         {"generated_at": observer.iso(NOW + timedelta(hours=1))},
                         {"components": [{"component": "aula-mcp", "status": "current", "reason": "PRIVATE"},
                                         {"component": "tilbudstrolden-mcp", "status": "current"}]}):
            hostile = copy.deepcopy(raw)
            hostile["mcp_update_report"].update(mutation)
            self.assertFalse(any(row["status"] == "healthy" for row in report(system, hostile, NOW)["observations"]))

    def test_host_counts_each_actual_result_and_keeps_review_unverified(self):
        cases = [("current", 0, 0, 0), ("succeeded", 0, 0, 0), ("delegated", 0, 0, 0),
                 ("approval_required", 1, 0, 0), ("available", 1, 0, 0), ("review", 1, 0, 1),
                 ("failed", 0, 1, 1), ("blocked", 0, 1, 1), ("uncertain", 0, 1, 1)]
        for host in ("home-core", "home-spark"):
            for status, candidates, errors, gaps in cases:
                with self.subTest(host=host, status=status):
                    system, raw = host_fixture(host, status)
                    main, coverage = report(system, raw, NOW)["observations"]
                    self.assertEqual(main["evidence"]["candidate_count"], candidates)
                    self.assertEqual(main["evidence"]["count"], errors)
                    self.assertEqual(coverage["evidence"]["count"], gaps)
                    self.assertEqual(main["status"], "degraded" if candidates or errors else "healthy")
                    self.assertEqual(coverage["status"], "degraded" if gaps else "healthy")
        system, raw = host_fixture()
        source = raw["host_update_report"]
        source["items"] = [{"component": str(index), "kind": "packages", "status": status}
                           for index, (status, *_) in enumerate(cases)]
        source["summary"] = {status: 1 for status, *_ in cases}
        main, coverage = report(system, raw, NOW)["observations"]
        self.assertEqual((main["evidence"]["candidate_count"], main["evidence"]["count"], coverage["evidence"]["count"]), (3, 3, 4))

    def test_host_summary_must_match_actual_items_and_unknown_never_claims_current(self):
        system, raw = host_fixture()
        for mutation in ({"summary": {"failed": 0}}, {"items": [], "summary": {}},
                         {"summary": {"current": True}}, {"summary": {"current": 2}},
                         {"items": [{"component": "x", "kind": "docker", "status": "unknown"}], "summary": {"unknown": 1}},
                         {"host": "home-spark"}, {"schema_version": True}):
            hostile = copy.deepcopy(raw)
            hostile["host_update_report"].update(mutation)
            self.assertFalse(any(row["status"] == "healthy" for row in report(system, hostile, NOW)["observations"]))
        raw["host_update_report"].update(items=[], summary={"blocked": 1})
        self.assertTrue(all(row["status"] == "degraded" for row in report(system, raw, NOW)["observations"]))
        self.assertTrue(all(row["evidence"]["count"] == 1 for row in report(system, raw, NOW)["observations"]))

    def test_host_daily_expiry_and_plan_timestamp_independent_private_digest(self):
        system, raw = host_fixture(status="approval_required")
        raw["host_update_report"]["items"][0].update(component="PRIVATE_COMPONENT", installed="PRIVATE_1",
            candidate="PRIVATE_2", plan_id="PRIVATE_PLAN", reason="PRIVATE_REASON", token="SECRET")
        first = report(system, raw, NOW)["observations"]
        self.assertEqual(first[0]["expires_at"], observer.iso(NOW + timedelta(hours=36)))
        later = NOW + timedelta(days=1)
        raw["host_update_report"]["generated_at"] = observer.iso(later)
        raw["host_update_report"]["items"][0]["plan_id"] = "PRIVATE_NEW_PLAN"
        second = report(system, raw, later)["observations"]
        self.assertEqual(first[0]["evidence"]["source_digest"], second[0]["evidence"]["source_digest"])
        summary = render("home-core", [("finding", {"observation": second[0]})])
        self.assertIn("Host software updates", summary)
        self.assertIn("maintenance_prepare a fresh exact plan", summary)
        self.assertIn("image-<container>, nixos, or packages", summary)
        self.assertIn("Routine patch/minor updates deploy automatically after checks", summary)
        self.assertIn("major changes, uncertain results, and build gaps", summary)
        self.assertNotIn("PRIVATE", json.dumps(first + second) + summary)
        self.assertNotIn("SECRET", json.dumps(first + second) + summary)
        raw["host_update_report"]["items"][0]["candidate"] = "PRIVATE_3"
        self.assertNotEqual(first[0]["evidence"]["source_digest"], report(system, raw, later)["observations"][0]["evidence"]["source_digest"])
        stale = report(system, raw, later + timedelta(hours=37))["observations"]
        self.assertEqual([row["status"] for row in stale], ["unknown", "degraded"])
        self.assertTrue(all(row["evidence"]["reason"] == "stale" for row in stale))

    def test_host_feed_uses_fixed_local_path_and_embedded_spark_snapshot(self):
        for host in ("home-core", "home-spark"):
            system, raw = host_fixture(host)
            snapshot = dict(raw, generated_at=observer.iso(datetime.now(timezone.utc)), unit_states={})
            with patch("openclaw_infrastructure.root_snapshot", return_value=snapshot), \
                 patch.object(update_feed, "bounded_command", return_value=json.dumps(snapshot).encode()) as remote, \
                 patch.object(update_feed, "read_report", return_value=raw["host_update_report"]) as reader:
                result = update_feed.core_snapshot(system)
            self.assertEqual(result["host_update_report"], raw["host_update_report"])
            if host == "home-core":
                self.assertEqual(reader.call_args_list[-1].args, (Path("/var/lib/homecompute-host-maintenance-public/report.json"),))
                remote.assert_not_called()
            else:
                reader.assert_not_called()
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(remote.call_args.args[0][-1], "homecompute-machine-snapshot")

    def test_host_outbox_deduplicates_renewed_plan_and_notices_changed_candidate(self):
        system, raw = host_fixture(status="approval_required")
        item = raw["host_update_report"]["items"][0]
        item.update(candidate="2.0.0", plan_id="old-plan")
        settings = {"timezone": "Europe/Copenhagen", "quiet_start_hour": 22,
                    "quiet_end_hour": 7, "cooldown_seconds": 1800}
        allowed = {"home-core:" + key: {"category": "updates", "ttl": 129600}
                   for key in ("host-update-report", "host-update-report.coverage")}
        with tempfile.TemporaryDirectory() as directory:
            store = Outbox(Path(directory) / "state.sqlite3", settings)
            store.observations(report(system, raw, NOW), allowed, NOW.timestamp())
            first = store.db.execute("SELECT count(*) FROM events").fetchone()[0]
            later = NOW + timedelta(days=1)
            raw["host_update_report"]["generated_at"] = observer.iso(later)
            item["plan_id"] = "renewed-plan"
            store.observations(report(system, raw, later), allowed, later.timestamp())
            self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], first)
            later += timedelta(minutes=1)
            raw["host_update_report"]["generated_at"] = observer.iso(later)
            item["candidate"] = "3.0.0"
            store.observations(report(system, raw, later), allowed, later.timestamp())
            self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], first + 1)

    def test_counts_only_and_hash_no_names_or_credentials(self):
        system, raw = fixture()
        raw["maintenance_report"]["package_updates"]["candidates"][0].update(name="PRIVATE", token="SECRET")
        raw["maintenance_report"]["docker_image_updates"]["images"][0]["image"] = "PRIVATE"
        serialized = json.dumps(report(system, raw, NOW)["observations"])
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("SECRET", serialized)
        self.assertIn('"candidate_count": 1', serialized)

    def test_incomplete_indexes_unknown_not_current(self):
        system, raw = fixture()
        raw["maintenance_report"]["package_updates"].update(status="current", candidate_count=0,
                                                             provenance={"skipped_sources": 1})
        rows = report(system, raw, NOW)["observations"]
        self.assertEqual(next(x for x in rows if x["check_id"] == "package-update-report")["status"], "unknown")
        self.assertEqual(next(x for x in rows if x["check_id"] == "package-update-report.coverage")["status"], "degraded")

    def test_untracked_or_failed_images_never_all_current(self):
        system, raw = fixture()
        for images in ([], [{"state": "unknown"}], [{"state": "untracked"}]):
            raw["maintenance_report"]["docker_image_updates"]["images"] = images
            rows = report(system, raw, NOW)["observations"]
            self.assertEqual(next(x for x in rows if x["check_id"] == "docker-image-report.coverage")["status"], "degraded")

    def test_known_candidates_remain_actionable_with_separate_coverage_gap(self):
        system, raw = fixture()
        raw["maintenance_report"]["package_updates"]["provenance"] = {"skipped_sources": 4}
        rows = report(system, raw, NOW)["observations"]
        self.assertEqual(next(x for x in rows if x["check_id"] == "package-update-report")["status"], "degraded")
        self.assertEqual(next(x for x in rows if x["check_id"] == "package-update-report.coverage")["status"], "degraded")

    def test_future_or_wrong_host_reports_cannot_claim_current(self):
        system, raw = fixture()
        for mutation in ({"generated_at": observer.iso(NOW + timedelta(hours=1))}, {"host": "other"}, {"schema_version": 20}):
            hostile = copy.deepcopy(raw)
            hostile["maintenance_report"].update(mutation)
            self.assertFalse(any(x["status"] == "healthy" for x in report(system, hostile, NOW)["observations"]))


if __name__ == "__main__":
    unittest.main()
