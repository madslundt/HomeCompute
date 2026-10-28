#!/usr/bin/env python3
"""Check model preparation never publishes corrupt or oversized artifacts."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("embeddings_setup", ROOT / "scripts/setup-home-core-embeddings.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.target = Path(self.temporary.name) / "model.safetensors"
        self.payload = b"complete model artifact"
        self.expected = {"size": len(self.payload), "sha256": hashlib.sha256(self.payload).hexdigest()}

    def test_verified_download_is_published_read_only(self):
        with patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)):
            MODULE.download("https://example.test/model", self.target, self.expected)
        self.assertTrue(MODULE.verified(self.target, self.expected))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o444)

    def test_corrupt_download_does_not_replace_existing_artifact(self):
        self.target.write_bytes(b"previous artifact")
        corrupt = b"x" * len(self.payload)
        with patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(corrupt)):
            with self.assertRaisesRegex(ValueError, "verification"):
                MODULE.download("https://example.test/model", self.target, self.expected)
        self.assertEqual(self.target.read_bytes(), b"previous artifact")
        self.assertFalse(self.target.with_suffix(".safetensors.partial").exists())

    def test_oversized_response_is_rejected_before_publication(self):
        with patch.object(MODULE.urllib.request, "urlopen", return_value=io.BytesIO(self.payload + b"x")):
            with self.assertRaisesRegex(ValueError, "size"):
                MODULE.download("https://example.test/model", self.target, self.expected)
        self.assertFalse(self.target.exists())

    def test_symlink_does_not_pass_integrity_check(self):
        actual = self.target.with_name("other")
        actual.write_bytes(self.payload)
        self.target.symlink_to(actual)
        self.assertFalse(MODULE.verified(self.target, self.expected))


if __name__ == "__main__":
    unittest.main()
