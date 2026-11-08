#!/usr/bin/env python3
"""Restore the qualified Hermes sandbox config owners after the UID transition.

The release image requires config.yaml and .env to be owned by the sandbox
user/group. This repair keeps config.yaml at its backed-up private mode and
.env at mode 0640, changing metadata only while retaining exact file bytes. It
is admitted only for the known retained owner container, a stopped
gateway/container, and a matching private whole-sandbox backup.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
NAME = "hermes-personal-v23"
CONTAINER = "ba08f69f253d44bb5f8f16e1eceb329ea93b20a1d3b5cfcd3c5a22bdb89bd2f0"
IMAGE = "sha256:f15b60053b28b681efe555f89a6d170c7f7cd51036ed570b9951908c19f85311"
ENV = "sandbox/.hermes/.env"
CONFIG = "sandbox/.hermes/config.yaml"
STATE_DB = "sandbox/.hermes/runtime/state.db"
GATEWAY_UNIT = "nemoclaw-openshell-gateway.service"
RECEIPT_DIR = Path.home() / ".local/state/homecompute/hermes-env-ownership-recovery"
TARGET_MODE = 0o640
MAX_ENV_BYTES = 4 * 1024 * 1024


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError("guarded Hermes ownership operation failed: " + args[0])
    return result.stdout


def file_from_tar(raw, expected_name):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = [row for row in archive.getmembers() if row.name.rstrip("/") == expected_name]
        if len(members) != 1 or not members[0].isfile() or members[0].issparse():
            raise ValueError("one regular Hermes file required")
        member = members[0]
        if member.size > MAX_ENV_BYTES:
            raise ValueError("bounded Hermes file required")
        return archive.extractfile(member).read(), member


def docker_file(container, path):
    raw = run(["docker", "cp", container + ":/" + path, "-"])
    return file_from_tar(raw, path.rsplit("/", 1)[-1])


def backup_file(archive, name):
    member = archive.getmember(name)
    if not member.isfile() or member.issparse() or member.size > MAX_ENV_BYTES:
        raise ValueError("private full-sandbox archive has an invalid Hermes file")
    return archive.extractfile(member).read(), member


def private_backup(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("real private backup path required")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("operator-owned mode-0600 full-sandbox archive required")
    return path


def validate_sandbox_directories(archive):
    sandbox = archive.getmember("sandbox")
    hermes_dir = archive.getmember("sandbox/.hermes")
    if not sandbox.isdir() or not hermes_dir.isdir():
        raise ValueError("expected Hermes sandbox directories in backup")
    if hermes_dir.uid != 998 or hermes_dir.gid != 999:
        raise ValueError("Hermes config directory owner differs from the selected image")
    return sandbox, hermes_dir


def inspect(revision, backup_path):
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("full published revision required")
    spec = importlib.util.spec_from_file_location("native_publication", ROOT / "scripts/setup-hermes-mcp.py")
    publication = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publication)
    publication.published(revision)
    private_backup(backup_path)
    backup_sha = hashlib.file_digest(backup_path.open("rb"), "sha256").hexdigest()
    with tarfile.open(backup_path) as archive:
        sandbox, hermes_dir = validate_sandbox_directories(archive)
        env_before, env_meta = backup_file(archive, ENV)
        config_before, config_meta = backup_file(archive, CONFIG)
        state_member = archive.getmember(STATE_DB)
        if not state_member.isfile() or state_member.size < 16:
            raise ValueError("real Hermes application database required in backup")
        state_before = archive.extractfile(state_member).read(16)
    if not state_before.startswith(b"SQLite format 3\x00"):
        raise ValueError("real Hermes application database required in backup")
    env_target = (hermes_dir.uid, hermes_dir.gid, TARGET_MODE)
    env_backup_meta = (env_meta.uid, env_meta.gid, env_meta.mode)
    current, current_meta = docker_file(CONTAINER, ENV)
    if current != env_before or (current_meta.uid, current_meta.gid, current_meta.mode) not in {
            env_backup_meta, env_target}:
        raise ValueError("Hermes env changed since the selected full-sandbox backup")
    config_target = (hermes_dir.uid, hermes_dir.gid, config_meta.mode)
    config_backup_meta = (config_meta.uid, config_meta.gid, config_meta.mode)
    current_config, current_config_meta = docker_file(CONTAINER, CONFIG)
    if (current_config != config_before or
            (current_config_meta.uid, current_config_meta.gid, current_config_meta.mode) not in {
                config_backup_meta, config_target}):
        raise ValueError("Hermes config changed since the selected full-sandbox backup")
    docker = json.loads(run(["docker", "inspect", CONTAINER]))[0]
    if (docker["Id"] != CONTAINER or docker["Name"] != "/openshell-default--" + NAME
            + "-d90b7fc4-1506-4d12-b055-f0ab2aa4dcc7" or docker["Image"] != IMAGE):
        raise ValueError("qualified retained Hermes owner container required")
    if docker["State"]["Running"]:
        raise ValueError("stop the selected Hermes sandbox before metadata repair")
    gateway = subprocess.run(["systemctl", "--user", "is-active", GATEWAY_UNIT],
                             capture_output=True, timeout=10).stdout.strip()
    if gateway != b"inactive":
        raise ValueError("stop the selected OpenShell gateway before metadata repair")

    env_actual = (current_meta.uid, current_meta.gid, current_meta.mode)
    if env_actual not in {(1000, 1000, TARGET_MODE), env_target}:
        raise ValueError("Hermes env owner/mode differs from the diagnosed UID transition")
    if config_meta.mode != 0o600:
        raise ValueError("Hermes config mode differs from the diagnosed private mode")
    config_actual = (current_config_meta.uid, current_config_meta.gid, current_config_meta.mode)
    if config_actual not in {(1000, 1000, config_meta.mode), config_target}:
        raise ValueError("Hermes config owner/mode differs from the diagnosed UID transition")
    report = {"revision": revision, "sandbox": NAME, "container": CONTAINER,
              "backup_sha256": backup_sha,
              "env_before": {"uid": env_actual[0], "gid": env_actual[1], "mode": env_actual[2]},
              "env_after": {"uid": env_target[0], "gid": env_target[1], "mode": env_target[2]},
              "config_before": {"uid": config_actual[0], "gid": config_actual[1], "mode": config_actual[2]},
              "config_after": {"uid": config_target[0], "gid": config_target[1], "mode": config_target[2]},
              "config_sha256": hashlib.sha256(config_before).hexdigest(),
              "already_repaired": env_actual == env_target and config_actual == config_target}
    return report, (current, current_meta, config_before, current_config_meta), (env_target, config_target)


def replacement_tar(name, data, old_meta, target):
    encoded = io.BytesIO()
    with tarfile.open(fileobj=encoded, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.uid, member.gid, member.mode = target
        member.uname, member.gname = "sandbox", "sandbox"
        member.mtime = old_meta.mtime
        archive.addfile(member, io.BytesIO(data))
    return encoded.getvalue()


def apply(report, files, targets, expected):
    if hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest() != expected:
        raise ValueError("reviewed Hermes ownership plan changed")
    if report["already_repaired"]:
        return {"revision": report["revision"], "sandbox": NAME, "applied": True,
                "already_repaired": True}
    env_data, env_meta, config_data, config_meta = files
    env_target, config_target = targets
    run(["docker", "cp", "-", CONTAINER + ":/sandbox/.hermes"],
        replacement_tar(".env", env_data, env_meta, env_target))
    check, metadata = docker_file(CONTAINER, ENV)
    if check != env_data or (metadata.uid, metadata.gid, metadata.mode) != env_target:
        raise ValueError("Hermes env metadata repair readback failed")
    run(["docker", "cp", "-", CONTAINER + ":/sandbox/.hermes"],
        replacement_tar("config.yaml", config_data, config_meta, config_target))
    check, metadata = docker_file(CONTAINER, CONFIG)
    if check != config_data or (metadata.uid, metadata.gid, metadata.mode) != config_target:
        raise ValueError("Hermes config metadata repair readback failed")

    RECEIPT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = RECEIPT_DIR.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("private Hermes ownership recovery receipt directory required")
    receipt = RECEIPT_DIR / ("receipt-" + str(time.time_ns()) + ".json")
    payload = {**report, "applied": True, "applied_at": int(time.time())}
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    return {"revision": report["revision"], "sandbox": NAME, "applied": True,
            "already_repaired": False, "receipt": str(receipt)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    report, files, targets = inspect(args.revision, args.backup)
    if args.apply:
        if not args.expected_sha256:
            raise ValueError("reviewed plan SHA-256 required for apply")
        result = apply(report, files, targets, args.expected_sha256)
    else:
        result = {**report, "plan_sha256": hashlib.sha256(
            json.dumps(report, sort_keys=True).encode()).hexdigest(), "applied": False}
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Hermes env ownership repair held (" + type(error).__name__ + "); preserve private state.",
              file=__import__("sys").stderr)
        raise SystemExit(2)
