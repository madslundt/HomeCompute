#!/usr/bin/env python3
"""Record and evaluate privacy-safe evidence from explicit GB10 Codex trials."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MINIMUM_TASKS = 20
MINIMUM_SUCCESS_RATE = 70.0
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TrialError(RuntimeError):
    """Trial evidence is missing or invalid."""


def _boolean(value: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise argparse.ArgumentTypeError("must be yes or no")


def _nonnegative(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return number


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _nonnegative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def _absolute(path: Path) -> Path:
    """Return an absolute path without resolving a final symbolic link."""
    return Path(os.path.abspath(path.expanduser()))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="append one metadata-only trial record")
    record.add_argument("--ledger", type=Path, required=True)
    record.add_argument("--task-id", required=True)
    record.add_argument("--representative", type=_boolean, required=True)
    record.add_argument("--outcome", choices=("completed", "failed"), required=True)
    record.add_argument("--local-attempts", type=_positive_integer, required=True)
    record.add_argument("--verification", choices=("passed", "failed", "not_run"), required=True)
    record.add_argument("--cloud-reimplementation", type=_boolean, required=True)
    record.add_argument(
        "--cloud-review",
        choices=("passed", "serious_defect", "not_completed"),
        required=True,
    )
    record.add_argument("--duration-minutes", type=_nonnegative, required=True)
    record.add_argument("--model", required=True)
    record.add_argument("--runtime", required=True)
    record.add_argument("--input-tokens", type=_nonnegative_integer)
    record.add_argument("--output-tokens", type=_nonnegative_integer)
    record.add_argument("--throughput-tokens-per-second", type=_nonnegative)
    status = commands.add_parser("status", help="evaluate, but never activate, the promotion gate")
    status.add_argument("--ledger", type=Path, required=True)
    status.add_argument("--output", type=Path)
    return result


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise TrialError("trial ledger must be a regular file")
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrialError(f"invalid JSON on ledger line {number}") from exc
        _validate_record(record, number)
        records.append(record)
    task_ids = [record["task_id"] for record in records]
    if len(task_ids) != len(set(task_ids)):
        raise TrialError("trial ledger contains duplicate task ids")
    return records


def _validate_record(record: Any, line: int | None = None) -> None:
    prefix = f"ledger line {line}" if line else "trial record"
    expected = {
        "schema_version", "task_id", "recorded_at", "representative", "session_mode",
        "outcome", "local_attempts", "verification", "cloud_reimplementation",
        "cloud_review", "duration_minutes", "model", "runtime",
        "input_tokens", "output_tokens", "throughput_tokens_per_second",
    }
    if not isinstance(record, dict) or set(record) != expected:
        raise TrialError(f"{prefix} has unexpected or missing fields")
    if record["schema_version"] != SCHEMA_VERSION or record["session_mode"] != "gb10_local":
        raise TrialError(f"{prefix} has an unsupported schema or session mode")
    if not isinstance(record["task_id"], str) or not TASK_ID.fullmatch(record["task_id"]):
        raise TrialError(f"{prefix} task_id must be an opaque identifier")
    if not isinstance(record["representative"], bool):
        raise TrialError(f"{prefix} representative must be boolean")
    if record["outcome"] not in {"completed", "failed"}:
        raise TrialError(f"{prefix} has an invalid outcome")
    if not isinstance(record["local_attempts"], int) or isinstance(record["local_attempts"], bool) or record["local_attempts"] < 1:
        raise TrialError(f"{prefix} has invalid local_attempts")
    if record["verification"] not in {"passed", "failed", "not_run"}:
        raise TrialError(f"{prefix} has an invalid verification result")
    if not isinstance(record["cloud_reimplementation"], bool):
        raise TrialError(f"{prefix} cloud_reimplementation must be boolean")
    if record["cloud_review"] not in {"passed", "serious_defect", "not_completed"}:
        raise TrialError(f"{prefix} has an invalid cloud review result")
    duration = record["duration_minutes"]
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
        raise TrialError(f"{prefix} has invalid duration")
    if not all(isinstance(record[key], str) and record[key] for key in ("recorded_at", "model", "runtime")):
        raise TrialError(f"{prefix} has invalid metadata")
    for key in ("input_tokens", "output_tokens"):
        value = record[key]
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise TrialError(f"{prefix} has invalid {key}")
    throughput = record["throughput_tokens_per_second"]
    if throughput is not None and (
        not isinstance(throughput, (int, float))
        or isinstance(throughput, bool)
        or not math.isfinite(throughput)
        or throughput < 0
    ):
        raise TrialError(f"{prefix} has invalid throughput metadata")


def _qualifies(record: dict[str, Any]) -> bool:
    return (
        record["outcome"] == "completed"
        and record["local_attempts"] <= 2
        and record["verification"] == "passed"
        and not record["cloud_reimplementation"]
        and record["cloud_review"] == "passed"
    )


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    representative = [record for record in records if record["representative"]]
    qualifying = [record for record in representative if _qualifies(record)]
    count = len(representative)
    success_rate = round(100 * len(qualifying) / count, 2) if count else 0.0
    checks = {
        "minimum_representative_tasks": count >= MINIMUM_TASKS,
        "minimum_qualifying_success_rate": success_rate >= MINIMUM_SUCCESS_RATE,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "document_type": "codex_local_trial_gate",
        "automatic_delegation_enabled": False,
        "decision": "eligible_for_consideration" if all(checks.values()) else "continue_explicit_trials",
        "thresholds": {
            "minimum_representative_tasks": MINIMUM_TASKS,
            "minimum_qualifying_success_rate": MINIMUM_SUCCESS_RATE,
            "maximum_local_attempts_per_qualifying_task": 2,
            "verification_required": "passed",
            "cloud_reimplementation_required": False,
            "cloud_review_required": "passed",
        },
        "evidence": {
            "representative_tasks": count,
            "qualifying_tasks": len(qualifying),
            "qualifying_success_rate": success_rate,
            "failed_or_incomplete_tasks": count - len(qualifying),
        },
        "checks": checks,
        "note": "Eligibility is evidence for a human promotion decision; it never enables automatic delegation.",
    }


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise TrialError("trial ledger must be a regular file")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (json.dumps(record, sort_keys=True) + "\n").encode())
    finally:
        os.close(descriptor)


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    try:
        ledger = _absolute(args.ledger)
        records = _read_records(ledger)
        if args.command == "record":
            if not TASK_ID.fullmatch(args.task_id):
                raise TrialError("task_id must be an opaque identifier using letters, numbers, dot, dash, or underscore")
            if args.task_id in {record["task_id"] for record in records}:
                raise TrialError(f"task_id already exists: {args.task_id}")
            record = {
                "schema_version": SCHEMA_VERSION,
                "task_id": args.task_id,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "representative": args.representative,
                "session_mode": "gb10_local",
                "outcome": args.outcome,
                "local_attempts": args.local_attempts,
                "verification": args.verification,
                "cloud_reimplementation": args.cloud_reimplementation,
                "cloud_review": args.cloud_review,
                "duration_minutes": args.duration_minutes,
                "model": args.model,
                "runtime": args.runtime,
                "input_tokens": args.input_tokens,
                "output_tokens": args.output_tokens,
                "throughput_tokens_per_second": args.throughput_tokens_per_second,
            }
            _validate_record(record)
            _append(ledger, record)
            print(ledger)
        else:
            result = evaluate(records)
            payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if args.output:
                output = _absolute(args.output)
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(descriptor, payload.encode())
                finally:
                    os.close(descriptor)
                print(output)
            else:
                print(payload, end="")
        return 0
    except (OSError, TrialError) as exc:
        print(f"codex trial error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
