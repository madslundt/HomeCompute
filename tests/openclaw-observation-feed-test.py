#!/usr/bin/env python3
"""Core feed placement reads only fixed reports and preserves update admission."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("feed", ROOT / "scripts/openclaw-observation-feed.py")
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)
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


class CoreFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.observer = feed.load_observer()
        self.registry = self.observer.validate_registry(self.observer.read_json(ROOT / "config/system-monitoring.json"))
        self.core = self.registry["systems"][0]
        self.spark = self.registry["systems"][1]

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_spark_transport_has_no_jump_or_general_command(self):
        self.assertEqual(feed.SPARK_COMMAND[0], "/run/current-system/sw/bin/ssh")
        self.assertEqual(feed.SPARK_COMMAND[-2:], ["madslundt@192.168.30.126", "cat /var/lib/homecompute-maintenance/report.json"])
        for value in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes", "ForwardAgent=no"):
            self.assertIn(value, feed.SPARK_COMMAND)
        self.assertNotIn("-J", feed.SPARK_COMMAND)
        with patch.object(feed, "bounded_command", return_value=json.dumps(maintenance("home-spark")).encode()) as command, \
             patch.object(feed, "read_report", side_effect=AssertionError("no local report for Spark")):
            raw = feed.core_snapshot(self.spark)
        command.assert_called_once_with(feed.SPARK_COMMAND)
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
            raw = feed.core_snapshot(self.spark)
            self.assertIsNone(raw["maintenance_report"])
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

    def test_core_feed_reuses_validation_ttls_and_filters_all_health_and_private_metadata(self):
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
             patch.object(feed, "bounded_command", return_value=json.dumps(maintenance("home-spark")).encode()), \
             patch.object(feed, "request", return_value={"accepted": True, "actions_enabled": False}) as admit:
            result = feed.feed(ROOT / "config/system-monitoring.json", tokens, destination, core_host_mode=True)
        report = admit.call_args.args[-1]
        self.assertTrue(result["accepted"])
        self.assertTrue(all(row["category"] == "updates" for row in report["observations"]))
        self.assertFalse(any(row["system_id"] == "home-assistant" for row in report["observations"]))
        self.assertNotIn("private", json.dumps(report))
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
