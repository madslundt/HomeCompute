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

    def test_flash_candidate_is_pinned_and_isolated_from_production_alias(self) -> None:
        artifact = CATALOG["artifacts"]["qwen38-flash-next-nvfp4"]
        deployment = CATALOG["deployments"]["automation-flash-next-candidate"]
        runtime = deployment["runtime_config"]
        self.assertEqual("nvidia/Qwen3.8-Flash-Next-NVFP4", artifact["upstream_model_id"])
        self.assertRegex(artifact["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual("candidate", artifact["qualification"]["automation"])
        self.assertEqual("candidate", deployment["availability"])
        self.assertEqual("automation-qualification", deployment["served_model_name"])
        self.assertEqual(262144, deployment["context_tokens"])
        self.assertFalse(runtime["yarn"])
        self.assertEqual("hybrid", runtime["mode"])
        self.assertEqual(2, runtime["mtp"])
        self.assertEqual("qwen3_coder", runtime["tool_call_parser"])
        self.assertEqual("qwen3", runtime["reasoning_parser"])
        self.assertEqual("nvidia-open-model-license", artifact["license_id"])
        self.assertEqual("automation-spark-primary", ROUTES["routes"]["automation"]["deployments"][0])
        self.assertEqual("candidate", ROUTES["routes"]["automation-qualification"]["state"])

    def test_ultrafast_is_separate_unqualified_artifact_and_shared_qualification_contract(self) -> None:
        artifact = CATALOG["artifacts"]["qwen38-flash-next-ultrafast-autoround"]
        deployment = CATALOG["deployments"]["automation-flash-next-ultrafast-challenger"]
        runtime = deployment["runtime_config"]
        self.assertEqual("Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid", artifact["upstream_model_id"])
        self.assertEqual("8b82f0b7abe3d1150a7827d298c75e86267636ae", artifact["revision"])
        self.assertEqual("50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14", artifact["ple_table"]["revision"])
        self.assertEqual("candidate", artifact["qualification"]["automation"])
        self.assertEqual("qwen3_xml", runtime["tool_call_parser"])
        self.assertEqual("qwen3", runtime["reasoning_parser"])
        self.assertEqual("automation-qualification", deployment["served_model_name"])
        self.assertEqual(262144, deployment["context_tokens"])
        self.assertEqual(3, runtime["mtp"])
        self.assertEqual("16g", runtime["kv_cache_memory_bytes"])
        self.assertEqual("candidate", ROUTES["routes"]["automation-qualification"]["state"])

    def test_candidate_route_can_select_either_profile_without_changing_consumer_routes(self) -> None:
        routes = copy.deepcopy(ROUTES)
        REGISTRY.select_candidate_deployment(
            copy.deepcopy(CATALOG), routes, "automation-qualification", "automation-flash-next-ultrafast-challenger"
        )
        self.assertEqual(
            ["automation-flash-next-ultrafast-challenger"],
            routes["routes"]["automation-qualification"]["deployments"],
        )
        self.assertEqual(ROUTES["routes"]["automation"]["deployments"], routes["routes"]["automation"]["deployments"])
        with self.assertRaisesRegex(REGISTRY.RegistryError, "isolated candidate route"):
            REGISTRY.select_candidate_deployment(copy.deepcopy(CATALOG), routes, "automation", "automation-spark-primary")

        routes = copy.deepcopy(ROUTES)
        with self.assertRaisesRegex(REGISTRY.RegistryError, "operator-on-demand candidate deployments"):
            REGISTRY.select_candidate_deployment(copy.deepcopy(CATALOG), routes, "automation-qualification", "automation-spark-primary")

    def test_flash_candidate_cannot_be_promoted_by_flipping_route_state_alone(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["routes"]["automation-qualification"]["state"] = "active"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "candidate route.*cannot be active"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

    def test_route_aliases_must_be_semantic_and_not_checkpoint_names(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["routes"]["Qwen3.8-Flash-Next"] = copy.deepcopy(routes["routes"]["automation-qualification"])
        with self.assertRaisesRegex(REGISTRY.RegistryError, "lowercase semantic alias"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

        routes = copy.deepcopy(ROUTES)
        routes["routes"]["nvidia-qwen3-8-flash-next-nvfp4"] = copy.deepcopy(routes["routes"]["automation-qualification"])
        with self.assertRaisesRegex(REGISTRY.RegistryError, "checkpoint identity"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

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
        catalog["artifacts"]["qwen36-35b-a3b-nvfp4"]["qualification"]["automation"] = "candidate"
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
        catalog["deployments"]["automation-spark-primary"]["availability"] = "intentionally-stopped"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not active"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_local_only_route_cannot_select_cloud_deployment(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-spark-primary"]["network_scope"] = "cloud"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "non-local deployment"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_cold_standby_is_not_eligible_as_an_active_route(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-flash-next-candidate"]["availability"] = "active"
        catalog["deployments"]["automation-flash-next-candidate"]["lifecycle"] = "cold-standby"
        routes = copy.deepcopy(ROUTES)
        routes["routes"]["automation-qualification"].pop("kind")
        routes["routes"]["automation-qualification"]["state"] = "active"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "non-resident"):
            REGISTRY.validate(catalog, routes)

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
        self.assertIn('model_name: "automation-qualification"', rendered)
        for alias in ("coding", "research", "meeting", 'model_name: "assistant"', 'model_name: "auto"'):
            self.assertNotIn(alias, rendered)

    def test_per_capability_timeouts_render_on_deployments(self) -> None:
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertRegex(rendered, r'(?s)model_name: "automation".*?timeout: 600')
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
