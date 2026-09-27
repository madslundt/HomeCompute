#!/usr/bin/env python3
"""Validate HomeCompute model artifacts, deployments, and capability routes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/model-catalog.json"
ROUTES_PATH = ROOT / "config/capability-routes.json"
LITELLM_PATH = ROOT / "deploy/control-plane/litellm-config.yaml"
YAML_BLOCK = re.compile(r"^model_list:\n.*?(?=^litellm_settings:\n)", re.MULTILINE | re.DOTALL)
REVISION = re.compile(r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
LIFECYCLES = {"resident", "hot-standby", "cold-standby", "operator-on-demand", "disabled"}
AVAILABILITY = {"active", "intentionally-stopped", "candidate", "unavailable"}


class RegistryError(ValueError):
    """The model registry or its generated gateway representation is invalid."""


def obj(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{name} must be an object")
    return value


def string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{name} must be a non-empty string")
    return value


def string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise RegistryError(f"{name} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise RegistryError(f"{name} must not contain duplicates")
    if not value and not allow_empty:
        raise RegistryError(f"{name} must not be empty")
    return value


def reject_secrets(value: Any, name: str = "configuration") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in ("api_key", "password", "secret", "access_token")):
                raise RegistryError(f"{name} contains secret-bearing field {key!r}")
            reject_secrets(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secrets(child, f"{name}[{index}]")


def validate(catalog: dict[str, Any], routes_doc: dict[str, Any]) -> None:
    reject_secrets(catalog, "catalog")
    reject_secrets(routes_doc, "routes")
    if catalog.get("schema_version") != 1 or routes_doc.get("schema_version") != 1:
        raise RegistryError("catalog and routes schema_version must be 1")

    runtime_profiles = obj(catalog.get("runtime_profiles"), "runtime_profiles")
    if not runtime_profiles:
        raise RegistryError("runtime_profiles must not be empty")
    for runtime_id, raw_runtime in runtime_profiles.items():
        runtime = obj(raw_runtime, f"runtime_profiles.{runtime_id}")
        string(runtime.get("family"), f"runtime_profiles.{runtime_id}.family")
        string_list(runtime.get("supported_protocols"), f"runtime_profiles.{runtime_id}.supported_protocols")
        string_list(runtime.get("capabilities"), f"runtime_profiles.{runtime_id}.capabilities")

    artifacts = obj(catalog.get("artifacts"), "artifacts")
    deployments = obj(catalog.get("deployments"), "deployments")
    if not artifacts or not deployments:
        raise RegistryError("artifacts and deployments must not be empty")
    artifact_capability_sets: dict[str, set[str]] = {}
    for artifact_id, raw_artifact in artifacts.items():
        artifact = obj(raw_artifact, f"artifacts.{artifact_id}")
        string(artifact.get("upstream_model_id"), f"artifacts.{artifact_id}.upstream_model_id")
        revision = string(artifact.get("revision"), f"artifacts.{artifact_id}.revision")
        if not REVISION.fullmatch(revision):
            raise RegistryError(f"artifacts.{artifact_id}.revision must be an immutable commit or sha256 digest")
        if artifact.get("tokenizer_revision") != revision or artifact.get("code_revision") != revision:
            raise RegistryError(f"artifacts.{artifact_id} tokenizer/code revisions must be pinned explicitly")
        string(artifact.get("license_id"), f"artifacts.{artifact_id}.license_id")
        string(artifact.get("quantization"), f"artifacts.{artifact_id}.quantization")
        context = artifact.get("max_context_tokens")
        if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
            raise RegistryError(f"artifacts.{artifact_id}.max_context_tokens must be positive")
        artifact_runtimes = string_list(artifact.get("runtime_profiles"), f"artifacts.{artifact_id}.runtime_profiles")
        capabilities = set(string_list(artifact.get("capabilities"), f"artifacts.{artifact_id}.capabilities"))
        unknown = set(artifact_runtimes) - set(runtime_profiles)
        if unknown:
            raise RegistryError(f"artifacts.{artifact_id} references unknown runtime profile(s): {', '.join(sorted(unknown))}")
        for capability, status in obj(artifact.get("qualification"), f"artifacts.{artifact_id}.qualification").items():
            if status not in {"qualified", "candidate", "failed", "not-tested"}:
                raise RegistryError(f"artifacts.{artifact_id}.qualification.{capability} is invalid")
            if capability == "" or not isinstance(capability, str):
                raise RegistryError(f"artifacts.{artifact_id} qualification names must be non-empty")
        artifact_capability_sets[artifact_id] = capabilities

    for deployment_id, raw_deployment in deployments.items():
        deployment = obj(raw_deployment, f"deployments.{deployment_id}")
        artifact_id = string(deployment.get("artifact"), f"deployments.{deployment_id}.artifact")
        runtime_id = string(deployment.get("runtime_profile"), f"deployments.{deployment_id}.runtime_profile")
        if artifact_id not in artifacts:
            raise RegistryError(f"deployments.{deployment_id} references unknown artifact {artifact_id}")
        if runtime_id not in runtime_profiles or runtime_id not in artifacts[artifact_id]["runtime_profiles"]:
            raise RegistryError(f"deployments.{deployment_id} selects an unsupported runtime profile")
        string(deployment.get("host_role"), f"deployments.{deployment_id}.host_role")
        if deployment.get("network_scope") not in {"private_local", "cloud"}:
            raise RegistryError(f"deployments.{deployment_id}.network_scope is invalid")
        endpoint_env = string(deployment.get("endpoint_env"), f"deployments.{deployment_id}.endpoint_env")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*_BASE_URL", endpoint_env):
            raise RegistryError(f"deployments.{deployment_id}.endpoint_env must reference a named environment variable")
        string(deployment.get("served_model_name"), f"deployments.{deployment_id}.served_model_name")
        context = deployment.get("context_tokens")
        if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
            raise RegistryError(f"deployments.{deployment_id}.context_tokens must be positive")
        if context > artifacts[artifact_id]["max_context_tokens"]:
            raise RegistryError(f"deployments.{deployment_id} exceeds its artifact context limit")
        concurrency = deployment.get("concurrency")
        if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
            raise RegistryError(f"deployments.{deployment_id}.concurrency must be positive")
        if deployment.get("lifecycle") not in LIFECYCLES:
            raise RegistryError(f"deployments.{deployment_id}.lifecycle is invalid")
        if deployment.get("availability") not in AVAILABILITY:
            raise RegistryError(f"deployments.{deployment_id}.availability is invalid")
        priority = deployment.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            raise RegistryError(f"deployments.{deployment_id}.priority must be positive")

    timeouts = obj(routes_doc.get("timeout_profiles"), "timeout_profiles")
    for timeout_id, raw_timeout in timeouts.items():
        timeout = obj(raw_timeout, f"timeout_profiles.{timeout_id}")
        seconds = timeout.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
            raise RegistryError(f"timeout_profiles.{timeout_id}.seconds must be a positive integer")
    routes = obj(routes_doc.get("routes"), "routes")
    for alias, raw_route in routes.items():
        route = obj(raw_route, f"routes.{alias}")
        if alias == "auto":
            raise RegistryError("auto is not a supported capability; clients must choose an explicit alias")
        state = route.get("state")
        if state not in {"active", "candidate", "disabled"}:
            raise RegistryError(f"routes.{alias}.state is invalid")
        if route.get("privacy") != "local_only":
            raise RegistryError(f"routes.{alias}.privacy must be local_only")
        required_capabilities = set(string_list(route.get("required_capabilities"), f"routes.{alias}.required_capabilities"))
        protocols = set(string_list(route.get("required_protocols"), f"routes.{alias}.required_protocols"))
        minimum_context = route.get("minimum_context_tokens")
        if not isinstance(minimum_context, int) or isinstance(minimum_context, bool) or minimum_context <= 0:
            raise RegistryError(f"routes.{alias}.minimum_context_tokens must be positive")
        timeout_id = string(route.get("timeout_profile"), f"routes.{alias}.timeout_profile")
        qualification_capability = route.get("qualification_capability", alias)
        if not isinstance(qualification_capability, str) or not qualification_capability:
            raise RegistryError(f"routes.{alias}.qualification_capability must be non-empty")
        if timeout_id not in timeouts:
            raise RegistryError(f"routes.{alias} references unknown timeout profile {timeout_id}")
        deployment_ids = string_list(route.get("deployments"), f"routes.{alias}.deployments", allow_empty=state == "disabled")
        if state == "disabled" and deployment_ids:
            raise RegistryError(f"disabled route {alias} must not contain deployments")
        if state != "disabled" and not deployment_ids:
            raise RegistryError(f"enabled route {alias} must contain at least one deployment")
        if state == "active" and route.get("kind") in {"candidate", "canary"}:
            raise RegistryError(f"candidate route {alias} cannot be active")
        for deployment_id in deployment_ids:
            if deployment_id not in deployments:
                raise RegistryError(f"routes.{alias} references unknown deployment {deployment_id}")
            deployment = deployments[deployment_id]
            if route["privacy"] == "local_only" and deployment.get("network_scope") != "private_local":
                raise RegistryError(f"local-only route {alias} references a non-local deployment")
            if state == "active" and deployment.get("availability") != "active":
                raise RegistryError(f"active route {alias} references a deployment that is not active")
            if state == "active" and deployment.get("lifecycle") not in {"resident", "hot-standby"}:
                raise RegistryError(f"active route {alias} references a non-resident deployment")
            artifact = artifacts[deployment["artifact"]]
            runtime = runtime_profiles[deployment["runtime_profile"]]
            if not required_capabilities.issubset(artifact_capability_sets[deployment["artifact"]]):
                raise RegistryError(f"deployment {deployment_id} lacks a capability required by {alias}")
            if not required_capabilities.issubset(runtime["capabilities"]):
                raise RegistryError(f"runtime for {deployment_id} lacks a capability required by {alias}")
            if not protocols.issubset(runtime["supported_protocols"]):
                raise RegistryError(f"runtime for {deployment_id} lacks a protocol required by {alias}")
            if deployment["context_tokens"] < minimum_context:
                raise RegistryError(f"deployment {deployment_id} does not meet {alias}'s context requirement")
            if state == "active" and artifact.get("qualification", {}).get(qualification_capability) != "qualified":
                raise RegistryError(f"active route {alias} includes an artifact not qualified for that capability")


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_model_list(catalog: dict[str, Any], routes_doc: dict[str, Any]) -> str:
    deployments = catalog["deployments"]
    lines = ["model_list:"]
    for alias, route in routes_doc["routes"].items():
        if route["state"] == "disabled":
            continue
        ordered = sorted(route["deployments"], key=lambda deployment_id: deployments[deployment_id]["priority"])
        for position, deployment_id in enumerate(ordered, start=1):
            deployment = deployments[deployment_id]
            timeout = routes_doc["timeout_profiles"][route["timeout_profile"]]["seconds"]
            lines.extend(
                [
                    f"  - model_name: {_yaml_quote(alias)}",
                    "    litellm_params:",
                    f"      model: {_yaml_quote('openai/' + deployment['served_model_name'])}",
                    f"      order: {position}",
                    f"      api_base: {_yaml_quote('os.environ/' + deployment['endpoint_env'])}",
                    "      api_key: os.environ/COMPUTE_API_KEY",
                    f"      timeout: {timeout}",
                    f"      stream_timeout: {timeout}",
                ]
            )
    return "\n".join(lines) + "\n"


def render_config(current_text: str, catalog: dict[str, Any], routes_doc: dict[str, Any]) -> str:
    matches = list(YAML_BLOCK.finditer(current_text))
    if len(matches) != 1:
        raise RegistryError("LiteLLM config must contain exactly one model_list block before litellm_settings")
    match = matches[0]
    return current_text[:match.start()] + render_model_list(catalog, routes_doc) + current_text[match.end():]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return obj(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    validate_parser.add_argument("--routes", type=Path, default=ROUTES_PATH)
    for command in ("render", "check"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
        command_parser.add_argument("--routes", type=Path, default=ROUTES_PATH)
        command_parser.add_argument("--config", type=Path, default=LITELLM_PATH)
        if command == "render":
            command_parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        catalog = load_json(args.catalog)
        routes_doc = load_json(args.routes)
        validate(catalog, routes_doc)
        if args.command == "validate":
            print(json.dumps({"catalog_version": catalog["catalog_version"], "valid": True}, sort_keys=True))
            return 0
        current = args.config.read_text(encoding="utf-8")
        rendered = render_config(current, catalog, routes_doc)
        if args.command == "check":
            if rendered != current:
                print("LiteLLM configuration is stale; run scripts/model_registry.py render", file=sys.stderr)
                return 1
            print("LiteLLM configuration matches the canonical registry")
            return 0
        output = args.output or args.config
        output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output}")
        return 0
    except (RegistryError, OSError) as exc:
        print(f"model-registry: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
