#!/usr/bin/env python3
"""Behavioral tests for bounded Hugging Face snapshot acquisition."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "model_cache_integrity", REPO_ROOT / "scripts" / "model-cache-integrity.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REVISION = "1" * 40


class FakeApi:
    sizes = [1]

    def model_info(self, *, repo_id, revision, files_metadata, token):
        del repo_id, files_metadata, token
        siblings = [
            types.SimpleNamespace(size=size, rfilename=f"file-{index}")
            for index, size in enumerate(self.sizes)
        ]
        return types.SimpleNamespace(sha=revision, siblings=siblings)


def fake_snapshot_download(*, repo_id, revision, token, cache_dir):
    del repo_id, token
    destination = Path(cache_dir) / "models--owner--model" / "snapshots" / revision
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "model.bin").write_bytes(b"bounded")


def fake_cumulative_overflow_download(*, repo_id, revision, token, cache_dir):
    del repo_id, token
    destination = Path(cache_dir) / "models--owner--model" / "snapshots" / revision
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "first.bin").write_bytes(b"one")
    (destination / "second.bin").write_bytes(b"two")


class BoundedFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.cache = Path(self.temporary_directory.name)
        self.fake_module = types.SimpleNamespace(
            HfApi=FakeApi,
            snapshot_download=fake_snapshot_download,
        )

    def fetch(self, *, artifact_bytes=8192, artifact_files=8) -> None:
        with patch.dict(sys.modules, {"huggingface_hub": self.fake_module}):
            MODULE.bounded_fetch(
                str(self.cache),
                "owner/model",
                [REVISION],
                artifact_bytes,
                artifact_files,
                1024 * 1024,
                100,
                1,
            )

    def test_rejects_metadata_over_byte_limit_before_download(self) -> None:
        FakeApi.sizes = [600, 600]
        with self.assertRaisesRegex(RuntimeError, "artifact metadata exceeds tuple limit"):
            self.fetch(artifact_bytes=1000)
        self.assertFalse((self.cache / "hub").exists())

    def test_rejects_metadata_over_file_limit_before_download(self) -> None:
        FakeApi.sizes = [1, 1, 1]
        with self.assertRaisesRegex(RuntimeError, "artifact metadata exceeds tuple limit"):
            self.fetch(artifact_files=2)
        self.assertFalse((self.cache / "hub").exists())

    def test_rejects_actual_cumulative_download_over_byte_limit(self) -> None:
        FakeApi.sizes = [1, 1]
        fake_module = types.SimpleNamespace(
            HfApi=FakeApi,
            snapshot_download=fake_cumulative_overflow_download,
        )
        with patch.dict(sys.modules, {"huggingface_hub": fake_module}):
            with self.assertRaisesRegex(
                RuntimeError, "artifact download storage budget exceeded"
            ):
                MODULE.bounded_fetch(
                    str(self.cache),
                    "owner/model",
                    [REVISION],
                    4096,
                    8,
                    1024 * 1024,
                    100,
                    1,
                )

    def test_cli_parses_fetch_arguments_before_validation(self) -> None:
        result = subprocess.run(
            [
                str(REPO_ROOT / "scripts" / "model-cache-integrity.py"),
                "fetch",
                "--cache-root",
                str(self.cache),
                "--repo-id",
                "owner/model",
                "--revision",
                REVISION,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("fetch requires every storage limit", result.stderr)
        self.assertNotIn("NameError", result.stderr)

    def test_download_runs_under_storage_budgets(self) -> None:
        FakeApi.sizes = [7]
        self.fetch()
        downloaded = (
            self.cache
            / "hub"
            / "models--owner--model"
            / "snapshots"
            / REVISION
            / "model.bin"
        )
        self.assertEqual(downloaded.read_bytes(), b"bounded")


if __name__ == "__main__":
    unittest.main()
