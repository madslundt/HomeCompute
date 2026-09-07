#!/usr/bin/env python3
"""Behavioral tests for the legacy compute configuration migration."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATOR = REPO_ROOT / "scripts" / "migrate-compute-config.py"


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.existing = self.root / "existing.env"
        self.template = self.root / "template.env"
        self.output = self.root / "output.env"

    def migrate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(MIGRATOR), str(self.existing), str(self.template), str(self.output)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_preserves_existing_values_and_adds_current_defaults(self) -> None:
        self.existing.write_text(
            "LISTENER_ADDRESS=10.77.10.10\n"
            "MODEL_ID='literal $(touch /tmp/never)'\n"
            "FIREWALL_CONFIRMED=true\n",
            encoding="utf-8",
        )
        self.template.write_text(
            "# Current schema\n"
            "LISTENER_ADDRESS=127.0.0.1\n"
            "MODEL_ID=default/model\n"
            "COMPUTE_HOST_PORTS=8000,8001\n",
            encoding="utf-8",
        )

        result = self.migrate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.output.read_text(encoding="utf-8"),
            "# Current schema\n"
            "LISTENER_ADDRESS=10.77.10.10\n"
            "MODEL_ID='literal $(touch /tmp/never)'\n"
            "COMPUTE_HOST_PORTS=8000,8001\n",
        )

    def test_rejects_unknown_legacy_keys_without_writing_output(self) -> None:
        self.existing.write_text("UNKNOWN_SETTING=value\n", encoding="utf-8")
        self.template.write_text("MODEL_ID=default/model\n", encoding="utf-8")

        result = self.migrate()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown keys: UNKNOWN_SETTING", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_duplicate_keys_without_writing_output(self) -> None:
        self.existing.write_text("MODEL_ID=first\nMODEL_ID=second\n", encoding="utf-8")
        self.template.write_text("MODEL_ID=default/model\n", encoding="utf-8")

        result = self.migrate()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate key", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
