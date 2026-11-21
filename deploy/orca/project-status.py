#!/usr/bin/env python3
"""Read public GitHub backlog and recent PR check results for one Orca project."""

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

TEAMS = Path("/etc/homecompute-orca/teams.json")
API = "https://api.github.com"
PAGE_SIZE = 50


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


def main():
    projects = {entry["id"]: entry for entry in json.loads(TEAMS.read_bytes())["projects"]}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=projects)
    args = parser.parse_args()
    try:
        print(json.dumps(review(projects[args.project]["repository"]), indent=2))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
        print("GitHub project metadata is unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
