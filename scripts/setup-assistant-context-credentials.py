#!/usr/bin/env python3
"""Plan and provision the fixed synthetic assistant-context credentials."""
from __future__ import annotations

import argparse
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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{40,256}\Z")
SOURCE_NAMES = ["home-assistant", "daythread", "aula", "calendar"]
ROUTINE_NAMES = ["morning-overview", "tomorrow-preparation"]
OWNER_ORIGIN_ID = "owner-mattermost-context"


class SetupRefused(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def token() -> str:
    return secrets.token_urlsafe(48)


def client(name: str, value: str, role: str, *, routines: list[str] | None = None,
           origin_id: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "token": value,
        "role": role,
        "principal_scope": "owner",
        "data_domain": "personal",
        "visibility": "private",
        "sources": list(SOURCE_NAMES),
    }
    if routines is not None:
        row["routines"] = list(routines)
    if origin_id is not None:
        row["origin_id"] = origin_id
    return row


def expected_config(values: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "clients": [
            client("policy-health", values["policy-health"], "operator"),
            client("hermes-owner", values["hermes-owner"], "assistant", origin_id=OWNER_ORIGIN_ID),
            client("owner-source-sync", values["owner-source-sync"], "collector"),
            client("owner-briefing-workflows", values["owner-briefing-workflows"], "routine", routines=ROUTINE_NAMES),
        ],
    }


def validate_config(raw: bytes) -> dict[str, str]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SetupRefused("client declaration contains duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _value: (_ for _ in ()).throw(
            SetupRefused("client declaration contains nonfinite values")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupRefused("client declaration is not valid JSON") from exc
    if (not isinstance(value, dict) or set(value) != {"schema_version", "clients"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["clients"], list) or len(value["clients"]) != 4):
        raise SetupRefused("client declaration schema or client count differs")
    names = ["policy-health", "hermes-owner", "owner-source-sync", "owner-briefing-workflows"]
    roles = ["operator", "assistant", "collector", "routine"]
    result: dict[str, str] = {}
    for index, (row, name, role) in enumerate(zip(value["clients"], names, roles, strict=True)):
        expected_keys = {"name", "token", "role", "principal_scope", "data_domain", "visibility", "sources"}
        if index == 1:
            expected_keys.add("origin_id")
        if index == 3:
            expected_keys.add("routines")
        if (not isinstance(row, dict) or set(row) != expected_keys
                or row.get("name") != name or row.get("role") != role
                or row.get("principal_scope") != "owner" or row.get("data_domain") != "personal"
                or row.get("visibility") != "private" or row.get("sources") != SOURCE_NAMES
                or (index == 1 and row.get("origin_id") != OWNER_ORIGIN_ID)
                or (index == 3 and row.get("routines") != ROUTINE_NAMES)):
            raise SetupRefused("client declaration contains unknown or inconsistent settings")
        value_token = row.get("token")
        if not isinstance(value_token, str) or TOKEN_RE.fullmatch(value_token) is None:
            raise SetupRefused("client declaration contains an invalid credential")
        result[name] = value_token
    if len(set(result.values())) != 4:
        raise SetupRefused("client credentials must be independent")
    return result


class CredentialBootstrap:
    """Narrow fixed-path bootstrap with a hash-bound plan and atomic file creation."""

    def __init__(self, *, clients_path: Path, owner_token_path: Path, collector_token_path: Path,
                 openclaw_dir: Path, assistant_dir: Path, etc_dir: Path,
                 root_uid: int, root_gid: int, service_uid: int, service_gid: int):
        self.clients_path = clients_path
        self.owner_token_path = owner_token_path
        self.collector_token_path = collector_token_path
        self.openclaw_dir = openclaw_dir
        self.assistant_dir = assistant_dir
        self.etc_dir = etc_dir
        self.root_uid = root_uid
        self.root_gid = root_gid
        self.service_uid = service_uid
        self.service_gid = service_gid

    @staticmethod
    def _safe_dir(path: Path, uid: int, *, missing_ok: bool = False, private: bool = False) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise SetupRefused("required credential directory is absent")
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != uid
                or mode & 0o022 or private and mode & 0o077):
            raise SetupRefused("credential directory ownership or mode is unsafe")
        return True

    def _file(self, path: Path, uid: int) -> tuple[bytes | None, str]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None, "absent"
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 16384):
            raise SetupRefused("credential file ownership, type, or mode is unsafe")
        raw = path.read_bytes()
        return raw, digest(raw)

    def inspect(self) -> dict[str, Any]:
        self._safe_dir(self.etc_dir, self.root_uid)
        self._safe_dir(self.openclaw_dir, self.service_uid, private=True)
        self._safe_dir(self.assistant_dir, self.service_uid, missing_ok=True, private=True)
        config_raw, config_sig = self._file(self.clients_path, self.root_uid)
        owner_raw, owner_sig = self._file(self.owner_token_path, self.service_uid)
        collector_raw, collector_sig = self._file(self.collector_token_path, self.service_uid)
        files = [config_raw, owner_raw, collector_raw]
        state = "absent" if all(raw is None for raw in files) else "complete" if all(raw is not None for raw in files) else "partial"
        signatures = {"clients": config_sig, "owner_token": owner_sig, "collector_token": collector_sig}
        if state == "partial":
            raise SetupRefused("partial credential installation; preserve files and reconcile manually")
        if state == "complete":
            values = validate_config(config_raw or b"")
            owner = (owner_raw or b"").decode("ascii", errors="strict").strip()
            collector = (collector_raw or b"").decode("ascii", errors="strict").strip()
            if (owner != values["hermes-owner"] or collector != values["owner-source-sync"]
                    or TOKEN_RE.fullmatch(owner) is None or TOKEN_RE.fullmatch(collector) is None):
                raise SetupRefused("credential files do not match the client declaration")
            if (owner_raw != (owner + "\n").encode() or collector_raw != (collector + "\n").encode()):
                raise SetupRefused("credential token files contain unexpected formatting")
        return {"state": state, "signatures": signatures}

    def plan(self, revision: str) -> dict[str, Any]:
        if REVISION_RE.fullmatch(revision) is None:
            raise SetupRefused("full published source SHA required")
        inspected = self.inspect()
        body = {
            "schema_version": 1,
            "revision": revision,
            "state": inspected["state"],
            "paths": [str(self.clients_path), str(self.owner_token_path), str(self.collector_token_path)],
            "preimage_sha256": inspected["signatures"],
        }
        return {**body, "plan_sha256": digest(canonical(body))}

    def apply(self, revision: str, expected_plan_sha256: str) -> dict[str, Any]:
        reviewed = self.plan(revision)
        if (not isinstance(expected_plan_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256) is None
                or reviewed["plan_sha256"] != expected_plan_sha256):
            raise SetupRefused("credential files changed since the reviewed plan")
        if reviewed["state"] == "complete":
            return {"status": "already_configured", "paths": reviewed["paths"]}

        values = {
            "policy-health": token(),
            "hermes-owner": token(),
            "owner-source-sync": token(),
            "owner-briefing-workflows": token(),
        }
        config_raw = canonical(expected_config(values))
        owner_raw = (values["hermes-owner"] + "\n").encode()
        collector_raw = (values["owner-source-sync"] + "\n").encode()
        self._safe_dir(self.etc_dir, self.root_uid)
        self._safe_dir(self.openclaw_dir, self.service_uid, private=True)
        self._ensure_assistant_dir()
        created: list[Path] = []
        try:
            self._create(self.owner_token_path, owner_raw, self.service_uid, self.service_gid)
            created.append(self.owner_token_path)
            self._create(self.collector_token_path, collector_raw, self.service_uid, self.service_gid)
            created.append(self.collector_token_path)
            self._create(self.clients_path, config_raw, self.root_uid, self.root_gid)
            created.append(self.clients_path)
        except Exception:
            for path in reversed(created):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        result = self.inspect()
        if result["state"] != "complete":
            raise SetupRefused("credential files failed post-install verification")
        return {"status": "created", "paths": [str(self.clients_path), str(self.owner_token_path), str(self.collector_token_path)]}

    def _ensure_assistant_dir(self) -> None:
        if self._safe_dir(self.assistant_dir, self.service_uid, missing_ok=True):
            return
        parent = self.assistant_dir.parent
        self._safe_dir(parent, self.root_uid)
        try:
            self.assistant_dir.mkdir(mode=0o700)
        except FileExistsError:
            self._safe_dir(self.assistant_dir, self.service_uid, private=True)
            return
        os.chown(self.assistant_dir, self.service_uid, self.service_gid)
        os.chmod(self.assistant_dir, 0o700)

    @staticmethod
    def _create(path: Path, raw: bytes, uid: int, gid: int) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        linked = False
        temporary_stat = None
        try:
            os.fchown(descriptor, uid, gid)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
            temporary_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path, follow_symlinks=False)
            linked = True
            os.unlink(temporary)
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if linked and temporary_stat is not None:
                try:
                    installed = path.lstat()
                    if (installed.st_dev, installed.st_ino) == (temporary_stat.st_dev, temporary_stat.st_ino):
                        path.unlink()
                except OSError:
                    pass
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


