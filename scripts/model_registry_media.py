#!/usr/bin/env python3
"""Media registry contracts kept independent from text context requirements."""
from __future__ import annotations

import json
import re
from typing import Any

REVISION = re.compile(r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
SEMANTIC_ALIAS = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
LIFECYCLES = {"resident", "hot-standby", "cold-standby", "operator-on-demand", "disabled"}
AVAILABILITY = {"active", "intentionally-stopped", "candidate", "unavailable"}


class MediaRegistry:
    def __init__(self, error_type: type[ValueError] = ValueError):
        self.error_type = error_type

    def obj(self, value: Any, name: str) -> dict:
        if not isinstance(value, dict):
            raise self.error_type(f"{name} must be an object")
        return value

    def string(self, value: Any, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise self.error_type(f"{name} must be a non-empty string")
        return value

    def string_list(self, value: Any, name: str, *, allow_empty=False) -> list[str]:
        if (not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
                or len(value) != len(set(value)) or not value and not allow_empty):
            raise self.error_type(f"{name} must be a nonempty list of unique strings")
        return value

    def validate_tts(self, catalog: dict[str, Any], routes_doc: dict[str, Any]) -> None:
        """Validate text-to-speech deployments without imposing text-model fields."""
        deployments = self.obj(catalog.get("tts_deployments", {}), "tts_deployments")
        for deployment_id, raw_deployment in deployments.items():
            name = f"tts_deployments.{deployment_id}"
            deployment = self.obj(raw_deployment, name)
            self.string(deployment.get("upstream_model_id"), f"{name}.upstream_model_id")
            for revision_field in ("revision", "code_revision"):
                revision = self.string(deployment.get(revision_field), f"{name}.{revision_field}")
                if not REVISION.fullmatch(revision):
                    raise self.error_type(f"{name}.{revision_field} must be an immutable commit or sha256 digest")
            self.string(deployment.get("license_id"), f"{name}.license_id")
            self.string(deployment.get("host_role"), f"{name}.host_role")
            self.string(deployment.get("served_model_name"), f"{name}.served_model_name")
            self.string(deployment.get("voice_alias"), f"{name}.voice_alias")
            endpoint = self.string(deployment.get("endpoint_env"), f"{name}.endpoint_env")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*_BASE_URL", endpoint):
                raise self.error_type(f"{name}.endpoint_env must reference a named environment variable")
            if deployment.get("network_scope") != "private_local":
                raise self.error_type(f"{name}.network_scope must be private_local")
            for field in ("concurrency", "priority"):
                value = deployment.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise self.error_type(f"{name}.{field} must be positive")
            if deployment.get("lifecycle") not in LIFECYCLES or deployment.get("availability") not in AVAILABILITY:
                raise self.error_type(f"{name} lifecycle/availability is invalid")

        identities = {
            re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")
            for identity in [*deployments, *(item["upstream_model_id"] for item in deployments.values())]
        }
        used_aliases = set(routes_doc["routes"]) | set(routes_doc.get("transcription_routes", {}))
        for alias, raw_route in self.obj(routes_doc.get("tts_routes", {}), "tts_routes").items():
            name = f"tts_routes.{alias}"
            route = self.obj(raw_route, name)
            if not SEMANTIC_ALIAS.fullmatch(alias) or alias == "auto" or alias in identities:
                raise self.error_type(f"{name} must use a lowercase semantic alias, not a checkpoint identity")
            if alias in used_aliases:
                raise self.error_type(f"route alias {alias!r} is already used by another route")
            if route.get("privacy") != "local_only":
                raise self.error_type(f"{name}.privacy must be local_only")
            state = route.get("state")
            if state not in {"active", "candidate", "disabled"}:
                raise self.error_type(f"{name}.state is invalid")
            timeout_id = self.string(route.get("timeout_profile"), f"{name}.timeout_profile")
            if timeout_id not in routes_doc["timeout_profiles"]:
                raise self.error_type(f"{name} references unknown timeout profile")
            selected = self.string_list(route.get("deployments"), f"{name}.deployments", allow_empty=state == "disabled")
            if (state == "disabled") != (not selected):
                raise self.error_type(f"{name} disabled state and deployments do not agree")
            for deployment_id in selected:
                if deployment_id not in deployments:
                    raise self.error_type(f"{name} references unknown TTS deployment {deployment_id}")
                deployment = deployments[deployment_id]
                if state == "active" and deployment["availability"] != "active":
                    raise self.error_type(f"active route {alias} references a deployment that is not active")
                if state == "active" and deployment["lifecycle"] not in {"resident", "hot-standby"}:
                    raise self.error_type(f"active route {alias} references a non-resident deployment")

    def validate_video(self, catalog: dict[str, Any], routes_doc: dict[str, Any]) -> None:
        deployments = self.obj(catalog.get("video_deployments", {}), "video_deployments")
        for identifier, raw in deployments.items():
            name = f"video_deployments.{identifier}"
            item = self.obj(raw, name)
            if any(field in item for field in ("context_tokens", "max_context_tokens", "tokenizer_revision")):
                raise self.error_type(f"{name} must not declare a text context or tokenizer requirement")
            for field in ("upstream_model_id", "license_id", "host_role", "served_model_name", "quantization"):
                self.string(item.get(field), f"{name}.{field}")
            for field in ("revision", "code_revision"):
                if not REVISION.fullmatch(self.string(item.get(field), f"{name}.{field}")):
                    raise self.error_type(f"{name}.{field} must be an immutable commit or sha256 digest")
            endpoint = self.string(item.get("endpoint_env"), f"{name}.endpoint_env")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*_BASE_URL", endpoint):
                raise self.error_type(f"{name}.endpoint_env must reference a named environment variable")
            if item.get("network_scope") != "private_local":
                raise self.error_type(f"{name}.network_scope must be private_local")
            runtime_id = self.string(item.get("runtime_profile"), f"{name}.runtime_profile")
            runtime = catalog["runtime_profiles"].get(runtime_id, {})
            if ("videos" not in runtime.get("supported_protocols", [])
                    or "video_generation" not in runtime.get("capabilities", [])):
                raise self.error_type(f"runtime for {identifier} lacks the video protocol/capability")
            for field in ("concurrency", "priority"):
                if type(item.get(field)) is not int or item[field] < 1:
                    raise self.error_type(f"{name}.{field} must be positive")
            if item.get("lifecycle") not in LIFECYCLES or item.get("availability") not in AVAILABILITY:
                raise self.error_type(f"{name} lifecycle/availability is invalid")
            if item.get("qualification") not in {"qualified", "candidate", "failed", "not-tested"}:
                raise self.error_type(f"{name}.qualification is invalid")
        identities = {
            re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
            for value in [*deployments, *(item["upstream_model_id"] for item in deployments.values()),
                          *(item["served_model_name"] for item in deployments.values())]
        }
        used = set(routes_doc["routes"]) | set(routes_doc.get("transcription_routes", {})) | set(routes_doc.get("tts_routes", {}))
        for alias, raw in self.obj(routes_doc.get("video_routes", {}), "video_routes").items():
            name = f"video_routes.{alias}"
            route = self.obj(raw, name)
            if not SEMANTIC_ALIAS.fullmatch(alias) or alias == "auto" or alias in identities:
                raise self.error_type(f"{name} must use a lowercase semantic alias, not a checkpoint identity")
            if alias in used:
                raise self.error_type(f"route alias {alias!r} is already used by another route")
            if "minimum_context_tokens" in route:
                raise self.error_type(f"{name} must not declare a text context requirement")
            state = route.get("state")
            if state not in {"active", "candidate", "disabled"} or route.get("privacy") != "local_only":
                raise self.error_type(f"{name} must have a valid state and local_only privacy")
            if route.get("timeout_profile") not in routes_doc["timeout_profiles"]:
                raise self.error_type(f"{name} references unknown timeout profile")
            selected = self.string_list(route.get("deployments"), f"{name}.deployments", allow_empty=state == "disabled")
            if (state == "disabled") != (not selected):
                raise self.error_type(f"{name} disabled state and deployments do not agree")
            for identifier in selected:
                if identifier not in deployments:
                    raise self.error_type(f"{name} references unknown video deployment {identifier}")
                item = deployments[identifier]
                if state == "active" and item["availability"] != "active":
                    raise self.error_type(f"active route {alias} references a deployment that is not active")
                if state == "active" and item["qualification"] != "qualified":
                    raise self.error_type(f"active route {alias} references an unqualified video adapter")
                if state == "active" and item["lifecycle"] not in {"resident", "hot-standby", "operator-on-demand"}:
                    raise self.error_type(f"active route {alias} references an unavailable video lifecycle")

    @staticmethod
    def render_video(catalog: dict[str, Any], routes_doc: dict[str, Any]) -> list[str]:
        lines = []
        deployments = catalog.get("video_deployments", {})
        quote = lambda value: json.dumps(value, ensure_ascii=False)
        for alias, route in routes_doc.get("video_routes", {}).items():
            if route["state"] == "disabled":
                continue
            ordered = sorted(route["deployments"], key=lambda value: deployments[value]["priority"])
            for position, identifier in enumerate(ordered, 1):
                item = deployments[identifier]
                timeout = routes_doc["timeout_profiles"][route["timeout_profile"]]["seconds"]
                lines.extend([
                    f"  - model_name: {quote(alias)}", "    litellm_params:",
                    f"      model: {quote('openai/' + item['served_model_name'])}",
                    f"      order: {position}",
                    f"      api_base: {quote('os.environ/' + item['endpoint_env'])}",
                    '      api_key: "local"', f"      timeout: {timeout}",
                    "    model_info:", "      mode: video_generation",
                    "      input_cost_per_token: 0", "      output_cost_per_token: 0",
                    "      input_cost_per_second: 0", "      output_cost_per_second: 0",
                ])
        return lines
