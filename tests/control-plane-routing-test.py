#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LITELLM = ROOT / "deploy" / "control-plane" / "litellm-config.yaml"
COMPUTE = ROOT / "deploy" / "compute-node" / "compose.yaml"
EXPECTED = {"auto", "assistant", "automation", "coding", "home", "meeting", "research"}


class LocalOnlyControlPlaneTests(unittest.TestCase):
    def test_litellm_exposes_exact_local_alias_set(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        names = set(re.findall(r"^\s*- model_name:\s*(\S+)\s*$", text, re.MULTILINE))
        upstreams = set(re.findall(r"^\s*model:\s*openai/(\S+)\s*$", text, re.MULTILINE))
        self.assertEqual(EXPECTED, names)
        self.assertEqual(EXPECTED, upstreams)
        self.assertIn("api_base: os.environ/COMPUTE_OPENAI_BASE_URL", text)

    def test_gateway_has_no_cloud_provider_configuration(self) -> None:
        text = "\n".join(
            [
                LITELLM.read_text(encoding="utf-8"),
                (ROOT / "deploy" / "control-plane" / "compose.yaml").read_text(encoding="utf-8"),
                (ROOT / "config" / "control-plane.env.example").read_text(encoding="utf-8"),
            ]
        ).lower()
        for forbidden in (
            "anthropic_api_key",
            "openai_api_key",
            "gemini_api_key",
            "api.openai.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
            "openrouter.ai",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_compute_serves_auto_and_semantic_aliases_from_one_process(self) -> None:
        text = COMPUTE.read_text(encoding="utf-8")
        match = re.search(r"--served-model-name\s+(.+?)\s+--host", text)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(EXPECTED, set(match.group(1).split()))


if __name__ == "__main__":
    unittest.main()
