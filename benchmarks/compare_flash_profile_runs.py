#!/usr/bin/env python3
"""Compare two cold-swapped Flash profile runs over an identical corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .benchmark_core import BenchmarkError, _safe_path, read_json, write_json_exclusive
else:
    from benchmark_core import BenchmarkError, _safe_path, read_json, write_json_exclusive


def load_run(path: Path, expected_candidate: str) -> dict[str, Any]:
    path = path.absolute()
    _safe_path(path, path.parent, "benchmark run", directory=True, recursive=True)
    metadata = read_json(_safe_path(path / "run.json", path, "run metadata"))
    release = read_json(_safe_path(path / "release.json", path, "release manifest"))
    plan = read_json(_safe_path(path / "plan.json", path, "benchmark plan"))
    cases = read_json(_safe_path(path / "cases.json", path, "case snapshot"))
    generation_path = _safe_path(path / "generations.jsonl", path, "generation records")
    candidate_map = metadata.get("candidate_map")
    if not isinstance(candidate_map, dict) or len(candidate_map) != 1:
        raise BenchmarkError(f"{path} must contain exactly one selected candidate")
    candidate_id = next(iter(candidate_map.values()))
    if candidate_id != expected_candidate:
        raise BenchmarkError(f"{path} contains {candidate_id!r}, expected {expected_candidate!r}")
    plan_candidates = {item.get("id"): item for item in plan.get("candidates", []) if isinstance(item, dict)}
    candidate = plan_candidates.get(candidate_id)
    if not isinstance(candidate, dict):
        raise BenchmarkError(f"{path} plan does not define selected candidate {candidate_id!r}")
    artifact_id = candidate.get("artifact_ref")
    artifacts = [item for item in release.get("artifacts", []) if isinstance(item, dict) and item.get("id") == artifact_id]
    if len(artifacts) != 1:
        raise BenchmarkError(f"{path} release does not identify exactly one artifact for {candidate_id!r}")
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for line_number, line in enumerate(generation_path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
            key = (row["case_id"], row["trial"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BenchmarkError(f"{path} has invalid generation record on line {line_number}") from exc
        if key in rows:
            raise BenchmarkError(f"{path} repeats case/trial {key!r}")
        if row.get("candidate_label") not in candidate_map:
            raise BenchmarkError(f"{path} has an unknown candidate label")
        rows[key] = row
    return {
        "path": path,
        "metadata": metadata,
        "plan": plan,
        "cases": cases,
        "artifact": artifacts[0],
        "rows": rows,
    }


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    rows = list(run["rows"].values())
    completed = [row for row in rows if row.get("status") == "completed"]
    passed = [row for row in completed if row.get("objective", {}).get("passed") is True]
    checks = [check for row in completed for check in row.get("objective", {}).get("checks", [])]
    exact_checks = [check for check in checks if str(check.get("id", "")).startswith("exact-")]
    durations = sorted(row["duration_ms"] for row in completed if isinstance(row.get("duration_ms"), (int, float)))
    def mean_metric(name: str) -> float:
        return round(sum(row.get(name, 0) for row in completed) / len(rows), 2) if rows else 0

    return {
        "artifact_id": run["artifact"]["id"],
        "source": run["artifact"]["source"],
        "revision": run["artifact"]["revision"],
        "runtime": run["artifact"]["runtime"],
        "quantization": run["artifact"]["quantization"],
        "cases": len({case.get("id") for case in run["cases"]}),
        "trials": run["metadata"].get("trials"),
        "records": len(rows),
        "completed": len(completed),
        "objective_pass_rate_percent": round(100 * len(passed) / len(rows), 2) if rows else 0,
        "mean_objective_score": round(sum(row.get("objective", {}).get("score", 0) for row in completed) / len(rows), 2) if rows else 0,
        "exact_check_pass_rate_percent": round(100 * sum(check.get("passed") is True for check in exact_checks) / len(exact_checks), 2) if exact_checks else None,
        "median_duration_ms": durations[(len(durations) - 1) // 2] if durations else None,
        "mean_tool_calls": mean_metric("tool_call_count"),
        "mean_tool_rounds": mean_metric("tool_round_count"),
        "mean_wrong_tool_calls": mean_metric("wrong_tool_count"),
        "mean_duplicate_tool_calls": mean_metric("duplicate_tool_count"),
        "mean_malformed_tool_calls": mean_metric("malformed_tool_count"),
        "mean_tool_errors": mean_metric("tool_error_count"),
        "mean_missing_call_ids": mean_metric("missing_call_id_count"),
        "mean_duplicate_call_ids": mean_metric("duplicate_call_id_count"),
    }


def compare(baseline: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    for field in ("benchmark_id", "benchmark_version", "plan_sha256", "random_seed", "trials"):
        if baseline["metadata"].get(field) != challenger["metadata"].get(field):
            raise BenchmarkError(f"runs differ in {field}; comparison requires the same plan and trial schedule")
    if baseline["cases"] != challenger["cases"]:
        raise BenchmarkError("run case snapshots differ")
    if baseline["rows"].keys() != challenger["rows"].keys():
        raise BenchmarkError("runs do not cover the same case/trial pairs")
    for key in baseline["rows"]:
        if baseline["rows"][key].get("case_sha256") != challenger["rows"][key].get("case_sha256"):
            raise BenchmarkError(f"case fixture hash differs for {key!r}")
    left = summarize(baseline)
    right = summarize(challenger)
    return {
        "schema_version": 1,
        "benchmark_id": baseline["metadata"].get("benchmark_id"),
        "baseline": left,
        "challenger": right,
        "deltas": {
            "objective_pass_rate_percentage_points": round(right["objective_pass_rate_percent"] - left["objective_pass_rate_percent"], 2),
            "mean_objective_score_points": round(right["mean_objective_score"] - left["mean_objective_score"], 2),
            "exact_check_pass_rate_percentage_points": (
                round(right["exact_check_pass_rate_percent"] - left["exact_check_pass_rate_percent"], 2)
                if left["exact_check_pass_rate_percent"] is not None and right["exact_check_pass_rate_percent"] is not None
                else None
            ),
            "median_duration_ms": (
                round(right["median_duration_ms"] - left["median_duration_ms"], 2)
                if left["median_duration_ms"] is not None and right["median_duration_ms"] is not None
                else None
            ),
        },
        "decision": "evidence-only; no promotion recommendation is inferred",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--baseline-candidate", default="flash-quality-blazux-nvidia")
    parser.add_argument("--challenger-run", type=Path, required=True)
    parser.add_argument("--challenger-candidate", default="flash-ultrafast-dime-autoround")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(
            load_run(args.baseline_run, args.baseline_candidate),
            load_run(args.challenger_run, args.challenger_candidate),
        )
        rendered = json.dumps(result, indent=2, sort_keys=True)
        if args.output:
            write_json_exclusive(args.output, result)
            print(args.output.absolute())
        else:
            print(rendered)
        return 0
    except (BenchmarkError, OSError) as exc:
        print(f"flash profile comparison error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
