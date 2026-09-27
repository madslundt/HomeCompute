from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("modelctl", ROOT / "scripts/modelctl.py")
modelctl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(modelctl)


class ModelctlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = modelctl.load_registry()
        deployment, artifact, runtime = modelctl.deployment_tuple("general-spark-qwen38", self.catalog)
        recipe = deployment["runtime_config"]
        self.environ = {
            "MODEL_ID": artifact["upstream_model_id"],
            "MODEL_REVISION": artifact["revision"],
            "TOKENIZER_REVISION": artifact["tokenizer_revision"],
            "CODE_REVISION": artifact["code_revision"],
            "MODEL_LICENSE_ID": artifact["license_id"],
            "MODEL_QUANTIZATION": artifact["quantization"].lower(),
            "MODEL_PROVENANCE_URL": artifact["provenance_url"],
            "MODEL_WEIGHT_FORMAT": artifact["weight_format"],
            "CHAT_TEMPLATE_SHA256": artifact["chat_template_sha256"],
            "TEXT_ARTIFACT_MAX_BYTES": str(artifact["artifact_max_bytes"]),
            "TEXT_ARTIFACT_MAX_FILES": str(artifact["artifact_max_files"]),
            "VLLM_IMAGE": runtime["image"],
            "VLLM_HOST_PORT": str(deployment["compute_host_port"]),
            "VLLM_MAX_MODEL_LEN": str(recipe["max_model_len"]),
            "VLLM_MAX_NUM_SEQS": str(recipe["max_num_seqs"]),
            "VLLM_MAX_BATCHED_TOKENS": str(recipe["max_batched_tokens"]),
            "VLLM_GPU_MEMORY_UTILIZATION": "0.40",
            "VLLM_ATTENTION_BACKEND": recipe["attention_backend"],
            "VLLM_MOE_BACKEND": recipe["moe_backend"],
            "VLLM_REASONING_PARSER": recipe["reasoning_parser"],
            "VLLM_TOOL_CALL_PARSER": recipe["tool_call_parser"],
            "VLLM_SPECULATIVE_CONFIG": json.dumps(recipe["speculative_config"], separators=(",", ":")),
            "VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS": json.dumps(recipe["default_chat_template_kwargs"], separators=(",", ":")),
        }

    def test_accepts_environment_matching_catalog(self) -> None:
        modelctl.validate_env("general-spark-qwen38", self.catalog, self.environ)

    def test_rejects_artifact_or_runtime_drift(self) -> None:
        for field in ("MODEL_ID", "MODEL_REVISION", "CHAT_TEMPLATE_SHA256", "VLLM_IMAGE", "VLLM_TOOL_CALL_PARSER"):
            with self.subTest(field=field):
                changed = dict(self.environ)
                changed[field] += "-changed"
                with self.assertRaisesRegex(ValueError, "differs from catalog"):
                    modelctl.validate_env("general-spark-qwen38", self.catalog, changed)

    def test_rejects_unknown_deployment_and_nonlocal_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown deployment"):
            modelctl.validate_env("does-not-exist", self.catalog, self.environ)
        catalog = json.loads(json.dumps(self.catalog))
        catalog["deployments"]["general-spark-qwen38"]["network_scope"] = "cloud"
        with self.assertRaisesRegex(ValueError, "private_local"):
            modelctl.validate_env("general-spark-qwen38", catalog, self.environ)

    def test_validates_prefixed_automation_deployment_from_same_catalog(self) -> None:
        deployment, artifact, runtime = modelctl.deployment_tuple("automation-spark-primary", self.catalog)
        recipe = deployment["runtime_config"]
        env = {
            "AUTOMATION_MODEL_ID": artifact["upstream_model_id"],
            "AUTOMATION_MODEL_REVISION": artifact["revision"],
            "AUTOMATION_TOKENIZER_REVISION": artifact["tokenizer_revision"],
            "AUTOMATION_CODE_REVISION": artifact["code_revision"],
            "AUTOMATION_MODEL_LICENSE_ID": artifact["license_id"],
            "AUTOMATION_MODEL_QUANTIZATION": artifact["quantization"],
            "AUTOMATION_CHAT_TEMPLATE_SHA256": artifact["chat_template_sha256"],
            "AUTOMATION_ARTIFACT_MAX_BYTES": str(artifact["artifact_max_bytes"]),
            "AUTOMATION_ARTIFACT_MAX_FILES": str(artifact["artifact_max_files"]),
            "VLLM_IMAGE": runtime["image"],
            "AUTOMATION_HOST_PORT": str(recipe["host_port"]),
            "AUTOMATION_MAX_MODEL_LEN": str(recipe["max_model_len"]),
            "AUTOMATION_MAX_NUM_SEQS": str(recipe["max_num_seqs"]),
            "AUTOMATION_MAX_BATCHED_TOKENS": str(recipe["max_batched_tokens"]),
            "AUTOMATION_GPU_MEMORY_UTILIZATION": "0.40",
            "AUTOMATION_MOE_BACKEND": recipe["moe_backend"],
            "AUTOMATION_FP8_MOE_BACKEND": recipe["fp8_moe_backend"],
            "AUTOMATION_TOOL_CALL_PARSER": recipe["tool_call_parser"],
            "AUTOMATION_SPECULATIVE_CONFIG": "",
            "AUTOMATION_DEFAULT_CHAT_TEMPLATE_KWARGS": json.dumps(recipe["default_chat_template_kwargs"], separators=(",", ":")),
        }
        modelctl.validate_env("automation-spark-primary", self.catalog, env, "AUTOMATION_")
        env["AUTOMATION_MODEL_ID"] += "-changed"
        with self.assertRaisesRegex(ValueError, "differs from catalog"):
            modelctl.validate_env("automation-spark-primary", self.catalog, env, "AUTOMATION_")

    def test_validates_prefixed_home_deployment_from_same_catalog(self) -> None:
        deployment, artifact, runtime = modelctl.deployment_tuple("home-spark-primary", self.catalog)
        recipe = deployment["runtime_config"]
        env = {
            "HOME_MODEL_ID": artifact["upstream_model_id"],
            "HOME_MODEL_REVISION": artifact["revision"],
            "HOME_TOKENIZER_REVISION": artifact["tokenizer_revision"],
            "HOME_CODE_REVISION": artifact["code_revision"],
            "HOME_MODEL_LICENSE_ID": artifact["license_id"],
            "HOME_MODEL_QUANTIZATION": artifact["quantization"],
            "HOME_CHAT_TEMPLATE_SHA256": artifact["chat_template_sha256"],
            "HOME_ARTIFACT_MAX_BYTES": str(artifact["artifact_max_bytes"]),
            "HOME_ARTIFACT_MAX_FILES": str(artifact["artifact_max_files"]),
            "VLLM_IMAGE": runtime["image"],
            "HOME_MODEL_HOST_PORT": str(deployment["compute_host_port"]),
            "HOME_MAX_MODEL_LEN": str(recipe["max_model_len"]),
            "HOME_MAX_NUM_SEQS": str(recipe["max_num_seqs"]),
            "HOME_MAX_BATCHED_TOKENS": str(recipe["max_batched_tokens"]),
            "HOME_GPU_MEMORY_UTILIZATION": "0.24",
            "HOME_DEFAULT_CHAT_TEMPLATE_KWARGS": json.dumps(recipe["default_chat_template_kwargs"], separators=(",", ":")),
        }
        modelctl.validate_env("home-spark-primary", self.catalog, env, "HOME_")
        env["HOME_MODEL_HOST_PORT"] = "8007"
        with self.assertRaisesRegex(ValueError, "differs from catalog"):
            modelctl.validate_env("home-spark-primary", self.catalog, env, "HOME_")


if __name__ == "__main__":
    unittest.main()
