#!/usr/bin/env python3
"""Publish bounded, read-only GitHub Projects snapshots for the Orca teams."""

import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


HOST_ROOT = Path("/var/lib/homecompute/orca")
READER_ID = 10001
TEAMS = Path(__file__).with_name("teams.json")
QUERY = """
query OrcaProject($owner: String!, $number: Int!, $filter: String!) {
  OWNER_KIND(login: $owner) {
    projectV2(number: $number) {
      closed
      items(first: 40, query: $filter) {
        pageInfo { hasNextPage }
        nodes {
          content {
            __typename
            ... on Issue { number state repository { nameWithOwner isPrivate } }
            ... on PullRequest { number state repository { nameWithOwner isPrivate } }
          }
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          priority: fieldValueByName(name: "Priority") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def projects():
    entries = json.loads(TEAMS.read_bytes())["projects"]
    for entry in entries:
        if not re.fullmatch(r"[a-z0-9-]+", entry["id"]):
            raise ValueError("invalid Orca project ID")
    return entries


def github_graphql(query, variables, token):
    if not re.fullmatch(r"ghp_[A-Za-z0-9]+", token):
        raise ValueError("expected a classic GitHub token with read:project only")
    request = Request("https://api.github.com/graphql", data=json.dumps({
        "query": query,
        "variables": variables,
    }).encode(), headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "HomeCompute-Orca-Projects",
    })
    with build_opener(NoRedirect).open(request, timeout=15) as response:
        scopes = {scope.strip() for scope in response.headers.get("X-OAuth-Scopes", "").split(",")}
        if scopes != {"read:project"}:
            raise ValueError("token must have only the read:project scope")
        raw = response.read(256_001)
    if len(raw) > 256_000:
        raise ValueError("board response exceeds the size bound")
    data = json.loads(raw)
    if data.get("errors"):
        raise ValueError("GitHub rejected the board query")
    return data["data"]


def project_items(entry, number, token):
    board = entry["github_project"]
    kind, owner = board["kind"], board["owner"]
    repository = entry["repository"]
    if (kind not in {"user", "organization"} or not re.fullmatch(r"[A-Za-z0-9-]+", owner)
            or type(number) is not int or number < 1
            or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)):
        raise ValueError("invalid board configuration")
    data = github_graphql(QUERY.replace("OWNER_KIND", kind), {
        "owner": owner, "number": number, "filter": f"repo:{repository} is:open",
    }, token)
    project = data[kind]["projectV2"]
    if project["closed"]:
        raise ValueError("board is closed")

    items = []
    for node in project["items"]["nodes"]:
        content = node["content"]
        if not content or content["__typename"] not in {"Issue", "PullRequest"}:
            continue
        repo = content["repository"]
        if (repo["isPrivate"] or repo["nameWithOwner"].casefold() != repository.casefold()
                or content["state"] != "OPEN"):
            continue
        selection = {
            key: (node[key] or {}).get("name", "")[:64]
            for key in ("status", "priority")
        }
        item_type = "issues" if content["__typename"] == "Issue" else "pull"
        items.append({
            "number": content["number"],
            "url": f"https://github.com/{repository}/{item_type}/{content['number']}",
            **selection,
        })
    return {"items": items, "more_items": project["items"]["pageInfo"]["hasNextPage"]}


def atomic_write(path, contents, mode, gid=None):
    with NamedTemporaryFile(dir=path.parent, prefix=".orca-", delete=False) as draft:
        temporary = Path(draft.name)
        try:
            if gid is not None:
                os.fchown(draft.fileno(), 0, gid)
            os.fchmod(draft.fileno(), mode)
            draft.write(contents)
            draft.flush()
            os.fsync(draft.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def refresh(entries, credential_file, snapshots):
    credentials = {}
    try:
        with credential_file.open("rb") as file:
            credentials = json.loads(file.read(16_384))
    except FileNotFoundError:
        pass
    token = credentials.get("token")
    boards = credentials.get("boards", {})
    for entry in entries:
        result = {
            "repository": entry["repository"],
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "status": "not configured",
        }
        board = entry.get("github_project")
        if board:
            result["board"] = board
            result["status"] = "credential unavailable"
            if token and entry["id"] in boards:
                try:
                    result.update(project_items(entry, boards[entry["id"]], token))
                    result["status"] = "available"
                except (OSError, URLError, ValueError, KeyError, TypeError):
                    result["status"] = "unavailable"
        atomic_write(snapshots / (entry["id"] + ".json"), json.dumps(result).encode(), 0o644)
    current = {entry["id"] + ".json" for entry in entries}
    for retired in snapshots.glob("*.json"):
        if retired.name not in current and re.fullmatch(r"[a-z0-9-]+\.json", retired.name):
            retired.unlink()


def prepare():
    if os.geteuid() != 0:
        raise ValueError("host preparation requires root")
    root = HOST_ROOT / "github-projects"
    for path, uid, gid, mode in [
        (HOST_ROOT, 0, 0, 0o700),
        (root, 0, 0, 0o700),
        (root / "credentials", 0, READER_ID, 0o750),
        (root / "snapshots", READER_ID, READER_ID, 0o755),
    ]:
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            pass
        else:
            os.chown(path, uid, gid)
            path.chmod(mode)
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_gid != gid
                or stat.S_IMODE(metadata.st_mode) != mode):
            raise ValueError("unexpected GitHub Projects directory ownership or mode")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "provision", "serve"))
    command = parser.parse_args().command
    if command == "serve":
        entries = projects()
        while True:
            refresh(entries, Path("/run/orca-projects/credentials/github.json"), Path("/snapshots"))
            sleep(900)
    root = prepare()
    if command == "provision":
        entries = [entry for entry in projects() if entry.get("github_project")]
        if not entries or not sys.stdin.isatty():
            raise ValueError("configure a board first and use a private terminal")
        token = getpass("Dedicated GitHub read:project token: ").strip()
        boards = {}
        for entry in entries:
            number = int(input(f"Approved board number for {entry['id']}: ").strip())
            project_items(entry, number, token)
            boards[entry["id"]] = number
        data = json.dumps({"token": token, "boards": boards}).encode()
        atomic_write(root / "credentials/github.json", data, 0o640, gid=READER_ID)
        print("Verified read-only scope and configured boards.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, URLError, ValueError, KeyError, TypeError):
        sys.exit("Orca GitHub Projects setup or refresh failed.")
