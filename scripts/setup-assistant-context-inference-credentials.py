#!/usr/bin/env python3
"""Provision the two fixed, narrowly scoped assistant-context LiteLLM keys.

Run as root on home-core from the exact published HomeCompute release. The
default plan is read-only. Apply requires the reviewed plan hash. Key material
stays in process memory or root-only files and is never printed or put in argv.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import stat
import subprocess
import sys
from typing import Any, Callable


REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{20,512}\Z")
LITELLM_CONTAINER = "homecompute-control-plane-litellm-1"
KEYS = {
    "embedding": {
        "path": Path("/etc/homecompute/assistant-context-embedding.json"),
        "owner": "assistant-context-embedding",
        "model": "embedding",
        "purpose": "assistant-context local semantic retrieval",
    },
    "briefing": {
        "path": Path("/etc/homecompute/assistant-context-briefing.json"),
        "owner": "assistant-context-briefing",
        "model": "automation-moe",
        "purpose": "assistant-context bounded local briefing interpretation",
    },
}


class Refused(ValueError):
    """Operator-safe failure without secret material."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def valid_key(value: str) -> bool:
    return KEY_RE.fullmatch(value) is not None


def litellm_request(path: str, payload: dict[str, Any], *, query: bool = False,
                    runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    # The master key remains inside LiteLLM. Payload values are sent on stdin to
    # `docker exec`; generated bearer tokens never appear in command arguments.
    source = '''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
payload = %r
url = 'http://127.0.0.1:4000' + %r
query = %r
if query:
    url += '?' + urllib.parse.urlencode(payload)
request = urllib.request.Request(url, data=None if query else json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
print(json.dumps(json.load(urllib.request.urlopen(request, timeout=30))))
''' % (payload, path, query)
    try:
        result = runner(["docker", "exec", "-i", LITELLM_CONTAINER, "python3", "-"],
                        input=source, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("litellm-key-request-unconfirmed") from None
    if result.returncode:
        raise Refused("litellm-key-request-refused")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise Refused("litellm-key-response-refused") from None
    if not isinstance(value, dict):
        raise Refused("litellm-key-response-refused")
    return value


class InferenceKeySetup:
    """Hash-bound creation of only the two assistant-context key files."""

    def __init__(self, *, key_paths: dict[str, Path] | None = None,
                 root_uid: int = 0, root_gid: int = 0,
                 runner: Callable[..., Any] = subprocess.run):
        self.keys = {
            name: {**spec, "path": (key_paths or {}).get(name, spec["path"])}
            for name, spec in KEYS.items()
        }
        self.root_uid = root_uid
        self.root_gid = root_gid
        self.runner = runner

    def _read(self, name: str) -> tuple[str | None, str]:
        path = self.keys[name]["path"]
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return None, "absent"
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Refused("credential-file-ownership-or-mode-unsafe") from None
            raise
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.root_uid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 1024):
                raise Refused("credential-file-ownership-or-mode-unsafe")
            raw = handle.read(1025)
        if len(raw) > 1024:
            raise Refused("credential-file-ownership-or-mode-unsafe")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            raise Refused("credential-file-format-invalid") from None
        if not text.endswith("\n") or text.count("\n") != 1:
            raise Refused("credential-file-format-invalid")
        key = text[:-1]
        if not valid_key(key):
            raise Refused("credential-file-format-invalid")
        return key, sha256(raw)

    def _pending_path(self, name: str) -> Path:
        path: Path = self.keys[name]["path"]
        return path.with_name(path.name + ".pending")

    def _read_pending(self, name: str) -> tuple[bytes | None, str]:
        path = self._pending_path(name)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return None, "absent"
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Refused("pending-intent-file-ownership-or-mode-unsafe") from None
            raise
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.root_uid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096):
                raise Refused("pending-intent-file-ownership-or-mode-unsafe")
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise Refused("pending-intent-file-ownership-or-mode-unsafe")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Refused("pending-intent-file-format-invalid") from None
        spec = self.keys[name]
        if (not isinstance(value, dict) or set(value) != {"schema_version", "name", "model", "owner", "revision", "intent_id"}
                or value.get("schema_version") != 1 or value.get("name") != name
                or value.get("model") != spec["model"] or value.get("owner") != spec["owner"]
                or not isinstance(value.get("revision"), str) or REVISION_RE.fullmatch(value["revision"]) is None
                or not isinstance(value.get("intent_id"), str) or re.fullmatch(r"[0-9a-f]{32}", value["intent_id"]) is None):
            raise Refused("pending-intent-file-format-invalid")
        return raw, sha256(raw)

    def _write_pending(self, name: str, revision: str) -> str:
        spec = self.keys[name]
        intent_id = secrets.token_hex(16)
        intent = canonical({"schema_version": 1, "name": name, "model": spec["model"],
                            "owner": spec["owner"], "revision": revision,
                            "intent_id": intent_id})
        path = self._pending_path(name)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchown(descriptor, self.root_uid, self.root_gid)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(intent)
                handle.flush()
                os.fsync(handle.fileno())
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            # If any write or durability step is uncertain, preserve the intent
            # when it exists so a retry cannot silently mint another key.
            raise
        return intent_id

    def _resolve_pending(self, name: str, expected_hash: str, resolution: str) -> dict[str, str]:
        path = self._pending_path(name)
        raw, current_hash = self._read_pending(name)
        if raw is None or not HASH_RE.fullmatch(expected_hash) or current_hash != expected_hash:
            raise Refused("pending-intent-changed-since-review")
        key, _signature = self._read(name)
        if resolution == "key-verified":
            if key is None:
                raise Refused("verified-key-file-required-for-resolution")
            pending_value = json.loads(raw)
            self._verify(name, key, expected_intent_id=pending_value["intent_id"])
        elif resolution == "orphan-revoked":
            if key is not None:
                raise Refused("remove-no-key-file-required-for-orphan-resolution")
        else:
            raise Refused("explicit-pending-resolution-required")
        archived = path.with_name(path.name + ".resolved-" + resolution + "-" + current_hash)
        try:
            os.link(path, archived, follow_symlinks=False)
            path.unlink()
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError:
            raise Refused("pending-resolution-receipt-already-exists") from None
        return {"status": "pending_resolved", "resolution": resolution,
                "receipt": str(archived), "receipt_sha256": current_hash}

    def _api(self, path: str, payload: dict[str, Any], *, query: bool = False) -> dict[str, Any]:
        return litellm_request(path, payload, query=query, runner=self.runner)

    def _verify(self, name: str, key: str, *, expected_intent_id: str | None = None) -> None:
        spec = self.keys[name]
        # LiteLLM accepts the SHA-256 token hash here; raw keys in the query
        # string may be captured by proxy access logs.
        key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
        response = self._api("/key/info", {"key": key_hash}, query=True)
        info = response.get("info")
        if (not isinstance(info, dict) or info.get("models") != [spec["model"]]
                or info.get("user_id") != spec["owner"] or info.get("expires") is not None):
            raise Refused("existing-key-scope-mismatch")
        if expected_intent_id is not None:
            metadata = info.get("metadata")
            if (not isinstance(metadata, dict)
                    or metadata.get("setup_intent_id") != expected_intent_id):
                raise Refused("pending-key-intent-does-not-match-key")

    def inspect(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        for name, spec in self.keys.items():
            pending, pending_signature = self._read_pending(name)
            if pending is not None:
                raise Refused("pending-key-intent-requires-operator-reconciliation")
            key, signature = self._read(name)
            if key is None:
                state = "absent"
            else:
                self._verify(name, key)
                state = "valid"
            states[name] = {"state": state, "path": str(spec["path"]),
                            "model": spec["model"], "owner": spec["owner"],
                            "sha256": signature, "pending": "absent",
                            "pending_sha256": pending_signature}
        return states

    def plan(self, revision: str) -> dict[str, Any]:
        if REVISION_RE.fullmatch(revision) is None:
            raise Refused("full-published-source-sha-required")
        states = self.inspect()
        body = {"schema_version": 1, "revision": revision, "keys": states}
        return {**body, "plan_sha256": sha256(canonical(body))}

    def _create(self, name: str, revision: str) -> None:
        spec = self.keys[name]
        path: Path = spec["path"]
        intent_id = self._write_pending(name, revision)
        data = self._api("/key/generate", {
            "models": [spec["model"]], "key_alias": spec["owner"],
            "user_id": spec["owner"], "duration": None,
            "object_permission": {"mcp_servers": ["no-mcp-servers"]},
            "metadata": {"purpose": spec["purpose"], "setup_intent_id": intent_id},
        })
        key = data.get("key")
        if (not isinstance(key, str) or not valid_key(key)
                or data.get("models") != [spec["model"]] or data.get("expires") is not None):
            raise Refused("generated-key-scope-mismatch")
        self._verify(name, key, expected_intent_id=intent_id)
        temporary = path.with_name(f".{path.name}.{intent_id}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchown(descriptor, self.root_uid, self.root_gid)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                os.fchmod(handle.fileno(), 0o600)
                handle.write((key + "\n").encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
                prepared = os.fstat(handle.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                raise Refused("credential-file-appeared-during-apply") from None
            temporary.unlink()
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            installed_info = path.lstat()
            if (not stat.S_ISREG(installed_info.st_mode) or installed_info.st_uid != self.root_uid
                    or stat.S_IMODE(installed_info.st_mode) != 0o600 or installed_info.st_nlink != 1
                    or (installed_info.st_dev, installed_info.st_ino) != (prepared.st_dev, prepared.st_ino)):
                raise Refused("credential-file-postcheck-failed")
            stored_key, _signature = self._read(name)
            if stored_key != key:
                raise Refused("credential-file-postcheck-failed")
            self._verify(name, key, expected_intent_id=intent_id)
        except Exception:
            # Before the atomic link, temporary material is disposable. Once
            # installed, preserve it with the pending fence so an operator can
            # correlate and verify this exact key instead of losing it.
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except OSError:
                pass
        pending = self._pending_path(name)
        pending.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def apply(self, revision: str, expected_plan_sha256: str) -> dict[str, Any]:
        reviewed = self.plan(revision)
        if (not isinstance(expected_plan_sha256, str) or HASH_RE.fullmatch(expected_plan_sha256) is None
                or reviewed["plan_sha256"] != expected_plan_sha256):
            raise Refused("credential-state-changed-since-reviewed-plan")
        created: list[str] = []
        for name, state in reviewed["keys"].items():
            if state["state"] == "absent":
                self._create(name, revision)
                created.append(name)
        final = self.inspect()
        if any(state["state"] != "valid" for state in final.values()):
            raise Refused("credential-install-postcheck-failed")
        return {"status": "created" if created else "already_configured",
                "created": created, "paths": [row["path"] for row in final.values()]}


def published_revision(revision: str, root: Path) -> None:
    if REVISION_RE.fullmatch(revision) is None:
        raise Refused("full-published-source-sha-required")
    expected_root = Path("/srv/homecompute/releases") / revision
    if root != expected_root:
        raise Refused("selected-release-directory-required")
    root_info = root.lstat()
    helper = root / "scripts/setup-assistant-context-inference-credentials.py"
    helper_info = helper.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0 or stat.S_IMODE(root_info.st_mode) & 0o022
            or not stat.S_ISREG(helper_info.st_mode) or helper_info.st_uid != 0
            or stat.S_IMODE(helper_info.st_mode) & 0o022):
        raise Refused("root-owned-read-only-release-required")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != revision:
        raise Refused("exact-published-release-checkout-required")
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    if status:
        raise Refused("clean-published-source-checkout-required")
    tracked = subprocess.run(["git", "-C", str(root), "show", "HEAD:scripts/setup-assistant-context-inference-credentials.py"],
                             capture_output=True, check=True).stdout
    if tracked != helper.read_bytes():
        raise Refused("helper-source-does-not-match-published-commit")
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", revision, "origin/master"],
                   capture_output=True, check=True)


def production_setup() -> InferenceKeySetup:
    if platform.system() != "Linux" or os.geteuid() != 0 or socket.gethostname() != "home-core":
        raise Refused("root-on-linux-home-core-required")
    etc = Path("/etc/homecompute")
    info = etc.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
        raise Refused("root-owned-homecompute-directory-required")
    return InferenceKeySetup()


def acquire_setup_lock() -> int:
    path = Path("/run/lock/homecompute-assistant-context-inference-credentials.lock")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise Refused("credential-lifecycle-lock-unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan", help="inspect the two fixed key paths and scopes")
    plan.add_argument("--revision", required=True)
    apply = sub.add_parser("apply", help="create absent keys after exact plan review")
    apply.add_argument("--revision", required=True)
    apply.add_argument("--expected-plan-sha256", required=True)
    reconcile = sub.add_parser("resolve-pending", help="record an explicit operator reconciliation of one interrupted key creation")
    reconcile.add_argument("--revision", required=True)
    reconcile.add_argument("--name", choices=tuple(KEYS), required=True)
    reconcile.add_argument("--expected-pending-sha256", required=True)
    reconcile.add_argument("--resolution", choices=("key-verified", "orphan-revoked"), required=True)
    args = parser.parse_args(argv)
    try:
        published_revision(args.revision, Path(__file__).resolve().parents[1])
        lock = acquire_setup_lock()
        try:
            setup = production_setup()
            if args.action == "plan":
                result = setup.plan(args.revision)
            elif args.action == "apply":
                result = setup.apply(args.revision, args.expected_plan_sha256)
            else:
                result = setup._resolve_pending(args.name, args.expected_pending_sha256, args.resolution)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "held", "reason": type(exc).__name__}, separators=(",", ":")),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
