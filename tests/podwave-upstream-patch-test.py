#!/usr/bin/env python3
"""Tests the pinned PodWave reasoning-model probe compatibility patch."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("podwave_patch", ROOT / "deploy/podwave/patch-upstream.py")
PATCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PATCH)


class PodWavePatchTests(unittest.TestCase):
    def test_patch_expands_only_the_test_probe_output_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="podwave-patch-test-") as temp:
            path = Path(temp) / "podwave.py"
            path.write_text('body = {"temperature": 0, "max_tokens": 16}\nother = 16\n')
            self.assertTrue(PATCH.apply(path))
            self.assertEqual(path.read_text(), 'body = {"temperature": 0, "max_tokens": 128}\nother = 16\n')
            self.assertFalse(PATCH.apply(path))

    def test_patch_refuses_upstream_source_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="podwave-patch-drift-") as temp:
            path = Path(temp) / "podwave.py"
            path.write_text('body = {"temperature": 0, "max_tokens": 32}\n')
            with self.assertRaises(SystemExit):
                PATCH.apply(path)


if __name__ == "__main__":
    unittest.main()
