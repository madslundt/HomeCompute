#!/usr/bin/env python3
"""Daily host-local package and registry metadata collection; never apply updates."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat

import docker_image_updates
import package_updates


def collect(host: str, policy: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        packages = package_updates.nix_report() if host == "home-core" else package_updates.apt_report(fresh=True)
        packages = package_updates.project_report(packages, datetime.now(timezone.utc), ttl=129600)
    except Exception:
        packages = {"schema_version": 1, "lane": "nixos-flake" if host == "home-core" else "apt",
                    "status": "error", "candidate_count": None, "reason": "collection_failed"}
    try:
        images = docker_image_updates.collect(host, policy)
    except Exception:
        images = {"schema_version": 1, "images": [], "summary": {"unknown": 1}, "reason": "collection_failed"}
    return {"schema_version": 1, "mode": "observe-only", "host": host,
            "generated_at": now, "automatic_actions": False,
            "package_updates": packages, "docker_image_updates": images}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("home-core", "home-spark"), required=True)
    parser.add_argument("--tracking", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o022)
    if args.state.is_symlink():
        raise ValueError("state symlink prohibited")
    args.state.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(args.state / "collection.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular lock required")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.tracking.is_symlink() or args.tracking.stat().st_size > 65536:
            raise ValueError("bounded reviewed tracking file required")
        policies = json.loads(args.tracking.read_text())
        report = collect(args.host, policies["hosts"][args.host])
        content = json.dumps(report, sort_keys=True).encode()
        if len(content) > 1048576:
            raise ValueError("report exceeds metadata budget")
        temporary = args.state / "report.tmp"
        target = args.state / "report.json"
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(out, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        print(json.dumps({"host": args.host, "generated_at": report["generated_at"],
                          "package_status": report["package_updates"].get("status"),
                          "images": report["docker_image_updates"].get("summary", {})}), flush=True)
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
