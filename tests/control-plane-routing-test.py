#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LITELLM = ROOT / "deploy" / "control-plane" / "litellm-config.yaml"
LITELLM_BACKUP = ROOT / "deploy" / "control-plane" / "litellm-config-automation-backup.yaml"
COMPUTE = ROOT / "deploy" / "compute-node" / "compose.yaml"
DEPLOYMENT = ROOT / "docs" / "control-plane-deployment.md"
SEMANTIC = {"auto", "assistant", "automation", "coding", "home", "meeting", "research"}
EXPECTED = SEMANTIC | {"assistant-canary", "automation-moe"}


class LocalOnlyControlPlaneTests(unittest.TestCase):
    def test_litellm_exposes_exact_local_alias_set(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        names = set(re.findall(r"^\s*- model_name:\s*(\S+)\s*$", text, re.MULTILINE))
        upstreams = set(re.findall(r"^\s*model:\s*openai/(\S+)\s*$", text, re.MULTILINE))
        self.assertEqual(EXPECTED, names)
        self.assertEqual(
            (EXPECTED - {"home", "assistant-canary"})
            | {"home-fast", "automation-backup"},
            upstreams,
        )
        self.assertIn("api_base: os.environ/COMPUTE_OPENAI_BASE_URL", text)
        self.assertIn("api_base: os.environ/COMPUTE_AUTOMATION_BASE_URL", text)
        self.assertIn("api_base: os.environ/COMPUTE_HOME_BASE_URL", text)
        self.assertRegex(
            text,
            r"(?s)model: openai/automation\s+order: 1.*?model: openai/automation-backup.*?order: 2",
        )
        self.assertRegex(
            text,
            r"(?s)model_name: assistant-canary\s+litellm_params:\s+"
            r"model: openai/automation-moe\s+"
            r"api_base: os\.environ/COMPUTE_AUTOMATION_BASE_URL",
        )
        self.assertIn("drop_params: false", text)
        self.assertRegex(text, r"(?s)model_name: home\s+litellm_params:\s+model: openai/home-fast")
        self.assertRegex(text, r"(?s)model_name: assistant-canary\s+litellm_params:\s+model: openai/automation-moe")

    def test_maintenance_config_only_inverts_automation_priority(self) -> None:
        normal = LITELLM.read_text(encoding="utf-8")
        backup = LITELLM_BACKUP.read_text(encoding="utf-8")
        self.assertRegex(
            normal,
            r"(?s)model: openai/automation\s+order: 1.*?"
            r"model: openai/automation-backup.*?order: 2",
        )
        self.assertRegex(
            backup,
            r"(?s)model: openai/automation-backup.*?order: 1.*?"
            r"model: openai/automation\s+order: 2",
        )
        normal_names = re.findall(r"^\s*- model_name:\s*(\S+)\s*$", normal, re.MULTILINE)
        backup_names = re.findall(r"^\s*- model_name:\s*(\S+)\s*$", backup, re.MULTILINE)
        self.assertCountEqual(normal_names, backup_names)

    def test_gateway_has_no_cloud_provider_configuration(self) -> None:
        text = "\n".join(
            [
                LITELLM.read_text(encoding="utf-8"),
                LITELLM_BACKUP.read_text(encoding="utf-8"),
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
        self.assertEqual(SEMANTIC, set(match.group(1).split()))

    def test_client_key_scope_is_distinguished_from_backend_tool_support(self) -> None:
        deployment = re.sub(r"\s+", " ", DEPLOYMENT.read_text(encoding="utf-8"))
        helper = (ROOT / "scripts" / "homecompute_chat.py").read_text(encoding="utf-8")
        self.assertIn("each list only `automation-moe`", deployment)
        self.assertIn("returns HTTP 403", deployment)
        self.assertIn("does not change key permissions", deployment)
        self.assertIn("does not grant access", helper)
        self.assertIn("Documented client keys currently allow only automation-moe", helper)


if __name__ == "__main__":
    unittest.main()
