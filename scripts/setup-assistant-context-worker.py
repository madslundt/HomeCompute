#!/usr/bin/env python3
"""Plan/apply/rollback the one isolated no-tool OpenClaw context role in a guest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SOURCE = ROOT / "config/assistant-delegation.json"
DEFAULT_NATIVE = Path("/sandbox/.openclaw/openclaw.json")
DEFAULT_STATE = Path("/sandbox/.openclaw/assistant-context-worker-setup")
DEFAULT_WORKER = Path("/sandbox/.openclaw/homecompute-assistant-context-worker.py")
DEFAULT_WORKSPACE = Path("/sandbox/.openclaw/workspace-assistant-context-worker")


class SetupRefused(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_source(path: Path = CONFIG_SOURCE) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("native_runtime", {}).get("version") != "2026.9.1"):
        raise SetupRefused("reviewed source declaration refused")
    return value


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise SetupRefused("symlink path refused")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SetupRefused("private directory required")


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    private_dir(path.parent)
    if path.is_symlink():
        raise SetupRefused("symlink target refused")
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def candidate(before: dict[str, Any], declaration: dict[str, Any]) -> dict[str, Any]:
    expected_ids = declaration["preserve_agent_ids"]
    role = declaration["native_runtime"]["agent_id"]
    actual_ids = set(before.get("agents", {}).get("entries", {}))
    if actual_ids == set(expected_ids) | {role}:
        if before["agents"]["entries"][role] == declaration["native_agent"]:
            return before
        raise SetupRefused("existing context worker differs from reviewed source")
    if actual_ids != set(expected_ids):
        raise SetupRefused("unknown native agent roster drift")
    entries = before["agents"]["entries"]
    after = json.loads(json.dumps(before))
    after["agents"]["entries"][role] = declaration["native_agent"]
    # Confirm the patch changes only the one role entry. All defaults, global
    # grants, plugin grants, bindings, state and pre-existing roles are retained.
    for key, value in before.items():
        if key != "agents" and after.get(key) != value:
            raise SetupRefused("unexpected top-level candidate mutation")
    if ({k: v for k, v in after["agents"].items() if k != "entries"}
            != {k: v for k, v in before["agents"].items() if k != "entries"}):
        raise SetupRefused("unexpected agent defaults mutation")
    for agent_id, entry in entries.items():
        if after["agents"]["entries"].get(agent_id) != entry:
            raise SetupRefused("existing native agent mutation refused")
    return after


def native_validate(config: Path, runner: Callable[..., Any] = subprocess.run) -> None:
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SSH_AUTH_SOCK"}}
    env["OPENCLAW_CONFIG_PATH"] = str(config)
    try:
        result = runner(["openclaw", "config", "validate"], capture_output=True, text=True,
                        timeout=60, env=env, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupRefused("native config validation unavailable") from None
    if result.returncode != 0:
        raise SetupRefused("native config validation failed")


def install(native_config: Path, state_root: Path, worker_source: Path, workspace_source: Path,
            *, apply: bool = False, expected_config_sha256: str | None = None,
            runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    declaration = read_source()
    if native_config.is_symlink() or not native_config.is_file():
        raise SetupRefused("native config file unavailable")
    before_raw = native_config.read_bytes()
    if len(before_raw) > 4 * 1024 * 1024:
        raise SetupRefused("native config exceeds budget")
    before_hash = digest(before_raw)
    if expected_config_sha256 is not None and expected_config_sha256 != before_hash:
        raise SetupRefused("native config changed since reviewed plan")
    before = json.loads(before_raw)
    after = candidate(before, declaration)
    after_raw = (json.dumps(after, sort_keys=True, indent=2) + "\n").encode()
    worker_target = DEFAULT_WORKER
    workspace_target = DEFAULT_WORKSPACE
    if not worker_source.is_file() or worker_source.is_symlink():
        raise SetupRefused("reviewed worker source unavailable")
    if not workspace_source.is_dir() or workspace_source.is_symlink():
        raise SetupRefused("reviewed worker workspace unavailable")
    worker_bytes = worker_source.read_bytes()
    if len(worker_bytes) > 65536:
        raise SetupRefused("worker source exceeds budget")
    instructions = workspace_source / "AGENTS.md"
    if instructions.is_symlink() or not instructions.is_file():
        raise SetupRefused("worker role policy is missing")
    workspace_bytes = instructions.read_bytes()
    if len(workspace_bytes) > 8192:
        raise SetupRefused("worker role policy exceeds budget")
    expected_workspace = {"AGENTS.md": digest(workspace_bytes)}
    for path in (worker_target, workspace_target / "AGENTS.md"):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
                raise SetupRefused("unexpected existing worker file")
            expected_digest = digest(worker_bytes) if path == worker_target else expected_workspace["AGENTS.md"]
            if digest(path.read_bytes()) != expected_digest:
                raise SetupRefused("worker file drift refused")
    validate_candidate = native_config.parent / (".assistant-context-candidate-" + uuid.uuid4().hex + ".json")
    atomic_write(validate_candidate, after_raw)
    try:
        native_validate(validate_candidate, runner)
    finally:
        validate_candidate.unlink(missing_ok=True)
    summary = {"source_sha256": digest(CONFIG_SOURCE.read_bytes()), "before_config_sha256": before_hash,
               "after_config_sha256": digest(after_raw), "config_changed": before_raw != after_raw,
               "role_id": declaration["native_runtime"]["agent_id"], "existing_roles_preserved": len(before["agents"]["entries"]),
               "worker_sha256": digest(worker_bytes), "workspace_sha256": digest(workspace_bytes),
               "native_schema_validated": True, "applied": False, "restart_required": before_raw != after_raw}
    if not apply:
        return summary
    if native_config.read_bytes() != before_raw:
        raise SetupRefused("native config changed after validation")
    if (worker_target.exists() and digest(worker_target.read_bytes()) != digest(worker_bytes)
            or (workspace_target / "AGENTS.md").exists()
            and digest((workspace_target / "AGENTS.md").read_bytes()) != digest(workspace_bytes)):
        raise SetupRefused("worker files changed after validation")
    private_dir(state_root)
    if before_raw != after_raw:
        backup_id = uuid.uuid4().hex
        backup = state_root / backup_id
        private_dir(backup)
        atomic_write(backup / "openclaw-before.json", before_raw)
        metadata = {"schema_version": 1, "backup_id": backup_id, "before_sha256": before_hash,
                    "after_sha256": digest(after_raw), "source_sha256": summary["source_sha256"],
                    "worker_sha256": summary["worker_sha256"], "workspace_sha256": summary["workspace_sha256"]}
        atomic_write(backup / "receipt.json", (json.dumps(metadata, sort_keys=True) + "\n").encode())
    else:
        backup_id = None
    workspace_existed = workspace_target.exists()
    worker_existed = worker_target.exists()
    instructions_target = workspace_target / "AGENTS.md"
    instructions_existed = instructions_target.exists()
    private_dir(workspace_target)
    try:
        if not worker_existed:
            atomic_write(worker_target, worker_bytes, 0o700)
        if not instructions_existed:
            atomic_write(instructions_target, workspace_bytes, 0o600)
        if before_raw != after_raw:
            atomic_write(native_config, after_raw)
        native_validate(native_config, runner)
    except Exception:
        if before_raw != after_raw:
            atomic_write(native_config, before_raw)
        # A failed validation must not leave an unreferenced worker role behind.
        # Remove only files this invocation created; preserve pre-existing exact
        # copies and any state that was present before apply.
        if not instructions_existed:
            instructions_target.unlink(missing_ok=True)
        if not worker_existed:
            worker_target.unlink(missing_ok=True)
        if not workspace_existed:
            try:
                workspace_target.rmdir()
            except OSError:
                pass
        raise
    summary.update({"applied": True, "backup_id": backup_id,
                    "installed_config_sha256": digest(native_config.read_bytes())})
    return summary


def rollback(native_config: Path, state_root: Path, backup_id: str,
             *, expected_config_sha256: str, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if not re_full_uuidhex(backup_id):
        raise SetupRefused("rollback receipt id invalid")
    if native_config.is_symlink() or not native_config.is_file() or state_root.is_symlink() or not state_root.is_dir():
        raise SetupRefused("rollback target unavailable")
    backup = state_root / backup_id
    meta_path, raw_path = backup / "receipt.json", backup / "openclaw-before.json"
    root_info = state_root.stat()
    backup_info = backup.lstat() if backup.exists() or backup.is_symlink() else None
    if (root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700
            or backup_info is None or not stat.S_ISDIR(backup_info.st_mode)
            or backup_info.st_uid != os.geteuid() or stat.S_IMODE(backup_info.st_mode) != 0o700
            or any(path.is_symlink() or not path.is_file() for path in (meta_path, raw_path))):
        raise SetupRefused("rollback receipt unavailable")
    for path in (meta_path, raw_path):
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise SetupRefused("rollback receipt file refused")
    meta = json.loads(meta_path.read_text())
    current = native_config.read_bytes()
    if (digest(current) != expected_config_sha256 or digest(current) != meta.get("after_sha256")
            or meta.get("schema_version") != 1
            or meta.get("source_sha256") != digest(CONFIG_SOURCE.read_bytes())):
        raise SetupRefused("rollback refused after stale source or config drift")
    old = raw_path.read_bytes()
    before = json.loads(old)
    actual = json.loads(current)
    role = "assistant-context-worker"
    if actual.get("agents", {}).get("entries", {}).get(role) != read_source()["native_agent"]:
        raise SetupRefused("installed context worker no longer matches rollback receipt")
    candidate_config = native_config.parent / (".assistant-context-rollback-" + uuid.uuid4().hex + ".json")
    atomic_write(candidate_config, old)
    try:
        native_validate(candidate_config, runner)
    finally:
        candidate_config.unlink(missing_ok=True)
    atomic_write(native_config, old)
    native_validate(native_config, runner)
    return {"rolled_back": True, "restored_config_sha256": digest(old),
            "preserved_state": True, "role_id": role}


def re_full_uuidhex(value: str) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "rollback"), default="plan")
    parser.add_argument("--config", type=Path, default=DEFAULT_NATIVE)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--worker-source", type=Path, default=ROOT / "scripts/assistant-context-worker.py")
    parser.add_argument("--workspace-source", type=Path, default=ROOT / "deploy/openclaw/assistant-context-worker")
    parser.add_argument("--expected-config-sha256")
    parser.add_argument("--backup-id")
    args = parser.parse_args()
    try:
        if args.action == "rollback":
            if not args.backup_id or not args.expected_config_sha256:
                raise SetupRefused("rollback requires exact backup and installed config hash")
            result = rollback(args.config, args.state_root, args.backup_id,
                              expected_config_sha256=args.expected_config_sha256)
        else:
            result = install(args.config, args.state_root, args.worker_source, args.workspace_source,
                             apply=args.action == "apply", expected_config_sha256=args.expected_config_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reason = str(error) if isinstance(error, SetupRefused) else "setup-input-refused"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
