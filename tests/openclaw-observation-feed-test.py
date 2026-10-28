#!/usr/bin/env python3
"""Scheduled finite health/update admission preserves freshness and delivery ownership."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("feed", ROOT / "scripts/openclaw-observation-feed.py")
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)
import openclaw_infrastructure as infrastructure
from openclaw_notifications import Outbox
NOW = datetime.now(timezone.utc)


def maintenance(host):
    return {"schema_version": 1, "host": host, "mode": "observe-only", "generated_at": NOW.isoformat(),
            "package_updates": {"status": "updates_available", "candidate_count": 4, "security_count": 1,
                                "scope": "installed_packages", "reboot_required": True,
                                "candidates": ["private-package"], "provenance": {"skipped_sources": 0}},
            "docker_image_updates": {"images": [{"state": "update_available", "image": "private-image"}]}}


def model_report():
    return {"schema_version": 1, "document_type": "model_update_report", "mode": "review-only",
            "status": "attention", "generated_at": NOW.isoformat(),
            "summary": {"changed": 2, "pin_drift": 1, "source_errors": 0, "outperforms_active": 0}}


def machine(host, status="healthy", at=NOW):
    names = {"home-core": ("homecompute-control-plane-caddy-1", "homecompute-control-plane-litellm-1",
                           "homecompute-control-plane-postgres-1", "homecompute-automation-n8n-1"),
             "home-spark": ("qwen38-flash-ultrafast", "gb10-hviske-primary", "homecompute-plapre-plapre-primary-1")}
    return {"schema_version": 1, "host": host, "generated_at": at.isoformat(),
            "containers": [{"name": name, "status": "Up observed (" + status + ")", "private": "PRIVATE"}
                           for name in names[host]],
            "unit_states": {}, "maintenance_report": maintenance(host)}


class CoreFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.observer = feed.load_observer()
        self.registry = self.observer.validate_registry(self.observer.read_json(ROOT / "config/system-monitoring.json"))
        self.core = self.registry["systems"][0]
        self.spark = self.registry["systems"][1]
        self.root_snapshot = patch.object(infrastructure, "root_snapshot", return_value=machine("home-core"))
        self.root_snapshot.start()
        self.addCleanup(self.root_snapshot.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_spark_transport_has_no_jump_or_general_command(self):
        self.assertEqual(feed.SPARK_COMMAND[0], "/run/current-system/sw/bin/ssh")
        self.assertEqual(feed.SPARK_COMMAND[-2:], ["madslundt@192.168.30.126", "cat /var/lib/homecompute-maintenance/report.json"])
        for value in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes", "ForwardAgent=no"):
            self.assertIn(value, feed.SPARK_COMMAND)
        self.assertNotIn("-J", feed.SPARK_COMMAND)
        with patch.object(feed, "bounded_command", return_value=json.dumps(machine("home-spark")).encode()) as command, \
             patch.object(feed, "read_report", side_effect=AssertionError("no local report for Spark")):
            raw = feed.core_snapshot(self.spark)
        command.assert_called_once_with([*feed.SPARK_COMMAND[:-1], "homecompute-machine-snapshot"], timeout=5)
        self.assertEqual(raw["host"], "home-spark")

    def test_core_reads_fixed_reports_and_projects_finite_model_metadata(self):
        model = model_report()
        model.update(payloads="private", token="private")
        model["summary"]["secret"] = "private"
        def read(path):
            return maintenance("home-core") if path == feed.MAINTENANCE_REPORT else model
        with patch.object(feed, "read_report", side_effect=read) as reader, \
             patch.object(feed, "bounded_command", side_effect=AssertionError("core is local")):
            raw = feed.core_snapshot(self.core)
        self.assertEqual([call.args[0] for call in reader.call_args_list], [feed.MAINTENANCE_REPORT, feed.MODEL_REPORT])
        self.assertNotIn("private", json.dumps(raw["model_update_report"]))
        self.assertEqual(set(raw["model_update_report"]["summary"]), {"changed", "pin_drift", "source_errors", "outperforms_active"})

    def test_failure_disabled_ha_and_unknown_targets_do_not_expand_authority(self):
        with patch.object(feed, "bounded_command", side_effect=subprocess.TimeoutExpired("ssh", 25)), \
             patch.object(feed, "read_report", side_effect=PermissionError()):
            with self.assertRaises(subprocess.TimeoutExpired):
                feed.core_snapshot(self.spark)
            core = feed.core_snapshot(self.core)
            self.assertIsNone(core["model_update_report"])
        with patch.object(feed, "bounded_command", side_effect=AssertionError("must not connect")), \
             patch.object(feed, "read_report", side_effect=AssertionError("must not read")):
            self.assertIsNone(feed.core_snapshot(dict(self.spark, enabled=False)))
            self.assertIsNone(feed.core_snapshot(self.registry["systems"][2]))
            self.assertIsNone(feed.core_snapshot(dict(self.spark, id="other", target="other")))
            self.assertIsNone(feed.core_snapshot(dict(self.spark, target="home-core")))

    def test_local_report_rejects_symlink_oversize_and_non_object(self):
        path = self.path / "report"
        path.write_text('{"schema_version":1}')
        self.assertEqual(feed.read_report(path), {"schema_version": 1})
        link = self.path / "link"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            feed.read_report(link)
        for data in (b"x" * (feed.MAX_REPORT + 1), b"[]", b"invalid"):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                feed.read_report(path)

    def test_subprocess_output_and_deadline_are_bounded_without_network(self):
        result = feed.bounded_command([sys.executable, "-c", "print('{}')"], timeout=2)
        self.assertEqual(feed.parse_report(result), {})
        with self.assertRaises(ValueError):
            feed.bounded_command([sys.executable, "-c", "import sys;sys.stdout.write('x'*1048577)"], timeout=2)
        with self.assertRaises(subprocess.TimeoutExpired):
            feed.bounded_command([sys.executable, "-c", "import time;time.sleep(3)"], timeout=.05)
        with self.assertRaises(ValueError):
            feed.bounded_command([sys.executable, "-c", "raise SystemExit(1)"], timeout=2)

    def test_core_feed_admits_finite_health_updates_with_source_ttls_and_no_private_metadata(self):
        tokens = self.path / "tokens.json"
        tokens.write_text(json.dumps({"collector": "synthetic-token"}))
        os.chmod(tokens, 0o600)
        destination = self.path / "latest.json"
        def read(path):
            if path == feed.MODEL_REPORT:
                return model_report()
            return maintenance("home-core")
        with patch.object(feed, "load_observer", return_value=self.observer), \
             patch.object(self.observer, "collect", side_effect=AssertionError("no broad operator collector")), \
             patch.object(feed, "read_report", side_effect=read), \
             patch.object(feed, "bounded_command", return_value=json.dumps(machine("home-spark", "unhealthy")).encode()), \
             patch.object(feed, "request", return_value={"accepted": True, "actions_enabled": False}) as admit:
            result = feed.feed(ROOT / "config/system-monitoring.json", tokens, destination, core_host_mode=True)
        report = admit.call_args.args[-1]
        self.assertTrue(result["accepted"])
        self.assertEqual({row["category"] for row in report["observations"]}, {"health", "updates"})
        health = [row for row in report["observations"] if row["category"] == "health"]
        self.assertTrue(any(row["system_id"] == "home-spark" and row["status"] == "degraded" for row in health))
        self.assertTrue(any(row["system_id"] == "home-core" and row["status"] == "healthy" for row in health))
        self.assertTrue(all(row["observed_at"] == NOW.isoformat().replace("+00:00", "Z") for row in health))
        self.assertFalse(any(row["system_id"] == "home-assistant" for row in report["observations"]))
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["changes"], [])
        for row in report["observations"]:
            if row["check_id"].startswith(("package-update-report", "docker-image-report")):
                self.assertEqual(row["observed_at"], NOW.isoformat().replace("+00:00", "Z"))
        self.assertEqual(json.loads(destination.read_text()), report)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_default_mac_collector_is_unchanged(self):
        tokens = self.path / "tokens.json"
        tokens.write_text(json.dumps({"collector": "synthetic-token"}))
        os.chmod(tokens, 0o600)
        with patch.object(feed, "load_observer", return_value=self.observer), \
             patch.object(self.observer, "collect", return_value=None) as collect, \
             patch.object(feed, "core_snapshot", side_effect=AssertionError("explicit core mode required")), \
             patch.object(feed, "request", return_value={"accepted": True, "actions_enabled": False}):
            feed.feed(ROOT / "config/system-monitoring.json", tokens, self.path / "latest.json")
        self.assertEqual(collect.call_count, len(self.registry["systems"]))

    def test_health_snapshot_rejects_wrong_host_schema_stale_future_and_unbounded_data(self):
        invalid = [dict(machine("home-core"), host="home-spark"),
                   dict(machine("home-core"), schema_version=True),
                   dict(machine("home-core"), generated_at=None),
                   machine("home-core", at=NOW - timedelta(seconds=61)),
                   machine("home-core", at=NOW + timedelta(seconds=10)),
                   dict(machine("home-core"), containers=[{}] * 1001),
                   dict(machine("home-core"), unit_states=[])]
        for value in invalid:
            with self.subTest(value=value.get("generated_at")), \
                 patch.object(infrastructure, "root_snapshot", return_value=value), \
                 self.assertRaises(ValueError):
                feed.core_snapshot(self.core)

    def test_enabled_ha_health_updates_survive_compute_health_admission(self):
        registry = json.loads(json.dumps(self.registry))
        registry["systems"][2]["enabled"] = True
        registry_path = self.path / "registry.json"
        registry_path.write_text(json.dumps(registry))
        tokens = self.path / "tokens.json"
        tokens.write_text(json.dumps({"collector": "synthetic-token"}))
        tokens.chmod(0o600)
        ha = {"schema_version": 1, "host": "home-assistant", "generated_at": NOW.isoformat(),
              "installed_version": "2026.10.1", "update_available_count": 1, "unavailable_count": 1,
              "household_private": "PRIVATE"}
        with patch.object(feed, "load_observer", return_value=self.observer), \
             patch.object(self.observer, "collect", return_value=ha) as collect, \
             patch.object(feed, "read_report", side_effect=lambda path: model_report() if path == feed.MODEL_REPORT else maintenance("home-core")), \
             patch.object(feed, "bounded_command", return_value=json.dumps(machine("home-spark")).encode()), \
             patch.object(feed, "request", return_value={"accepted": True, "actions_enabled": False}) as admit:
            feed.feed(registry_path, tokens, self.path / "latest.json", core_host_mode=True)
        collect.assert_called_once_with(registry["systems"][2], ha_transport=None, core_host_mode=True)
        report = admit.call_args.args[-1]
        self.assertEqual({row["system_id"] for row in report["observations"]}, {"home-core", "home-spark", "home-assistant"})
        ha_rows = [row for row in report["observations"] if row["system_id"] == "home-assistant"]
        self.assertEqual({row["category"] for row in ha_rows}, {"health", "updates"})
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_scheduled_health_deduplicates_and_only_fresh_health_recovers_delivered_incident(self):
        settings = {"destination": "operator-private", "timezone": "Europe/Copenhagen",
                    "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}
        store = Outbox(self.path / "outbox.sqlite3", settings)
        self.addCleanup(store.db.close)
        spec = importlib.util.spec_from_file_location("feed_communication", ROOT / "scripts/openclaw-communication.py")
        communication = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(communication)
        allowed = communication.registry_keys(ROOT / "config/system-monitoring.json")
        tokens = self.path / "tokens.json"
        tokens.write_text(json.dumps({"collector": "synthetic-token"}))
        tokens.chmod(0o600)
        start = NOW.replace(hour=12, minute=0, second=0, microsecond=0)
        topic = "home-spark:container.homecompute-plapre-plapre-primary-1"

        def collect(status, offset, stale=False):
            current = start + timedelta(seconds=offset)
            core = machine("home-core", at=current)
            spark = machine("home-spark", status, at=current - timedelta(seconds=120) if stale else current)
            def admit(url, route, token, report):
                store.observations(report, allowed, current.timestamp())
                return {"accepted": True, "actions_enabled": False}
            with patch.object(feed, "datetime", wraps=datetime) as clock, \
                 patch.object(infrastructure, "root_snapshot", return_value=core), \
                 patch.object(feed, "read_report", side_effect=lambda path: model_report() if path == feed.MODEL_REPORT else maintenance("home-core")), \
                 patch.object(feed, "bounded_command", return_value=json.dumps(spark).encode()), \
                 patch.object(feed, "request", side_effect=admit):
                clock.now.return_value = current
                feed.feed(ROOT / "config/system-monitoring.json", tokens, self.path / "latest.json", core_host_mode=True)
            return current.timestamp()

        first = collect("unhealthy", 0)
        count = store.db.execute("SELECT count(*) FROM events WHERE topic=?", (topic,)).fetchone()[0]
        self.assertEqual(count, 1)
        collect("unhealthy", 1)
        self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE topic=?", (topic,)).fetchone()[0], 1)
        # Deliver through the unchanged machine claim/receipt path.
        while (event := store.claim(first + 1)) is not None:
            store.acknowledge(event["delivery_key"], event["claim"], "synthetic:" + event["claim"], first + 1)
        self.assertEqual(store.db.execute("SELECT state FROM events WHERE topic=?", (topic,)).fetchone()[0], "delivered")
        collect("healthy", 2, stale=True)
        self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE topic=? AND kind='recovery'", (topic,)).fetchone()[0], 0)
        collect("healthy", 3)
        self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE topic=? AND kind='recovery'", (topic,)).fetchone()[0], 1)
        collect("healthy", 4)
        self.assertEqual(store.db.execute("SELECT count(*) FROM events WHERE topic=? AND kind='recovery'", (topic,)).fetchone()[0], 1)

    def projection_context(self):
        stack = ExitStack()
        destination = self.path / "projection/model-update-report.json"
        source = self.path / "private-model.json"
        stack.enter_context(patch.object(feed, "MODEL_REPORT", destination))
        stack.enter_context(patch.object(feed, "PRIVATE_MODEL_REPORT", source))
        stack.enter_context(patch.object(feed.os, "geteuid", return_value=0))
        fstat, stat = os.fstat, os.stat
        def root_owned(info):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        stack.enter_context(patch.object(feed.os, "fstat", side_effect=lambda fd: root_owned(fstat(fd))))
        stack.enter_context(patch.object(feed.os, "stat", side_effect=lambda *args, **kwargs: root_owned(stat(*args, **kwargs))))
        return stack, source, destination

    def test_root_projection_is_atomic_public_and_contains_only_finite_fields(self):
        stack, source, destination = self.projection_context()
        value = model_report()
        value.update(changes=[{"private": "SECRET"}], token="PRIVATE")
        value["summary"]["unbenchmarked"] = 99
        with stack:
            source.write_text(json.dumps(value))
            self.assertTrue(feed.project_core_model())
            self.assertEqual(json.loads(destination.read_text()), model_report())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
            self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_invalid_missing_or_oversize_source_removes_prior_projection(self):
        stack, source, destination = self.projection_context()
        with stack:
            destination.parent.mkdir()
            for data in ("bad json PRIVATE", "[]", "x" * (2 * feed.MAX_REPORT + 1),
                         "[" * 2000 + "]" * 2000, None):
                destination.write_text(json.dumps(model_report()))
                if data is None:
                    source.unlink()
                else:
                    source.write_text(data)
                self.assertFalse(feed.project_core_model())
                self.assertFalse(destination.exists())

    def test_projection_rejects_non_root_directory_symlink_and_destination_symlink(self):
        with patch.object(feed.os, "geteuid", return_value=501), self.assertRaises(PermissionError):
            feed.project_core_model()
        stack, source, destination = self.projection_context()
        with stack:
            source.write_text(json.dumps(model_report()))
            victim = self.path / "victim"
            victim.mkdir()
            destination.parent.symlink_to(victim)
            with self.assertRaises(OSError):
                feed.project_core_model()
            destination.parent.unlink()
            destination.parent.mkdir()
            destination.symlink_to(source)
            with self.assertRaises(ValueError):
                feed.project_core_model()
            self.assertEqual(json.loads(source.read_text()), model_report())

    def test_model_projection_rejects_private_counter_values_and_invalid_provenance(self):
        for change in ({"mode": "write"}, {"schema_version": True}, {"status": "SECRET"},
                       {"status": []},
                       {"generated_at": "2026-10-10T12:00:00"}, {"summary": {"changed": "SECRET"}},
                       {"document_type": "other"}):
            with self.assertRaises(ValueError):
                feed.model_projection(dict(model_report(), **change))

    def test_project_action_needs_no_feed_arguments_and_never_calls_transport(self):
        with patch.object(sys, "argv", ["feed", "--project-core-model"]), \
             patch.object(feed, "project_core_model", return_value=False) as project, \
             patch.object(feed, "request", side_effect=AssertionError("no transport")), \
             patch("builtins.print"):
            self.assertEqual(feed.main(), 0)
        project.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
