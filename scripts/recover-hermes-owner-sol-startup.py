#!/usr/bin/env python3
"""Restore the local Hermes model when managed startup rejects the raw Sol key."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

import yaml

CONTAINER = "7234f2cf2085fa2eb8c6aa80f7521fad6f5435491f7166d5f73859114b39819d"
IMAGE = "sha256:7bfb994f8c32a3722219ab7ab93c43d7d799a871c88c6c59b321f0c4acc22aa7"
HOME = "/sandbox/.hermes"
KEY = b"HOMECOMPUTE_CHATGPT_API_KEY"
MODEL = "chatgpt-sol-latest"
PROVIDER = "homecompute-sol"


def docker(*args, data=None):
    return subprocess.run(
        ["docker", *args], input=data, capture_output=True, check=True, timeout=25
    ).stdout


def read(path):
    data = docker("cp", f"{CONTAINER}:{path}", "-")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        (item,) = archive.getmembers()
        if not item.isfile() or item.size > 65536:
            raise ValueError("unexpected owner configuration document")
        return archive.extractfile(item).read(), item


def digest(config, env):
    return hashlib.sha256(config + b"\0" + env).hexdigest()


def replacement(config, env, receipt):
    prior_config, prior_env = (bytes.fromhex(receipt[name]) for name in ("config", "env"))
    if digest(prior_config, prior_env) != receipt["before"]:
        raise ValueError("owner Sol receipt digest differs")
    prior = yaml.safe_load(prior_config)
    active = yaml.safe_load(config)
    selected = active["model"]
    if (selected.get("default"), selected.get("provider"), selected.get("key_env")) != (
        MODEL, f"custom:{PROVIDER}", KEY.decode()
    ) or prior["model"].get("default") != "automation-moe" or prior["model"].get(
        "api_key"
    ) != "${COMPATIBLE_API_KEY}":
        raise ValueError("active and previous model bindings differ from the incident")
    keys = [line for line in env.splitlines(keepends=True) if line.startswith(KEY + b"=")]
    if len(keys) != 1 or not re.fullmatch(KEY + rb"=sk-[A-Za-z0-9_-]+\n?", keys[0]):
        raise ValueError("the rejected raw credential must appear exactly once")
    if any(line.startswith(KEY + b"=") for line in prior_env.splitlines()):
        raise ValueError("the previous environment contained a Sol binding")
    configured = active["providers"].pop(PROVIDER)
    if configured != {
        "name": PROVIDER, "base_url": selected["base_url"], "default_model": MODEL,
        "api_mode": "chat_completions", "key_env": KEY.decode(),
    }:
        raise ValueError("the active named Sol provider differs")
    active["model"] = prior["model"]
    if not active["providers"] and "providers" not in prior:
        del active["providers"]
    return (
        yaml.safe_dump(active, sort_keys=False).encode(),
        b"".join(line for line in env.splitlines(keepends=True) if line != keys[0]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="REVIEWED_PREIMAGE_SHA256")
    parser.add_argument("--resume-from", type=Path, metavar="PRIVATE_CONFIGURATION_BACKUP")
    args = parser.parse_args()
    os.umask(0o077)
    (container,) = json.loads(docker("inspect", CONTAINER))
    if container["Image"] != IMAGE or not container["Name"].startswith(
        "/openshell-default--hermes-personal-v2-"
    ) or container["State"]["Running"]:
        raise ValueError("the stopped incident container is required")
    config, config_metadata = read(f"{HOME}/config.yaml")
    env, env_metadata = read(f"{HOME}/.env")
    receipt, _ = read(f"{HOME}/.homecompute-owner-sol-before.json")
    backup = args.resume_from
    if backup is not None:
        if backup.is_symlink() or stat.S_IMODE(backup.stat().st_mode) != 0o700 or backup.stat().st_uid != os.getuid():
            raise ValueError("private operator-owned configuration backup required")
        preimages = (backup / "config.yaml", backup / ".env")
        for path in preimages:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("private regular configuration preimage required")
        original_config, original_env = (path.read_bytes() for path in preimages)
    else:
        original_config, original_env = config, env
    revised_config, revised_env = replacement(original_config, original_env, json.loads(receipt))
    documents = (
        ("config.yaml", config, config_metadata, original_config, revised_config, 0o600),
        (".env", env, env_metadata, original_env, revised_env, 0o640),
    )
    for _, current, item, original, revised, mode in documents:
        owner = (item.uid, item.gid)
        if item.mode != mode or not (
            owner == (998, 999) and current in (original, revised)
            or backup is not None and owner == (0, 0) and current == revised
        ):
            raise ValueError("owner document preimage, mode or identity differs")
    before = digest(original_config, original_env)
    print(f"Owner Sol startup recovery preimage SHA-256 {before}", flush=True)
    if not args.apply:
        return
    if args.apply != before:
        raise ValueError("owner config changed since the recovery plan")
    if backup is None:
        backup = Path(tempfile.mkdtemp(prefix="hermes-sol-startup-", dir=Path.home() / ".local/state"))
        for name, contents in (("config.yaml", original_config), (".env", original_env)):
            (backup / name).write_bytes(contents)
    for name, current, metadata, _, revised, mode in documents:
        observed, item = read(f"{HOME}/{name}")
        if observed != current or (item.uid, item.gid, item.mode) != (metadata.uid, metadata.gid, mode):
            raise ValueError("owner file changed during recovery")
        if observed != revised or (item.uid, item.gid) != (998, 999):
            update = io.BytesIO()
            with tarfile.open(fileobj=update, mode="w") as archive:
                entry = tarfile.TarInfo(name)
                entry.size = len(revised)
                entry.uid, entry.gid, entry.mode = 998, 999, mode
                entry.uname, entry.gname, entry.mtime = "sandbox", "sandbox", metadata.mtime
                archive.addfile(entry, io.BytesIO(revised))
            docker("cp", "-", f"{CONTAINER}:{HOME}", data=update.getvalue())
        observed, item = read(f"{HOME}/{name}")
        if observed != revised or (item.uid, item.gid, item.mode) != (998, 999, mode):
            raise ValueError("owner startup recovery readback failed")
    print(f"Restored local inference for startup. Private configuration backup retained at {backup}.")


if __name__ == "__main__":
    main()
