#!/usr/bin/env python3
"""Create a sanitized, SQLite-consistent tar of a Hermes native home.

This script is intended to run inside the Hermes sandbox through NemoClaw's
stdin exec path. It never reads or archives generated configuration, dotenv
credentials, runtime logs, or rebuildable caches.
"""
from __future__ import annotations

from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tarfile
import tempfile

SOURCE = Path(os.environ.get("HERMES_NATIVE_HOME", "/sandbox/.hermes"))
OUTPUT = Path(os.environ.get("HERMES_SNAPSHOT_OUTPUT", "/tmp/homecompute-hermes-native-home.tar.gz"))
MAX_BYTES = 20 * 1024**3
EXCLUDED_NAMES = {"auth.json", "config.yaml", "install_id", ".config-hash"}
EXCLUDED_DIRS = {
    "backups", "cache", "desktop-plugins", "installs", "lazy-packages", "logs",
    "plugin-update-checks", "__pycache__", ".curator_backups", ".locks",
}


def excluded(relative: Path) -> bool:
    name = relative.name
    lowered = name.lower()
    sqlite_sidecar = any(lowered.endswith(suffix) for suffix in ("-wal", "-shm", "-journal"))
    runtime_file = (lowered.startswith(".env") or lowered.startswith("auth.json")
                    or lowered.startswith("config.yaml") or lowered.endswith((".lock", ".pid", ".tmp", ".log"))
                    or "_cache" in lowered or lowered in {"ticker_heartbeat", "ticker_last_success"})
    return (sqlite_sidecar or runtime_file or any(part in EXCLUDED_DIRS for part in relative.parts)
            or name in EXCLUDED_NAMES)


def sqlite_copy(source: Path, destination: Path) -> None:
    uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as original, closing(
        sqlite3.connect(destination)
    ) as copy:
        original.execute("PRAGMA busy_timeout=10000")
        original.backup(copy, pages=256, sleep=0.05)
        if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Hermes SQLite snapshot failed integrity validation")
    destination.chmod(0o600)


def snapshot() -> Path:
    if SOURCE.is_symlink() or not SOURCE.is_dir():
        raise ValueError("Hermes native home must be a real directory")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError("Hermes snapshot output already exists")

    with tempfile.TemporaryDirectory(prefix="homecompute-hermes-state-") as temporary:
        root = Path(temporary) / "native-home" / ".hermes"
        root.mkdir(mode=0o700, parents=True)
        total = 0
        for parent, directories, filenames in os.walk(SOURCE, followlinks=False):
            source_parent = Path(parent)
            relative_parent = source_parent.relative_to(SOURCE)
            if source_parent.is_symlink():
                raise ValueError("Hermes native home contains a symlinked directory")
            output_parent = root / relative_parent
            output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            kept_directories = []
            for name in directories:
                child = source_parent / name
                relative = relative_parent / name
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("Hermes native home contains a symlink")
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("Hermes native home contains a special directory entry")
                if not excluded(relative):
                    kept_directories.append(name)
                    (output_parent / name).mkdir(mode=0o700, exist_ok=True)
            directories[:] = kept_directories

            for name in filenames:
                source = source_parent / name
                relative = relative_parent / name
                if excluded(relative):
                    continue
                info = source.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Hermes native home contains a linked or special file")
                total += info.st_size
                if total > MAX_BYTES:
                    raise ValueError("Hermes native home exceeds the 20 GiB snapshot limit")
                destination = output_parent / name
                if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    sqlite_copy(source, destination)
                else:
                    before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    shutil.copyfile(source, destination)
                    after_info = source.lstat()
                    after = (after_info.st_dev, after_info.st_ino, after_info.st_size,
                             after_info.st_mtime_ns, after_info.st_ctime_ns)
                    if before != after:
                        raise ValueError("Hermes native-home file changed during backup")
                    destination.chmod(stat.S_IMODE(info.st_mode) & 0o700)

        manifest = {
            "schema_version": 1,
            "scope": "Hermes native-home state",
            "sandbox": "hermes-personal-v2",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "database_consistency": "SQLite online backup API",
            "excluded": ["dotenv and auth files", "generated config.yaml and backups", "logs", "caches",
                         "runtime packages and installs", "locks, process IDs and SQLite sidecars"],
            "container_images_included": False,
        }
        (Path(temporary) / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        with tarfile.open(OUTPUT, "x:gz", compresslevel=6) as archive:
            archive.add(Path(temporary) / "manifest.json", arcname="manifest.json")
            archive.add(root.parent, arcname="native-home", recursive=True)
    OUTPUT.chmod(0o600)
    return OUTPUT


if __name__ == "__main__":
    path = snapshot()
    print(f"Sanitized Hermes native-home snapshot created ({path.stat().st_size} bytes).")
