#!/usr/bin/env python3
"""Keep a busy-writer backup deferral distinct from switch failure."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/deploy-home-core.sh").read_text()


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


if __name__ == "__main__":
    unittest.main()
