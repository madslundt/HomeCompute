#!/usr/bin/env python3
"""Monitoring boundaries, deduplication, stale evidence and failure recovery."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("system_monitoring", ROOT / "scripts/observe-homecompute.py")
MONITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MONITOR)
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def registry():
    return MONITOR.validate_registry(json.loads((ROOT / "config/system-monitoring.json").read_text()))


def snapshot(system, *, failing=False):
    return {"schema_version": 1, "host": system["target"], "failed_units": [],
            "unit_states": {u["name"]: {"LoadState": "loaded", "ActiveState": "active" if u["kind"] == "persistent" else "inactive",
                             "SubState": "running" if u["kind"] == "persistent" else "dead", "Result": "success",
                             "ExecMainStatus": "0", "ExecMainStartTimestampMonotonic": "1"} for u in system["units"]},
            "containers": [{"name": s["name"], "status": "Restarting (1) 3 seconds ago" if failing else "Up 8 days (healthy)"}
                           for s in system["containers"]],
            "platform_updates": None,
            "model_update_report": {"schema_version": 1, "mode": "review-only", "generated_at": MONITOR.iso(NOW),
                                    "summary": {"changed": 0, "pin_drift": 0, "source_errors": 0, "outperforms_active": 0}}}


def fixtures(reg):
    return {s["id"]: snapshot(s) for s in reg["systems"] if s["collector"] == "homecompute-status"}


class MonitoringTest(unittest.TestCase):
    def test_registry_cannot_supply_commands_hosts_or_automatic_actions(self):
        for mutation in (lambda r: r["systems"][0].update(command="sudo reboot"),
                         lambda r: r["systems"][0].update(target="-oProxyCommand=bad"),
                         lambda r: r.update(automatic_actions=True),
                         lambda r: r.update(physical_device_actions=True),
                         lambda r: r["systems"][2].update(enabled=True)):
            reg = registry(); mutation(reg)
            with self.assertRaises(ValueError): MONITOR.validate_registry(reg)

    def test_projection_omits_secrets_logs_and_unlisted_services(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"].update(api_key="SECRET", logs="PRIVATE", household="OCCUPANCY")
        data["home-core"]["containers"].append({"name": "PRIVATE", "status": "PRIVATE"})
        data["home-core"]["model_update_report"].update(changes=[{"credential": "SECRET"}])
        report = MONITOR.make_report(reg, data, NOW)
        serialized = json.dumps(report)
        for secret in ("SECRET", "PRIVATE", "OCCUPANCY"):
            self.assertNotIn(secret, serialized)
        self.assertFalse(report["automatic_actions"])
        self.assertFalse(report["physical_device_actions"])

    def test_unchanged_findings_are_quiet_and_have_same_episode(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"] = snapshot(reg["systems"][0], failing=True)
        first = MONITOR.make_report(reg, data, NOW)
        next_report = MONITOR.make_report(reg, data, NOW + timedelta(minutes=1), first)
        self.assertEqual(next_report["changes"], [])
        self.assertEqual([x["episode_key"] for x in first["findings"]],
                         [x["episode_key"] for x in next_report["findings"]])

    def test_collection_failure_cannot_resolve_known_incidents(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"] = snapshot(reg["systems"][0], failing=True)
        first = MONITOR.make_report(reg, data, NOW)
        data["home-core"] = None
        failed = MONITOR.make_report(reg, data, NOW + timedelta(minutes=20), first)
        old = [x for x in first["findings"] if x["status"] == "degraded"]
        carried = {x["stable_key"]: x for x in failed["findings"]}
        for finding in old:
            self.assertEqual(carried[finding["stable_key"]], finding)
        self.assertFalse(any(x["transition"] == "resolved" for x in failed["changes"]))

    def test_recovery_resolves_and_recurrence_opens_new_episode(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"] = snapshot(reg["systems"][0], failing=True)
        first = MONITOR.make_report(reg, data, NOW)
        recovered = MONITOR.make_report(reg, fixtures(reg), NOW + timedelta(minutes=1), first)
        self.assertTrue(any(x["transition"] == "resolved" for x in recovered["changes"]))
        recurrence = MONITOR.make_report(reg, data, NOW + timedelta(minutes=2), recovered)
        original = {x["stable_key"]: x for x in first["findings"]}
        for finding in recurrence["findings"]:
            if finding["status"] == "degraded":
                self.assertNotEqual(finding["episode_key"], original[finding["stable_key"]]["episode_key"])

    def test_stale_update_report_is_unknown_not_upgrade_or_current(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"]["model_update_report"]["generated_at"] = "2026-09-01T12:00:00Z"
        report = MONITOR.make_report(reg, data, NOW)
        observation = next(x for x in report["observations"] if x["check_id"] == "model-update-report")
        self.assertEqual(observation["status"], "unknown")
        self.assertEqual(observation["evidence"], {"reason": "stale"})

    def test_unknown_schema_does_not_claim_health(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"]["schema_version"] = 77
        report = MONITOR.make_report(reg, data, NOW)
        core = [x for x in report["observations"] if x["system_id"] == "home-core"]
        self.assertEqual(len(core), 1)
        self.assertEqual(core[0]["status"], "unknown")

    def test_update_health_separate_and_no_latest_tag_comparison(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"]["model_update_report"]["summary"]["changed"] = 7
        for service in data["home-core"]["containers"]: service["image"] = "application:latest"
        report = MONITOR.make_report(reg, data, NOW)
        check = next(x for x in report["observations"] if x["check_id"] == "model-update-report.changed")
        self.assertEqual(check["category"], "updates")
        self.assertEqual(check["evidence"], {"count": 7, "source_report_generated_at": MONITOR.iso(NOW)})
        self.assertEqual(check["expires_at"], MONITOR.iso(NOW + timedelta(seconds=691200)))
        self.assertTrue(all(x["status"] == "healthy" for x in report["observations"] if x["check_id"].startswith("container.") and x["system_id"] == "home-core"))
        self.assertNotIn("latest", json.dumps(report))

    def test_empty_cached_apt_inventory_remains_unknown(self):
        reg = registry(); report = MONITOR.make_report(reg, fixtures(reg), NOW)
        check = next(x for x in report["observations"] if x["check_id"] == "cached-apt-candidates")
        self.assertEqual(check["status"], "unknown")
        self.assertEqual(check["evidence"]["reason"], "cached_inventory_only")

    def test_ha_projection_only_version_update_and_unavailable_counters(self):
        reg = registry(); data = fixtures(reg)
        data["home-assistant"] = {"schema_version": 1, "host": "home-assistant", "installed_version": "2026.10.1",
                                  "update_available_count": 2, "unavailable_count": 3,
                                  "states": [{"state": "OCCUPANCY", "attributes": {"token": "SECRET"}}]}
        report = MONITOR.make_report(reg, data, NOW)
        serialized = json.dumps(report)
        self.assertNotIn("OCCUPANCY", serialized); self.assertNotIn("SECRET", serialized)
        checks = [x for x in report["observations"] if x["system_id"] == "home-assistant"]
        self.assertEqual(len(checks), 4)
        self.assertTrue(any(x["check_id"] == "unavailable_count" and x["evidence"] == {"count": 3} for x in checks))

    def test_previous_report_rejects_unprojected_data(self):
        reg = registry(); data = fixtures(reg)
        prior = MONITOR.make_report(reg, data, NOW)
        for key, value in (("evidence", {"attributes": "SECRET"}), ("system_id", "SECRET"), ("episode_key", "SECRET")):
            hostile = copy.deepcopy(prior); hostile["findings"][0][key] = value
            with self.assertRaises(ValueError): MONITOR.make_report(reg, data, NOW, hostile)

    def test_live_collector_reuses_fixed_ssh_and_narrow_unit_projection(self):
        system = registry()["systems"][0]
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"schema_version": 1}), "SECRET")
        with patch.object(MONITOR.homecompute, "remote_status", return_value=snapshot(system)):
            raw = MONITOR.collect(system, runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][-2:], ["home-core", "python3 -"])
        self.assertIn("StrictHostKeyChecking=yes", calls[0][0])
        self.assertIn("homecompute-agents-vm.service", calls[0][1]["input_text"])
        self.assertIn("systemctl','show'", calls[0][1]["input_text"])
        self.assertNotIn("SECRET", json.dumps(raw))

    def test_disabled_ha_never_uses_transport(self):
        def forbidden(*args, **kwargs): raise AssertionError("HA transport invoked")
        self.assertIsNone(MONITOR.collect(registry()["systems"][2], forbidden))

    def test_disabled_ha_has_not_provisioned_reason(self):
        report = MONITOR.make_report(registry(), {}, NOW)
        row = next(x for x in report["observations"] if x["system_id"] == "home-assistant")
        self.assertEqual(row["evidence"], {"reason": "not_provisioned"})

    def test_no_failed_units_does_not_prove_stopped_vm_is_healthy(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"]["unit_states"]["homecompute-agents-vm.service"]["ActiveState"] = "inactive"
        report = MONITOR.make_report(reg, data, NOW)
        row = next(x for x in report["observations"] if x["check_id"] == "unit.homecompute-agents-vm.service")
        self.assertEqual(row["status"], "degraded")
        del data["home-core"]["unit_states"]
        report = MONITOR.make_report(reg, data, NOW)
        row = next(x for x in report["observations"] if x["check_id"] == "unit.homecompute-agents-vm.service")
        self.assertEqual(row["status"], "unknown")

    def test_oneshot_never_run_or_running_is_unknown(self):
        reg = registry(); data = fixtures(reg)
        unit = data["home-core"]["unit_states"]["homecompute-model-update-monitor.service"]
        unit["ExecMainStartTimestampMonotonic"] = "0"
        report = MONITOR.make_report(reg, data, NOW)
        row = next(x for x in report["observations"] if x["check_id"] == "unit.homecompute-model-update-monitor.service")
        self.assertEqual(row["evidence"], {"reason": "never_started"})
        unit.update(ExecMainStartTimestampMonotonic="1", ActiveState="activating")
        report = MONITOR.make_report(reg, data, NOW)
        row = next(x for x in report["observations"] if x["check_id"] == "unit.homecompute-model-update-monitor.service")
        self.assertEqual(row["status"], "unknown")

    def test_new_upstream_report_with_same_count_is_not_suppressed(self):
        reg = registry(); data = fixtures(reg)
        data["home-core"]["model_update_report"]["summary"]["changed"] = 7
        first = MONITOR.make_report(reg, data, NOW)
        data["home-core"]["model_update_report"]["generated_at"] = MONITOR.iso(NOW + timedelta(minutes=1))
        second = MONITOR.make_report(reg, data, NOW + timedelta(minutes=1), first)
        change = next(x for x in second["changes"] if x["stable_key"] == "home-core:model-update-report.changed")
        self.assertEqual(change["transition"], "changed")


if __name__ == "__main__":
    unittest.main()
