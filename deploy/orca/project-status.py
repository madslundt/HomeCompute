#!/usr/bin/env python3
"""Read public GitHub backlog and recent PR check results for one Orca project."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

TEAMS = Path("/etc/homecompute-orca/teams.json")
API = "https://api.github.com"
PAGE_SIZE = 50
BOT_ROOT = Path("/data/profile/bots")
SNAPSHOT = "project-status.json"
BOARDS = Path("/etc/homecompute-orca/github-projects")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def get(path):
    request = Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "HomeCompute-Orca",
    })
    with build_opener(NoRedirect).open(request, timeout=12) as response:
        return json.load(response)


def item(row):
    return {key: row[key] for key in ("number", "title", "html_url", "updated_at")}


def review(repository):
    prefix = f"/repos/{repository}"
    query = f"?state=open&sort=updated&direction=desc&per_page={PAGE_SIZE}"
    issues = get(f"{prefix}/issues{query}")
    pulls = get(f"{prefix}/pulls{query}")
    checks = {}
    for pull in pulls[:5]:
        head = pull["head"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", head):
            raise ValueError("unexpected pull request head")
        try:
            rows = get(f"{prefix}/commits/{quote(head)}/check-runs?per_page=20")
            checks[pull["number"]] = [{
                key: row[key] for key in ("name", "status", "conclusion")
            } for row in rows["check_runs"]]
        except (HTTPError, URLError, TimeoutError):
            checks[pull["number"]] = None
    return {
        "repository": repository,
        "issues": [item(row) for row in issues if "pull_request" not in row],
        "pull_requests": [{**item(row), "draft": row["draft"]} for row in pulls],
        "check_runs_for_first_five_pull_requests": checks,
        "page_may_be_truncated": len(issues) == PAGE_SIZE or len(pulls) == PAGE_SIZE,
    }


def board_status(project):
    fallback = {"github_projects_status": "unavailable: repository metadata only"}
    if not (entry := project.get("github_project")):
        return fallback
    if not re.fullmatch(r"[a-z0-9-]+", project["id"]):
        raise ValueError("unexpected project ID")
    try:
        with (BOARDS / (project["id"] + ".json")).open("rb") as file:
            snapshot = json.loads(file.read(64_000))
        timestamp = datetime.fromisoformat(snapshot["captured_at"])
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if (snapshot["repository"] == project["repository"] and snapshot.get("board") == entry
                and snapshot["status"] == "available" and 0 <= age <= 1800):
            return {"github_projects_status": "available", "github_projects": snapshot}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return fallback


def save_review(project, destination):
    repository = project["repository"]
    summary = {
        "repository": repository,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **board_status(project),
    }
    try:
        summary.update(review(repository))
        summary["repository_status"] = "available"
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
        summary["repository_status"] = "unavailable"
    # Replace, rather than follow, a filename left behind by a previous bot run.
    with tempfile.NamedTemporaryFile(
        dir=destination, prefix=".project-status.", mode="w", encoding="utf-8", delete=False,
    ) as draft:
        path = Path(draft.name)
        try:
            json.dump(summary, draft, indent=2)
            draft.write("\n")
            draft.close()
            path.replace(destination / SNAPSHOT)
        finally:
            path.unlink(missing_ok=True)


def main():
    projects = {entry["id"]: entry for entry in json.loads(TEAMS.read_bytes())["projects"]}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=projects)
    parser.add_argument("--save-review", action="store_true",
                        help="Write the weekly precheck snapshot inside the bot home.")
    args = parser.parse_args()
    project = projects[args.project]
    repository = project["repository"]
    try:
        if args.save_review:
            destination = Path.cwd().resolve()
            if destination.parent != BOT_ROOT or not (destination / "SOUL.md").is_file():
                raise ValueError("expected an Orca bot home directory")
            save_review(project, destination)
        else:
            print(json.dumps({**board_status(project), **review(repository)}, indent=2))
    except (OSError, ValueError, KeyError):
        print("GitHub project metadata is unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
