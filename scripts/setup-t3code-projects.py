#!/usr/bin/env python3
"""Register the declared public GitHub checkouts with T3 Code on Home Core."""

import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory


CONTAINER = "homecompute-t3code-t3code-1"
USER = "12073:12073"
PROJECTS = Path("/data/projects")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def validate_projects(projects):
    names = set()
    if not isinstance(projects, list) or not projects:
        raise ValueError("Expected a nonempty list of projects")
    for item in projects:
        if (not isinstance(item, dict) or set(item) != {"repository", "branch"}
                or not isinstance(item["repository"], str)
                or not re.fullmatch(r"madslundt/[A-Za-z0-9_][A-Za-z0-9._-]*", item["repository"])
                or not isinstance(item["branch"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", item["branch"])):
            raise ValueError("Expected an approved GitHub repository and branch")
        name = item["repository"].split("/")[1].casefold()
        if name in names:
            raise ValueError("Duplicate project checkout name")
        names.add(name)
    return projects


def git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    return env


def ensure_checkout(root, project):
    repository = project["repository"]
    url = f"https://github.com/{repository}.git"
    checkout = root / repository.split("/")[1]
    env = git_env()
    if not checkout.exists() and not checkout.is_symlink():
        with TemporaryDirectory(prefix=".github-clone-", dir=root) as temporary:
            clone = Path(temporary) / "checkout"
            run("git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "clone",
                "--branch", project["branch"], "--no-recurse-submodules", "--", url, str(clone), env=env)
            # GNU mv preserves an independently created destination on a concurrent retry.
            run("mv", "-T", "--no-clobber", "--", str(clone), str(checkout))
    if checkout.is_symlink() or (checkout / ".git").is_symlink() or not (checkout / ".git").is_dir():
        raise ValueError(f"Expected a regular Git checkout at {checkout}")
    if run("git", "-C", str(checkout), "config", "--local", "--get-all", "remote.origin.url", env=env) != url:
        raise ValueError(f"Existing checkout at {checkout} has a different origin")
    return checkout


def ensure_project(checkout):
    result = subprocess.run(
        ["t3", "project", "add", "--title", checkout.name, str(checkout)],
        text=True, capture_output=True,
    )
    if result.returncode == 0:
        return "registered"
    expected = f"ProjectAlreadyExistsError: An active project already exists for '{checkout}'."
    if any(line.endswith(expected) for line in (result.stdout + result.stderr).splitlines()):
        return "already registered"
    raise ValueError(f"T3 Code could not register {checkout.name}")


def reconcile(projects):
    if os.getuid() != 12073 or not PROJECTS.is_dir() or PROJECTS.resolve() != PROJECTS:
        raise ValueError("Expected the unprivileged T3 Code workspace root")
    for project in projects:
        checkout = ensure_checkout(PROJECTS, project)
        print(f"{checkout.name}: {ensure_project(checkout)}", flush=True)


def apply():
    script = Path(__file__).resolve()
    release = script.parents[1]
    revision = release.name
    if (os.geteuid() != 0 or socket.gethostname() != "home-core"
            or release.parent != Path("/srv/homecompute/releases")
            or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("Run as root from a published release on home-core")
    git = ("git", "-C", str(release))
    if (run(*git, "rev-parse", "HEAD") != revision
            or run(*git, "status", "--porcelain", "--untracked-files=all")):
        raise ValueError("Expected the clean release checkout matching its directory")
    run(*git, "fetch", "--quiet", "https://github.com/madslundt/HomeCompute.git", "master")
    run(*git, "merge-base", "--is-ancestor", revision, "FETCH_HEAD")
    projects = validate_projects(json.loads((release / "deploy/t3code/projects.json").read_text()))
    with open("/var/lib/homecompute/deploy.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row, = json.loads(run("docker", "inspect", CONTAINER))
        labels = row["Config"]["Labels"]
        if (labels.get("com.docker.compose.project") != "homecompute-t3code"
                or labels.get("com.docker.compose.service") != "t3code"
                or row["Config"]["User"] != USER or row["State"]["Health"]["Status"] != "healthy"
                or not any(mount["Destination"] == "/data" and mount["Type"] == "bind"
                           and mount["Source"] == "/srv/state/t3code" and mount["RW"]
                           for mount in row["Mounts"])):
            raise ValueError("Expected the healthy managed T3 Code container with its private state mount")
        subprocess.run(["docker", "exec", "-i", "--user", USER, CONTAINER, "python3", "-",
                        json.dumps(projects)], check=True, text=True, input=script.read_text())


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            apply()
        elif len(sys.argv) == 2 and sys.argv[0] == "-":
            reconcile(validate_projects(json.loads(sys.argv[1])))
        else:
            raise ValueError("Usage: sudo python3 scripts/setup-t3code-projects.py")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
