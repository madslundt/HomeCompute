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
    def test_multilingual_whisper_route_is_pinned_separate_and_uses_json_default(self) -> None:
        deployment = CATALOG["transcription_deployments"]["whisper-turbo-spark-primary"]
        self.assertEqual("openai/whisper-large-v3-turbo", deployment["upstream_model_id"])
        self.assertEqual("41f01f3fe87f28c78e2fbf8b568835947dd65ed9", deployment["revision"])
        self.assertEqual("COMPUTE_STT_MULTILINGUAL_BASE_URL", deployment["endpoint_env"])
        self.assertEqual(1, deployment["concurrency"])
        self.assertEqual("mit", deployment["license_id"])
        self.assertNotIn("context_tokens", deployment)
        self.assertEqual(["whisper-turbo-spark-primary"], ROUTES["transcription_routes"]["whisper-turbo"]["deployments"])
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        whisper = rendered.split('model_name: "whisper-turbo"', 1)[1]
        self.assertIn('model: "openai/whisper-turbo"', whisper)
        self.assertIn('COMPUTE_STT_MULTILINGUAL_BASE_URL', whisper)
        self.assertIn('timeout: 120', whisper)
        self.assertIn('response_format: json', whisper)
        self.assertIn('mode: audio_transcription', whisper)

    def test_transcription_is_pinned_and_has_no_text_context_contract(self) -> None:
        deployment = CATALOG["transcription_deployments"]["hviske-spark-primary"]
        self.assertEqual("syvai/hviske-v5.3", deployment["upstream_model_id"])
        self.assertEqual("5d1a09822018702dc51d763e3a867b62d26b3501", deployment["revision"])
        self.assertEqual("COMPUTE_STT_DANISH_BASE_URL", deployment["endpoint_env"])
        self.assertNotIn("context_tokens", deployment)
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertRegex(rendered, r'(?s)model_name: "stt-danish".*?model: "openai/stt-danish".*?timeout: 120.*?mode: audio_transcription')

    def test_transcription_validation_rejects_unpinned_nonlocal_and_stopped_backends(self) -> None:
        for field, value, error in (("revision", "main", "immutable"),
                                    ("network_scope", "cloud", "non-local"),
                                    ("availability", "intentionally-stopped", "not active"),
                                    ("lifecycle", "cold-standby", "non-resident"),
                                    ("endpoint_env", "https://example.com/v1", "environment variable"),
                                    ("context_tokens", 4096, "text context")):
            with self.subTest(field=field):
                catalog = copy.deepcopy(CATALOG)
                catalog["transcription_deployments"]["hviske-spark-primary"][field] = value
                with self.assertRaisesRegex(REGISTRY.RegistryError, error):
                    REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_transcription_routes_cannot_reuse_text_aliases_or_select_text_deployments(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["transcription_routes"]["home"] = routes["transcription_routes"].pop("stt-danish")
        with self.assertRaisesRegex(REGISTRY.RegistryError, "already used"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)
        routes = copy.deepcopy(ROUTES)
        routes["transcription_routes"]["stt-danish"]["deployments"] = ["home-spark-primary"]
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unknown transcription deployment"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

    def test_transcription_runtime_must_support_audio_protocol(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["transcription_deployments"]["hviske-spark-primary"]["runtime_profile"] = "vllm-qwen"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "transcription protocol"):
            REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_optional_transcription_sections_preserve_text_only_registries(self) -> None:
        catalog, routes = copy.deepcopy(CATALOG), copy.deepcopy(ROUTES)
        catalog.pop("transcription_deployments")
        routes.pop("transcription_routes")
        REGISTRY.validate(catalog, routes)
        self.assertNotIn('model_name: "stt-danish"', REGISTRY.render_model_list(catalog, routes))

    def test_disabled_transcription_route_is_omitted(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["transcription_routes"]["stt-danish"]["state"] = "disabled"
        routes["transcription_routes"]["stt-danish"]["deployments"] = []
        REGISTRY.validate(copy.deepcopy(CATALOG), routes)
        self.assertNotIn('model_name: "stt-danish"', REGISTRY.render_model_list(copy.deepcopy(CATALOG), routes))

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
        self.assertEqual("automation-flash-next-candidate", ROUTES["routes"]["automation"]["deployments"][0])
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

    def test_candidate_route_selects_candidate_deployments_without_changing_other_routes(self) -> None:
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
            REGISTRY.select_candidate_deployment(copy.deepcopy(CATALOG), routes, "home", "automation-flash-next-candidate")

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
        routes = copy.deepcopy(ROUTES)
        catalog["artifacts"]["qwen38-flash-next-nvfp4"]["qualification"]["automation"] = "candidate"
        catalog["deployments"]["automation-flash-next-candidate"]["availability"] = "active"
        catalog["deployments"]["automation-flash-next-candidate"]["lifecycle"] = "resident"
        routes["routes"]["automation-qualification"]["state"] = "active"
        routes["routes"]["automation-qualification"].pop("kind")
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not qualified"):
            REGISTRY.validate(catalog, routes)

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
        routes = copy.deepcopy(ROUTES)
        catalog["deployments"]["automation-flash-next-candidate"]["availability"] = "intentionally-stopped"
        routes["routes"]["automation-qualification"]["state"] = "active"
        routes["routes"]["automation-qualification"].pop("kind")
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not active"):
            REGISTRY.validate(catalog, routes)

    def test_local_only_route_cannot_select_cloud_deployment(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["deployments"]["automation-flash-next-candidate"]["network_scope"] = "cloud"
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

    def test_transcription_defaults_to_json_without_changing_text_defaults(self) -> None:
        rendered = REGISTRY.render_model_list(CATALOG, ROUTES)
        audio = rendered.split('model_name: "stt-danish"', 1)[1]
        self.assertIn('response_format: json', audio)
        self.assertNotIn('response_format:', rendered.split('model_name: "stt-danish"', 1)[0])


if __name__ == "__main__":
    unittest.main()
