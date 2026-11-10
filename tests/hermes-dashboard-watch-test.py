#!/usr/bin/env python3
"""Static checks for the Hermes authenticated dashboard recovery watcher."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNIT = (ROOT / 'deploy/hermes/hermes-dashboard-watch.service.in').read_text()
SETUP = (ROOT / 'scripts/setup-hermes-ha-api.py').read_text()


class HermesDashboardWatchTest(unittest.TestCase):
    def test_watcher_is_not_canceled_when_hermes_startup_retries(self):
        self.assertIn('Wants=homecompute-hermes-personal.service', UNIT)
        self.assertNotIn('Requires=homecompute-hermes-personal.service', UNIT)
        self.assertIn('After=default.target homecompute-hermes-personal.service', UNIT)
        self.assertIn('Restart=on-failure', UNIT)

    def test_watcher_only_refresh_preserves_owner_sandbox(self):
        self.assertIn("parser.add_argument('--refresh-watcher', action='store_true'", SETUP)
        self.assertIn("'owner_sandbox_restarted': False", SETUP)
        self.assertIn('refresh_watcher(args.revision)', SETUP)
        refresh = SETUP.split('def refresh_watcher', 1)[1].split('\ndef verify_api_profile', 1)[0]
        self.assertIn('update_watcher(revision)', refresh)
        self.assertIn('homecompute-hermes-dashboard-watch.service', refresh)
        self.assertNotIn("call(['nemohermes', NAME, 'stop']", refresh)
        self.assertNotIn("call(['nemohermes', NAME, 'start']", refresh)


if __name__ == '__main__':
    unittest.main()
