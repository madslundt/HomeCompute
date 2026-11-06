"""Bounded public artifact staging and cleanup; synthetic bytes, no network."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("qwen_artifacts", ROOT / "scripts/qwen_asr_artifacts.py")
ART = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ART)
import qwen_asr_guard as guard


class Response(io.BytesIO):
    url = "https://huggingface.co/synthetic"


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.source = ROOT / "deploy/qwen-asr-shadow"
        self.raw = b"synthetic model bytes"
        self.manifest = {"model_repository": "synthetic/model", "model_snapshot": "a" * 40,
                         "files": [{"path": "model.safetensors", "size": len(self.raw),
                                    "sha256": hashlib.sha256(self.raw).hexdigest()}]}
        self.memory = patch.object(guard, "memory", return_value={})
        self.memory.start(); self.addCleanup(self.memory.stop)

    def test_exact_download_is_verified_and_published_read_only(self):
        with patch.object(ART.urllib.request, "urlopen", return_value=Response(self.raw)) as fetch:
            result = ART.provision(self.state, self.source, self.manifest)
        self.assertEqual(result["model_snapshot"], "a" * 40)
        directory = self.state / ("model-" + "a" * 40)
        self.assertEqual((directory / "model.safetensors").read_bytes(), self.raw)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o555)
        self.assertEqual((directory / "model.safetensors").stat().st_mode & 0o777, 0o444)
        self.assertEqual(fetch.call_count, 1)
        request = fetch.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)

    def test_truncated_oversized_and_wrong_digest_downloads_are_not_published(self):
        for raw in (self.raw[:-1], self.raw + b"x", b"x" * len(self.raw)):
            with self.subTest(length=len(raw)), patch.object(ART.urllib.request, "urlopen", return_value=Response(raw)):
                with self.assertRaises((ValueError, RuntimeError)):
                    ART.provision(self.state, self.source, self.manifest)
                self.assertEqual(list(self.state.iterdir()), [])

    def test_cached_corruption_is_refused_without_network_repair(self):
        directory = self.state / ("model-" + "a" * 40)
        directory.mkdir(); (directory / "model.safetensors").write_bytes(b"x" * len(self.raw))
        with patch.object(ART.urllib.request, "urlopen") as fetch:
            with self.assertRaises(RuntimeError):
                ART.provision(self.state, self.source, self.manifest)
        fetch.assert_not_called()

    def test_guard_failure_leaves_no_published_model(self):
        with patch.object(guard, "memory", side_effect=guard.MonitorUnavailable("synthetic")), \
                patch.object(ART.urllib.request, "urlopen", return_value=Response(self.raw)):
            with self.assertRaises(guard.MonitorUnavailable):
                ART.provision(self.state, self.source, self.manifest)
        self.assertEqual(list(self.state.iterdir()), [])

    def tearDown(self):
        # Public cache is intentionally immutable; allow the temporary test owner to remove it.
        for path in self.state.iterdir():
            if path.is_dir():
                path.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
