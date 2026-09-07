"""Blinded judging and retained benchmark reports."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

if __package__:
    from .benchmark_adapters import post_chat
    from .benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _write_bytes_atomic,
        append_jsonl,
        read_json,
        read_jsonl,
        write_json,
    )
    from .benchmark_validation import validate_candidate
else:
    from benchmark_adapters import post_chat
    from benchmark_core import (
        SCHEMA_VERSION,
        BenchmarkError,
        LoadedPlan,
        _write_bytes_atomic,
        append_jsonl,
        read_json,
        read_jsonl,
        write_json,
    )
    from benchmark_validation import validate_candidate

def judge_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "benchmark_judgment",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "score": {"type": "number", "minimum": 0, "maximum": 4},
                            },
                            "required": ["id", "score"],
                            "additionalProperties": False,
                        },
                    },
                    "fatal_error": {"type": "boolean"},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["scores", "fatal_error", "unsupported_claims", "rationale", "confidence"],
                "additionalProperties": False,
            },
        },
    }


def _retained_cases(plan: LoadedPlan, output_dir: Path) -> dict[str, dict[str, Any]]:
    retained_plan = read_json(output_dir / "plan.json")
    retained_cases = read_json(output_dir / "cases.json")
    current_cases = [
        {key: value for key, value in case.items() if not key.startswith("_")}
        for case in plan.cases
    ]
    if retained_plan != plan.value or retained_cases != current_cases:
        raise BenchmarkError(
            "supplied plan and cases do not exactly match retained run evidence"
        )
    if not isinstance(retained_cases, list) or not all(
        isinstance(case, dict) and isinstance(case.get("id"), str)
        for case in retained_cases
    ):
        raise BenchmarkError("retained cases evidence is invalid")
    cases = {case["id"]: case for case in retained_cases}
    if len(cases) != len(retained_cases):
        raise BenchmarkError("retained cases evidence contains duplicate ids")
    return cases


def judge_run(plan: LoadedPlan, output_dir: Path, judge_id: str) -> None:
    cases = _retained_cases(plan, output_dir)
    judges = plan.value.get("judges", [])
    matches = [judge for judge in judges if judge.get("id") == judge_id]
    if len(matches) != 1:
        raise BenchmarkError(f"judge not found exactly once in plan: {judge_id}")
    judge = matches[0]
    validate_candidate(judge, plan)
    already = {
        (row["judge_id"], row["case_id"], row["candidate_label"], row["trial"])
        for row in read_jsonl(output_dir / "judgments.jsonl")
        if row.get("status") == "completed"
    }
    for generation in read_jsonl(output_dir / "generations.jsonl"):
        key = (judge_id, generation["case_id"], generation["candidate_label"], generation["trial"])
        if key in already or generation["status"] != "completed":
            continue
        case = cases[generation["case_id"]]
        rubric = case.get("rubric", [])
        if not rubric:
            continue
        prompt = {
            "task_messages": case["messages"],
            "rubric": rubric,
            "anonymous_response_label": generation["candidate_label"],
            "response": generation["text"],
            "objective_results": generation["objective"],
            "instructions": "Score every rubric id from 0 to 4. Use only the supplied evidence. Do not infer model identity. A fatal error means the response is unusable regardless of style.",
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "judge_id": judge_id,
            "case_id": generation["case_id"],
            "candidate_label": generation["candidate_label"],
            "trial": generation["trial"],
        }
        try:
            response = post_chat(
                judge,
                [{"role": "system", "content": "You are a strict, evidence-bound benchmark evaluator."},
                 {"role": "user", "content": json.dumps(prompt, sort_keys=True)}],
                judge_schema(),
            )
            judgment = json.loads(response["text"])
            score_items = judgment["scores"]
            scores = {item["id"]: item["score"] for item in score_items}
            expected_ids = {item["id"] for item in rubric}
            if len(scores) != len(score_items) or set(scores) != expected_ids:
                raise BenchmarkError("judge returned scores that do not match rubric ids")
            if not all(
                isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 4
                for score in scores.values()
            ):
                raise BenchmarkError("judge scores must be numbers from 0 to 4")
            confidence = judgment.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                raise BenchmarkError("judge confidence must be a number from 0 to 1")
            judgment["scores"] = scores
            record.update({"status": "completed", "judgment": judgment, "usage": response["usage"], "duration_ms": response["duration_ms"]})
        except (BenchmarkError, json.JSONDecodeError, KeyError, TypeError) as exc:
            append_jsonl(
                output_dir / "judgment-attempts.jsonl",
                {**record, "status": "error", "error": str(exc)},
            )
        else:
            append_jsonl(output_dir / "judgments.jsonl", record)
    build_report(plan, output_dir)


def mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def build_report(plan: LoadedPlan, output_dir: Path) -> dict[str, Any]:
    run = read_json(output_dir / "run.json")
    generations = read_jsonl(output_dir / "generations.jsonl")
    judgments = read_jsonl(output_dir / "judgments.jsonl")
    cases = {case["id"]: case for case in plan.cases}
    by_label: dict[str, list[dict[str, Any]]] = {}
    for row in generations:
        by_label.setdefault(row["candidate_label"], []).append(row)
    summaries = []
    for label, rows in sorted(by_label.items()):
        completed = [row for row in rows if row["status"] == "completed"]
        judge_scores = []
        for judgment_row in judgments:
            if judgment_row.get("candidate_label") != label or judgment_row.get("status") != "completed":
                continue
            rubric = cases[judgment_row["case_id"]].get("rubric", [])
            weights = {item["id"]: float(item.get("weight", 1)) for item in rubric}
            scores = judgment_row["judgment"]["scores"]
            total_weight = sum(weights.values())
            if total_weight:
                judge_scores.append(100 * sum(float(scores[key]) * weights[key] for key in weights) / (4 * total_weight))
        costs = [float(row.get("usage", {}).get("cost", 0) or 0) for row in completed]
        summaries.append({
            "candidate_label": label,
            "candidate_id": run["candidate_map"][label],
            "attempts": len(rows),
            "completed": len(completed),
            "objective_pass_rate": round(100 * sum(row["objective"]["passed"] for row in rows) / len(rows), 2),
            "objective_score_mean": mean([float(row["objective"]["score"]) for row in rows]),
            "judge_score_mean": mean(judge_scores),
            "duration_ms_mean": mean([float(row["duration_ms"]) for row in completed]),
            "total_cost": round(sum(costs), 8),
        })
    report = {"schema_version": SCHEMA_VERSION, "run_id": run["run_id"], "candidates": summaries}
    write_json(output_dir / "summary.json", report)
    lines = [
        f"# Benchmark summary: {run['run_id']}",
        "",
        f"Benchmark `{run['benchmark_id']}` version `{run['benchmark_version']}`; release `{run['release_id']}`.",
        "",
        "| Candidate | Attempts | Completed | Objective pass | Objective score | Judge score | Mean duration | Total cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summaries:
        judge_value = "not run" if item["judge_score_mean"] is None else f"{item['judge_score_mean']:.2f}%"
        lines.append(
            f"| {item['candidate_id']} | {item['attempts']} | {item['completed']} | "
            f"{item['objective_pass_rate']:.2f}% | {item['objective_score_mean']:.2f}% | "
            f"{judge_value} | {item['duration_ms_mean'] or 0:.2f} ms | {item['total_cost']:.8f} |"
        )
    lines.extend(["", "Raw generations may contain sensitive content and must not be committed.", ""])
    _write_bytes_atomic(output_dir / "summary.md", "\n".join(lines).encode())
    return report


def build_review_packet(plan: LoadedPlan, output_dir: Path) -> Path:
    cases = _retained_cases(plan, output_dir)
    items = []
    for generation in read_jsonl(output_dir / "generations.jsonl"):
        if generation.get("status") != "completed":
            continue
        case = cases[generation["case_id"]]
        items.append({
            "case_id": generation["case_id"],
            "track": generation["track"],
            "trial": generation["trial"],
            "candidate_label": generation["candidate_label"],
            "task_messages": case["messages"],
            "rubric": case.get("rubric", []),
            "objective_results": generation["objective"],
            "response": generation["text"],
            "workflow_trace": generation.get("workflow_trace", []),
        })
    packet = {
        "schema_version": SCHEMA_VERSION,
        "instructions": (
            "Review each response without guessing model identity. Score every rubric item from 0 to 4, "
            "identify fatal errors and unsupported claims, then compare anonymous candidates. Objective "
            "failures take precedence over stylistic preference."
        ),
        "items": items,
    }
    path = output_dir / "review-packet.json"
    write_json(path, packet)
    return path
