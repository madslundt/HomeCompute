#!/usr/bin/env python3
"""Refresh repository-scoped GitHub Projects snapshots for the T3 coding team."""

import argparse
from datetime import datetime, timezone
from functools import partial
from getpass import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import Event, Thread

from github_project_api import GitHubProjectsError, atomic_write, github_graphql, project_items


HERE = Path(__file__).resolve().parent
STATE = Path("/var/lib/homecompute/t3code/github-projects")
READER_ID = 10001
OWNER = "madslundt"
BOARD_QUERY = """
query T3OwnedBoards($cursor: String) {
  viewer {
    login
    projectsV2(first: 50, after: $cursor) {
      nodes {
        number closed
        owner { __typename ... on User { login } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
FAILURES = (OSError, ValueError, KeyError, TypeError)


class ProvisioningError(ValueError):
    """A message safe to show at the private provision prompt."""


def repositories():
    entries = json.loads((HERE / "projects.json").read_text())
    repos = [entry["repository"] for entry in entries]
    if not repos or any(not re.fullmatch(rf"{OWNER}/[A-Za-z0-9_][A-Za-z0-9._-]*", repo) for repo in repos):
        raise ValueError("Expected repositories in the T3 project allowlist")
    return repos


def owned_boards(graphql, token):
    cursor = None
    cursors = set()
    numbers = set()
    while True:
        viewer = graphql(BOARD_QUERY, {"cursor": cursor}, token)["viewer"]
        if viewer["login"].casefold() != OWNER:
            raise ProvisioningError(f"Token belongs to a different GitHub account. Create it while signed in as {OWNER}.")
        page = viewer["projectsV2"]
        numbers.update(item["number"] for item in page["nodes"]
                       if item and item["closed"] is False and item["owner"]["__typename"] == "User"
                       and item["owner"]["login"].casefold() == OWNER
                       and type(item["number"]) is int and item["number"] > 0)
        if not page["pageInfo"]["hasNextPage"]:
            return sorted(numbers)
        cursor = page["pageInfo"]["endCursor"]
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError("GitHub returned an invalid project cursor")
        cursors.add(cursor)


def project_snapshot(reader, repository, numbers, token):
    boards = []
    failures = 0
    entry = {"repository": repository, "github_project": {"kind": "user", "owner": OWNER}}
    for number in numbers:
        try:
            result = reader(entry, number, token)
        except FAILURES:
            failures += 1
            continue
        if result["items"] or result["more_items"]:
            boards.append({"url": f"https://github.com/users/{OWNER}/projects/{number}", **result})
    status = "available"
    if failures:
        status = "partial" if failures < len(numbers) else "unavailable"
    return {"status": status, "boards": boards}


def refresh(graphql, repos, credential, snapshots):
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        token = credential.read_text().strip()
        numbers = owned_boards(graphql, token)
    except FileNotFoundError:
        pending = "credential unavailable"
    except FAILURES:
        pending = "unavailable"
    else:
        pending = None
    names = set()
    for repo in repos:
        snapshot = {"repository": repo, "captured_at": checked_at}
        if pending:
            snapshot.update(status=pending, boards=[])
        else:
            snapshot.update(project_snapshot(partial(project_items, graphql=graphql), repo, numbers, token))
        name = repo.split("/")[1] + ".json"
        atomic_write(snapshots / name, json.dumps(snapshot).encode(), 0o644)
        names.add(name)
    for retired in snapshots.glob("*.json"):
        if retired.name not in names and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*\.json", retired.name):
            retired.unlink()


def prepare():
    if os.geteuid() != 0:
        raise ValueError("Host preparation requires root")
    for path, uid, gid, mode in (
        (STATE.parent, 0, 0, 0o700), (STATE, 0, 0, 0o700),
        (STATE / "credentials", 0, READER_ID, 0o750),
        (STATE / "snapshots", READER_ID, READER_ID, 0o755),
    ):
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            pass
        else:
            os.chown(path, uid, gid)
            path.chmod(mode)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != mode):
            raise ValueError("Unexpected GitHub Projects directory ownership or mode")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "provision", "serve"))
    parser.add_argument("--write", action="store_true", help="provision a dedicated token with Projects write permission")
    args = parser.parse_args()
    command = args.command
    if args.write and command != "provision":
        parser.error("--write requires provision")
    if command == "serve":
        from github_project_writes import serve_writes

        repos = repositories()
        credential = Path("/run/github-projects/credentials/token")
        snapshots = Path("/snapshots")
        read_graphql = partial(github_graphql, allowed_scopes=("read:project", "project"))
        updated = Event()
        with serve_writes(snapshots / "write.sock", github_graphql, GitHubProjectsError,
                          credential, OWNER, repos, updated) as server:
            Thread(target=server.serve_forever, daemon=True).start()
            while True:
                updated.clear()
                refresh(read_graphql, repos, credential, snapshots)
                updated.wait(900)
    prepare()
    if command == "provision":
        if not sys.stdin.isatty():
            raise ValueError("Use a private terminal for the Projects credential")
        scope = "project" if args.write else "read:project"
        token = getpass(f"Dedicated GitHub {scope} token: ").strip()
        graphql = partial(github_graphql, allowed_scopes=(scope,))
        try:
            boards = owned_boards(graphql, token)
        except GitHubProjectsError as error:
            raise ProvisioningError(str(error)) from None
        except ProvisioningError:
            raise
        except FAILURES:
            raise ProvisioningError("Could not verify the personal boards returned by GitHub.") from None
        try:
            atomic_write(STATE / "credentials/token", token.encode(), 0o640, gid=READER_ID)
        except OSError:
            raise ProvisioningError("Could not save the credential in the protected Home Core directory.") from None
        print(f"Verified the {scope} grant and found {len(boards)} open personal boards.")


if __name__ == "__main__":
    try:
        main()
    except ProvisioningError as error:
        sys.exit(str(error))
    except FAILURES:
        sys.exit("T3 Code GitHub Projects setup or refresh failed.")
