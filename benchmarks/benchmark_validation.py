"""Benchmark plan, case, candidate, and release validation."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

if __package__:
    from .benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _safe_path,
        _safe_reference,
        read_json,
        require_fields,
    )
else:
    from benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _safe_path,
        _safe_reference,
        read_json,
        require_fields,
    )


SUPPORTED_OBJECTIVE_CHECKS = {
    "contains",
    "not_contains",
    "regex",
    "valid_json",
    "json_path_equals",
    "file_exists",
    "file_contains",
    "command",
}
MAX_COMMAND_TIMEOUT_SECONDS = 3600


def _validate_objective_checks(checks: Any, case_id: str) -> None:
    if not isinstance(checks, list):
        raise BenchmarkError(f"case {case_id} objective_checks must be an array")
    for index, check in enumerate(checks, start=1):
        context = f"case {case_id} objective check {index}"
        if not isinstance(check, dict):
            raise BenchmarkError(f"{context} must be a JSON object")
        kind = check.get("type")
        if kind not in SUPPORTED_OBJECTIVE_CHECKS:
            raise BenchmarkError(f"{context} has unsupported type: {kind}")
        check_id = check.get("id")
        if check_id is not None and (not isinstance(check_id, str) or not check_id):
            raise BenchmarkError(f"{context} id must be a non-empty string")
        weight = check.get("weight", 1)
        if (
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or weight <= 0
        ):
            raise BenchmarkError(f"{context} weight must be a finite positive number")
        if kind in {"contains", "not_contains", "file_contains"} and not isinstance(
            check.get("value"), str
        ):
            raise BenchmarkError(f"{context} value must be a string")
        if kind == "regex":
            pattern = check.get("pattern")
            if not isinstance(pattern, str):
                raise BenchmarkError(f"{context} pattern must be a string")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise BenchmarkError(f"{context} pattern is invalid: {exc}") from exc
        if kind == "json_path_equals":
            if not isinstance(check.get("path"), str) or not check["path"] or "value" not in check:
                raise BenchmarkError(f"{context} requires a non-empty path and value")
        if kind in {"file_exists", "file_contains"} and (
            not isinstance(check.get("path"), str) or not check["path"]
        ):
            raise BenchmarkError(f"{context} path must be a non-empty string")
        if kind == "command":
            arguments = check.get("argv")
            if (
                not isinstance(arguments, list)
                or not arguments
                or not all(isinstance(item, str) and item for item in arguments)
            ):
                raise BenchmarkError(f"{context} argv must be a non-empty string array")
            expected_exit = check.get("expected_exit", 0)
            if (
                not isinstance(expected_exit, int)
                or isinstance(expected_exit, bool)
                or not 0 <= expected_exit <= 255
            ):
                raise BenchmarkError(f"{context} expected_exit must be an integer from 0 to 255")
            timeout = check.get("timeout_seconds", 300)
            if (
                not isinstance(timeout, int)
                or isinstance(timeout, bool)
                or not 1 <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS
            ):
                raise BenchmarkError(
                    f"{context} timeout_seconds must be an integer from 1 to "
                    f"{MAX_COMMAND_TIMEOUT_SECONDS}"
                )

def load_plan(path: Path) -> LoadedPlan:
    path = _absolute(path)
    benchmark_root = path.parent.parent
    _safe_path(path, benchmark_root, "benchmark plan")
    value = read_json(path)
    require_fields(
        value,
        ["schema_version", "benchmark_id", "version", "trials", "cases", "candidates"],
        "plan",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError(f"unsupported plan schema_version: {value['schema_version']}")
    if not isinstance(value["trials"], int) or isinstance(value["trials"], bool) or value["trials"] < 1:
        raise BenchmarkError("plan trials must be a positive integer")
    if not isinstance(value["cases"], list):
        raise BenchmarkError("plan cases must be an array")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reference in value["cases"]:
        case_path = _safe_reference(
            path.parent,
            reference,
            benchmark_root,
            "benchmark case fixture",
        )
        case = read_json(case_path)
        require_fields(case, ["schema_version", "id", "track", "messages", "objective_checks"], str(case_path))
        if case["schema_version"] != SCHEMA_VERSION:
            raise BenchmarkError(f"unsupported case schema_version in {case_path}")
        if case["id"] in seen:
            raise BenchmarkError(f"duplicate case id: {case['id']}")
        if not case["messages"] or not all(
            isinstance(message, dict) and {"role", "content"} <= message.keys()
            for message in case["messages"]
        ):
            raise BenchmarkError(f"case {case['id']} has invalid messages")
        _validate_objective_checks(case["objective_checks"], case["id"])
        if case["track"] == "code-implementation":
            workspace = case.get("workspace")
            if not isinstance(workspace, dict) or "source" not in workspace:
                raise BenchmarkError(f"case {case['id']} needs an existing workspace.source directory")
            _safe_reference(
                case_path.parent,
                workspace["source"],
                benchmark_root,
                f"case {case['id']} workspace source",
                directory=True,
                recursive=True,
            )
            if "hidden_overlay" in workspace:
                _safe_reference(
                    case_path.parent,
                    workspace["hidden_overlay"],
                    benchmark_root,
                    f"case {case['id']} hidden overlay",
                    directory=True,
                    recursive=True,
                )
        seen.add(case["id"])
        case["_source"] = str(case_path)
        cases.append(case)
    if not cases:
        raise BenchmarkError("plan must contain at least one case")
    if not isinstance(value["candidates"], list):
        raise BenchmarkError("plan candidates must be an array")
    candidate_ids = [
        candidate.get("id") if isinstance(candidate, dict) else None
        for candidate in value["candidates"]
    ]
    if not all(isinstance(candidate_id, str) and candidate_id for candidate_id in candidate_ids):
        raise BenchmarkError("candidate ids must be non-empty strings")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise BenchmarkError("candidate ids must be unique")
    return LoadedPlan(path, benchmark_root, value, cases)


def validate_release(plan: LoadedPlan, release_path: Path) -> dict[str, Any]:
    release = read_json(release_path)
    if not isinstance(release, dict):
        raise BenchmarkError("release manifest must be a JSON object")
    require_fields(
        release,
        ["schema_version", "release_id", "benchmark_commit", "environment", "artifacts"],
        "release manifest",
    )
    if release["schema_version"] != SCHEMA_VERSION:
        raise BenchmarkError(f"unsupported release schema_version: {release['schema_version']}")
    if not isinstance(release["environment"], dict):
        raise BenchmarkError("release environment must be a JSON object")
    require_fields(release["environment"], ["kind", "hardware", "operating_system"], "release environment")
    if not isinstance(release["artifacts"], list) or not release["artifacts"]:
        raise BenchmarkError("release manifest must contain at least one artifact")
    artifact_counts: dict[str, int] = {}
    for artifact in release["artifacts"]:
        if not isinstance(artifact, dict):
            raise BenchmarkError("each release artifact must be a JSON object")
        require_fields(
            artifact,
            ["id", "source", "revision", "runtime", "quantization"],
            "release artifact",
        )
        expected_fields = {"id", "source", "revision", "runtime", "quantization"}
        if set(artifact) != expected_fields:
            raise BenchmarkError(
                "release artifact fields must be exactly: "
                + ", ".join(sorted(expected_fields))
            )
        if not all(
            isinstance(artifact[field], str) and artifact[field]
            for field in ("id", "source", "revision", "runtime", "quantization")
        ):
            raise BenchmarkError("release artifact provenance fields must be non-empty strings")
        artifact_counts[artifact["id"]] = artifact_counts.get(artifact["id"], 0) + 1
    for candidate in plan.value["candidates"]:
        if not candidate.get("enabled", True):
            continue
        artifact_ref = candidate.get("artifact_ref")
        matches = artifact_counts.get(artifact_ref, 0) if isinstance(artifact_ref, str) else 0
        if matches == 0:
            raise BenchmarkError(
                f"enabled candidate {candidate.get('id')} has no matching release artifact"
            )
        if matches != 1:
            raise BenchmarkError(
                f"enabled candidate {candidate.get('id')} has ambiguous release artifact linkage"
            )
    return release


def validate_candidate(candidate: dict[str, Any], plan: LoadedPlan) -> None:
    require_fields(candidate, ["id", "adapter", "model", "artifact_ref"], "candidate")
    adapter = candidate["adapter"]
    if adapter == "mock":
        if "responses_file" not in candidate:
            raise BenchmarkError(f"mock candidate {candidate['id']} needs responses_file")
        responses_path = _safe_reference(
            plan.path.parent,
            candidate["responses_file"],
            plan.root,
            f"mock candidate {candidate['id']} response fixture",
        )
        read_json(responses_path)
    elif adapter in {"openrouter", "openai_compatible"}:
        if adapter == "openai_compatible" and "base_url" not in candidate:
            raise BenchmarkError(f"candidate {candidate['id']} needs base_url")
        if adapter == "openrouter":
            provider = candidate.get("request", {}).get("provider", {})
            if not provider.get("only") or provider.get("allow_fallbacks") is not False:
                raise BenchmarkError(
                    f"OpenRouter candidate {candidate['id']} must pin provider.only and disable fallbacks"
                )
    elif adapter == "n8n_webhook":
        if not candidate.get("webhook_url") and not candidate.get("webhook_url_env"):
            raise BenchmarkError(f"n8n candidate {candidate['id']} needs webhook_url or webhook_url_env")
        if candidate.get("safety_acknowledgement") not in {
            "synthetic-inputs-no-side-effects",
            "real-read-only-data-no-side-effects",
        }:
            raise BenchmarkError(
                f"n8n candidate {candidate['id']} must acknowledge its allowed data class and no side effects"
            )
    elif adapter == "codex_exec":
        require_fields(candidate, ["base_url", "api_key_env"], f"Codex candidate {candidate['id']}")
        if candidate["api_key_env"] in {
            "CODEX_HOME", "HOME", "LANG", "LC_ALL", "PATH", "TMPDIR",
        }:
            raise BenchmarkError(
                f"Codex candidate {candidate['id']} api_key_env conflicts with isolated runtime"
            )
        if candidate.get("wire_api", "responses") != "responses":
            raise BenchmarkError(f"Codex candidate {candidate['id']} must use the Responses wire API")
    else:
        raise BenchmarkError(f"unsupported adapter for {candidate['id']}: {adapter}")


def validate_all(plan_path: Path, release_path: Path) -> tuple[LoadedPlan, dict[str, Any]]:
    plan = load_plan(plan_path)
    release = validate_release(plan, release_path)
    enabled = [candidate for candidate in plan.value["candidates"] if candidate.get("enabled", True)]
    if not enabled:
        raise BenchmarkError("plan has no enabled candidates")
    for candidate in enabled:
        validate_candidate(candidate, plan)
    return plan, release
