#!/usr/bin/env python3
"""Validate and exercise HomeCompute's no-switch model-routing policy."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any


class PolicyError(ValueError):
    """A policy or decision request violates the routing contract."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{name} must be a non-empty string")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PolicyError(f"{name} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise PolicyError(f"{name} must not contain duplicates")
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    allowed_top_level = {
        "schema_version",
        "policy_version",
        "mode",
        "load_on_demand",
        "qualification",
        "default_model",
        "auto",
        "activation",
        "aliases",
        "client_policies",
        "models",
    }
    unknown = set(policy) - allowed_top_level
    if unknown:
        raise PolicyError(f"unknown policy keys: {', '.join(sorted(unknown))}")
    if policy.get("schema_version") != 1:
        raise PolicyError("schema_version must be 1")
    _string(policy.get("policy_version"), "policy_version")
    if policy.get("mode") not in {"disabled", "shadow", "active"}:
        raise PolicyError("mode must be disabled, shadow, or active")
    if not isinstance(policy.get("load_on_demand"), bool):
        raise PolicyError("load_on_demand must be boolean")

    activation = _object(policy.get("activation"), "activation")
    if set(activation) != {"enabled"} or not isinstance(activation.get("enabled"), bool):
        raise PolicyError("activation must contain one boolean enabled field")
    if policy["load_on_demand"] or activation["enabled"]:
        raise PolicyError("model activation is not implemented; load_on_demand and activation.enabled must be false")

    qualification = _object(policy.get("qualification"), "qualification")
    if set(qualification) != {"status", "selected_primary", "resident_text_model_limit"}:
        raise PolicyError("qualification has unsupported or missing keys")
    if qualification["status"] not in {"pending", "qualified"}:
        raise PolicyError("qualification.status must be pending or qualified")
    if qualification["resident_text_model_limit"] != 1:
        raise PolicyError("qualification.resident_text_model_limit must be 1")

    models = _object(policy.get("models"), "models")
    if not models:
        raise PolicyError("models must not be empty")
    for model_name, raw_model in models.items():
        _string(model_name, "model name")
        model = _object(raw_model, f"models.{model_name}")
        required = {
            "upstream_model",
            "selection_lane",
            "quality_rank",
            "auto_eligible",
            "capabilities",
            "protocols",
            "max_context_tokens",
        }
        if set(model) != required:
            raise PolicyError(f"models.{model_name} must contain exactly {', '.join(sorted(required))}")
        _string(model["upstream_model"], f"models.{model_name}.upstream_model")
        if model["selection_lane"] not in {"primary", "heavy"}:
            raise PolicyError(f"models.{model_name}.selection_lane is invalid")
        if not isinstance(model["quality_rank"], int):
            raise PolicyError(f"models.{model_name}.quality_rank must be an integer")
        if not isinstance(model["auto_eligible"], bool):
            raise PolicyError(f"models.{model_name}.auto_eligible must be boolean")
        _string_list(model["capabilities"], f"models.{model_name}.capabilities")
        _string_list(model["protocols"], f"models.{model_name}.protocols")
        if not isinstance(model["max_context_tokens"], int) or model["max_context_tokens"] <= 0:
            raise PolicyError(f"models.{model_name}.max_context_tokens must be a positive integer")

    aliases = _object(policy.get("aliases"), "aliases")
    required_aliases = {"assistant", "automation", "coding", "home", "meeting", "research"}
    if set(aliases) != required_aliases:
        raise PolicyError("aliases must contain exactly the six task-semantic aliases")
    if qualification["status"] == "pending":
        if policy["mode"] != "disabled":
            raise PolicyError("pending qualification requires disabled routing mode")
        selected_primary = _string(qualification["selected_primary"], "qualification.selected_primary")
        if selected_primary not in models or models[selected_primary]["selection_lane"] != "primary":
            raise PolicyError("qualification.selected_primary must reference the selected primary model")
        if policy.get("default_model") is not None:
            raise PolicyError("pending qualification cannot activate a default")
        if any(model_name is not None for model_name in aliases.values()):
            raise PolicyError("pending qualification requires unbound aliases")
        if any(model["auto_eligible"] for model in models.values()):
            raise PolicyError("pending qualification cannot mark models auto-eligible")
    else:
        default_model = _string(policy.get("default_model"), "default_model")
        if default_model not in models:
            raise PolicyError("default_model must reference models")
        selected_primary = _string(qualification["selected_primary"], "qualification.selected_primary")
        if selected_primary not in models or models[selected_primary]["selection_lane"] != "primary":
            raise PolicyError("qualification.selected_primary must reference the primary lane")
        if default_model != selected_primary:
            raise PolicyError("default_model must be the selected primary")
        for alias, model_name in aliases.items():
            if model_name not in models:
                raise PolicyError(f"aliases.{alias} references an unknown model")
        for alias, model_name in aliases.items():
            if model_name != selected_primary:
                raise PolicyError(f"aliases.{alias} must reference the selected primary; heavy mode is operator-swapped")

    auto = _object(policy.get("auto"), "auto")
    if set(auto) != {"confidence_threshold", "low_confidence_policy", "classifier_failure_policy"}:
        raise PolicyError("auto contains unsupported or missing keys")
    confidence = auto["confidence_threshold"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise PolicyError("auto.confidence_threshold must be between 0 and 1")
    if auto["low_confidence_policy"] not in {"strongest_available", "fixed_default", "reject"}:
        raise PolicyError("auto.low_confidence_policy is invalid")
    if auto["classifier_failure_policy"] != "active_default":
        raise PolicyError("auto.classifier_failure_policy must be active_default")

    client_policies = _object(policy.get("client_policies"), "client_policies")
    if not client_policies:
        raise PolicyError("client_policies must not be empty")
    for client_name, raw_client in client_policies.items():
        client = _object(raw_client, f"client_policies.{client_name}")
        required = {"allowed_aliases", "allowed_exact_models", "auto_model_allowlist"}
        if set(client) != required:
            raise PolicyError(f"client_policies.{client_name} has unsupported or missing keys")
        allowed_aliases = _string_list(client["allowed_aliases"], f"client_policies.{client_name}.allowed_aliases")
        unknown_aliases = set(allowed_aliases) - (set(aliases) | {"auto"})
        if unknown_aliases:
            raise PolicyError(f"client_policies.{client_name} contains unknown aliases")
        for field in ("allowed_exact_models", "auto_model_allowlist"):
            values = _string_list(client[field], f"client_policies.{client_name}.{field}")
            if set(values) - set(models):
                raise PolicyError(f"client_policies.{client_name}.{field} contains unknown models")
        if qualification["status"] == "pending" and (
            client["allowed_exact_models"] or client["auto_model_allowlist"]
        ):
            raise PolicyError("pending qualification cannot authorize exact or automatic model selection")


def load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read {path}: {exc}") from exc
    return _object(document, str(path))


def _runtime_models(runtime: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_models = _object(runtime.get("models"), "runtime.models")
    result: dict[str, dict[str, Any]] = {}
    for name, raw_state in raw_models.items():
        state = _object(raw_state, f"runtime.models.{name}")
        if set(state) != {"state", "healthy"}:
            raise PolicyError(f"runtime.models.{name} must contain exactly state and healthy")
        if state["state"] not in {"active", "inactive", "loading", "draining", "failed"}:
            raise PolicyError(f"runtime.models.{name}.state is invalid")
        if not isinstance(state["healthy"], bool):
            raise PolicyError(f"runtime.models.{name}.healthy must be boolean")
        result[name] = state
    return result


def _request_constraints(request: dict[str, Any]) -> tuple[set[str], str, int]:
    requirements = _object(request.get("requirements", {}), "request.requirements")
    capabilities = set(_string_list(requirements.get("capabilities", []), "request.requirements.capabilities"))
    protocol = requirements.get("protocol", "responses")
    if protocol not in {"chat_completions", "responses"}:
        raise PolicyError("request.requirements.protocol is invalid")
    context_tokens = requirements.get("context_tokens", 0)
    if not isinstance(context_tokens, int) or isinstance(context_tokens, bool) or context_tokens < 0:
        raise PolicyError("request.requirements.context_tokens must be a non-negative integer")
    return capabilities, protocol, context_tokens


def _model_is_eligible(
    policy: dict[str, Any],
    model_name: str,
    runtime_models: dict[str, dict[str, Any]],
    constraints: tuple[set[str], str, int],
    *,
    require_auto: bool,
) -> bool:
    capabilities, protocol, context_tokens = constraints
    model = policy["models"][model_name]
    state = runtime_models.get(model_name, {"state": "inactive", "healthy": False})
    return bool(
        (model["auto_eligible"] or not require_auto)
        and state["state"] == "active"
        and state["healthy"]
        and capabilities.issubset(model["capabilities"])
        and protocol in model["protocols"]
        and context_tokens <= model["max_context_tokens"]
    )


def _eligible_models(
    policy: dict[str, Any], request: dict[str, Any], runtime_models: dict[str, dict[str, Any]], client: dict[str, Any]
) -> list[str]:
    constraints = _request_constraints(request)

    eligible: list[str] = []
    for name in client["auto_model_allowlist"]:
        if _model_is_eligible(policy, name, runtime_models, constraints, require_auto=True):
            eligible.append(name)
    return eligible


def _result(
    policy: dict[str, Any],
    requested_model: str,
    selected_model: str,
    selection_mode: str,
    *,
    upstream_model: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "decision_id": str(uuid.uuid4()),
        "policy_version": policy["policy_version"],
        "requested_model": requested_model,
        "selected_model": selected_model,
        "upstream_model": upstream_model or policy["models"][selected_model]["upstream_model"],
        "selection_mode": selection_mode,
        "activation_requested": False,
        **extra,
    }


def decide(policy: dict[str, Any], request: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    validate_policy(policy)
    if policy["qualification"]["status"] != "qualified":
        raise PolicyError("routing_not_qualified")
    requested_model = _string(request.get("requested_model"), "request.requested_model")
    client_policy_name = _string(request.get("client_policy"), "request.client_policy")
    try:
        client = policy["client_policies"][client_policy_name]
    except KeyError as exc:
        raise PolicyError("request.client_policy is unknown") from exc
    runtime_models = _runtime_models(runtime)
    potentially_resident_models = [
        name for name, state in runtime_models.items() if state["state"] != "inactive"
    ]
    if len(potentially_resident_models) > policy["qualification"]["resident_text_model_limit"]:
        raise PolicyError("resident_text_model_limit_exceeded")
    constraints = _request_constraints(request)

    if requested_model in policy["aliases"]:
        if requested_model not in client["allowed_aliases"]:
            raise PolicyError("requested alias is not authorized")
        selected = policy["aliases"][requested_model]
        state = runtime_models.get(selected, {"state": "inactive", "healthy": False})
        if state["state"] != "active" or not state["healthy"]:
            raise PolicyError("model_not_active")
        if not _model_is_eligible(policy, selected, runtime_models, constraints, require_auto=False):
            raise PolicyError("model_not_compatible")
        return _result(
            policy,
            requested_model,
            selected,
            "manual_alias",
            upstream_model=requested_model,
            classifier_invoked=False,
        )

    if requested_model in policy["models"]:
        if requested_model not in client["allowed_exact_models"]:
            raise PolicyError("exact model is not authorized")
        state = runtime_models.get(requested_model, {"state": "inactive", "healthy": False})
        if state["state"] != "active" or not state["healthy"]:
            raise PolicyError("model_not_active")
        if not _model_is_eligible(policy, requested_model, runtime_models, constraints, require_auto=False):
            raise PolicyError("model_not_compatible")
        return _result(policy, requested_model, requested_model, "manual_exact", classifier_invoked=False)

    if requested_model != "auto":
        raise PolicyError("requested model is unknown")
    if "auto" not in client["allowed_aliases"]:
        raise PolicyError("auto routing is not authorized")

    eligible = _eligible_models(policy, request, runtime_models, client)
    default_model = policy["default_model"]
    if default_model not in eligible:
        raise PolicyError("no authorized compatible active default model")

    if policy["mode"] == "disabled":
        return _result(
            policy,
            requested_model,
            default_model,
            "disabled",
            classifier_invoked=False,
            classifier_status="not_called",
            classifier_confidence=None,
            classifier_reason_code=None,
            shadow_model=None,
            selector_fallback=True,
        )

    classifier = _object(request.get("classifier", {"status": "unavailable"}), "request.classifier")
    classifier_status = classifier.get("status")
    if classifier_status not in {"ok", "unavailable", "timeout", "invalid"}:
        raise PolicyError("request.classifier.status is invalid")
    proposed_model = classifier.get("model") if classifier_status == "ok" else None
    confidence = classifier.get("confidence") if classifier_status == "ok" else None
    reason_code = classifier.get("reason_code") if classifier_status == "ok" else None
    if proposed_model is not None and not isinstance(proposed_model, str):
        raise PolicyError("request.classifier.model must be a string")
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1
    ):
        raise PolicyError("request.classifier.confidence must be between 0 and 1")
    if reason_code is not None and not isinstance(reason_code, str):
        raise PolicyError("request.classifier.reason_code must be a string")

    if policy["mode"] == "shadow":
        shadow_model = proposed_model if proposed_model in eligible else None
        return _result(
            policy,
            requested_model,
            default_model,
            policy["mode"],
            classifier_invoked=policy["mode"] == "shadow",
            classifier_status=classifier_status,
            classifier_confidence=confidence,
            classifier_reason_code=reason_code,
            shadow_model=shadow_model,
            selector_fallback=classifier_status != "ok" or shadow_model is None,
        )

    threshold = policy["auto"]["confidence_threshold"]
    if classifier_status == "ok" and proposed_model in eligible and confidence is not None and confidence >= threshold:
        return _result(
            policy,
            requested_model,
            proposed_model,
            "auto",
            classifier_invoked=True,
            classifier_status=classifier_status,
            classifier_confidence=confidence,
            classifier_reason_code=reason_code,
            selector_fallback=False,
        )

    if classifier_status != "ok" or proposed_model not in eligible:
        selected = default_model
        fallback = True
    else:
        low_confidence_policy = policy["auto"]["low_confidence_policy"]
        if low_confidence_policy == "reject":
            raise PolicyError("auto routing confidence is below threshold")
        if low_confidence_policy == "fixed_default":
            selected = default_model
        else:
            selected = max(eligible, key=lambda name: policy["models"][name]["quality_rank"])
        fallback = False
    return _result(
        policy,
        requested_model,
        selected,
        "auto",
        classifier_invoked=True,
        classifier_status=classifier_status,
        classifier_confidence=confidence,
        classifier_reason_code=reason_code,
        selector_fallback=fallback,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--policy", type=Path, required=True)
    decide_parser = subparsers.add_parser("decide")
    decide_parser.add_argument("--policy", type=Path, required=True)
    decide_parser.add_argument("--request", type=Path, required=True)
    decide_parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        policy = load_document(args.policy)
        validate_policy(policy)
        if args.command == "validate":
            print(json.dumps({"valid": True, "policy_version": policy["policy_version"]}, sort_keys=True))
        else:
            request = load_document(args.request)
            runtime = load_document(args.runtime)
            print(json.dumps(decide(policy, request, runtime), sort_keys=True))
    except PolicyError as exc:
        print(f"model-router-policy: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
