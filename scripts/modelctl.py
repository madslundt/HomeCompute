#!/usr/bin/env python3
"""Inspect and validate catalog-backed compute deployments."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_registry() -> dict[str, Any]:
    return json.loads((ROOT / "config/model-catalog.json").read_text(encoding="utf-8"))


def deployment_tuple(deployment_id: str, catalog: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    deployments = catalog["deployments"]
    if deployment_id not in deployments:
        raise ValueError(f"unknown deployment: {deployment_id}")
    deployment = deployments[deployment_id]
    artifact = catalog["artifacts"][deployment["artifact"]]
    runtime = catalog["runtime_profiles"][deployment["runtime_profile"]]
    return deployment, artifact, runtime


def validate_env(deployment_id: str, catalog: dict[str, Any], environ: dict[str, str], prefix: str = "") -> None:
    deployment, artifact, runtime = deployment_tuple(deployment_id, catalog)
    runtime_config = deployment.get("runtime_config", {})
    key = lambda name: f"{prefix}{name}"
    artifact_key = lambda name: f"TEXT_{name}" if not prefix else key(name)
    expected = {
        key("MODEL_ID"): artifact["upstream_model_id"],
        key("MODEL_REVISION"): artifact["revision"],
        key("TOKENIZER_REVISION"): artifact["tokenizer_revision"],
        key("CODE_REVISION"): artifact["code_revision"],
        key("MODEL_LICENSE_ID"): artifact["license_id"],
        key("MODEL_QUANTIZATION"): artifact["quantization"].lower(),
        key("MODEL_PROVENANCE_URL"): artifact.get("provenance_url"),
        key("MODEL_WEIGHT_FORMAT"): artifact.get("weight_format"),
        key("CHAT_TEMPLATE_SHA256"): artifact.get("chat_template_sha256"),
        artifact_key("ARTIFACT_MAX_BYTES"): None if "artifact_max_bytes" not in artifact else str(artifact["artifact_max_bytes"]),
        artifact_key("ARTIFACT_MAX_FILES"): None if "artifact_max_files" not in artifact else str(artifact["artifact_max_files"]),
        "VLLM_IMAGE": runtime.get("image"),
        key("MODEL_HOST_PORT" if prefix == "HOME_" else "HOST_PORT" if prefix else "VLLM_HOST_PORT"): str(deployment.get("compute_host_port", runtime_config.get("host_port", ""))),
        key("MAX_MODEL_LEN" if prefix else "VLLM_MAX_MODEL_LEN"): str(runtime_config.get("max_model_len", "")),
        key("MAX_NUM_SEQS" if prefix else "VLLM_MAX_NUM_SEQS"): str(runtime_config.get("max_num_seqs", "")),
        key("MAX_BATCHED_TOKENS" if prefix else "VLLM_MAX_BATCHED_TOKENS"): str(runtime_config.get("max_batched_tokens", "")),
        key("GPU_MEMORY_UTILIZATION" if prefix else "VLLM_GPU_MEMORY_UTILIZATION"): str(runtime_config.get("gpu_memory_utilization", "")),
        key("ATTENTION_BACKEND" if prefix else "VLLM_ATTENTION_BACKEND"): runtime_config.get("attention_backend"),
        key("MOE_BACKEND" if prefix else "VLLM_MOE_BACKEND"): runtime_config.get("moe_backend"),
        key("FP8_MOE_BACKEND"): runtime_config.get("fp8_moe_backend"),
        key("REASONING_PARSER" if prefix else "VLLM_REASONING_PARSER"): runtime_config.get("reasoning_parser"),
        key("TOOL_CALL_PARSER" if prefix else "VLLM_TOOL_CALL_PARSER"): runtime_config.get("tool_call_parser"),
        key("SPECULATIVE_CONFIG" if prefix else "VLLM_SPECULATIVE_CONFIG"): None if "speculative_config" not in runtime_config else json.dumps(
            runtime_config.get("speculative_config"), separators=(",", ":")
        ) if runtime_config.get("speculative_config") is not None else "",
        key("DEFAULT_CHAT_TEMPLATE_KWARGS" if prefix else "VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS"): json.dumps(
            runtime_config.get("default_chat_template_kwargs", {}), separators=(",", ":")
        ),
    }
    failures = []
    for name, value in expected.items():
        if value is None:
            continue
        actual = environ.get(name)
        if name.endswith("MODEL_QUANTIZATION") and actual is not None:
            actual, value = actual.lower(), value.lower()
        elif name.endswith("GPU_MEMORY_UTILIZATION") and actual is not None:
            try:
                actual, value = float(actual), float(value)
            except ValueError:
                pass
        if actual != value:
            failures.append(f"{name} differs from catalog")
    if deployment.get("network_scope") != "private_local":
        failures.append("compute launcher only accepts private_local deployments")
    if deployment.get("lifecycle") not in {"resident", "hot-standby", "disabled"}:
        failures.append("deployment lifecycle is not supported by this launcher")
    if failures:
        raise ValueError("; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("show", help="show one catalog deployment")
    show.add_argument("--deployment", required=True)
    validate = subparsers.add_parser("validate", help="compare the environment with the catalog")
    validate.add_argument("--deployment", required=True)
    validate.add_argument("--prefix", default="", help="optional environment prefix for isolated profiles")
    args = parser.parse_args()
    try:
        catalog = load_registry()
        deployment, artifact, runtime = deployment_tuple(args.deployment, catalog)
        if args.command == "show":
            print(json.dumps({"deployment": deployment, "artifact": artifact, "runtime_profile": runtime}, indent=2))
        else:
            validate_env(args.deployment, catalog, dict(os.environ), args.prefix)
            print(f"deployment {args.deployment} matches catalog")
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
        print(f"modelctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
