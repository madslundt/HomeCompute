#!/usr/bin/env python3
"""Root-only encrypted backups of completed recovery archives to a separate NAS share."""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile

MOUNT = Path("/mnt/homecompute-backups")
STATE = Path("/var/lib/homecompute/nas-recovery-backup")
SOURCE = Path("/srv/backups/agent-recovery")
PASSWORD = Path("/run/secrets/restic/password")
REPOSITORY = MOUNT / "restic-homecompute-agent-recovery"
CONFIG = Path("/etc/homecompute/nas-recovery-backup.json")
RESTORE_ROOT = Path("/srv/restore-tests")


def configuration(value: dict) -> dict:
    if set(value) != {"address", "share", "credentialsFile"}:
        raise ValueError("configuration must contain only address, share, credentialsFile")
    address = str(ipaddress.IPv4Address(value["address"]))
    share = value["share"]
    if not isinstance(share, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", share):
        raise ValueError("share must be a single SMB share name")
    if any(word in share.lower() for word in ("immich", "photo", "video", "library", "upload")):
        raise ValueError("backup destination must be separate from media shares")
    credential = value["credentialsFile"]
    if not isinstance(credential, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", credential):
        raise ValueError("credentialsFile must be an absolute simple path")
    if ".." in Path(credential).parts:
        raise ValueError("credential path traversal is forbidden")
    return {"address": address, "share": share, "credentialsFile": credential}


def private_file(path: Path) -> None:
    # Runtime SOPS files are root-owned symlinks; validate the target too.
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or not info.st_size:
        raise ValueError(f"root-only nonempty regular file required: {path}")


def root_directory(path: Path, create: bool = False) -> None:
    if create and not path.exists():
        path.mkdir(mode=0o700, parents=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError(f"real root-only directory required: {path}")


def load_configuration(path: Path) -> dict:
    # NixOS /etc entries point into the immutable store; validate their target.
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("configuration must be a real root-owned file without untrusted writes")
    return configuration(json.loads(path.read_text()))


def require_mount(config: dict) -> None:
    document = json.loads(subprocess.check_output([
        "findmnt", "--json", "--target", str(MOUNT), "--output", "TARGET,SOURCE,FSTYPE",
    ], text=True))
    entries = document.get("filesystems", [])
    expected = f"//{config['address']}/{config['share']}"
    if len(entries) != 1 or entries[0] != {"target": str(MOUNT), "source": expected, "fstype": "cifs"}:
        raise ValueError("exact separate NAS CIFS mount is unavailable; refusing local fallback")
    private_file(Path(config["credentialsFile"]))


def restic(args: list[str]) -> str:
    private_file(PASSWORD)
    root_directory(STATE)
    root_directory(REPOSITORY)
    env = {"PATH": os.environ.get("PATH", "/run/current-system/sw/bin"),
           "HOME": str(STATE), "RESTIC_PASSWORD_FILE": str(PASSWORD),
           "RESTIC_REPOSITORY": str(REPOSITORY), "RESTIC_CACHE_DIR": str(STATE / "cache")}
    return subprocess.check_output(["restic", *args], env=env, text=True, timeout=6600)


def record(name: str, value: dict) -> None:
    value["time"] = dt.datetime.now(dt.timezone.utc).isoformat()
    destination = STATE / name
    with tempfile.NamedTemporaryFile(mode="w", dir=STATE, delete=False) as handle:
        json.dump(value, handle)
        temporary = Path(handle.name)
    os.replace(temporary, destination)


def initialize() -> None:
    # Never initialize over existing files, including an existing Restic repo.
    if REPOSITORY.exists() or REPOSITORY.is_symlink():
        raise ValueError("repository path already exists; initialization refused")
    root_directory(STATE, create=True)
    private_file(PASSWORD)
    REPOSITORY.mkdir(mode=0o700)
    restic(["init", "--json"])
    print("Encrypted repository initialized using the existing SOPS-managed Restic password.")


def backup() -> None:
    root_directory(SOURCE)
    if not any(path.is_file() and not path.name.endswith(".partial") for path in SOURCE.iterdir()):
        raise ValueError("no completed recovery archives are available")
    output = restic(["backup", "--json", "--tag", "agent-recovery-archives", "--exclude", "*.partial", str(SOURCE)])
    summary = next((json.loads(line) for line in output.splitlines()
                    if json.loads(line).get("message_type") == "summary"), None)
    if not summary or not summary.get("snapshot_id"):
        raise ValueError("Restic did not confirm a completed archive snapshot")
    record("last-backup.json", {"snapshot": summary["snapshot_id"], "scope": str(SOURCE)})
    print("Encrypted archive snapshot completed.")


def proof() -> None:
    root_directory(STATE)
    with tempfile.TemporaryDirectory(prefix="restore-proof-", dir=STATE) as temporary:
        directory = Path(temporary)
        payload = directory / "proof.bin"
        expected = secrets.token_bytes(128)
        payload.write_bytes(expected)
        payload.chmod(0o600)
        output = restic(["backup", "--json", "--tag", "restore-proof", str(payload)])
        snapshot = next(json.loads(line)["snapshot_id"] for line in output.splitlines()
                        if json.loads(line).get("message_type") == "summary")
        restored = directory / "restored"
        restic(["restore", snapshot, "--target", str(restored), "--include", str(payload)])
        result = restored / str(payload).lstrip("/")
        if result.read_bytes() != expected:
            raise ValueError("restore proof bytes do not match")
        record("last-restore-proof.json", {"snapshot": snapshot, "bytes": len(expected), "matched": True})
    print("Encrypted NAS write, snapshot, and isolated restore proof passed.")


def restore_application(snapshot: str, archive: str, target: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        raise ValueError("an exact full snapshot ID is required")
    if not re.fullmatch(r"homecompute-applications-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z\.tar", archive):
        raise ValueError("an exact application recovery archive name is required")
    root_directory(RESTORE_ROOT)
    if (not target.is_absolute() or target.parent.resolve() != RESTORE_ROOT.resolve()
            or target.exists() or target.is_symlink()):
        raise ValueError("restore needs a new direct child of /srv/restore-tests")
    target.mkdir(mode=0o700)
    source = SOURCE / archive
    restic(["restore", snapshot, "--target", str(target), "--include", str(source)])
    restored = target / str(source).lstrip("/")
    private_file(restored)
    record("last-application-restore.json", {"snapshot": snapshot, "archive": archive,
                                            "scope": "isolated archive restore; application verification pending"})
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "init", "backup", "proof", "check", "restore-application"))
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--snapshot")
    parser.add_argument("--archive")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError("trusted root operator required")
    os.umask(0o077)
    config = load_configuration(args.config)
    require_mount(config)
    if args.command == "validate":
        print("Dedicated NAS mount and credential custody validated.")
    elif args.command == "init":
        initialize()
    elif args.command == "backup":
        backup()
    elif args.command == "proof":
        proof()
    elif args.command == "restore-application":
        if not args.snapshot or not args.archive or not args.target:
            parser.error("restore-application requires --snapshot, --archive and --target")
        print(json.dumps({"restored_archive": str(restore_application(args.snapshot, args.archive, args.target)),
                          "application_verification": "pending"}))
    else:
        restic(["check", "--read-data-subset=5%"])
        record("last-check.json", {"passed": True})
        print("NAS repository integrity check passed.")


if __name__ == "__main__":
    main()
