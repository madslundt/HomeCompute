#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
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
    def test_nvidia_candidate_uses_only_the_canonical_served_name(self) -> None:
        deployment = CATALOG["deployments"]["automation-spark-nvidia-candidate"]
        self.assertEqual("automation-moe", deployment["served_model_name"])
        compose = (ROOT / "deploy/compute-node/compose.yaml").read_text(encoding="utf-8")
        self.assertIn("--served-model-name automation-moe --host", compose)
        self.assertNotIn("automation-moe-nvidia", compose)

    def test_automation_moe_is_the_only_exposed_text_alias(self) -> None:
        rendered = REGISTRY.render_model_list(CATALOG, ROUTES)
        text_aliases = [alias for alias in ('automation', 'home', 'automation-moe',
                                             'automation-moe-nvidia', 'automation-qualification')
                        if f'model_name: "{alias}"' in rendered]
        self.assertEqual(text_aliases, ['automation-moe'])
        self.assertIn('openai/automation-qualification', rendered)
        self.assertNotIn('COMPUTE_HOME_BASE_URL', rendered)

    def test_video_is_pinned_on_demand_and_renders_the_native_video_api(self) -> None:
        item = CATALOG["video_deployments"]["ltx25-spark"]
        self.assertEqual(item["upstream_model_id"], "Lightricks/LTX-2.5")
        self.assertEqual(item["revision"], "49b093a39c19169a8a81721dcc541e07e945232f")
        self.assertEqual(item["license_id"], "ltx-2.x-community-license-agreement")
        self.assertEqual(item["lifecycle"], "operator-on-demand")
        self.assertEqual(item["quantization"], "NVFP4")
        REGISTRY.validate(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        video = REGISTRY.render_model_list(CATALOG, ROUTES).split('model_name: "video"', 1)[1]
        self.assertIn('model: "openai/ltx-2.5"', video)
        self.assertIn('api_base: "os.environ/VIDEO_GENERATION_BASE_URL"', video)
        self.assertIn('api_key: "local"', video)
        self.assertIn('mode: video_generation', video)
        self.assertIn('output_cost_per_second: 0', video)
        self.assertIn('timeout: 120', video)
        for field in ('stream_timeout', 'response_format', 'max_input_tokens', 'COMPUTE_API_KEY'):
            self.assertNotIn(field, video)
        self.assertNotIn('context_tokens', item)

    def test_video_validates_pin_network_protocol_and_no_text_contract(self) -> None:
        for field, value, expected in (("revision", "main", "immutable"),
                                       ("code_revision", "latest", "immutable"),
                                       ("network_scope", "cloud", "private_local"),
                                       ("endpoint_env", "https://example.com", "environment variable"),
                                       ("context_tokens", 100, "text context"),
                                       ("tokenizer_revision", "main", "tokenizer"),
                                       ("runtime_profile", "vllm-qwen", "video protocol"),
                                       ("concurrency", True, "positive")):
            with self.subTest(field=field):
                catalog = copy.deepcopy(CATALOG)
                catalog["video_deployments"]["ltx25-spark"][field] = value
                with self.assertRaisesRegex(REGISTRY.RegistryError, expected):
                    REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_video_aliases_do_not_collide_and_disabled_video_is_not_rendered(self) -> None:
        for alias in ('automation-moe', 'stt-danish', 'plapre', 'ltx-2-5', 'auto'):
            with self.subTest(alias=alias):
                routes = copy.deepcopy(ROUTES)
                routes["video_routes"][alias] = routes["video_routes"].pop('video')
                with self.assertRaises(REGISTRY.RegistryError):
                    REGISTRY.validate(copy.deepcopy(CATALOG), routes)
        routes = copy.deepcopy(ROUTES)
        routes["video_routes"]["video"].update(state="disabled", deployments=[])
        REGISTRY.validate(copy.deepcopy(CATALOG), routes)
        self.assertNotIn('model_name: "video"', REGISTRY.render_model_list(CATALOG, routes))
        catalog = copy.deepcopy(CATALOG)
        catalog.pop('video_deployments')
        routes.pop('video_routes')
        REGISTRY.validate(catalog, routes)
        self.assertNotIn('model_name: "video"', REGISTRY.render_model_list(catalog, routes))

    def test_active_video_requires_qualification_but_keeps_on_demand_lifecycle(self) -> None:
        catalog, routes = copy.deepcopy(CATALOG), copy.deepcopy(ROUTES)
        routes["video_routes"]["video"]["state"] = "active"
        catalog["video_deployments"]["ltx25-spark"]["qualification"] = "candidate"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unqualified"):
            REGISTRY.validate(catalog, routes)
        catalog["video_deployments"]["ltx25-spark"]["qualification"] = "qualified"
        REGISTRY.validate(catalog, routes)
        catalog["video_deployments"]["ltx25-spark"]["availability"] = "intentionally-stopped"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "not active"):
            REGISTRY.validate(catalog, routes)
        catalog["video_deployments"]["ltx25-spark"].update(availability="active", lifecycle="cold-standby")
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unavailable video lifecycle"):
            REGISTRY.validate(catalog, routes)

    def test_embedding_route_matches_pinned_service_and_preserves_native_dimensions(self) -> None:
        model = json.loads((ROOT / "config/embedding-model.json").read_text())
        artifact = CATALOG["artifacts"]["harrier-oss-v1-0.6b"]
        deployment = CATALOG["deployments"]["harrier-home-core-primary"]
        runtime = CATALOG["runtime_profiles"]["tei-cpu"]
        self.assertEqual(model["model"], artifact["upstream_model_id"])
        for field in ("revision", "tokenizer_revision", "code_revision"):
            self.assertEqual(model["revision"], artifact[field])
        self.assertEqual(model["image"], runtime["image"])
        self.assertEqual(model["dimensions"], artifact["embedding_dimensions"])
        self.assertEqual(["embeddings"], runtime["supported_protocols"])
        self.assertEqual("EMBEDDING_BASE_URL", deployment["endpoint_env"])
        self.assertEqual(2048, deployment["context_tokens"])
        self.assertEqual(1, deployment["runtime_config"]["max_batch_requests"])
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        embedding = rendered.split('model_name: "embedding"', 1)[1].split('  - model_name:', 1)[0]
        self.assertIn('model: "openai/' + model["model"] + '"', embedding)
        self.assertIn('api_base: "os.environ/EMBEDDING_BASE_URL"', embedding)
        self.assertIn('api_key: "local"', embedding)
        self.assertIn("timeout: 60", embedding)
        self.assertIn("mode: embedding", embedding)
        self.assertIn("max_input_tokens: 2048", embedding)
        self.assertIn("output_vector_size: 1024", embedding)
        self.assertNotIn("stream_timeout", embedding)
        self.assertNotIn("COMPUTE_API_KEY", embedding)

    def test_embedding_route_rejects_incompatible_runtime_and_invalid_dimensions(self) -> None:
        for field, value, error in (("supported_protocols", ["chat_completions"], "protocol"),
                                    ("capabilities", ["chat"], "capability")):
            with self.subTest(field=field):
                catalog = copy.deepcopy(CATALOG)
                catalog["runtime_profiles"]["tei-cpu"][field] = value
                with self.assertRaisesRegex(REGISTRY.RegistryError, error):
                    REGISTRY.validate(catalog, copy.deepcopy(ROUTES))
        for dimensions in (None, 0, -1, True):
            with self.subTest(dimensions=dimensions):
                catalog = copy.deepcopy(CATALOG)
                catalog["artifacts"]["harrier-oss-v1-0.6b"]["embedding_dimensions"] = dimensions
                with self.assertRaisesRegex(REGISTRY.RegistryError, "embedding_dimensions"):
                    REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_multilingual_whisper_route_is_pinned_separate_and_uses_json_default(self) -> None:
        deployment = CATALOG["transcription_deployments"]["whisper-large-v3-spark-primary"]
        runtime = CATALOG["runtime_profiles"][deployment["runtime_profile"]]
        self.assertEqual("openai/whisper-large-v3", deployment["upstream_model_id"])
        self.assertEqual("06f233fe06e710322aca913c1bc4249a0d71fce1", deployment["revision"])
        self.assertEqual("whisper-cpp-cuda-large-v3", deployment["runtime_profile"])
        self.assertEqual("whisper.cpp-cuda", runtime["family"])
        self.assertEqual("sha256:2a9e90e60708dbfb6c95c68b84bb93385cccdeb6a3861761347a8f045f406164", runtime["image"])
        self.assertEqual("f049fff95a089aa9969deb009cdd4892b3e74916", runtime["source_revision"])
        self.assertEqual("bdb35cb972abe273d64ac4087b32a2ca2a2c61b9", runtime["homecompute_source_revision"])
        self.assertEqual("c521a4b02f422512d734391fdf08bb08c0862f68", runtime["model_snapshot"])
        self.assertEqual("64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2", runtime["model_sha256"])
        self.assertEqual("qualified_reliability", runtime["qualification_status"])
        self.assertEqual("request_determinism", runtime["qualification_scope"])
        self.assertEqual("NOT_QUALIFIED", runtime["accuracy_status"])
        rollback = CATALOG["transcription_deployments"]["whisper-large-v3-spark-vllm-rollback"]
        self.assertEqual("vllm-whisper", rollback["runtime_profile"])
        self.assertEqual("intentionally-stopped", rollback["availability"])
        self.assertEqual("operator-on-demand", rollback["lifecycle"])
        for route in ROUTES["transcription_routes"].values():
            self.assertEqual(["whisper-large-v3-spark-primary"], route["deployments"])
        self.assertEqual("COMPUTE_STT_MULTILINGUAL_BASE_URL", deployment["endpoint_env"])
        self.assertEqual(1, deployment["concurrency"])
        self.assertEqual("apache-2.0", deployment["license_id"])
        self.assertNotIn("context_tokens", deployment)
        self.assertEqual(["whisper-large-v3-spark-primary"], ROUTES["transcription_routes"]["whisper-turbo"]["deployments"])
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        whisper = rendered.split('model_name: "whisper-turbo"', 1)[1]
        self.assertIn('model: "openai/whisper-large-v3"', whisper)
        self.assertIn('COMPUTE_STT_MULTILINGUAL_BASE_URL', whisper)
        self.assertIn('timeout: 120', whisper)
        self.assertIn('response_format: json', whisper)
        self.assertIn('mode: audio_transcription', whisper)

    def test_transcription_is_pinned_and_has_no_text_context_contract(self) -> None:
        deployment = CATALOG["transcription_deployments"]["whisper-large-v3-spark-primary"]
        self.assertEqual("openai/whisper-large-v3", deployment["upstream_model_id"])
        self.assertEqual("06f233fe06e710322aca913c1bc4249a0d71fce1", deployment["revision"])
        self.assertEqual("COMPUTE_STT_MULTILINGUAL_BASE_URL", deployment["endpoint_env"])
        self.assertNotIn("context_tokens", deployment)
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertRegex(rendered, r'(?s)model_name: "stt-danish".*?model: "openai/whisper-large-v3".*?timeout: 120.*?mode: audio_transcription')

    def test_transcription_validation_rejects_unpinned_nonlocal_and_stopped_backends(self) -> None:
        for field, value, error in (("revision", "main", "immutable"),
                                    ("network_scope", "cloud", "non-local"),
                                    ("availability", "intentionally-stopped", "not active"),
                                    ("lifecycle", "cold-standby", "non-resident"),
                                    ("endpoint_env", "https://example.com/v1", "environment variable"),
                                    ("context_tokens", 4096, "text context")):
            with self.subTest(field=field):
                catalog = copy.deepcopy(CATALOG)
                catalog["transcription_deployments"]["whisper-large-v3-spark-primary"][field] = value
                with self.assertRaisesRegex(REGISTRY.RegistryError, error):
                    REGISTRY.validate(catalog, copy.deepcopy(ROUTES))

    def test_transcription_routes_cannot_reuse_text_aliases_or_select_text_deployments(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["transcription_routes"]["automation-moe"] = routes["transcription_routes"].pop("stt-danish")
        with self.assertRaisesRegex(REGISTRY.RegistryError, "already used"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)
        routes = copy.deepcopy(ROUTES)
        routes["transcription_routes"]["stt-danish"]["deployments"] = ["home-spark-primary"]
        with self.assertRaisesRegex(REGISTRY.RegistryError, "unknown transcription deployment"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

    def test_transcription_runtime_must_support_audio_protocol(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        catalog["transcription_deployments"]["whisper-large-v3-spark-primary"]["runtime_profile"] = "vllm-qwen"
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
        self.assertEqual("automation-flash-next-candidate", ROUTES["routes"]["automation-moe"]["deployments"][0])
        self.assertEqual("candidate", ROUTES["routes"]["automation-moe"]["state"])

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
        self.assertEqual("9g", runtime["kv_cache_memory_bytes"])
        self.assertEqual("candidate", ROUTES["routes"]["automation-moe"]["state"])

    def test_candidate_route_selects_candidate_deployments_on_canonical_alias(self) -> None:
        routes = copy.deepcopy(ROUTES)
        REGISTRY.select_candidate_deployment(
            copy.deepcopy(CATALOG), routes, "automation-moe", "automation-flash-next-ultrafast-challenger"
        )
        self.assertEqual(
            ["automation-flash-next-ultrafast-challenger"],
            routes["routes"]["automation-moe"]["deployments"],
        )
        REGISTRY.select_candidate_deployment(
            copy.deepcopy(CATALOG), routes, "automation-moe", "automation-flash-next-candidate"
        )
        self.assertEqual(
            ["automation-flash-next-candidate"],
            routes["routes"]["automation-moe"]["deployments"],
        )

        routes = copy.deepcopy(ROUTES)
        with self.assertRaisesRegex(REGISTRY.RegistryError, "operator-on-demand candidate deployments"):
            REGISTRY.select_candidate_deployment(copy.deepcopy(CATALOG), routes, "automation-moe", "automation-spark-primary")

    def test_flash_candidate_cannot_be_promoted_by_flipping_route_state_alone(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["routes"]["automation-moe"]["state"] = "active"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "candidate route.*cannot be active"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

    def test_route_aliases_must_be_semantic_and_not_checkpoint_names(self) -> None:
        routes = copy.deepcopy(ROUTES)
        routes["routes"]["Qwen3.8-Flash-Next"] = copy.deepcopy(routes["routes"]["automation-moe"])
        with self.assertRaisesRegex(REGISTRY.RegistryError, "lowercase semantic alias"):
            REGISTRY.validate(copy.deepcopy(CATALOG), routes)

        routes = copy.deepcopy(ROUTES)
        routes["routes"]["nvidia-qwen3-8-flash-next-nvfp4"] = copy.deepcopy(routes["routes"]["automation-moe"])
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
        routes["routes"]["automation-moe"]["state"] = "active"
        routes["routes"]["automation-moe"].pop("kind")
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
        routes["routes"]["automation-moe"]["state"] = "active"
        routes["routes"]["automation-moe"].pop("kind")
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
        routes["routes"]["automation-moe"].pop("kind")
        routes["routes"]["automation-moe"]["state"] = "active"
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
        self.assertIn('model_name: "automation-moe"', rendered)
        for alias in ('automation', 'home', 'automation-moe-nvidia', 'automation-qualification'):
            self.assertNotIn(f'model_name: "{alias}"', rendered)
        self.assertNotIn('model_name: "assistant-canary"', rendered)
        for alias in ("coding", "research", "meeting", 'model_name: "assistant"', 'model_name: "auto"'):
            self.assertNotIn(alias, rendered)

    def test_per_capability_timeouts_render_on_deployments(self) -> None:
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertRegex(rendered, r'(?s)model_name: "automation-moe".*?timeout: 600')
        self.assertIn("COMPUTE_API_KEY", rendered)

    def test_chatgpt_subscription_aliases_are_explicit_and_use_gateway_credentials(self) -> None:
        rendered = REGISTRY.render_model_list(copy.deepcopy(CATALOG), copy.deepcopy(ROUTES))
        self.assertNotIn('model_name: "gpt-*"', rendered)
        self.assertNotIn('model_name: "codex-*"', rendered)
        for alias, provider in (
            ("chatgpt-sol-latest", '"chatgpt/gpt-6.1-sol"'),
            ("chatgpt-terra-latest", '"chatgpt/gpt-6.1-terra"'),
            ("chatgpt-luna-latest", '"chatgpt/gpt-6-luna"'),
        ):
            block = rendered.split(f'model_name: "{alias}"', 1)[1].split('  - model_name:', 1)[0]
            self.assertIn(f"model: {provider}", block)
            self.assertIn("mode: responses", block)
            self.assertNotIn("api_key", block)

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
