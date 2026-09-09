#!/usr/bin/env python3
"""Start an explicit cloud or whole-session GB10 Codex session."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


POLICY_PATH = Path(".codex/data-policy.json")
CLASSIFICATIONS = {"local_only", "cloud_allowed"}


class SessionError(RuntimeError):
    """A session cannot be started without violating the local policy."""


def project_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    completed = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return candidate


def read_classification(root: Path) -> tuple[str, str]:
    policy = root / POLICY_PATH
    if policy.is_symlink():
        raise SessionError(f"repository policy must be a regular file: {policy}")
    if not policy.exists():
        return "local_only", "missing_metadata"
    if not policy.is_file():
        raise SessionError(f"repository policy must be a regular file: {policy}")
    committed = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"HEAD:{POLICY_PATH.as_posix()}"],
        check=False,
        capture_output=True,
        text=True,
    )
    clean = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", str(POLICY_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0 or clean.returncode != 0:
        return "local_only", "uncommitted_metadata"
    try:
        value = json.loads(policy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "local_only", "invalid_metadata"
    if not isinstance(value, dict) or set(value) != {"schema_version", "classification"}:
        return "local_only", "invalid_metadata"
    if value["schema_version"] != 1 or value["classification"] not in CLASSIFICATIONS:
        return "local_only", "invalid_metadata"
    return value["classification"], "committed_metadata"


def build_command(mode: str, extra: list[str]) -> list[str]:
    command = ["codex"]
    if mode == "local":
        command.extend(
            [
                "--strict-config",
                "--config",
                'model_provider="gb10"',
                "--model",
                "coding",
            ]
        )
    return command + extra


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("cloud", "local"),
        default="cloud",
        help="cloud is the default; local selects GB10 for the complete session",
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("codex_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> NoReturn | int:
    args = parse_args()
    root = project_root(args.project)
    classification, source = read_classification(root)
    if args.mode == "cloud" and classification != "cloud_allowed":
        raise SessionError(
            f"cloud session denied: {root} is {classification} ({source}); "
            "commit .codex/data-policy.json with classification cloud_allowed only after review"
        )
    extra = args.codex_args[1:] if args.codex_args[:1] == ["--"] else args.codex_args
    command = build_command(args.mode, extra)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "automatic_delegation": False,
                    "classification": classification,
                    "classification_source": source,
                    "command": command,
                    "mode": "GB10 Local" if args.mode == "local" else "Cloud",
                    "project_root": str(root),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    os.chdir(root)
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SessionError as exc:
        print(f"codex session error: {exc}", file=sys.stderr)
        raise SystemExit(2)
