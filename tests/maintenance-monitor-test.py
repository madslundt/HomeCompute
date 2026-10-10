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
