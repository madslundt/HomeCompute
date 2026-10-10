#!/usr/bin/env python3
"""Fixed host authority, finite projections, report bounds and publication."""
from collections import namedtuple
from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("snapshot", ROOT / "scripts/openclaw-machine-snapshot.py")
SNAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SNAP)


class MachineSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def runner(self, calls):
        def run(argv, timeout, *, host):
            calls.append((argv, timeout, host))
            if "inspect" in argv:
                return json.dumps({"Running": True, "Pid": 4242, "Error": "PRIVATE",
                                   "Health": {"Status": "healthy", "Log": ["PRIVATE"]}}).encode()
            return b"\n\n".join(("Id=" + name + "\nLoadState=loaded\nActiveState=active\nSubState=running\n"
                                  "Result=success\nExecMainStatus=0\nExecMainStartTimestampMonotonic=1234\nSECRET=PRIVATE").encode()
                                 for name in SNAP.UNITS[host])
        return run

    def test_allowlists_match_only_enabled_registered_services(self):
        registry = json.loads((ROOT / "config/system-monitoring.json").read_text())
        enabled = {s["id"]: s for s in registry["systems"] if s["enabled"]}
        self.assertEqual(set(SNAP.CONTAINERS), set(enabled))
        for host, system in enabled.items():
            self.assertEqual(SNAP.CONTAINERS[host], tuple(x["name"] for x in system["containers"]))
            self.assertEqual(SNAP.UNITS[host], tuple(x["name"] for x in system["units"]))

    def test_fixed_containers_units_and_sensitive_docker_fields_are_projected(self):
        for host in SNAP.CONTAINERS:
            calls = []
            with patch.object(SNAP, "host_metrics", return_value={"uptime_seconds": 12}), \
                    patch.object(SNAP, "read_report", side_effect=PermissionError("PRIVATE")):
                result = SNAP.snapshot(host, self.runner(calls))
            self.assertEqual(result["host"], host)
            self.assertEqual(result["schema_version"], 1)
            self.assertIn("generated_at", result)
            self.assertEqual(tuple(c["name"] for c in result["containers"]), SNAP.CONTAINERS[host])
            self.assertTrue(all(c["status"] == "Up observed (healthy)" for c in result["containers"]))
            self.assertEqual(set(result["unit_states"]), set(SNAP.UNITS[host]))
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertNotIn("4242", json.dumps(result))
            self.assertTrue(all(0 < c[1] <= 1.5 for c in calls))
            self.assertTrue(all("--host" in c[0] and "unix:///var/run/docker.sock" in c[0]
                                for c in calls if "inspect" in c[0]))
            self.assertNotIn("ps", [part for c in calls for part in c[0]])

    def test_command_failures_preserve_unknown_and_never_claim_global_units_healthy(self):
        def failing(*args, **kwargs): raise ValueError("PRIVATE")
        with patch.object(SNAP, "host_metrics", return_value={}), patch.object(SNAP, "read_report", side_effect=OSError()):
            result = SNAP.snapshot("home-core", failing)
        self.assertTrue(all(c["status"] == "Unknown" for c in result["containers"]))
        self.assertEqual(result["unit_states"], {})
        self.assertNotIn("failed_unit_count", result["host_metrics"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_total_command_deadline_prevents_late_systemctl_dispatch(self):
        calls = []
        with patch.object(SNAP.time, "monotonic", side_effect=[0, 0, 2, 4, 7.9, 8.1]), \
                patch.object(SNAP, "host_metrics", return_value={}), patch.object(SNAP, "read_report", side_effect=OSError()):
            result = SNAP.snapshot("home-core", self.runner(calls))
        self.assertEqual(len(calls), 4)
        self.assertLessEqual(sum(c[1] for c in calls), SNAP.COMMAND_BUDGET)
        self.assertEqual(result["unit_states"], {})

    def test_container_states_are_enum_only(self):
        for state, expected in (({"Running": True, "Health": {"Status": "PRIVATE"}}, "Up observed"),
                                ({"Running": True, "Health": {"Status": ["PRIVATE"]}}, "Up observed"),
                                ({"Status": "exited", "Error": "PRIVATE"}, "Exited observed"),
                                ({"Paused": True}, "Exited paused"), ({"Restarting": True}, "Restarting observed"),
                                ({"Dead": True}, "Dead"), ({"Status": "PRIVATE"}, "Unknown")):
            self.assertEqual(SNAP.container_status(state), expected)

    def test_unit_projection_rejects_foreign_names_and_private_values(self):
        name = SNAP.UNITS["home-core"][0]
        value = ("Id=" + name + "\nLoadState=loaded\nActiveState=failed\nSubState=PRIVATE\n"
                 "Result=PRIVATE\nExecMainStatus=secret\nExecMainStartTimestampMonotonic=-1\n\n"
                 "Id=private.service\nLoadState=loaded\nActiveState=failed").encode()
        result = SNAP.unit_projection(value, SNAP.UNITS["home-core"])
        self.assertEqual(set(result), {name})
        self.assertEqual(result[name]["ActiveState"], "failed")
        self.assertIsNone(result[name]["ExecMainStatus"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_host_metrics_use_fixed_proc_and_root_paths_and_finite_numbers(self):
        calls = []
        def read(path, limit):
            calls.append(path)
            return b"900.50 123.00" if path == Path("/proc/uptime") else b"MemTotal: 50331648 kB\nMemAvailable: 25165824 kB\nPrivate: address\n"
        disk = namedtuple("disk", "total used free")(1024, 512, 512)
        with patch.object(SNAP, "bounded_file", side_effect=read), patch.object(SNAP.shutil, "disk_usage", return_value=disk) as usage:
            result = SNAP.host_metrics()
        self.assertEqual(calls, [Path("/proc/uptime"), Path("/proc/meminfo")])
        usage.assert_called_once_with("/")
        self.assertEqual(result["memory_total_bytes"], 48 * 1024 ** 3)
        self.assertEqual(result["uptime_seconds"], 900.5)
        with patch.object(SNAP, "bounded_file", return_value=b"nan"), patch.object(SNAP.shutil, "disk_usage", side_effect=OSError()):
            self.assertEqual(SNAP.host_metrics(), {})

    def test_reports_reject_private_path_injection_symlinks_fifo_and_oversize(self):
        report = self.root / "report"
        report.write_text('{"schema_version":1}')
        self.assertEqual(SNAP.read_report(report), {"schema_version": 1})
        link = self.root / "link"; link.symlink_to(report)
        with self.assertRaises(OSError): SNAP.read_report(link)
        fifo = self.root / "fifo"; os.mkfifo(fifo)
        with self.assertRaises(ValueError): SNAP.read_report(fifo)
        for value in ("x" * (SNAP.MAX_REPORT + 1), "[]", "[" * 2000 + "]" * 2000):
            report.write_text(value)
            with self.assertRaises(ValueError): SNAP.read_report(report)
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["snapshot", "--ssh"]), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "cat /etc/PRIVATE"}), \
                patch.object(SNAP, "snapshot") as collect, patch.object(SNAP, "bounded_file") as read, redirect_stderr(stderr):
            self.assertEqual(SNAP.main(), 2)
        collect.assert_not_called(); read.assert_not_called()
        self.assertNotIn("PRIVATE", stderr.getvalue())

    def test_model_projection_discards_every_private_and_unrecognized_field(self):
        value = {"schema_version": 1, "mode": "review-only", "generated_at": "2026-10-10T12:00:00Z",
                 "summary": {"changed": 2, "pin_drift": 0, "source_errors": 0, "outperforms_active": 1, "secret": "PRIVATE"},
                 "auth": "PRIVATE", "changes": ["PRIVATE"]}
        projected = SNAP.model_projection(value)
        self.assertNotIn("PRIVATE", json.dumps(projected))
        self.assertEqual(set(projected["summary"]), {"changed", "pin_drift", "source_errors", "outperforms_active"})
        for generated in (None, [], "invalid PRIVATE", "2026-10-10T12:00:00"):
            with self.assertRaises(ValueError):
                SNAP.model_projection(dict(value, generated_at=generated))

    def test_command_timeout_output_budget_and_errors_are_bounded(self):
        self.assertEqual(SNAP.bounded_command([sys.executable, "-c", "print('{}')"], 2, host="home-spark").strip(), b"{}")
        for program, timeout in (("import time;time.sleep(3)", .02), ("print('x'*40000)", 2), ("raise SystemExit(1)", 2)):
            with self.assertRaises(ValueError):
                SNAP.bounded_command([sys.executable, "-c", program], timeout, host="home-spark")

    def publication_context(self):
        stack = ExitStack()
        target = self.root / "public/machine-status.json"
        stack.enter_context(patch.object(SNAP, "PUBLIC_SNAPSHOT", target))
        stack.enter_context(patch.object(SNAP.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(SNAP, "snapshot", return_value={"schema_version": 1, "host": "home-core"}))
        fstat, stat = os.fstat, os.stat
        def root_owned(info):
            values = list(info); values[4] = 0; return os.stat_result(values)
        stack.enter_context(patch.object(SNAP.os, "fstat", side_effect=lambda fd: root_owned(fstat(fd))))
        stack.enter_context(patch.object(SNAP.os, "stat", side_effect=lambda *a, **kw: root_owned(stat(*a, **kw))))
        return stack, target

    def test_core_publication_is_root_only_atomic_and_public_without_residual_temps(self):
        with patch.object(SNAP.os, "geteuid", return_value=1000), self.assertRaises(ValueError):
            SNAP.publish_core()
        stack, target = self.publication_context()
        with stack:
            SNAP.publish_core()
            self.assertEqual(json.loads(target.read_text())["host"], "home-core")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(target.parent.iterdir()), [target])
            target.unlink(); target.symlink_to(self.root / "PRIVATE")
            with self.assertRaises(ValueError): SNAP.publish_core()

    def test_ssh_dispatch_accepts_only_literal_snapshot_or_legacy_report(self):
        for command in ("homecompute-machine-snapshot; id", "homecompute-machine-snapshot --host other", " cat /var/lib/homecompute-maintenance/report.json"):
            with patch.object(sys, "argv", ["snapshot", "--ssh"]), patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}), \
                    redirect_stderr(io.StringIO()), patch.object(SNAP, "snapshot") as collect:
                self.assertEqual(SNAP.main(), 2)
                collect.assert_not_called()

    def test_ssh_snapshot_selects_spark_and_legacy_read_uses_only_fixed_path(self):
        output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with patch.object(sys, "argv", ["snapshot", "--ssh"]), patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "homecompute-machine-snapshot"}), \
                patch.object(SNAP, "snapshot", return_value={"host": "home-spark"}) as collect, patch.object(sys, "stdout", output):
            self.assertEqual(SNAP.main(), 0)
        collect.assert_called_once_with("home-spark")
        output.flush(); self.assertEqual(json.loads(output.buffer.getvalue())["host"], "home-spark")
        output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with patch.object(sys, "argv", ["snapshot", "--ssh"]), patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "cat /var/lib/homecompute-maintenance/report.json"}), \
                patch.object(SNAP, "bounded_file", return_value=b"{}") as read, patch.object(SNAP, "snapshot") as collect, \
                patch.object(sys, "stdout", output):
            self.assertEqual(SNAP.main(), 0)
        read.assert_called_once_with(SNAP.MAINTENANCE, SNAP.MAX_REPORT)
        collect.assert_not_called()

    def test_snapshot_output_budget_drops_raw_report_before_exceeding_bound(self):
        value = {"host": "home-core", "maintenance_report": {"candidates": "x" * (SNAP.MAX_REPORT + 1)}}
        output = SNAP.encode_snapshot(value)
        self.assertLessEqual(len(output), SNAP.MAX_REPORT)
        self.assertIsNone(json.loads(output)["maintenance_report"])


if __name__ == "__main__":
    unittest.main()
