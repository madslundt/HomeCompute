#!/usr/bin/env python3
"""Run repeatable, blinded model benchmarks with no third-party dependencies."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__:
    from .benchmark_core import BenchmarkError, _absolute, write_json_exclusive
    from .benchmark_validation import load_plan, validate_all
    from .benchmark_reporting import build_report, build_review_packet, judge_run
    from .benchmark_run import evaluate, evaluate_check, run_benchmark
    from .selection import SelectionError, select_run
else:
    from benchmark_core import BenchmarkError, _absolute, write_json_exclusive
    from benchmark_validation import load_plan, validate_all
    from benchmark_reporting import build_report, build_review_packet, judge_run
    from benchmark_run import evaluate, evaluate_check, run_benchmark
    from selection import SelectionError, select_run










def percentage(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number from 0 to 100") from exc
    if not 0 <= result <= 100:
        raise argparse.ArgumentTypeError("must be a number from 0 to 100")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        command = sub.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--release", type=Path, required=True)
        if name == "run":
            command.add_argument("--output", type=Path)
            command.add_argument("--candidate")
    judge = sub.add_parser("judge")
    judge.add_argument("--plan", type=Path, required=True)
    judge.add_argument("--run", type=Path, required=True)
    judge.add_argument("--judge", required=True)
    report = sub.add_parser("report")
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--run", type=Path, required=True)
    packet = sub.add_parser("review-packet")
    packet.add_argument("--plan", type=Path, required=True)
    packet.add_argument("--run", type=Path, required=True)
    selection = sub.add_parser("select")
    selection.add_argument("--run", type=Path, required=True)
    selection.add_argument("--output", type=Path)
    selection.add_argument("--minimum-quality", type=percentage, required=True)
    selection.add_argument("--minimum-objective-pass-rate", type=percentage, required=True)
    return result


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    try:
        if args.command == "validate":
            plan, _ = validate_all(args.plan, args.release)
            print(f"valid: {plan.value['benchmark_id']} ({len(plan.cases)} cases)")
        elif args.command == "run":
            plan, release = validate_all(args.plan, args.release)
            output = run_benchmark(plan, release, args.output, args.candidate)
            print(output)
        elif args.command == "judge":
            plan = load_plan(args.plan)
            judge_run(plan, args.run.resolve(), args.judge)
            print(args.run.resolve())
        elif args.command == "report":
            plan = load_plan(args.plan)
            build_report(plan, args.run.resolve())
            print(args.run.resolve() / "summary.md")
        elif args.command == "review-packet":
            plan = load_plan(args.plan)
            print(build_review_packet(plan, args.run.resolve()))
        elif args.command == "select":
            selection = select_run(
                args.run,
                args.minimum_quality,
                args.minimum_objective_pass_rate,
            )
            if args.output is None:
                print(json.dumps(selection, indent=2, sort_keys=True))
            else:
                output = _absolute(args.output)
                write_json_exclusive(output, selection)
                print(output.resolve(strict=True))
        return 0
    except (BenchmarkError, SelectionError, OSError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
