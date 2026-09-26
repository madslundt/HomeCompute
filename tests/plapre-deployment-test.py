#!/usr/bin/env python3
"""Static and rendered-Compose tests for the opt-in Plapre deployment."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compute-node" / "plapre" / "compose.yaml"
ENV_FILE = ROOT / "config" / "plapre-tts.env.example"
DOCKERFILE = ROOT / "deploy" / "compute-node" / "plapre" / "Dockerfile"


class PlapreDeploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(ENV_FILE), "-f", str(COMPOSE), "config", "--format", "json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.document = json.loads(result.stdout)

    def test_only_wyoming_edge_is_published(self) -> None:
        services = self.document["services"]
        self.assertNotIn("ports", services["plapre-primary"])
        self.assertNotIn("ports", services["plapre-wyoming"])
        self.assertEqual(services["plapre-edge"]["ports"][0]["published"], "10201")
        self.assertEqual(services["plapre-edge"]["ports"][0]["target"], 10201)
        self.assertEqual(set(services["plapre-primary"]["networks"]), {"inference"})
        self.assertEqual(set(services["plapre-wyoming"]["networks"]), {"inference"})
        self.assertEqual(set(services["plapre-edge"]["networks"]), {"edge", "inference"})
        self.assertTrue(self.document["networks"]["inference"]["internal"])

    def test_runtime_is_offline_least_privilege_and_pinned(self) -> None:
        services = self.document["services"]
        for name in services:
            service = services[name]
            self.assertTrue(service["read_only"], name)
            self.assertEqual(service["cap_drop"], ["ALL"], name)
            self.assertIn("no-new-privileges:true", service["security_opt"], name)
            self.assertNotEqual(service.get("network_mode"), "host", name)
            self.assertFalse(service.get("privileged", False), name)
        primary = services["plapre-primary"]
        self.assertEqual(primary["environment"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(primary["environment"]["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(primary["environment"]["USER"], "plapre")
        self.assertEqual(primary["environment"]["LOGNAME"], "plapre")
        self.assertEqual(primary["environment"]["TORCHINDUCTOR_CACHE_DIR"], "/var/cache/triton/torchinductor")
        self.assertEqual(primary["environment"]["TRITON_CACHE_DIR"], "/var/cache/triton")
        self.assertEqual(primary["environment"]["PLAPRE_GB10_HYBRID_DECODE"], "1")
        self.assertEqual(primary["environment"]["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(primary["environment"]["OMP_NUM_THREADS"], "2")
        self.assertIn("/var/cache/triton:rw,exec,nosuid,nodev,size=1g", primary["tmpfs"])
        command = " ".join(primary["command"])
        self.assertIn("/opt/plapre/models/plapre", command)
        self.assertNotIn("syvai/plapre-nano-v2", command)
        self.assertEqual(services["plapre-wyoming"]["environment"]["PLAPRE_VOICE_ALIAS"], "danish-default")
        self.assertEqual(services["plapre-wyoming"]["environment"]["PLAPRE_SPEAKER_ID"], "tor")
        self.assertEqual(services["plapre-wyoming"]["environment"]["PLAPRE_TEMPO"], "1.25")
        self.assertIn("--tempo=1.25", services["plapre-wyoming"]["command"])

    def test_image_build_closes_mutable_revision_gaps(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        expected = (
            "sha256:604e5b052d1ce1b87952c72bf95cc637192c0051fc15a32886dff20bffd5c514",
            "34c52a67e8941bbd8e6adaca0eb0b9eabec11d78",
            "b111239d3b099cfafcb54b3471a8e9e9ba71ae8b",
            "007e0b471e377dd5061786f7df6ad659d15c4d5f",
            "961f20bf892c59f391d0b6c5f7b88e70ed919b99",
            "cb2c8f10959ff0e5d3e97c9b82fcc3779c9532a5",
            "eec1ae6c79877dbd9379285cf8789c9e0879293d",
            "3386cc880324d4e98e05987b99107f49e40ed925b8ecc87c1f4939432d429879",
        )
        for pin in expected:
            self.assertIn(pin, text)
        self.assertIn('vllm.__version__.startswith("0.15.1")', text)
        self.assertIn('torchaudio.__version__ == "2.11.0+nv26.2"', text)
        self.assertNotIn("encodec==", text)
        self.assertNotIn("vocos==", text)
        self.assertIn("scipy==1.18.1", text)
        self.assertIn("ffmpeg libsndfile1", text)
        self.assertIn("--network=none", text)

        patch = (ROOT / "deploy" / "compute-node" / "plapre" / "plapre-offline.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn('os.environ.get("PLAPRE_GB10_HYBRID_DECODE") == "1"', patch)
        self.assertIn("self.kanade.mel_conv_upsample.cpu()", patch)
        self.assertEqual(patch.count("mel.unsqueeze(0).to(self.vocoder_device)"), 2)
        self.assertIn('log.exception("Vocoder failed for sentence %d", i)', patch)


if __name__ == "__main__":
    unittest.main()
