#!/usr/bin/env python3
"""Print the fresh GitHub Projects backlog for the current T3 checkout."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys


PROJECTS = Path("/etc/t3code/projects.json")
SNAPSHOTS = Path("/run/homecompute/github-projects")


def current_repository():
    remote = subprocess.run(["git", "config", "--local", "--get", "remote.origin.url"],
                            check=True, capture_output=True, text=True).stdout.strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)", remote)
    if not match:
        raise ValueError("The current checkout has no supported GitHub origin")
    origin = match[1].removesuffix(".git").casefold()
    for project in json.loads(PROJECTS.read_text()):
        if project["repository"].casefold() == origin:
            return project["repository"]
    raise ValueError("GitHub Projects access is available for the configured T3 repositories")


def fresh_snapshot(path, repository, now):
    with path.open("rb") as source:
        raw = source.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("The GitHub Projects snapshot exceeds the size limit")
    snapshot = json.loads(raw)
    timestamp = datetime.fromisoformat(snapshot["captured_at"])
    if (snapshot["repository"] != repository or timestamp.tzinfo is None
            or not 0 <= (now - timestamp).total_seconds() <= 1800):
        raise ValueError("A fresh GitHub Projects snapshot is unavailable for this repository")
    return snapshot


def main():
    try:
        repository = current_repository()
        snapshot = SNAPSHOTS / (repository.split("/")[1] + ".json")
        print(json.dumps(fresh_snapshot(snapshot, repository, datetime.now(timezone.utc)), indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        sys.exit("A fresh GitHub Projects snapshot is unavailable for this checkout.")


if __name__ == "__main__":
    main()
