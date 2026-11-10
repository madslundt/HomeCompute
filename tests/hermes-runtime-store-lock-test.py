#!/usr/bin/env python3
"""Guard the managed Hermes runtime-store lock's narrow ownership."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / 'deploy/hermes/Dockerfile.latest').read_text()
RUNBOOK = (ROOT / 'deploy/hermes/latest/README.md').read_text()


class HermesRuntimeStoreLockTest(unittest.TestCase):
    def test_image_precreates_only_the_runtime_lock_for_sandbox(self):
        self.assertIn(
            'install -o sandbox -g sandbox -m 0600 /dev/null /run/nemoclaw/.install.lock',
            DOCKERFILE,
        )
        self.assertIn(
            'os.open("/run/nemoclaw/.install.lock", os.O_CREAT | os.O_RDWR, 0o600)',
            DOCKERFILE,
        )
        self.assertNotIn('chmod 0777 /run/nemoclaw', DOCKERFILE)
        self.assertNotIn('chown sandbox:sandbox /run/nemoclaw ', DOCKERFILE)

    def test_runbook_records_runtime_reason_and_boundary(self):
        self.assertIn('HERMES_RUNTIME_DIR', RUNBOOK)
        self.assertIn('Keep that shared directory root-owned', RUNBOOK)
        self.assertIn('without granting the sandbox write access', RUNBOOK)


if __name__ == '__main__':
    unittest.main()
