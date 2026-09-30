#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/setup-compute-flash-next.sh").read_text(encoding="utf-8")
ENV = (ROOT / "config/compute-node.env.example").read_text(encoding="utf-8")
CATALOG = json.loads((ROOT / "config/model-catalog.json").read_text(encoding="utf-8"))
ROUTES = json.loads((ROOT / "config/capability-routes.json").read_text(encoding="utf-8"))
PATCH = (ROOT / "deploy/compute-node/patches/dime-ultrafast-private-launch.patch").read_text(encoding="utf-8")


class FlashNextLifecycleTests(unittest.TestCase):
    def test_candidate_is_pinned_and_native_context_has_no_yarn(self) -> None:
        candidate = CATALOG["deployments"]["automation-flash-next-candidate"]
        artifact = CATALOG["artifacts"][candidate["artifact"]]
        runtime = candidate["runtime_config"]
        self.assertEqual("nvidia/Qwen3.8-Flash-Next-NVFP4", artifact["upstream_model_id"])
        self.assertRegex(artifact["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(262144, candidate["context_tokens"])
        self.assertFalse(runtime["yarn"])
        self.assertEqual(2, runtime["mtp"])
        self.assertEqual(4, runtime["max_num_seqs"])
        self.assertEqual("auto", runtime["kv_cache_dtype"])
        self.assertEqual("qwen3_coder", runtime["tool_call_parser"])
        self.assertEqual("qwen3", runtime["reasoning_parser"])

    def test_production_route_stays_on_qwen36_and_candidate_is_separate(self) -> None:
        self.assertEqual("automation-spark-primary", ROUTES["routes"]["automation"]["deployments"][0])
        self.assertEqual("automation-spark-primary", ROUTES["routes"]["automation-moe"]["deployments"][0])
        self.assertEqual("candidate", ROUTES["routes"]["automation-qualification"]["state"])
        self.assertEqual("automation-flash-next-candidate", ROUTES["routes"]["automation-qualification"]["deployments"][0])

    def test_ultrafast_challenger_is_pinned_and_remains_unqualified(self) -> None:
        candidate = CATALOG["deployments"]["automation-flash-next-ultrafast-challenger"]
        artifact = CATALOG["artifacts"][candidate["artifact"]]
        runtime_profile = CATALOG["runtime_profiles"][candidate["runtime_profile"]]
        runtime = candidate["runtime_config"]
        self.assertEqual("candidate", candidate["availability"])
        self.assertEqual("candidate", artifact["qualification"]["automation"])
        self.assertEqual("0c391a3e74b6a775cfe248691ca7fd855b1876a5", runtime_profile["source_revision"])
        self.assertEqual("8b82f0b7abe3d1150a7827d298c75e86267636ae", artifact["revision"])
        self.assertEqual("50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14", artifact["ple_table"]["revision"])
        self.assertEqual(262144, runtime["context_tokens"])
        self.assertEqual("16g", runtime["kv_cache_memory_bytes"])
        self.assertEqual(3, runtime["mtp"])
        self.assertEqual(65536, runtime["draft_vocab_size"])
        self.assertEqual("qwen3_xml", runtime["tool_call_parser"])
        self.assertEqual("automation-flash-next-candidate", ROUTES["routes"]["automation-qualification"]["deployments"][0])

    def test_shared_lifecycle_and_private_non_destructive_launcher_patch(self) -> None:
        self.assertIn("PROFILE=quality", SCRIPT)
        self.assertIn("PROFILE=\"$2\"", SCRIPT)
        self.assertIn("install_ultrafast()", SCRIPT)
        self.assertIn("switch-profile)", SCRIPT)
        self.assertIn("quality (default) or ultrafast", SCRIPT)
        self.assertIn("Stop the active qualification candidate before installing or rebuilding a profile", SCRIPT)
        self.assertIn("configured private GB10 address", SCRIPT)
        self.assertIn('FLASH_ULTRAFAST_HOST_BIND" == "$GB10_BIND_ADDRESS', SCRIPT)
        self.assertIn("previous-text-containers", SCRIPT)
        self.assertIn("available_gib_on_existing_parent", SCRIPT)
        self.assertIn("while [[ ! -e \"$path\" ]]", SCRIPT)
        self.assertIn("HOST_BIND", PATCH)
        self.assertIn("docker container inspect", PATCH)
        self.assertIn("refusing to replace existing container", PATCH)
        self.assertNotIn("+docker rm -f", PATCH)
        self.assertIn("-docker rm -f", PATCH)
        self.assertIn('+DOCKER_RUN=(docker run -d --name "$NAME" \\', PATCH)
        expected = CATALOG["runtime_profiles"]["dime-qwen38-flash-ultrafast"]["homecompute_launch_overlay_sha256"]
        actual = hashlib.sha256((ROOT / "deploy/compute-node/patches/dime-ultrafast-private-launch.patch").read_bytes()).hexdigest()
        self.assertEqual(expected, actual)

    def test_lifecycle_records_and_restores_prior_text_containers(self) -> None:
        self.assertIn("previous-text-containers", SCRIPT)
        self.assertIn("restore_previous; trap - INT TERM; die 'Candidate failed to start", SCRIPT)
        self.assertIn("restore_previous; trap - INT TERM; die 'Candidate smoke failed", SCRIPT)
        self.assertIn("deactivate-canary", SCRIPT)
        self.assertIn("production routes were not changed", SCRIPT)
        self.assertNotRegex(SCRIPT, r"\bpromote\b")

    def test_27b_is_retained_as_nonresident_fallback(self) -> None:
        roster = json.loads((ROOT / "config/gb10-model-roster.json").read_text(encoding="utf-8"))
        model = roster["text_models"]["qwen38_27b_fallback"]
        self.assertEqual("unsloth/Qwen3.8-27B-NVFP4", model["model_id"])
        self.assertEqual("retained-efficient-cold-fallback-not-current-production", model["disposition"])
        self.assertIsNone(roster["operating_profiles"]["target_pending_approval"]["text_model"])
        self.assertTrue(roster["operating_profiles"]["target_pending_approval"]["exclusive_cold_swap"])

    def test_candidate_tuple_is_non_secret_and_has_separate_port(self) -> None:
        self.assertIn("FLASH_NEXT_SOURCE_REVISION=b05e14681325f3cc5bd22e7f48537feeeb0bf266", ENV)
        self.assertIn("FLASH_NEXT_MODEL_REVISION=fc694b54fb0174e0913e6adf86691ef85a4ead47", ENV)
        self.assertIn("FLASH_NEXT_CTX=262144", ENV)
        self.assertIn("FLASH_NEXT_YARN=0", ENV)
        self.assertIn("FLASH_NEXT_REASONING_EFFORT=xhigh", ENV)
        self.assertIn("FLASH_NEXT_HOST_PORT=18300", ENV)
        self.assertIn("FLASH_ULTRAFAST_TOOL_CALL_PARSER=qwen3_xml", ENV)
        self.assertIn("TOKENIZER_CONFIG_SHA256=%s", SCRIPT)
        self.assertIn("792fa3f0cb88b111e54ef3134c873531008c4df471d108da17903426e308aa7b", SCRIPT)
        self.assertNotRegex(ENV, r"(?im)^(?:HF_TOKEN|VLLM_API_KEY|PASSWORD|SECRET)=")


if __name__ == "__main__":
    unittest.main()
