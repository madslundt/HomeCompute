#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LITELLM = ROOT / "deploy/control-plane/litellm-config.yaml"
ROLLBACK = ROOT / "deploy/control-plane/litellm-config-pre-registry-rollback.yaml"
LITELLM_BACKUP = ROOT / "deploy/control-plane/litellm-config-automation-backup.yaml"
COMPUTE = ROOT / "deploy/compute-node/compose.yaml"
DEPLOYMENT = ROOT / "docs/control-plane-deployment.md"
MODULE_PATH = ROOT / "scripts/model_registry.py"
SPEC = importlib.util.spec_from_file_location("model_registry", MODULE_PATH)
assert SPEC and SPEC.loader
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)


class LocalOnlyControlPlaneTests(unittest.TestCase):
    def test_litellm_config_is_generated_from_canonical_routes(self) -> None:
        catalog = REGISTRY.load_json(ROOT / "config/model-catalog.json")
        routes = REGISTRY.load_json(ROOT / "config/capability-routes.json")
        REGISTRY.validate(catalog, routes)
        current = LITELLM.read_text(encoding="utf-8")
        self.assertEqual(current, REGISTRY.render_config(current, catalog, routes))

    def test_only_active_capabilities_and_isolated_migration_lanes_are_rendered(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        names = set(re.findall(r'^\s*- model_name:\s*"?([^"\s]+)', text, re.MULTILINE))
        self.assertEqual({"automation", "home", "automation-moe", "automation-moe-nvidia", "automation-qualification", "assistant-canary"}, names)
        for unavailable in ("auto", "coding", "research", "meeting", "assistant"):
            self.assertNotIn(f"model_name: {unavailable}", text)

    def test_deployment_mappings_and_timeouts_are_capability_scoped(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        self.assertRegex(text, r'(?s)model_name: "automation".*?openai/automation-moe.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "automation-moe".*?openai/automation-moe.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "automation-qualification".*?openai/automation-qualification.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "home".*?openai/home-fast.*?timeout: 20')
        self.assertRegex(text, r'(?s)model_name: "home".*?stream_timeout: 20')
        self.assertRegex(text, r'(?s)model_name: "assistant-canary".*?openai/automation-moe.*?timeout: 120')
        self.assertNotIn("automation-backup", text)
        self.assertIn("request_timeout: 600", text)
        self.assertIn("num_retries: 0", text)

    def test_security_settings_are_preserved_by_rendering(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        self.assertIn("turn_off_message_logging: true", text)
        self.assertIn("redact_user_api_key_info: true", text)
        self.assertIn("disable_spend_logs: true", text)
        self.assertIn("disable_error_logs: true", text)
        self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", text)
        self.assertIn("database_url: os.environ/DATABASE_URL", text)

    def test_checked_in_rollback_files_retain_known_routing_states(self) -> None:
        rollback = ROLLBACK.read_text(encoding="utf-8")
        maintenance = LITELLM_BACKUP.read_text(encoding="utf-8")
        self.assertIn("model_name: automation-moe", rollback)
        self.assertIn("model_name: assistant-canary", rollback)
        self.assertRegex(maintenance, r"(?s)model: openai/automation-backup.*?order: 1")
        self.assertRegex(maintenance, r"(?s)model: openai/automation\s+order: 2")

    def test_gateway_has_no_cloud_provider_configuration(self) -> None:
        text = "\n".join(
            [
                LITELLM.read_text(encoding="utf-8"),
                ROLLBACK.read_text(encoding="utf-8"),
                LITELLM_BACKUP.read_text(encoding="utf-8"),
                (ROOT / "deploy/control-plane/compose.yaml").read_text(encoding="utf-8"),
                (ROOT / "config/control-plane.env.example").read_text(encoding="utf-8"),
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

    def test_compute_backend_serves_only_an_internal_deployment_name(self) -> None:
        text = COMPUTE.read_text(encoding="utf-8")
        self.assertIn("--served-model-name general-spark-qwen38", text)
        self.assertIsNone(re.search(r"--served-model-name\s+(?:auto|coding|automation)(?:\s|$)", text))
        self.assertIn("COMPUTE_HOST_PORTS", (ROOT / "config/compute-node.env.example").read_text(encoding="utf-8"))

    def test_existing_client_key_scope_is_still_separate(self) -> None:
        deployment = re.sub(r"\s+", " ", DEPLOYMENT.read_text(encoding="utf-8"))
        self.assertIn("each list only `automation-moe`", deployment)
        self.assertIn("returns HTTP 403", deployment)
        self.assertIn("does not change key permissions", deployment)


if __name__ == "__main__":
    unittest.main()