def published_revision(revision: str) -> None:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != revision:
        raise SetupRefused("run this helper from the exact published release checkout")
    subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", revision, "origin/master"],
                   capture_output=True, check=True)


def production_bootstrap() -> CredentialBootstrap:
    if platform.system() != "Linux" or os.geteuid() != 0 or os.uname().nodename != "home-core":
        raise SetupRefused("credential bootstrap requires root on Linux home-core")
    try:
        service = pwd.getpwnam("homecompute-openclaw")
    except KeyError as exc:
        raise SetupRefused("existing OpenClaw service identity is required") from exc
    return CredentialBootstrap(
        clients_path=Path("/etc/homecompute/assistant-context-clients.json"),
        owner_token_path=Path("/var/lib/homecompute-openclaw/config/assistant-context-token"),
        collector_token_path=Path("/var/lib/homecompute-assistant/context-ingest-token"),
        openclaw_dir=Path("/var/lib/homecompute-openclaw/config"),
        assistant_dir=Path("/var/lib/homecompute-assistant"),
        etc_dir=Path("/etc/homecompute"),
        root_uid=0, root_gid=0, service_uid=service.pw_uid, service_gid=service.pw_gid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan", help="inspect fixed credential paths without creating secrets")
    plan.add_argument("--revision", required=True)
    apply = sub.add_parser("apply", help="create independent credentials after exact plan review")
    apply.add_argument("--revision", required=True)
    apply.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        published_revision(args.revision)
        installer = production_bootstrap()
        if args.action == "plan":
            result = installer.plan(args.revision)
        else:
            result = installer.apply(args.revision, args.expected_plan_sha256)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "held", "reason": type(exc).__name__}, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
