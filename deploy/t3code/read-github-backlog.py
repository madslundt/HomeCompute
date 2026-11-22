#!/usr/bin/env python3
"""Read a GitHub Projects backlog or edit its items from the current T3 checkout."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import socket
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


def send_write(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(100)
        connection.connect(str(SNAPSHOTS / "write.sock"))
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as response:
            raw = response.readline(1025)
    if len(raw) > 1024 or not raw.endswith(b"\n"):
        raise ValueError("Incomplete GitHub Projects response")
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action")
    for action, help_text in (
        ("add", "add an open issue or pull request to a board"),
        ("set", "set one field on an existing board item"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("board", type=int, help="number in the GitHub Projects board URL")
        command.add_argument("number", type=int, help="issue or pull request number")
        if action == "set":
            command.add_argument("field", choices=("Status", "Priority"))
            command.add_argument("value", help="exact name of the field option")
    args = parser.parse_args()
    try:
        repository = current_repository()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        sys.exit("GitHub Projects is available from a configured T3 checkout.")

    if args.action:
        try:
            result = send_write({**vars(args), "repository": repository})
            if result["ok"] is not True:
                sys.exit(result["message"])
            print(result["message"])
        except (OSError, ValueError, KeyError, TypeError):
            sys.exit("GitHub Projects did not confirm the update. Check the board before retrying.")
    else:
        try:
            snapshot = SNAPSHOTS / (repository.split("/")[1] + ".json")
            print(json.dumps(fresh_snapshot(snapshot, repository, datetime.now(timezone.utc)), indent=2))
        except (OSError, ValueError, KeyError, TypeError):
            sys.exit("A fresh GitHub Projects snapshot is unavailable for this checkout.")


if __name__ == "__main__":
    main()
