#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import re
import shutil
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/homecompute.py"
SPEC = importlib.util.spec_from_file_location("homecompute", MODULE_PATH)
hc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hc)


class HomeComputeCliTests(unittest.TestCase):
    def test_sha_validation(self):
        good = "a" * 40
        self.assertEqual(hc.valid_sha(good), good)
        for bad in ("a" * 39, "A" * 40, "../" + "a" * 37, "b" * 41):
            with self.assertRaises(Exception):
                hc.valid_sha(bad)

    def test_model_inventory_reads_both_canonical_sources(self):
        catalog = hc.load_json(hc.ROOT / "config/model-catalog.json")
        roster = hc.load_json(hc.ROOT / "config/gb10-model-roster.json")
        rows = hc.model_inventory(catalog, roster)
        self.assertTrue(any(row["id"] == "automation-spark-primary" for row in rows))
        self.assertTrue(any(row["id"] == "heavy" for row in rows))
        self.assertTrue(any(row["id"] == "stt_danish" for row in rows))

    def test_drift_requires_observed_revision(self):
        self.assertEqual(hc.compare_drift("a" * 40, None), "unknown")
        self.assertEqual(hc.compare_drift("a" * 40, {"revision": None}), "unknown")
        self.assertEqual(hc.compare_drift("a" * 40, {"revision": "a" * 40}), "current")
        self.assertEqual(hc.compare_drift("a" * 40, {"revision": "b" * 40}), "drifted")

    def test_update_classification_separates_application_and_platform(self):
        observed = {"model_update_report": {"summary": {"changed": 2, "pin_drift": 1, "source_errors": 0}},
                    "platform_updates": "nvidia-driver package candidate"}
        updates = hc.classify_updates(observed)
        self.assertEqual(len(updates["application"]), 2)
        self.assertEqual(updates["platform"], ["DGX OS package candidates reported by apt"])

    def test_remote_output_rejects_malformed_json_or_host(self):
        with self.assertRaises(hc.OperatorError):
            hc.parse_remote_output("not json", "home-core")
        with self.assertRaises(hc.OperatorError):
            hc.parse_remote_output(json.dumps({"schema_version": 1, "host": "other", "containers": [], "failed_units": []}), "home-core")

    def test_revision_falls_back_to_immutable_release_pointer(self):
        sha = "e" * 40
        payload = {"schema_version": 1, "host": "home-spark", "revision": None,
                   "current_target": f"/srv/homecompute/releases/{sha}",
                   "containers": None, "failed_units": []}
        self.assertEqual(hc.parse_remote_output(json.dumps(payload), "home-spark")["revision"], sha)

    def test_unreachable_host_is_clear_and_strict_host_key_is_enabled(self):
        def runner(argv, **kwargs):
            self.assertIn("StrictHostKeyChecking=yes", argv)
            return subprocess.CompletedProcess(argv, 255, "", "No route to host")
        with self.assertRaisesRegex(hc.OperatorError, "unreachable"):
            hc.remote_status("home-spark", runner)

    def test_partial_deploy_stops_before_second_host(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["git", "-C", str(hc.ROOT)]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            remote = argv[-1]
            if remote == "bash -s":
                host = argv[-2]
                payload = {"schema_version": 1, "host": host, "containers": [], "failed_units": [],
                           "required_tools": {"git": True, "docker": True, "docker-compose-plugin": True,
                                              "docker-access": True, "sudo-nopasswd": True, "sudo": True, "nix": True, "python3": True,
                                              "nixos-rebuild": True, "flock": True, "jq": True,
                                              "curl": True, "awk": True, "sha256sum": True, "realpath": True, "cmp": True,
                                              "nvidia-smi": True, "nvidia-ctk": True, "nvidia-container-cli": True}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            return subprocess.CompletedProcess(argv, 1, "", "guarded install failed")
        sha = "c" * 40
        rc = hc.main(["deploy", "all", "--revision", sha], runner)
        self.assertEqual(rc, 2)
        deploy_calls = [call for call in calls if "bash -s -- " in call[-1]]
        self.assertEqual(len(deploy_calls), 1)
        self.assertIn("home-spark", deploy_calls[0])

    def test_json_status_has_stable_top_level_shape(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["git", "ls-remote", "origin"]:
                return subprocess.CompletedProcess(argv, 0, "d" * 40 + "\trefs/heads/master\n", "")
            host = argv[-2]
            payload = {"schema_version": 1, "host": host, "revision": "d" * 40,
                       "containers": [], "failed_units": [], "os": "test", "current_target": None,
                       "gpu": None, "platform_updates": None, "required_tools": {}, "model_update_report": None}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with redirect_stdout(output):
            rc = hc.main(["status", "--json"], runner)
        self.assertEqual(rc, 0)
        self.assertEqual(set(json.loads(output.getvalue())), {"schema_version", "desired_revision", "hosts", "unverified"})

    def test_update_reader_notifier_and_systemd_state_use_one_directory(self):
        module = (hc.ROOT / "modules/nixos/model-update-monitor.nix").read_text()
        state_name = re.search(r'stateDirectoryName = "([^"]+)";', module).group(1)
        expected_path = "/var/lib/" + state_name
        self.assertIn('StateDirectory = stateDirectoryName;', module)
        self.assertIn('stateDirectory = "/var/lib/${stateDirectoryName}";', module)
        self.assertIn('reportFile = "${stateDirectory}/report.json";', module)
        self.assertIn('report=\'${reportFile}\'', module)
        self.assertIn('ReadWritePaths = [ stateDirectory ];', module)
        self.assertIn("report_file=" + expected_path + "/report.json", hc.REMOTE_STATUS)
        self.assertNotIn("/var/lib/homecompute/model-update-check", module + hc.REMOTE_STATUS)

    @unittest.skipUnless(shutil.which("jq"), "jq required for the real shell projection")
    def test_real_shell_report_reader_selects_summary_without_private_payloads(self):
        snippet = re.search(r'update_report=null\n(.*?)\ntools=', hc.REMOTE_STATUS, re.S).group(1)
        snippet = re.sub(r'^report_file=.*$', 'report_file="$1"', snippet, flags=re.M)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text(json.dumps({"schema_version": 1, "document_type": "model_update_report",
                                        "mode": "review-only", "status": "attention",
                                        "generated_at": "2026-10-09T12:00:00Z",
                                        "summary": {"changed": 7, "pin_drift": 1, "source_errors": 0, "outperforms_active": 0},
                                        "changes": [{"private": "SECRET"}], "source_errors": [{"message": "PRIVATE"}]}))
            result = subprocess.run(["bash", "-c", snippet + '\nprintf %s "$update_report"', "reader", str(path)],
                                    text=True, capture_output=True, check=True)
            projection = json.loads(result.stdout)
            self.assertEqual(projection["summary"]["changed"], 7)
            self.assertNotIn("SECRET", result.stdout)
            self.assertNotIn("PRIVATE", result.stdout)
            self.assertNotIn("changes", projection)
            path.write_text("invalid JSON SECRET")
            result = subprocess.run(["bash", "-c", snippet + '\nprintf %s "$update_report"', "reader", str(path)],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, "null")
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
