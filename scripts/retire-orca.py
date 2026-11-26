#!/usr/bin/env python3
"""Reconcile the retired Orca project while the Home Core deploy lock is held."""

import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import sys


PROJECT = "homecompute-orca"
RUNTIME = PROJECT + "-orca-1"
OWNER = "orca-inference"
RELEASES = Path("/srv/homecompute/releases")
STATE = Path("/srv/state/orca")
CONFIG = Path("/var/lib/homecompute/orca")


def output(*command):
    return subprocess.check_output(command, text=True).splitlines()


def compose_resources(kind, project):
    options = ("--all", "--format", "{{.Names}}") if kind == "container" else ("--format", "{{.Name}}")
    return output("docker", kind, "ls", "--filter", f"label=com.docker.compose.project={project}", *options)


def private_directory(path, owner):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError(f"Unexpected directory ownership, mode or type: {path}")


def archive_path(path, owner):
    if not (path.exists() or path.is_symlink()):
        return None
    private_directory(path, owner)
    parent = path.parent / ".retired"
    parent.mkdir(mode=0o700, exist_ok=True)
    private_directory(parent, 0)
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise ValueError(f"Recovery path already exists: {target}")
    return target


def backup_runtime():
    files, = output("docker", "container", "inspect", "--format",
                    '{{index .Config.Labels "com.docker.compose.project.config_files"}}', RUNTIME)
    match = re.fullmatch(r"/srv/homecompute/releases/([a-f0-9]{40})/deploy/orca/compose\.yaml(?:,.*)?",
                         files)
    if not match:
        raise ValueError("Unrecognized Orca release source")
    revision = match.group(1)
    release = RELEASES / revision
    if (release.resolve() != release or output("git", "-C", str(release), "rev-parse", "HEAD") != [revision]
            or output("git", "-C", str(release), "status", "--porcelain", "--untracked-files=all")):
        raise ValueError("The last Orca runtime is not backed by a clean exact release")
    subprocess.run(["bash", release / "scripts/backup-orca.sh", "--deployment-lock-held"], check=True)


def revoke_keys(request):
    def key_hashes():
        result = request("/key/list", {"key_alias": OWNER, "user_id": OWNER,
                                       "return_full_object": "true", "size": 100}, query=True)
        rows = result["keys"]
        if len(rows) != result["total_count"]:
            raise ValueError("Incomplete Orca credential inventory")
        hashes = []
        for row in rows:
            if (row["key_alias"] != OWNER or row["user_id"] != OWNER
                    or row["metadata"].get("purpose") != "Private Orca OpenCode inference relay"
                    or not re.fullmatch(r"[a-f0-9]{64}", row["token"])):
                raise ValueError("Unexpected Orca credential ownership")
            hashes.append(row["token"])
        return hashes

    keys = key_hashes()
    if keys:
        request("/key/delete", {"keys": keys})
        if key_hashes():
            raise ValueError("Orca credential revocation could not be verified")


def main():
    if os.geteuid() != 0 or socket.gethostname() != "home-core":
        raise ValueError("Run under the Home Core deployment lifecycle as root")

    containers = compose_resources("container", PROJECT)
    networks = compose_resources("network", PROJECT)
    ingress = "homecompute-orca-ingress"
    if ingress in compose_resources("network", "homecompute-control-plane"):
        networks.append(ingress)
    images = output("docker", "image", "ls", "homecompute/orca", "--format", "{{.Repository}}:{{.Tag}}")
    retired_state = archive_path(STATE, 12074)
    retired_config = archive_path(CONFIG, 0)
    if not any((containers, networks, images, retired_state, retired_config)):
        return
    if RUNTIME in containers:
        backup_runtime()
    if containers:
        subprocess.run(["docker", "container", "stop", *containers], check=True)
        subprocess.run(["docker", "container", "rm", *containers], check=True)
    if networks:
        subprocess.run(["docker", "network", "rm", *networks], check=True)
    if images:
        subprocess.run(["docker", "image", "rm", *images], check=True)

    helper = runpy.run_path(str(Path(__file__).with_name("setup-assistant-context-inference-credentials.py")))
    lock = helper["acquire_setup_lock"]()
    try:
        revoke_keys(helper["litellm_request"])
        if retired_config:
            (CONFIG / "inference-key").unlink(missing_ok=True)
            CONFIG.rename(retired_config)
    finally:
        os.close(lock)
    if retired_state:
        STATE.rename(retired_state)
    print("Removed the Orca runtime and revoked its inference credentials. Recovery data was retained.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        sys.exit(f"Orca retirement refused: {exc}")
    except (KeyError, OSError, TypeError, subprocess.SubprocessError):
        sys.exit("Orca retirement did not complete. Inspect the preceding output and the retirement runbook.")
