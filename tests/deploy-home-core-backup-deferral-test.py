#!/usr/bin/env python3
"""Keep a busy-writer backup deferral distinct from switch failure."""
from pathlib import Path
import importlib.util
import os
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/deploy-home-core.sh").read_text()
spec = importlib.util.spec_from_file_location(
    "platform_health_backup_deferral", ROOT / "scripts/platform-health-backup-deferral.py"
)
deferral = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deferral)


HEALTHY_METRICS = {
    "homecompute_edge_process_healthy",
    "homecompute_n8n_ready",
    "homecompute_n8n_edge_ready",
    "homecompute_wyoming_tts_ready",
    "homecompute_wyoming_stt_ready",
    "homecompute_text_ready",
    "homecompute_embedding_ready",
    "homecompute_stt_danish_ready",
    "homecompute_stt_multilingual_ready",
    "homecompute_tts_ready",
    "homecompute_nas_check_ready",
}


def metric_bytes(overrides=None):
    values = {key: 1 for key in HEALTHY_METRICS}
    values.update({
        "homecompute_platform_ready": 1,
        "homecompute_nas_backup_ready": 1,
        "homecompute_gx10_model_ready": 1,
        "homecompute_gx10_text_ready": 1,
        "homecompute_gx10_embedding_ready": 1,
        "homecompute_gx10_stt_ready": 1,
        "homecompute_gx10_tts_ready": 1,
        "homecompute_gx10_wyoming_ready": 1,
    })
    values.update(overrides or {})
    return "".join(f"{key} {value}\n" for key, value in sorted(values.items())).encode()


class DeployBackupDeferralTest(unittest.TestCase):
    def test_continues_only_after_exact_generation_and_idle_deferred_unit(self):
        self.assertIn('expected_system=$(readlink -f /var/lib/homecompute/result)', SCRIPT)
        self.assertIn('active_system=$(readlink -f /run/current-system)', SCRIPT)
        self.assertIn('"$backup_active" == activating', SCRIPT)
        self.assertIn('"$backup_substate" == auto-restart', SCRIPT)
        self.assertIn('"$backup_status" == 75', SCRIPT)
        self.assertIn('"$backup_pid" == 0', SCRIPT)
        self.assertIn('-z "$failed_units"', SCRIPT)
        self.assertIn('exit 1', SCRIPT)
        self.assertIn('its scheduled retry remains enabled', SCRIPT)

    def test_deployer_allows_only_the_known_backup_readiness_failure(self):
        self.assertIn('"$failed_units" == homecompute-platform-health.service', SCRIPT)
        self.assertIn('platform-health-backup-deferral.py', SCRIPT)
        self.assertIn('scheduled retry remains enabled', SCRIPT)

    def test_metrics_accept_only_fresh_nas_backup_readiness_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            path = Path(temporary) / "health.prom"
            path.write_bytes(metric_bytes({
                "homecompute_platform_ready": 0,
                "homecompute_nas_backup_ready": 0,
            }))
            path.chmod(0o644)
            self.assertTrue(deferral.only_backup_deferred(path))

            path.write_bytes(metric_bytes({
                "homecompute_platform_ready": 0,
                "homecompute_nas_backup_ready": 0,
                "homecompute_text_ready": 0,
            }))
            self.assertFalse(deferral.only_backup_deferred(path))

    def test_metrics_fail_closed_on_missing_stale_or_linked_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            path = Path(temporary) / "health.prom"
            path.write_bytes(metric_bytes({
                "homecompute_platform_ready": 0,
                "homecompute_nas_backup_ready": 0,
            }))
            path.chmod(0o644)
            self.assertFalse(deferral.only_backup_deferred(path, now=path.stat().st_mtime + 181))
            self.assertFalse(deferral.only_backup_deferred(path, expected_uid=path.stat().st_uid + 1))
            path.chmod(0o666)
            self.assertFalse(deferral.only_backup_deferred(path))
            path.chmod(0o644)
            path.write_bytes(b"homecompute_platform_ready 0\nhomecompute_nas_backup_ready 0\n")
            self.assertFalse(deferral.only_backup_deferred(path))
            linked = Path(temporary) / "linked.prom"
            linked.symlink_to(path)
            self.assertFalse(deferral.only_backup_deferred(linked))


if __name__ == "__main__":
    unittest.main()
