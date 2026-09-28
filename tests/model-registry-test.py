#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "model_registry.py"
SPEC = importlib.util.spec_from_file_location("model_registry", MODULE_PATH)
assert SPEC and SPEC.loader
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)
CATALOG = REGISTRY.load_json(ROOT / "config/model-catalog.json")
ROUTES = REGISTRY.load_json(ROOT / "config/capability-routes.json")


class ModelRegistryTests(unittest.TestCase):
    def test_checked_in_registry_validates(self) -> None:
        REGISTRY.validate(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))

    def test_model_replacement_with_supported_runtime_is_data_only(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        artifact = catalog["artifacts"].pop("qwen36-35b-a3b-nvfp4")
        artifact["upstream_model_id"] = "nvidia/Nemotron-Next-NVFP4"
        artifact["revision"] = "a" * 40
        artifact["tokenizer_revision"] = "a" * 40
        artifact["code_revision"] = "a" * 40
        catalog["artifacts"]["nemotron-next-nvfp4"] = artifact
        catalog["deployments"]["automation-spark-primary"]["artifact"] = "nemotron-next-nvfp4"
        REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_unqualified_candidate_cannot_be_promoted(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["artifacts"]["nvidia-qwen36-35b-a3b-nvfp4"]["qualification"]["automation"] = "candidate"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not qualified"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_dangling_deployment_and_runtime_references_fail(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-primary"]["artifact"] = "missing-model"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unknown artifact"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-primary"]["runtime_profile"] = "missing-runtime"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unsupported runtime"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_local_route_cannot_select_intentionally_stopped_backend(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-nvidia-candidate"]["availability"] = "intentionally-stopped"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not active"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_local_only_route_cannot_select_cloud_deployment(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-nvidia-candidate"]["network_scope"] = "cloud"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "non-local deployment"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_cold_standby_is_not_eligible_as_an_active_route(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-nvidia-candidate"]["lifecycle"] = "cold-standby"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "non-resident"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_adding_deployment_does_not_add_client_permissions(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["operator-candidate"] = copy.deepcopy(catalog["deployments"]["automation-spark-primary"])
        catalog["deployments"]["operator-candidate"]["availability"] = "candidate"
        routes = copy.deepcopy(ROUTES)
        REGISTRY.validate(catalog, routes)
        self.assertEqual(set(ROUTES["routes"]), set(routes["routes"]))

    def test_disabled_routes_and_auto_are_not_rendered(self) -> None:
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertIn('model_name: "automation"', rendered)
        self.assertIn('model_name: "home"', rendered)
        self.assertIn('model_name: "assistant-canary"', rendered)
        for alias in ("coding", "research", "meeting", 'model_name: "assistant"', 'model_name: "auto"'):
            self.assertNotIn(alias, rendered)

    def test_per_capability_timeouts_render_on_deployments(self) -> None:
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertRegex(rendered, r'(?s)model_name: "automation".*?timeout: 120')
        self.assertRegex(rendered, r'(?s)model_name: "home".*?timeout: 20')
        self.assertRegex(rendered, r'(?s)model_name: "home".*?stream_timeout: 20')
        self.assertIn("COMPUTE_API_KEY", rendered)

    def test_generated_model_section_preserves_security_settings(self) -> None:
        original = (ROOT / "deploy/control-plane/litellm-config.yaml").read_text(encoding="utf-8")
        generated = REGISTRY.render_config(original, copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", generated)
        self.assertIn("turn_off_message_logging: true", generated)
        self.assertIn("disable_error_logs: true", generated)
        self.assertEqual(generated, original)


if __name__ == "__main__":
    unittest.main()
