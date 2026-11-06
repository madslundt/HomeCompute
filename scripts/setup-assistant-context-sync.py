#!/usr/bin/env python3
"""Plan or install the fixed private assistant-context source selection."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config/assistant-context-sync.example.json"
TARGET = Path("/var/lib/homecompute-assistant/context-sync.json")
MAX_CONFIG_BYTES = 16_384
MAX_SELECTION_BYTES = 4_096
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
CALENDAR_ALIASES = ["mads", "family"]
DAYTHREAD_HOST = "daythread.home.arpa"


class SetupRefused(ValueError):
    """The private config selection or target state is not safe to apply."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SetupRefused("configuration contains duplicate fields")
        result[key] = value
    return result


def read_selection(stream: Any) -> list[int]:
    raw = stream.read(MAX_SELECTION_BYTES + 1)
    if len(raw) > MAX_SELECTION_BYTES:
        raise SetupRefused("profile selection exceeds its bound")
    try:
        selection = json.loads(raw, object_pairs_hook=_unique_object,
                               parse_constant=lambda _value: (_ for _ in ()).throw(
                                   SetupRefused("profile selection contains a nonfinite value")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SetupRefused("profile selection must be bounded JSON") from None
    if not isinstance(selection, dict) or set(selection) != {"aula_profile_ids"}:
        raise SetupRefused("profile selection schema differs")
    values = selection["aula_profile_ids"]
    if (not isinstance(values, list) or len(values) > 8
            or any(type(value) is not int or not 1 <= value <= 999_999_999 for value in values)
            or len(set(values)) != len(values)):
        raise SetupRefused("Aula profile selection must contain unique discovered positive IDs")
    return sorted(values)


def build_config(profile_ids: list[int], example_path: Path = EXAMPLE) -> tuple[bytes, str]:
    if (not isinstance(profile_ids, list) or len(profile_ids) > 8
            or any(type(value) is not int or not 1 <= value <= 999_999_999 for value in profile_ids)
            or len(set(profile_ids)) != len(profile_ids)):
        raise SetupRefused("Aula profile selection must contain unique discovered positive IDs")
    profile_ids = sorted(profile_ids)
    raw = example_path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SetupRefused("tracked assistant-context example is invalid") from None
    if (not isinstance(value, dict) or set(value) != {"schema_version", "aula", "calendar", "daythread"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value["aula"] != {"enabled": False}
            or value["calendar"] != {"enabled": True, "aliases": ["family"],
                                     "rolling": {"past_days": 1, "future_days": 7}}
            or value["daythread"] != {"enabled": False, "host": ""}):
        raise SetupRefused("tracked assistant-context example differs from its reviewed baseline")
    value["aula"] = {
        "enabled": bool(profile_ids),
        "profile_ids": profile_ids,
        "range": "this_week",
    }
    value["calendar"] = {
        "enabled": True,
        "aliases": CALENDAR_ALIASES,
        "rolling": {"past_days": 1, "future_days": 7},
    }
    value["daythread"] = {"enabled": True, "host": DAYTHREAD_HOST}
    encoded = canonical(value)
    return encoded, digest(raw)


class SyncConfigBootstrap:
    """Hash-bound private config builder for the fixed source selection."""

    def __init__(self, *, target: Path, example: Path, service_uid: int, service_gid: int,
                 root_uid: int = 0, root_gid: int = 0):
        self.target = target
        self.directory = target.parent
        self.example = example
        self.service_uid = service_uid
        self.service_gid = service_gid
        self.root_uid = root_uid
        self.root_gid = root_gid

    @staticmethod
    def _directory(path: Path, uid: int, gid: int, *, private: bool) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != uid or info.st_gid != gid
                or (mode != 0o700 if private else mode & 0o022 != 0)):
            raise SetupRefused("private config directory ownership or mode is unsafe")
        return True

    def _read_target(self) -> tuple[bytes | None, str]:
        try:
            info = self.target.lstat()
        except FileNotFoundError:
            return None, "absent"
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != self.service_uid or info.st_gid != self.service_gid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > MAX_CONFIG_BYTES):
            raise SetupRefused("private config file ownership, type, or mode is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(self.target, flags)
        try:
            opened = os.fstat(descriptor)
            if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
                raise SetupRefused("private config changed during inspection")
            raw = os.read(descriptor, MAX_CONFIG_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_CONFIG_BYTES:
            raise SetupRefused("private config exceeds its bound")
        return raw, digest(raw)

    def inspect(self, profile_ids: list[int]) -> dict[str, Any]:
        encoded, example_sha256 = build_config(profile_ids, self.example)
        parent = self.directory.parent
        if not self._directory(parent, self.root_uid, self.root_gid, private=False):
            raise SetupRefused("private config parent is absent")
        directory_present = self._directory(self.directory, self.service_uid,
                                            self.service_gid, private=True)
        if not directory_present:
            raw, preimage = None, "absent"
        else:
            raw, preimage = self._read_target()
        if raw is not None and raw != encoded:
            state = "drift"
            try:
                previous = json.loads(raw, object_pairs_hook=_unique_object)
                old_ids = previous["aula"]["profile_ids"]
                previous_canonical, _ = build_config(old_ids, self.example)
                if raw == previous_canonical:
                    state = "selection_change"
            except (ValueError, KeyError, TypeError):
                pass
        elif raw is None:
            state = "absent"
        else:
            state = "matching"
        body = {
            "schema_version": 1,
            "target": str(self.target),
            "state": state,
            "preimage_sha256": preimage,
            "example_sha256": example_sha256,
            "config_sha256": digest(encoded),
            "aula_enabled": bool(profile_ids),
            "aula_profile_count": len(profile_ids),
            "calendar_aliases": CALENDAR_ALIASES,
            "calendar_window": {"past_days": 1, "future_days": 7,
                                "timezone": "Europe/Copenhagen"},
            "daythread_enabled": True,
            "daythread_host": DAYTHREAD_HOST,
        }
        return {**body, "plan_sha256": digest(canonical(body))}

    def plan(self, revision: str, profile_ids: list[int]) -> dict[str, Any]:
        if REVISION_RE.fullmatch(revision) is None:
            raise SetupRefused("full published source SHA required")
        body = self.inspect(profile_ids)
        body.pop("plan_sha256")
        body["revision"] = revision
        return {**body, "plan_sha256": digest(canonical(body))}

    def apply(self, revision: str, profile_ids: list[int], expected_plan_sha256: str) -> dict[str, Any]:
        self._ensure_directory()
        descriptor = os.open(self.directory / "context-sync.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != self.service_uid or info.st_gid != self.service_gid
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise SetupRefused("private source-selection lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._apply_locked(revision, profile_ids, expected_plan_sha256)
        finally:
            os.close(descriptor)

    def _apply_locked(self, revision: str, profile_ids: list[int], expected_plan_sha256: str) -> dict[str, Any]:
        reviewed = self.plan(revision, profile_ids)
        if (not isinstance(expected_plan_sha256, str)
                or SHA256_RE.fullmatch(expected_plan_sha256) is None
                or reviewed["plan_sha256"] != expected_plan_sha256):
            raise SetupRefused("source selection or target changed since the reviewed plan")
        if reviewed["state"] == "drift":
            raise SetupRefused("existing private config differs; preserve and reconcile it")
        if reviewed["state"] == "matching":
            return {"status": "already_configured", "target": str(self.target),
                    "config_sha256": reviewed["config_sha256"]}

        self._ensure_directory()
        encoded, _ = build_config(profile_ids, self.example)
        changed = reviewed["state"] == "selection_change"
        if changed:
            self._replace_selection(encoded, reviewed["preimage_sha256"])
        else:
            self._create(encoded)
        installed = self._read_target()[0]
        if installed != encoded:
            raise SetupRefused("private config failed post-install verification")
        return {"status": "updated" if changed else "created", "target": str(self.target),
                "config_sha256": digest(encoded)}

    def _replace_selection(self, encoded: bytes, expected_preimage: str) -> None:
        previous, signature = self._read_target()
        if previous is None or signature != expected_preimage:
            raise SetupRefused("private config changed before selection update")
        history = self.directory / "selection-history"
        if not self._directory(history, self.service_uid, self.service_gid, private=True):
            history.mkdir(mode=0o700)
            os.chown(history, self.service_uid, self.service_gid)
        backup = history / (expected_preimage + ".json")
        if backup.exists() or backup.is_symlink():
            info = backup.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != self.service_uid or info.st_gid != self.service_gid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_CONFIG_BYTES
                    or backup.read_bytes() != previous):
                raise SetupRefused("private selection before-image differs")
        else:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.fchown(fd, self.service_uid, self.service_gid)
            with os.fdopen(fd, "wb") as stream:
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(history, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        fd, temporary_name = tempfile.mkstemp(prefix=".context-sync-selection-", dir=self.directory)
        temporary = Path(temporary_name)
        try:
            os.fchown(fd, self.service_uid, self.service_gid)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if self._read_target()[1] != expected_preimage:
                raise SetupRefused("private config changed during selection update")
            os.replace(temporary, self.target)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_directory(self) -> None:
        if self._directory(self.directory, self.service_uid, self.service_gid, private=True):
            return
        parent = self.directory.parent
        if not self._directory(parent, self.root_uid, self.root_gid, private=False):
            raise SetupRefused("private config parent is absent")
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            self._directory(self.directory, self.service_uid, self.service_gid, private=True)
            return
        os.chown(self.directory, self.service_uid, self.service_gid)
        os.chmod(self.directory, 0o700)

    def _create(self, raw: bytes) -> None:
        temporary = self.target.with_name(f".{self.target.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        linked = False
        temporary_stat = None
        try:
            os.fchown(descriptor, self.service_uid, self.service_gid)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("private config write made no progress")
                offset += written
            os.fsync(descriptor)
            temporary_stat = os.fstat(descriptor)
        except Exception:
            os.close(descriptor)
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        else:
            os.close(descriptor)
        try:
            os.link(temporary, self.target, follow_symlinks=False)
            linked = True
            os.unlink(temporary)
            directory_fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if linked and temporary_stat is not None:
                try:
                    installed = self.target.lstat()
                    if (installed.st_dev, installed.st_ino) == (temporary_stat.st_dev, temporary_stat.st_ino):
                        self.target.unlink()
                except OSError:
                    pass
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


def verify_published_source(root: Path, revision: str, *, releases_dir: Path,
                            root_uid: int = 0) -> None:
    if REVISION_RE.fullmatch(revision) is None:
        raise SetupRefused("full published source SHA required")
    expected = releases_dir / revision
    try:
        info = root.lstat()
    except OSError:
        raise SetupRefused("published release directory is unavailable") from None
    if (root != expected or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != root_uid or stat.S_IMODE(info.st_mode) & 0o022):
        raise SetupRefused("published release path or ownership is unsafe")

    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
            capture_output=True, check=True)
        return result.stdout

    head = git("rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    if head != revision:
        raise SetupRefused("run this helper from the exact published release checkout")
    subprocess.run(["git", "-c", f"safe.directory={root}", "-C", str(root),
                    "merge-base", "--is-ancestor", revision, "origin/master"],
                   capture_output=True, check=True)
    for relative in ("scripts/setup-assistant-context-sync.py",
                     "config/assistant-context-sync.example.json"):
        try:
            local_info = (root / relative).lstat()
            if not stat.S_ISREG(local_info.st_mode) or stat.S_ISLNK(local_info.st_mode):
                raise SetupRefused("published source file is not a regular file")
            tracked = git("show", f"{revision}:{relative}")
            local = (root / relative).read_bytes()
        except (OSError, subprocess.CalledProcessError, UnicodeError):
            raise SetupRefused("published source files cannot be verified") from None
        if local != tracked:
            raise SetupRefused("published source bytes differ from the requested revision")


def published_revision(revision: str) -> None:
    verify_published_source(ROOT, revision, releases_dir=Path("/srv/homecompute/releases"),
                            root_uid=0)


def production_bootstrap() -> SyncConfigBootstrap:
    if platform.system() != "Linux" or os.uname().nodename != "home-core":
        raise SetupRefused("private config provisioning requires Linux home-core")
    try:
        service = pwd.getpwnam("homecompute-openclaw")
    except KeyError:
        raise SetupRefused("existing OpenClaw source owner is required") from None
    if (os.geteuid(), os.getegid()) != (service.pw_uid, service.pw_gid):
        raise SetupRefused("run as the existing homecompute-openclaw source owner")
    return SyncConfigBootstrap(target=TARGET, example=EXAMPLE,
                               service_uid=service.pw_uid, service_gid=service.pw_gid,
                               root_uid=0, root_gid=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan_parser = sub.add_parser("plan", help="review a bounded private source config without writing it")
    plan_parser.add_argument("--revision", required=True)
    apply_parser = sub.add_parser("apply", help="atomically create the reviewed private source config")
    apply_parser.add_argument("--revision", required=True)
    apply_parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        published_revision(args.revision)
        profile_ids = read_selection(sys.stdin)
        bootstrap = production_bootstrap()
        if args.action == "plan":
            result = bootstrap.plan(args.revision, profile_ids)
        else:
            result = bootstrap.apply(args.revision, profile_ids, args.expected_plan_sha256)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "held", "reason": type(exc).__name__}, separators=(",", ":")),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
