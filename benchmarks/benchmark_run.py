"""Objective evaluation and benchmark run orchestration."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .benchmark_adapters import invoke, run_sandboxed_command
    from .benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _lstat,
        _prepare_parent,
        _safe_path,
        _safe_reference,
        append_jsonl,
        sha256_json,
        write_json_exclusive,
    )
    from .benchmark_reporting import build_report
else:
    from benchmark_adapters import invoke, run_sandboxed_command
    from benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _lstat,
        _prepare_parent,
        _safe_path,
        _safe_reference,
        append_jsonl,
        sha256_json,
        write_json_exclusive,
    )
    from benchmark_reporting import build_report

def json_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            raise KeyError(path)
    return current


def _workspace_file(workspace: Path, reference: Any, context: str) -> Path | None:
    workspace = _absolute(workspace)
    _safe_path(
        workspace,
        workspace,
        f"{context} workspace",
        directory=True,
        recursive=True,
    )
    if not isinstance(reference, str) or not reference:
        raise BenchmarkError(f"{context} path must be a non-empty string")
    referenced = Path(reference)
    target = _absolute(referenced if referenced.is_absolute() else workspace / referenced)
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise BenchmarkError(f"{context} path must remain under the coding workspace") from exc
    if not target.exists():
        return None
    return _safe_path(target, workspace, context)


def evaluate_check(text: str, check: dict[str, Any], workspace: Path | None = None) -> tuple[bool, str]:
    kind = check.get("type")
    if kind == "contains":
        passed = check["value"] in text
        return passed, f"contains {check['value']!r}"
    if kind == "not_contains":
        passed = check["value"] not in text
        return passed, f"does not contain {check['value']!r}"
    if kind == "regex":
        passed = re.search(check["pattern"], text, re.MULTILINE) is not None
        return passed, f"matches /{check['pattern']}/"
    if kind == "valid_json":
        try:
            json.loads(text)
            return True, "valid JSON"
        except json.JSONDecodeError:
            return False, "valid JSON"
    if kind == "json_path_equals":
        try:
            passed = json_path(json.loads(text), check["path"]) == check["value"]
        except (json.JSONDecodeError, KeyError):
            passed = False
        return passed, f"JSON {check['path']} equals expected value"
    if kind == "file_exists":
        if workspace is None:
            raise BenchmarkError("file_exists requires a coding workspace")
        target = _workspace_file(workspace, check.get("path"), "file_exists")
        return target is not None, f"file exists: {check.get('path')}"
    if kind == "file_contains":
        if workspace is None:
            raise BenchmarkError("file_contains requires a coding workspace")
        target = _workspace_file(workspace, check.get("path"), "file_contains")
        passed = (
            target is not None
            and check["value"] in target.read_text(encoding="utf-8")
        )
        return passed, f"file {check.get('path')} contains expected text"
    if kind == "command":
        if workspace is None:
            raise BenchmarkError("command requires a coding workspace")
        arguments = check.get("argv")
        if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) for item in arguments):
            raise BenchmarkError("command check argv must be a non-empty string array")
        completed = run_sandboxed_command(
            arguments,
            workspace,
            int(check.get("timeout_seconds", 300)),
        )
        expected_exit = int(check.get("expected_exit", 0))
        passed = completed.returncode == expected_exit
        return passed, f"command exits {expected_exit}: {' '.join(arguments)}"
    raise BenchmarkError(f"unsupported objective check type: {kind}")


def evaluate(text: str, checks: list[dict[str, Any]], workspace: Path | None = None) -> dict[str, Any]:
    if workspace is not None:
        workspace = _absolute(workspace)
        _safe_path(
            workspace,
            workspace,
            "candidate-modified coding workspace",
            directory=True,
            recursive=True,
        )
    results = []
    earned = 0.0
    available = 0.0
    for index, check in enumerate(checks):
        weight = float(check.get("weight", 1))
        passed, description = evaluate_check(text, check, workspace)
        results.append({
            "id": check.get("id", f"check-{index + 1}"),
            "passed": passed,
            "weight": weight,
            "description": description,
        })
        available += weight
        if passed:
            earned += weight
    return {
        "score": round(100 * earned / available, 2) if available else 100.0,
        "passed": all(result["passed"] for result in results),
        "checks": results,
    }


def default_output_dir(plan: LoadedPlan) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return plan.path.parents[1] / "results" / f"{stamp}-{plan.value['benchmark_id']}-{uuid.uuid4().hex[:8]}"


def _prepare_output_directory(path: Path) -> Path:
    path = _absolute(path)
    _prepare_parent(path)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        metadata = _lstat(path, "output directory")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise BenchmarkError(f"output directory must be a real directory: {path}")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise BenchmarkError(f"pre-existing output directory is unsafe: {path}")
        try:
            nonempty = next(path.iterdir(), None) is not None
        except OSError as exc:
            raise BenchmarkError(f"cannot inspect output directory {path}: {exc}") from exc
        if nonempty:
            raise BenchmarkError(f"output directory is not empty: {path}")
        path.chmod(0o700)
    metadata = _lstat(path, "output directory")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BenchmarkError(f"output directory must be a real directory: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        path.chmod(0o700)
    return path


def run_benchmark(
    plan: LoadedPlan,
    release: dict[str, Any],
    output_dir: Path | None,
    candidate_id: str | None = None,
) -> Path:
    output_dir = _prepare_output_directory(output_dir or default_output_dir(plan))
    candidates = [candidate for candidate in plan.value["candidates"] if candidate.get("enabled", True)]
    if candidate_id is not None:
        candidates = [candidate for candidate in candidates if candidate["id"] == candidate_id]
        if not candidates:
            raise BenchmarkError(f"enabled candidate not found: {candidate_id}")
    labels = [f"candidate_{index + 1:02d}" for index in range(len(candidates))]
    seed = int(plan.value.get("random_seed", 0))
    shuffled_candidates = list(candidates)
    random.Random(seed).shuffle(shuffled_candidates)
    label_pairs = list(zip(labels, shuffled_candidates, strict=True))
    candidate_map = {label: candidate["id"] for label, candidate in label_pairs}
    run_metadata = {
        "schema_version": SCHEMA_VERSION,
        "run_id": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": plan.value["benchmark_id"],
        "benchmark_version": plan.value["version"],
        "plan_sha256": sha256_json(plan.value),
        "release_sha256": sha256_json(release),
        "release_id": release["release_id"],
        "trials": plan.value["trials"],
        "random_seed": seed,
        "candidate_map": candidate_map,
        "note": "Raw generations may contain sensitive content; this directory must not be committed.",
    }
    write_json_exclusive(output_dir / "run.json", run_metadata)
    write_json_exclusive(output_dir / "release.json", release)
    write_json_exclusive(output_dir / "plan.json", plan.value)
    write_json_exclusive(
        output_dir / "cases.json",
        [{key: value for key, value in case.items() if not key.startswith("_")} for case in plan.cases],
    )
    jobs = [
        (case, label, candidate, trial)
        for trial in range(1, plan.value["trials"] + 1)
        for case in plan.cases
        for label, candidate in label_pairs
    ]
    random.Random(seed).shuffle(jobs)
    for case, label, candidate, trial in jobs:
        temporary_root_value: str | None = None
        record = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case["id"],
            "track": case["track"],
            "case_sha256": sha256_json({key: value for key, value in case.items() if not key.startswith("_")}),
            "candidate_label": label,
            "trial": trial,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            response = invoke(candidate, case, plan)
            workspace_value = response.pop("_workspace", None)
            temporary_root_value = response.pop("_temporary_root", None)
            record.update(response)
            hidden_overlay = case.get("workspace", {}).get("hidden_overlay")
            if workspace_value and hidden_overlay:
                workspace = Path(workspace_value)
                _safe_path(
                    workspace,
                    workspace,
                    "candidate-modified coding workspace",
                    directory=True,
                    recursive=True,
                )
                overlay_source = _safe_reference(
                    Path(case["_source"]).parent,
                    hidden_overlay,
                    plan.root,
                    f"case {case['id']} hidden overlay",
                    directory=True,
                    recursive=True,
                )
                try:
                    shutil.copytree(overlay_source, workspace, dirs_exist_ok=True)
                except OSError as exc:
                    raise BenchmarkError(f"cannot apply hidden overlay for case {case['id']}: {exc}") from exc
            record["objective"] = evaluate(
                response["text"],
                case["objective_checks"],
                Path(workspace_value) if workspace_value else None,
            )
            record["status"] = "completed"
        except BenchmarkError as exc:
            record.update({"status": "error", "error": str(exc), "objective": {"score": 0, "passed": False, "checks": []}})
        finally:
            if temporary_root_value:
                shutil.rmtree(temporary_root_value)
        append_jsonl(output_dir / "generations.jsonl", record)
    build_report(plan, output_dir)
    return output_dir
