#!/usr/bin/env python3
"""Root-only encrypted backups of completed recovery archives to a separate NAS share."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import sqlite3

MOUNT = Path("/mnt/homecompute-backups")
STATE = Path("/var/lib/homecompute/nas-recovery-backup")
SOURCE = Path("/srv/backups/homecompute-selected-state")
HERMES_ARCHIVE = SOURCE / "hermes-native-home.tar.gz"
IMMICH_LIBRARY = Path("/mnt/immich-nas")
IMMICH_DATABASE_DUMPS = Path("/srv/state/immich/db-backups")
HERMES_GUEST = "hermes-operator@10.77.20.2"
HERMES_KNOWN_HOST = "10.77.20.2"
PASSWORD = Path("/run/secrets/restic/password")
HERMES_SSH_KEY = Path("/run/secrets/hermes-backup/ssh-key")
HERMES_KNOWN_HOSTS = Path("/etc/homecompute/hermes-backup_known_hosts")
REPOSITORY = MOUNT / "restic-homecompute-selected"
CONFIG = Path("/etc/homecompute/nas-recovery-backup.json")
RESTORE_ROOT = Path("/srv/restore-tests")


def configuration(value: dict) -> dict:
    if set(value) != {"address", "share", "credentialsFile", "hermesSshKeyFile", "hermesKnownHostsFile",
                      "immichDbBackupScript", "immichComposeFile"}:
        raise ValueError("configuration has an unknown or missing selected-backup field")
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
    ssh_key = value["hermesSshKeyFile"]
    known_hosts = value["hermesKnownHostsFile"]
    for name, path in (("hermesSshKeyFile", ssh_key), ("hermesKnownHostsFile", known_hosts)):
        if (not isinstance(path, str) or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path)
                or ".." in Path(path).parts):
            raise ValueError(f"{name} must be an absolute simple path")
    db_script = value["immichDbBackupScript"]
    if (not isinstance(db_script, str) or not db_script.startswith("/nix/store/")
            or not re.fullmatch(r"/nix/store/[A-Za-z0-9+._?=-]+/scripts/immich-db-backup\.sh", db_script)):
        raise ValueError("immichDbBackupScript must name the tracked Nix-store helper")
    compose = value["immichComposeFile"]
    if (not isinstance(compose, str) or not compose.startswith("/nix/store/")
            or not re.fullmatch(r"/nix/store/[A-Za-z0-9+._?=-]+(?:/deploy/immich/compose\.yaml|-compose\.yaml)", compose)):
        raise ValueError("immichComposeFile must name the tracked Nix-store Compose file")
    return {"address": address, "share": share, "credentialsFile": credential,
            "hermesSshKeyFile": ssh_key, "hermesKnownHostsFile": known_hosts,
            "immichDbBackupScript": db_script, "immichComposeFile": compose}


def private_file(path: Path) -> None:
    # Runtime SOPS files are root-owned symlinks; validate the target too.
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or not info.st_size:
        raise ValueError(f"root-only nonempty regular file required: {path}")


def pinned_known_hosts(path: Path) -> None:
    info = path.stat()
    lines = path.read_text().splitlines()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or info.st_size > 4096 or len(lines) != 1):
        raise ValueError("one root-owned pinned agents guest host key is required")
    fields = lines[0].split()
    if len(fields) != 3 or fields[0] != HERMES_KNOWN_HOST or fields[1] != "ssh-ed25519":
        raise ValueError("agents guest known-hosts entry does not match the pinned SSH scope")


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
    cifs = {"target": str(MOUNT), "source": expected, "fstype": "cifs"}
    automount = {"target": str(MOUNT), "source": "systemd-1", "fstype": "autofs"}
    # findmnt reports both layers of a systemd automount after activation.
    # The order matters: the expected CIFS filesystem must be the top layer.
    if entries not in ([cifs], [automount, cifs]):
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


def verify_hermes_archive(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20 * 1024**3:
        raise ValueError("bounded regular Hermes state archive required")
    databases = []
    with tempfile.TemporaryDirectory(prefix="hermes-archive-check-", dir=STATE) as temporary:
        root = Path(temporary)
        with tarfile.open(path, "r:gz") as archive:
            manifest_member = archive.getmember("manifest.json")
            if not manifest_member.isfile() or manifest_member.size > 16384:
                raise ValueError("bounded Hermes backup manifest required")
            manifest = json.load(archive.extractfile(manifest_member))
            if (manifest.get("schema_version") != 1 or manifest.get("scope") != "Hermes native-home state"
                    or manifest.get("sandbox") != "hermes-personal-v2"
                    or manifest.get("container_images_included") is not False):
                raise ValueError("Hermes backup manifest scope mismatch")
            members = archive.getmembers()
            names = {member.name for member in members}
            normalized_names = {name.rstrip("/") for name in names}
            if (len(names) != len(members) or len(normalized_names) != len(members)
                    or not any(name.startswith("native-home/.hermes/") for name in names)
                    or sum(member.size for member in members) > 20 * 1024**3
                    or any(name.startswith("/") or ".." in Path(name).parts for name in names)
                    or any(name not in {"manifest.json", "native-home", "native-home/.hermes"}
                           and not name.startswith("native-home/.hermes/") for name in normalized_names)):
                raise ValueError("Hermes archive paths are unsafe or incomplete")
            for member in members:
                if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                    raise ValueError("Hermes archive contains a link or special file")
                parts = Path(member.name).parts
                names_lower = [part.lower() for part in parts]
                if (any(part.startswith(".env") or part.startswith("auth.json")
                        or part.startswith("config.yaml") or part.endswith((".lock", ".pid", ".tmp", ".log"))
                        or "_cache" in part or part in {"ticker_heartbeat", "ticker_last_success"}
                        for part in names_lower)
                        or any(part in {"backups", "cache", "desktop-plugins", "installs", "lazy-packages",
                                        "logs", "plugin-update-checks", "__pycache__", ".curator_backups", ".locks"}
                               for part in parts)):
                    raise ValueError("Hermes archive contains excluded runtime or credential state")
                if member.isfile() and member.name.lower().endswith((".db", ".sqlite", ".sqlite3")):
                    stream = archive.extractfile(member)
                    destination = root / (str(len(databases)) + ".sqlite")
                    with destination.open("xb") as output:
                        shutil.copyfileobj(stream, output, length=1024 * 1024)
                    destination.chmod(0o600)
                    with sqlite3.connect(destination) as database:
                        if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
                            raise ValueError("Hermes SQLite archive integrity failed")
                    databases.append(member.name)
    if not databases:
        raise ValueError("Hermes archive contains no SQLite agent state")
    return {"scope": manifest["scope"], "databases": len(databases)}


def extract_hermes_archive(source: Path, destination: Path) -> None:
    verify_hermes_archive(source)
    root = destination / "hermes-native-home"
    root.mkdir(mode=0o700)
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name in {"manifest.json", "native-home"}:
                continue
            relative_name = member.name.removeprefix("native-home/")
            relative = Path(relative_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Hermes archive path escapes the restore directory")
            output = root / relative
            if member.isdir():
                output.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            with output.open("xb") as handle:
                shutil.copyfileobj(stream, handle, length=1024 * 1024)
            output.chmod(stat.S_IMODE(member.mode) & 0o700)


def immich_library_mount() -> None:
    # Access triggers the systemd automount. Require the exact media share so
    # an empty local mountpoint can never masquerade as a complete backup.
    os.stat(IMMICH_LIBRARY)
    output = subprocess.check_output([
        "findmnt", "--json", "--target", str(IMMICH_LIBRARY), "--output", "TARGET,SOURCE,FSTYPE",
    ], text=True)
    entries = json.loads(output).get("filesystems", [])
    cifs = {"target": str(IMMICH_LIBRARY), "source": "//192.168.30.236/immich-library", "fstype": "cifs"}
    automount = {"target": str(IMMICH_LIBRARY), "source": "systemd-1", "fstype": "autofs"}
    if entries not in ([cifs], [automount, cifs]):
        raise ValueError("exact Immich media library mount is unavailable")


def export_hermes(config: dict) -> dict:
    ssh_key = Path(config["hermesSshKeyFile"])
    known_hosts = Path(config["hermesKnownHostsFile"])
    private_file(ssh_key)
    pinned_known_hosts(known_hosts)
    private_directory(SOURCE, create=True)
    temporary = SOURCE / ".hermes-native-home.tar.gz.partial"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    command = [
        "ssh", "-T", "-i", str(ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        HERMES_GUEST,
    ]
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, timeout=1800, check=False)
            stream.flush()
            os.fsync(stream.fileno())
        if result.returncode:
            raise RuntimeError("Hermes native-home export did not complete")
        checked = verify_hermes_archive(temporary)
        os.replace(temporary, HERMES_ARCHIVE)
        return checked
    finally:
        temporary.unlink(missing_ok=True)


def prune_retention() -> None:
    restic(["forget", "--keep-daily", "30", "--keep-weekly", "12", "--keep-monthly", "24", "--prune"])


def validate_immich_dumps(path: Path) -> int:
    root_directory(path)
    dumps = list(path.iterdir())
    if not dumps:
        raise ValueError("completed Immich PostgreSQL dump is required")
    for dump in dumps:
        info = dump.lstat()
        if (not re.fullmatch(r"immich-postgres-[0-9]{8}T[0-9]{6}Z\.sql\.gz", dump.name)
                or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size == 0):
            raise ValueError("Immich dump directory contains an unsafe or incomplete entry")
        with gzip.open(dump, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
    return len(dumps)


def initialize() -> None:
    # Never initialize over existing files, including an existing Restic repo.
    if REPOSITORY.exists() or REPOSITORY.is_symlink():
        raise ValueError("repository path already exists; initialization refused")
    root_directory(STATE, create=True)
    private_file(PASSWORD)
    REPOSITORY.mkdir(mode=0o700)
    restic(["init", "--json"])
    print("Selected-state encrypted Restic repository initialized.")


def backup(config: dict) -> None:
    root_directory(SOURCE, create=True)
    immich_library_mount()
    if IMMICH_DATABASE_DUMPS.is_symlink() or not IMMICH_DATABASE_DUMPS.is_dir():
        raise ValueError("Immich database dump directory is absent or unsafe")
    db_script = Path(config["immichDbBackupScript"])
    if db_script.is_symlink() or not db_script.is_file() or db_script.stat().st_mode & 0o022:
        raise ValueError("tracked Immich database backup helper is absent or writable")
    result = subprocess.run([str(db_script)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=600, check=False)
    if result.returncode:
        raise RuntimeError("application-consistent Immich database dump failed")
    validate_immich_dumps(IMMICH_DATABASE_DUMPS)
    hermes = export_hermes(config)
    output = restic(["backup", "--json", "--tag", "homecompute-selected-state",
                     str(HERMES_ARCHIVE), str(IMMICH_LIBRARY), str(IMMICH_DATABASE_DUMPS)])
    summary = next((json.loads(line) for line in output.splitlines()
                    if json.loads(line).get("message_type") == "summary"), None)
    if not summary or not summary.get("snapshot_id"):
        raise ValueError("Restic did not confirm a completed selected-state snapshot")
    prune_retention()
    record("last-backup.json", {
        "snapshot": summary["snapshot_id"],
        "scope": ["Immich media library", "Immich database dumps", "Hermes native-home state"],
        "hermes_sqlite_databases": hermes["databases"],
    })
    HERMES_ARCHIVE.unlink(missing_ok=True)
    print("Encrypted Immich and Hermes backup completed.")


def scheduled(config: dict) -> None:
    """Create only the selected Immich and Hermes data backup."""
    backup(config)


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
        restic(["forget", snapshot, "--prune"])
    print("Encrypted selected-state repository write and isolated restore proof passed.")


def restore_selected(snapshot: str, target: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        raise ValueError("an exact full snapshot ID is required")
    root_directory(RESTORE_ROOT)
    if (not target.is_absolute() or target.parent.resolve() != RESTORE_ROOT.resolve()
            or target.exists() or target.is_symlink()):
        raise ValueError("selected-state restore needs a new direct child of /srv/restore-tests")
    target.mkdir(mode=0o700)
    restic(["restore", snapshot, "--target", str(target), "--include", str(HERMES_ARCHIVE),
            "--include", str(IMMICH_LIBRARY), "--include", str(IMMICH_DATABASE_DUMPS)])
    restored_hermes = target / str(HERMES_ARCHIVE).lstrip("/")
    verify_hermes_archive(restored_hermes)
    extract_hermes_archive(restored_hermes, target)
    restored_library = target / str(IMMICH_LIBRARY).lstrip("/")
    if restored_library.is_symlink() or not restored_library.is_dir() or not any(restored_library.iterdir()):
        raise ValueError("restored Immich library is absent or empty")
    restored_dumps = target / str(IMMICH_DATABASE_DUMPS).lstrip("/")
    dump_count = validate_immich_dumps(restored_dumps)
    record("last-selected-restore.json", {
        "snapshot": snapshot,
        "scope": ["Immich media library", "Immich database dumps", "Hermes native-home state"],
        "hermes_archive_verified": True,
        "hermes_native_home_extracted": True,
        "immich_dump_files_verified": dump_count,
        "application_import_verified": False,
    })
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "init", "backup", "scheduled", "proof", "check", "restore-selected"))
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--snapshot")
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError("trusted root operator required")
    os.umask(0o077)
    config = load_configuration(args.config)
    require_mount(config)
    if args.command == "validate":
        private_file(Path(config["hermesSshKeyFile"]))
        pinned_known_hosts(Path(config["hermesKnownHostsFile"]))
        db_script = Path(config["immichDbBackupScript"])
        if db_script.is_symlink() or not db_script.is_file() or db_script.stat().st_mode & 0o022:
            raise ValueError("tracked Immich database backup helper is absent or writable")
        compose_file = Path(config["immichComposeFile"])
        if compose_file.is_symlink() or not compose_file.is_file() or compose_file.stat().st_mode & 0o022:
            raise ValueError("tracked Immich Compose input is absent or writable")
        print("Selected Immich and Hermes backup inputs and NAS custody validated.")
    elif args.command == "init":
        initialize()
    elif args.command == "backup":
        backup(config)
    elif args.command == "scheduled":
        scheduled(config)
    elif args.command == "proof":
        proof()
    elif args.command == "restore-selected":
        if not args.snapshot or not args.target:
            parser.error("restore-selected requires --snapshot and --target")
        print(json.dumps({"restored_to": str(restore_selected(args.snapshot, args.target)),
                          "application_import_verified": False}))
    else:
        restic(["check", "--read-data-subset=5%"])
        record("last-check.json", {"passed": True})
        print("NAS repository integrity check passed.")


if __name__ == "__main__":
    main()
