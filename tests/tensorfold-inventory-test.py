#!/usr/bin/env python3
"""Protect the migration inventory's credential boundary without a live host."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "inventory", Path(__file__).resolve().parents[1] / "scripts/tensorfold_inventory.py"
)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class InventoryTests(unittest.TestCase):
    def test_arguments_exclude_credentials_and_unreviewed_content(self):
        result = inventory.safe_args([
            "--model", "example/model", "--api-key", "secret", "--prompt", "private",
            "--max-model-len=262144", "--served-model-name", "automation-qualification",
        ])
        self.assertEqual(result, {
            "--model": "example/model", "--max-model-len": "262144",
            "--served-model-name": "automation-qualification",
        })
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private", str(result))

    def test_environment_allowlist_excludes_secret_keys(self):
        for key in ("API_KEY", "TENSORFOLD_API_KEY", "HF_TOKEN", "DATABASE_URL"):
            self.assertNotIn(key, inventory.SAFE_ENV)


if __name__ == "__main__":
    unittest.main()
