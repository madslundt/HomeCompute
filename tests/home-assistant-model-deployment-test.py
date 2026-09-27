#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compute-node" / "home-assistant-model" / "compose.yaml"
ENV = ROOT / "config" / "home-assistant-model.env.example"


class HomeAssistantModelDeploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        text = ENV.read_text().replace("REPLACE_WITH_INIT_UID", "996").replace("REPLACE_WITH_INIT_GID", "983")
        cls.runtime_env = ROOT / ".home-fast-test.env"
        cls.runtime_env.write_text(text)
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(cls.runtime_env), "-f", str(COMPOSE), "--profile", "prepare", "config", "--format", "json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.document = json.loads(result.stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runtime_env.unlink(missing_ok=True)

    def test_only_guarded_loopback_edge_is_published(self) -> None:
        services = self.document["services"]
        self.assertNotIn("ports", services["home-primary"])
        self.assertEqual(services["home-edge"]["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(services["home-edge"]["ports"][0]["published"], "8006")
        self.assertTrue(self.document["networks"]["inference"]["internal"])

    def test_runtime_is_text_only_nonthinking_and_tool_capable(self) -> None:
        primary = self.document["services"]["home-primary"]
        command = " ".join(primary["command"])
        self.assertIn("google/gemma-4-E4B-it-qat-w4a16-ct", command)
        self.assertIn("--served-model-name home-fast", command)
        self.assertIn("--language-model-only", command)
        self.assertIn("--tool-call-parser gemma4", command)
        self.assertIn("--reasoning-parser gemma4", command)
        expected_gpu_utilization = next(
            line.partition("=")[2]
            for line in ENV.read_text().splitlines()
            if line.startswith("HOME_GPU_MEMORY_UTILIZATION=")
        )
        self.assertIn(f'--gpu-memory-utilization "{expected_gpu_utilization}"', command)
        self.assertEqual(primary["environment"]["VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS"], '{"enable_thinking":false}')
        self.assertTrue(primary["read_only"])
        self.assertEqual(primary["cap_drop"], ["ALL"])


if __name__ == "__main__":
    unittest.main()
