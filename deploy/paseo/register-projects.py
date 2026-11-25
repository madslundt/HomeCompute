#!/usr/bin/env python3
"""Clone and register the declared public repositories through the Paseo CLI."""

import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

PROJECTS = Path("/home/paseo/projects")


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE)


def reconcile(projects):
    registered = json.loads(run("paseo", "--host", "127.0.0.1:6767", "project", "ls", "--json").stdout)
    if not isinstance(registered, list):
        raise ValueError("Unexpected Paseo project list")
    paths = {row["path"] for row in registered}
    for project in projects:
        repo, branch = project["repository"], project["branch"]
        if not re.fullmatch(r"madslundt/[A-Za-z0-9_][A-Za-z0-9._-]*", repo):
            raise ValueError("Unexpected GitHub repository")
        run("git", "check-ref-format", "--branch", branch)
        checkout = PROJECTS / repo.split("/")[1]
        url = f"https://github.com/{repo}.git"
        if not checkout.exists():
            with TemporaryDirectory(dir=PROJECTS) as directory:
                staging = Path(directory) / "project"
                run("git", "clone", "--branch", branch, "--no-recurse-submodules", "--", url, str(staging))
                staging.rename(checkout)
        if (checkout.is_symlink() or not (checkout / ".git").is_dir()
                or run("git", "remote", "get-url", "origin", cwd=checkout).stdout.strip() != url):
            raise ValueError(f"Preserving checkout with unexpected origin at {checkout}")
        if str(checkout) not in paths:
            run("paseo", "--host", "127.0.0.1:6767", "project", "create", str(checkout), "--json")
        print(f"{repo} registered.")


if __name__ == "__main__":
    try:
        reconcile(json.loads(Path("/etc/paseo/projects.json").read_text()))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        sys.exit("Could not register the declared Paseo projects")
