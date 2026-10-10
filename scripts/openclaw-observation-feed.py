#!/usr/bin/env python3
"""Trusted scheduled metadata admission; the existing n8n owner sends notifications."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import time

from openclaw_tasks import ADAPTER, private_json, request


def feed(registry_path: Path, tokens_path: Path, destination: Path) -> dict:
    spec = importlib.util.spec_from_file_location("hc_observe_feed", Path(__file__).with_name("observe-homecompute.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = module.validate_registry(module.read_json(registry_path))
    snapshots = {}
    for system in registry["systems"]:
        try:
            snapshots[system["id"]] = module.collect(system)
        except (OSError, ValueError, module.subprocess.TimeoutExpired, module.homecompute.OperatorError):
            snapshots[system["id"]] = None
    report = module.make_report(registry, snapshots, datetime.now(timezone.utc))
    # This feed covers only the authorized update extension. Health and HA
    # retain their separately configured collection/activation paths.
    report["observations"] = [x for x in report["observations"] if x["category"] == "updates"]
    report["findings"] = []
    report["changes"] = []
    token = private_json(tokens_path)["collector"]
    if request(ADAPTER, "/observations", token, report) != {"accepted": True, "actions_enabled": False}:
        raise ValueError("observation receipt unavailable")
    temporary = destination.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    return {"accepted": True, "observations": len(report["observations"]), "generated_at": report["generated_at"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.state.is_symlink() or args.state.stat().st_mode & 0o077:
        raise ValueError("private feed state required")
    fd = os.open(args.state / "feed.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular lock required")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                print(json.dumps(feed(args.registry, args.tokens, args.state / "latest.json")), flush=True)
            except Exception:
                print(json.dumps({"accepted": False, "reason": "collection_or_admission_failed"}), flush=True)
                if not args.follow:
                    return 2
            if not args.follow:
                return 0
            time.sleep(300)
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
