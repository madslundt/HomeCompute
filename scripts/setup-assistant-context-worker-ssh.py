#!/usr/bin/env python3
"""Install a forced-command-only key for the isolated OpenClaw context worker.

Run as root on the selected Ubuntu `agents` VM from a published HomeCompute
source SHA. The installer never changes the operator's other SSH keys.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shlex
import stat
import subprocess
import sys
import uuid
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "git@github.com:madslundt/HomeCompute.git"
BASE_REF = "origin/master"
HOSTNAME = "agents"
OPERATOR = "hermes-operator"
AUTHORIZED_KEYS = Path("/home/hermes-operator/.ssh/authorized_keys")
PUBLIC_KEY = Path("/etc/homecompute/assistant-context-worker/id_ed25519.pub")
STATE_ROOT = Path("/var/lib/homecompute-assistant-context-worker-ssh")
TEMPLATE = ROOT / "deploy/openclaw/assistant-context-worker/authorized_keys.template"
SOURCE_PATHS = ("scripts/setup-assistant-context-worker-ssh.py",
                "deploy/openclaw/assistant-context-worker/authorized_keys.template")
MANAGED_COMMENT = "homecompute-assistant-context-worker"
KEY_TYPE = "ssh-ed25519"
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{32}$")
WORKER_COMMAND = (
    "exec /usr/bin/env PATH=/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:"
    "/usr/local/bin:/usr/bin:/bin NEMOCLAW_GATEWAY_PORT=9123 "
    "/home/hermes-operator/.local/bin/nemoclaw agent-openclaw exec -- python3 "
    "/sandbox/.openclaw/homecompute-assistant-context-worker.py --stdio"
)


class Refused(ValueError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: Path, *, maximum: int, allowed_uids: set[int] | None = None) -> bytes:
    if path.is_symlink():
        raise Refused("symlink path refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum
                or info.st_mode & 0o022 or allowed_uids is not None and info.st_uid not in allowed_uids):
            raise Refused("private regular file required")
        raw = os.read(fd, maximum + 1)
    finally:
        os.close(fd)
    if len(raw) > maximum:
        raise Refused("file exceeds size bound")
    return raw


def parse_public_key(raw: bytes) -> tuple[str, str]:
    if len(raw) > 4096 or raw.count(b"\n") > 1:
        raise Refused("one bounded SSH public key required")
    try:
        fields = raw.decode("ascii").strip().split()
    except UnicodeDecodeError:
        raise Refused("ASCII SSH public key required") from None
    if len(fields) < 2 or fields[0] != KEY_TYPE:
        raise Refused("dedicated Ed25519 public key required")
    try:
        blob = base64.b64decode(fields[1].encode("ascii"), validate=True)
    except (ValueError, TypeError):
        raise Refused("SSH public key encoding invalid") from None
    # RFC 4251 string("ssh-ed25519") followed by the 32-byte public key.
    expected = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(blob) != len(expected) + 32 or not blob.startswith(expected):
        raise Refused("SSH key blob is not a canonical Ed25519 key")
    canonical = base64.b64encode(blob).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return canonical, fingerprint


def authorized_key_line(key_blob: str, template: Path = TEMPLATE) -> bytes:
    raw = template.read_bytes()
    if raw.count(b"{{PUBLIC_KEY_BLOB}}") != 1:
        raise Refused("authorized-key template must contain one key placeholder")
    rendered = raw.replace(b"{{PUBLIC_KEY_BLOB}}", key_blob.encode("ascii"))
    lines = [line for line in rendered.splitlines() if line and not line.startswith(b"#")]
    expected = ("restrict,command=\"" + WORKER_COMMAND + "\" " + KEY_TYPE + " " + key_blob +
                " " + MANAGED_COMMENT).encode("ascii")
    if lines != [expected]:
        raise Refused("authorized-key source template differs from the fixed worker command")
    return expected


def _authorized_parts(line: bytes) -> tuple[list[str], str | None, str | None, list[str]] | None:
    try:
        tokens = shlex.split(line.decode("utf-8"), comments=False, posix=True)
    except (UnicodeDecodeError, ValueError):
        return None
    key_index = next((i for i, token in enumerate(tokens)
                      if token.startswith("ssh-") or token.startswith("ecdsa-sha2-")
                      or token.startswith("sk-")), None)
    if key_index is None or key_index + 1 >= len(tokens):
        return None
    return tokens[:key_index], tokens[key_index], tokens[key_index + 1], tokens[key_index + 2:]


def candidate(before: bytes, managed_line: bytes, key_blob: str) -> bytes:
    if len(before) > 1024 * 1024 or b"\0" in before:
        raise Refused("authorized_keys exceeds size or content bound")
    current = before.splitlines()
    matching: list[int] = []
    for index, line in enumerate(current):
        if not line.strip() or line.lstrip().startswith(b"#"):
            continue
        parsed = _authorized_parts(line)
        if parsed is None:
            if MANAGED_COMMENT.encode() in line or key_blob.encode() in line:
                raise Refused("unparseable managed authorized-key line")
            continue
        _options, key_type, encoded, comment = parsed
        tagged = MANAGED_COMMENT in comment
        same_key = key_type == KEY_TYPE and encoded == key_blob
        if tagged or same_key:
            if line != managed_line:
                raise Refused("existing worker key entry drift refused")
            matching.append(index)
    if len(matching) > 1:
        raise Refused("duplicate worker key entries refused")
    if matching:
        return before
    suffix = b"" if not before or before.endswith(b"\n") else b"\n"
    return before + suffix + managed_line + b"\n"


def ensure_host(*, system: str | None = None, euid: int | None = None,
                hostname_file: Path = Path("/etc/hostname"), os_release: Path = Path("/etc/os-release"),
                virt: str | None = None, runner: Callable[..., Any] = subprocess.run) -> pwd.struct_passwd:
    if (system or platform.system()) != "Linux" or (os.geteuid() if euid is None else euid) != 0:
        raise Refused("root on the selected Linux guest is required")
    try:
        hostname = hostname_file.read_text().strip()
        release = os_release.read_text()
        distro = next((line.split("=", 1)[1].strip('"') for line in release.splitlines()
                       if line.startswith("ID=")), "")
    except OSError:
        raise Refused("selected agents VM identity is unavailable") from None
    if hostname != HOSTNAME or distro != "ubuntu":
        raise Refused("selected Ubuntu agents VM required")
    if virt is None:
        try:
            result = runner(["systemd-detect-virt", "--vm"], capture_output=True, text=True,
                            timeout=5, check=False, stdin=subprocess.DEVNULL,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("agents VM virtualization identity unavailable") from None
        virt = result.stdout.strip() if result.returncode == 0 else ""
    if virt not in {"kvm", "qemu"}:
        raise Refused("KVM/QEMU agents VM required")
    try:
        account = pwd.getpwnam(OPERATOR)
    except KeyError:
        raise Refused("pinned OpenClaw operator account unavailable") from None
    if account.pw_uid == 0 or account.pw_gid == 0 or Path(account.pw_dir) != Path("/home/hermes-operator"):
        raise Refused("pinned OpenClaw operator home required")
    return account


def verify_release(revision: str, *, repo: Path = ROOT, runner: Callable[..., Any] = subprocess.run) -> None:
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Refused("published full source SHA required")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    def git(args: list[str], *, check: bool = True):
        try:
            result = runner(["git", "-C", str(repo), *args], capture_output=True, timeout=20,
                            check=False, stdin=subprocess.DEVNULL, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("published source verification unavailable") from None
        if check and result.returncode:
            raise Refused("published source verification failed")
        return result
    try:
        remote = git(["remote", "get-url", "origin"]).stdout.decode().strip()
        commit = git(["rev-parse", "--verify", revision + "^{commit}"]).stdout.decode().strip()
        ancestor = git(["merge-base", "--is-ancestor", revision, BASE_REF], check=False)
    except (AttributeError, UnicodeDecodeError):
        raise Refused("published source verification failed") from None
    if remote != REMOTE or commit != revision or ancestor.returncode:
        raise Refused("source SHA must be published on the pinned origin/master")
    for relative in SOURCE_PATHS:
        source = (repo / relative).read_bytes()
        committed = git(["show", revision + ":" + relative]).stdout
        if source != committed:
            raise Refused("installer or template differs from selected published source")


class WorkerSSHInstaller:
    def __init__(self, *, authorized_keys: Path = AUTHORIZED_KEYS, public_key: Path = PUBLIC_KEY,
                 state_root: Path = STATE_ROOT, repo: Path = ROOT, test_mode: bool = False,
                 account: pwd.struct_passwd | None = None,
                 runner: Callable[..., Any] = subprocess.run):
        if not test_mode and (authorized_keys != AUTHORIZED_KEYS or public_key != PUBLIC_KEY or state_root != STATE_ROOT):
            raise Refused("production SSH paths are fixed")
        self.authorized_keys, self.public_key, self.state_root = map(Path, (authorized_keys, public_key, state_root))
        self.repo, self.test_mode, self.runner = Path(repo), test_mode, runner
        self.account = account

    def _guard(self, revision: str):
        if self.test_mode:
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise Refused("test source SHA invalid")
            if self.account is None:
                self.account = pwd.getpwuid(os.geteuid())
            return self.account
        account = ensure_host(runner=self.runner)
        verify_release(revision, repo=self.repo, runner=self.runner)
        return account

    def _paths(self, account: pwd.struct_passwd, *, create: bool = False):
        ssh_dir = self.authorized_keys.parent
        home = ssh_dir.parent
        if home.is_symlink() or not home.is_dir():
            raise Refused("operator home directory symlink or absence refused")
        home_info = home.stat()
        if (home_info.st_uid != account.pw_uid or home_info.st_mode & 0o022):
            raise Refused("operator home directory custody drift refused")
        if ssh_dir.is_symlink():
            raise Refused("operator SSH directory symlink refused")
        if create and not ssh_dir.exists():
            ssh_dir.mkdir(mode=0o700)
            os.chown(ssh_dir, account.pw_uid, account.pw_gid)
        if not ssh_dir.exists():
            return b"", account.pw_uid, account.pw_gid, 0o600
        if not ssh_dir.is_dir():
            raise Refused("operator SSH directory unavailable")
        info = ssh_dir.stat()
        if (info.st_uid != account.pw_uid or stat.S_IMODE(info.st_mode) != 0o700):
            raise Refused("operator SSH directory ownership/mode drift refused")
        if self.authorized_keys.is_symlink():
            raise Refused("authorized_keys symlink refused")
        if not self.authorized_keys.exists():
            return b"", account.pw_uid, account.pw_gid, 0o600
        info = self.authorized_keys.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, account.pw_uid} or info.st_mode & 0o022):
            raise Refused("authorized_keys custody or mode drift refused")
        if info.st_mode & 0o077 not in (0, 0o040, 0o044):
            raise Refused("authorized_keys permissions must be private or read-only")
        return _read_regular(self.authorized_keys, maximum=1024 * 1024,
                             allowed_uids={0, account.pw_uid}), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)

    def _candidate(self, revision: str, *, create: bool = False):
        account = self._guard(revision)
        for parent in (self.public_key.parent, self.public_key.parent.parent):
            if parent.is_symlink() or not parent.is_dir():
                raise Refused("external public-key directory symlink or absence refused")
            info = parent.stat()
            owner_ok = info.st_uid in ({0, os.geteuid()} if self.test_mode else {0})
            if not owner_ok or info.st_mode & 0o022:
                raise Refused("external public-key directory custody drift refused")
        key_owners = {0, os.geteuid()} if self.test_mode else {0}
        raw_key = _read_regular(self.public_key, maximum=4096, allowed_uids=key_owners)
        key_blob, fingerprint = parse_public_key(raw_key)
        line = authorized_key_line(key_blob)
        before, uid, gid, mode = self._paths(account, create=create)
        after = candidate(before, line, key_blob)
        return account, key_blob, fingerprint, line, before, after, uid, gid, mode

    def plan(self, revision: str) -> dict[str, Any]:
        _, _, fingerprint, _, before, after, _, _, _ = self._candidate(revision)
        return {"schema_version": 1, "source_revision": revision, "public_key_fingerprint": fingerprint,
                "authorized_keys_sha256": sha(before), "candidate_sha256": sha(after),
                "changed": before != after, "applied": False,
                "forced_command": "nemoclaw agent-openclaw exec -- python3 "
                                  "/sandbox/.openclaw/homecompute-assistant-context-worker.py --stdio",
                "restrictions": ["no-agent-forwarding", "no-port-forwarding", "no-pty", "no-x11", "forced-command"]}

    def _private_state(self):
        parents = (self.state_root,) if self.test_mode else (self.state_root, *self.state_root.parents)
        for parent in parents:
            if parent.is_symlink():
                raise Refused("rollback path symlink refused")
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.state_root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Refused("root-private rollback state required")

    @staticmethod
    def _atomic_write(path: Path, raw: bytes, *, uid: int, gid: int, mode: int):
        parent = path.parent
        temporary = parent / ("." + path.name + "." + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            if os.geteuid() == 0:
                os.fchown(fd, uid, gid)
            elif uid != os.geteuid() or gid != os.getegid():
                raise Refused("unprivileged synthetic install cannot change file custody")
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(fd)
        os.replace(temporary, path)
        dirfd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)

    def apply(self, revision: str, expected_sha256: str) -> dict[str, Any]:
        account, _, fingerprint, _, before, after, uid, gid, mode = self._candidate(revision)
        if not KEY_RE.fullmatch(expected_sha256) or sha(before) != expected_sha256:
            raise Refused("authorized_keys changed since reviewed plan")
        if before == after:
            return {"applied": True, "changed": False, "public_key_fingerprint": fingerprint,
                    "authorized_keys_sha256": sha(after)}
        self._private_state()
        backup_id = uuid.uuid4().hex
        backup = self.state_root / backup_id
        backup.mkdir(mode=0o700)
        metadata = {"schema_version": 1, "backup_id": backup_id, "source_revision": revision,
                    "before_sha256": sha(before), "after_sha256": sha(after),
                    "public_key_fingerprint": fingerprint, "uid": uid, "gid": gid, "mode": mode}
        state_info = self.state_root.stat()
        self._atomic_write(backup / "authorized_keys.before", before, uid=state_info.st_uid,
                           gid=state_info.st_gid, mode=0o600)
        self._atomic_write(backup / "receipt.json", (json.dumps(metadata, sort_keys=True) + "\n").encode(),
                           uid=state_info.st_uid, gid=state_info.st_gid, mode=0o600)
        current, _, _, _ = self._paths(account, create=True)
        if current != before:
            raise Refused("authorized_keys changed after plan")
        self._atomic_write(self.authorized_keys, after, uid=uid, gid=gid, mode=mode)
        return {"applied": True, "changed": True, "backup_id": backup_id,
                "public_key_fingerprint": fingerprint, "authorized_keys_sha256": sha(after)}

    def rollback(self, revision: str, backup_id: str, expected_sha256: str) -> dict[str, Any]:
        account = self._guard(revision)
        if not UUID_RE.fullmatch(backup_id) or not KEY_RE.fullmatch(expected_sha256):
            raise Refused("backup ID and installed hash required")
        self._private_state()
        backup = self.state_root / backup_id
        info = backup.lstat() if backup.exists() or backup.is_symlink() else None
        if (backup.is_symlink() or info is None or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise Refused("hash-bound rollback receipt unavailable")
        meta_path, preimage_path = backup / "receipt.json", backup / "authorized_keys.before"
        meta_raw = _read_regular(meta_path, maximum=8192, allowed_uids={os.geteuid()})
        before = _read_regular(preimage_path, maximum=1024 * 1024, allowed_uids={os.geteuid()})
        meta = json.loads(meta_raw)
        current, _, _, _ = self._paths(account)
        if (meta.get("schema_version") != 1 or meta.get("backup_id") != backup_id
                or meta.get("source_revision") != revision or sha(current) != expected_sha256
                or expected_sha256 != meta.get("after_sha256") or sha(before) != meta.get("before_sha256")):
            raise Refused("stale source, receipt or authorized_keys drift refused")
        self._atomic_write(self.authorized_keys, before, uid=meta["uid"], gid=meta["gid"], mode=meta["mode"])
        return {"rolled_back": True, "authorized_keys_sha256": sha(before), "backup_id": backup_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply", "rollback"):
        command = sub.add_parser(action)
        command.add_argument("--revision", required=True, help="published full HomeCompute source SHA")
    sub.choices["apply"].add_argument("--expected-authorized-keys-sha256", required=True)
    sub.choices["rollback"].add_argument("--backup-id", required=True)
    sub.choices["rollback"].add_argument("--expected-installed-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        installer = WorkerSSHInstaller()
        if args.action == "plan":
            result = installer.plan(args.revision)
        elif args.action == "apply":
            result = installer.apply(args.revision, args.expected_authorized_keys_sha256)
        else:
            result = installer.rollback(args.revision, args.backup_id, args.expected_installed_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, Refused) else "setup-input-refused"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
