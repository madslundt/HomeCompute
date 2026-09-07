"""Validate and rank retained benchmark evidence without promoting candidates."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
__all__ = ["SelectionError", "select_run"]


class SelectionError(Exception):
    """Retained evidence cannot produce a trustworthy selection."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SelectionError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SelectionError(f"invalid JSON in {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionError(f"invalid JSON in {path} line {number}: {exc}") from exc
        if not isinstance(row, dict):
            raise SelectionError(f"{path} line {number} must contain a JSON object")
        rows.append(row)
    return rows


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _require_fields(value: dict[str, Any], fields: list[str], context: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise SelectionError(f"{context} is missing: {', '.join(missing)}")


def select_run(output_dir: Path, minimum_quality: float, minimum_pass_rate: float) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    run = _read_json(output_dir / "run.json")
    plan = _read_json(output_dir / "plan.json")
    release = _read_json(output_dir / "release.json")
    case_rows = _read_json(output_dir / "cases.json")
    for value, context in ((run, "run"), (plan, "plan snapshot"), (release, "release snapshot")):
        if not isinstance(value, dict):
            raise SelectionError(f"{context} must be a JSON object")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise SelectionError(f"{context} has unsupported schema_version")
    _require_fields(run,
    [
        "run_id", "benchmark_id", "benchmark_version", "plan_sha256",
        "release_id", "release_sha256", "trials", "candidate_map",
    ],
    "run",)
    _require_fields(plan, ["benchmark_id", "version", "trials", "cases", "candidates"], "plan snapshot")
    if not isinstance(case_rows, list) or not case_rows:
        raise SelectionError("cases snapshot must be a non-empty array")
    if run["plan_sha256"] != _sha256_json(plan) or run["release_sha256"] != _sha256_json(release):
        raise SelectionError("run identity does not match retained plan and release evidence")
    if (
        run["benchmark_id"] != plan["benchmark_id"]
        or run["benchmark_version"] != plan["version"]
        or run["release_id"] != release["release_id"]
        or run["trials"] != plan["trials"]
    ):
        raise SelectionError("run identity is inconsistent with retained evidence")
    trials = plan["trials"]
    if not isinstance(trials, int) or isinstance(trials, bool) or trials < 1:
        raise SelectionError("retained plan trials must be a positive integer")

    cases: dict[str, dict[str, Any]] = {}
    rubric_weights: dict[str, dict[str, float]] = {}
    for case in case_rows:
        if not isinstance(case, dict):
            raise SelectionError("each retained case must be a JSON object")
        _require_fields(case, ["schema_version", "id", "track"], "retained case")
        if case["schema_version"] != SCHEMA_VERSION or not isinstance(case["id"], str):
            raise SelectionError("retained case has invalid identity")
        if case["id"] in cases:
            raise SelectionError(f"duplicate retained case id: {case['id']}")
        if not isinstance(case["track"], str):
            raise SelectionError(f"case {case['id']} has an invalid track")
        rubric = case.get("rubric", [])
        if not isinstance(rubric, list):
            raise SelectionError(f"case {case['id']} has an invalid rubric")
        weights: dict[str, float] = {}
        for item in rubric:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise SelectionError(f"case {case['id']} has an invalid rubric")
            weight = item.get("weight", 1)
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not math.isfinite(float(weight))
                or weight <= 0
                or item["id"] in weights
            ):
                raise SelectionError(f"case {case['id']} has an invalid rubric")
            weights[item["id"]] = float(weight)
        cases[case["id"]] = case
        rubric_weights[case["id"]] = weights
    if not any(rubric_weights.values()):
        raise SelectionError("selection requires retained cases with rubric judgments")

    if not isinstance(plan["cases"], list) or len(plan["cases"]) != len(cases):
        raise SelectionError("retained cases are inconsistent with the retained plan")
    if not isinstance(plan["candidates"], list):
        raise SelectionError("retained plan candidates must be an array")
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SelectionError("retained release must contain artifact provenance")
    artifacts_by_id: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise SelectionError("each retained release artifact must be a JSON object")
        _require_fields(
            artifact,
            ["id", "source", "revision", "runtime", "quantization"],
            "retained release artifact",
        )
        expected_fields = {"id", "source", "revision", "runtime", "quantization"}
        if set(artifact) != expected_fields:
            raise SelectionError(
                "retained release artifact fields must be exactly: "
                + ", ".join(sorted(expected_fields))
            )
        if not all(
            isinstance(artifact[field], str) and artifact[field]
            for field in ("id", "source", "revision", "runtime", "quantization")
        ):
            raise SelectionError("retained release artifact provenance fields must be non-empty strings")
        artifacts_by_id.setdefault(artifact["id"], []).append(artifact)
    candidate_rows = [
        candidate
        for candidate in plan["candidates"]
        if isinstance(candidate, dict) and candidate.get("enabled", True)
    ]
    candidate_ids = [candidate.get("id") for candidate in candidate_rows]
    if (
        not candidate_ids
        or not all(isinstance(candidate_id, str) for candidate_id in candidate_ids)
        or len(candidate_ids) != len(set(candidate_ids))
    ):
        raise SelectionError("retained plan enabled candidates are invalid")
    candidates = {candidate["id"]: candidate for candidate in candidate_rows}
    candidate_artifacts: dict[str, dict[str, Any]] = {}
    for candidate_id, candidate in candidates.items():
        artifact_ref = candidate.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise SelectionError(f"candidate {candidate_id} has no release artifact linkage")
        matches = artifacts_by_id.get(artifact_ref, [])
        if not matches:
            raise SelectionError(f"candidate {candidate_id} has no matching release artifact")
        if len(matches) != 1:
            raise SelectionError(f"candidate {candidate_id} has ambiguous release artifact linkage")
        artifact = matches[0]
        candidate_artifacts[candidate_id] = {
            "artifact_ref": artifact_ref,
            "source": artifact["source"],
            "revision": artifact["revision"],
            "runtime": artifact["runtime"],
            "quantization": artifact["quantization"],
            "artifact_sha256": _sha256_json(artifact),
        }
    candidate_map = run["candidate_map"]
    if (
        not isinstance(candidate_map, dict)
        or not candidate_map
        or not all(isinstance(label, str) and isinstance(candidate_id, str)
                   for label, candidate_id in candidate_map.items())
        or len(set(candidate_map.values())) != len(candidate_map)
        or set(candidate_map.values()) != set(candidates)
    ):
        raise SelectionError("run candidate_map is invalid or inconsistent with the retained plan")

    judge_rows = plan.get("judges", [])
    if not isinstance(judge_rows, list):
        raise SelectionError("retained plan judges must be an array")
    judges = [
        judge.get("id")
        for judge in judge_rows
        if isinstance(judge, dict) and judge.get("enabled", True)
    ]
    if (
        not judges
        or not all(isinstance(judge_id, str) for judge_id in judges)
        or len(judges) != len(set(judges))
    ):
        raise SelectionError("selection requires unique enabled judges in the retained plan")

    expected_generations = {
        (case_id, label, trial)
        for case_id in cases
        for label in candidate_map
        for trial in range(1, trials + 1)
    }
    generations: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in _read_jsonl(output_dir / "generations.jsonl"):
        _require_fields(row,
        [
            "schema_version", "case_id", "track", "case_sha256",
            "candidate_label", "trial", "status", "objective",
        ],
        "generation",)
        if (
            not isinstance(row["case_id"], str)
            or not isinstance(row["candidate_label"], str)
            or not isinstance(row["trial"], int)
            or isinstance(row["trial"], bool)
        ):
            raise SelectionError("generation identity is invalid")
        key = (row["case_id"], row["candidate_label"], row["trial"])
        if row["schema_version"] != SCHEMA_VERSION or key not in expected_generations or key in generations:
            raise SelectionError("generation identity is duplicate or inconsistent with retained evidence")
        if (
            row["track"] != cases[row["case_id"]]["track"]
            or row["case_sha256"] != _sha256_json(cases[row["case_id"]])
            or row["status"] not in {"completed", "error"}
        ):
            raise SelectionError("generation track or status is inconsistent with retained evidence")
        objective = row["objective"]
        if not isinstance(objective, dict) or not isinstance(objective.get("passed"), bool):
            raise SelectionError("generation objective evidence is invalid")
        checks = objective.get("checks")
        score = objective.get("score")
        if (
            not isinstance(checks, list)
            or not all(isinstance(check, dict) and isinstance(check.get("passed"), bool) for check in checks)
            or objective["passed"] != all(check["passed"] for check in checks)
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= score <= 100
        ):
            raise SelectionError("generation objective evidence is inconsistent")
        duration = row.get("duration_ms")
        if row["status"] == "completed" and (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or duration < 0
        ):
            raise SelectionError("completed generation has invalid duration evidence")
        generations[key] = row
    if set(generations) != expected_generations:
        raise SelectionError("generation evidence is missing for one or more candidate trials")

    expected_judgments = {
        (judge_id, case_id, label, trial)
        for case_id, label, trial in expected_generations
        if generations[(case_id, label, trial)]["status"] == "completed"
        and rubric_weights[case_id]
        for judge_id in judges
    }
    judgments: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for row in _read_jsonl(output_dir / "judgments.jsonl"):
        _require_fields(row, ["schema_version", "judge_id", "case_id", "candidate_label", "trial", "status"], "judgment")
        if (
            not isinstance(row["judge_id"], str)
            or not isinstance(row["case_id"], str)
            or not isinstance(row["candidate_label"], str)
            or not isinstance(row["trial"], int)
            or isinstance(row["trial"], bool)
        ):
            raise SelectionError("judgment identity is invalid")
        key = (row["judge_id"], row["case_id"], row["candidate_label"], row["trial"])
        if row["schema_version"] != SCHEMA_VERSION or key not in expected_judgments or key in judgments:
            raise SelectionError("judgment identity is duplicate or inconsistent with retained evidence")
        if row["status"] not in {"completed", "error"}:
            raise SelectionError("judgment status is invalid")
        if row["status"] == "completed":
            judgment = row.get("judgment")
            if not isinstance(judgment, dict) or not isinstance(judgment.get("fatal_error"), bool):
                raise SelectionError("completed judgment evidence is invalid")
            scores = judgment.get("scores")
            weights = rubric_weights[row["case_id"]]
            if not isinstance(scores, dict) or set(scores) != set(weights):
                raise SelectionError("completed judgment scores do not match retained rubric ids")
            if not all(
                isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 4
                for score in scores.values()
            ):
                raise SelectionError("completed judgment scores must be numbers from 0 to 4")
        judgments[key] = row
    if set(judgments) != expected_judgments:
        raise SelectionError("judgment evidence is missing for one or more completed candidate trials")

    evidence_rows = []
    ranking_inputs = []
    for label, candidate_id in sorted(candidate_map.items(), key=lambda item: item[1]):
        generation_rows = [
            generations[key] for key in sorted(generations)
            if key[1] == label
        ]
        judgment_rows = [
            judgments[key] for key in sorted(judgments)
            if key[2] == label
        ]
        completed_generations = [row for row in generation_rows if row["status"] == "completed"]
        completed_judgments = [row for row in judgment_rows if row["status"] == "completed"]
        rubric_total = sum(
            float(score) * rubric_weights[row["case_id"]][rubric_id]
            for row in completed_judgments
            for rubric_id, score in row["judgment"]["scores"].items()
        )
        rubric_maximum = sum(
            4 * sum(rubric_weights[row["case_id"]].values())
            for row in judgment_rows
        )
        quality = 100 * rubric_total / rubric_maximum if rubric_maximum else None
        pass_rate = 100 * sum(row["objective"]["passed"] for row in generation_rows) / len(generation_rows)
        median_duration = statistics.median(
            float(row["duration_ms"]) for row in completed_generations
        ) if completed_generations else None
        fatal_judgments = sum(
            row["judgment"]["fatal_error"] for row in completed_judgments
        )
        failed_objectives = sum(not row["objective"]["passed"] for row in generation_rows)
        reasons = []
        if len(completed_generations) != len(generation_rows):
            reasons.append("incomplete_trials")
        if len(completed_judgments) != len(judgment_rows):
            reasons.append("incomplete_judgments")
        if fatal_judgments:
            reasons.append("fatal_judgment")
        if failed_objectives:
            reasons.append("failed_objective_checks")
        if quality is not None and quality < minimum_quality:
            reasons.append("below_minimum_quality")
        if pass_rate < minimum_pass_rate:
            reasons.append("below_minimum_objective_pass_rate")
        evidence = {
            "expected_evaluations": len(generation_rows),
            "completed_evaluations": len(completed_generations),
            "expected_judgments": len(judgment_rows),
            "completed_judgments": len(completed_judgments),
            "fatal_judgments": fatal_judgments,
            "failed_objective_evaluations": failed_objectives,
            "rubric_total": round(rubric_total, 6),
            "rubric_maximum": round(rubric_maximum, 6),
            "quality": round(quality, 6) if quality is not None else None,
            "objective_pass_rate": round(pass_rate, 6),
            "median_duration_ms": round(median_duration, 6) if median_duration is not None else None,
        }
        item = {
            "candidate_id": candidate_id,
            "candidate_label": label,
            "artifact": candidate_artifacts[candidate_id],
            "eligible": not reasons,
            "ineligibility_reasons": reasons,
            "evidence": evidence,
            "rank": None,
        }
        evidence_rows.append(item)
        if item["eligible"]:
            if evidence["quality"] is None or evidence["median_duration_ms"] is None:
                raise SelectionError("eligible candidate is missing normalized ranking evidence")
            ranking_inputs.append((
                item,
                evidence["quality"],
                evidence["objective_pass_rate"],
                evidence["median_duration_ms"],
            ))
    ranking_inputs.sort(key=lambda value: (
        -value[1],
        -value[2],
        value[3],
        value[0]["candidate_id"],
    ))
    rankings = []
    for rank, (item, _, _, _) in enumerate(ranking_inputs, start=1):
        item["rank"] = rank
        rankings.append({
            "rank": rank,
            "candidate_id": item["candidate_id"],
            "candidate_label": item["candidate_label"],
            "artifact": item["artifact"],
            "quality": item["evidence"]["quality"],
            "objective_pass_rate": item["evidence"]["objective_pass_rate"],
            "median_duration_ms": item["evidence"]["median_duration_ms"],
        })
    winner = rankings[0] if rankings else None
    return {
        "schema_version": SCHEMA_VERSION,
        "document_type": "benchmark_selection",
        "identity": {
            "run_id": run["run_id"],
            "benchmark_id": run["benchmark_id"],
            "benchmark_version": run["benchmark_version"],
            "plan_sha256": run["plan_sha256"],
            "release_id": run["release_id"],
            "release_sha256": run["release_sha256"],
            "tracks": sorted({case["track"] for case in cases.values()}),
        },
        "parameters": {
            "minimum_quality": minimum_quality,
            "minimum_objective_pass_rate": minimum_pass_rate,
        },
        "candidates": evidence_rows,
        "rankings": rankings,
        "outcome": "winner_selected" if winner else "no_eligible_winner",
        "winner": winner,
    }
