#!/usr/bin/env python3
"""Exact, state-preserving native Error recovery; never replay agent turns.

Only a verified backup permits deletion. A mutation-stage failure durably
blocks another automatic transaction. All command output stays private.
"""
from __future__ import annotations

from contextlib import closing
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any
import uuid

NAME = "agent-openclaw"
GATEWAY = "nemoclaw-9123"
IMAGE = "ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a"
VOLUME = "/var/lib/docker/volumes/nemoclaw-openclaw-state-v1-agent-openclaw/_data"
MEMORY = 8 * 1024 ** 3
TOOLS = {"session_status", "memory_search", "memory_get", "write"}
BLOCKED_TOOLS = TOOLS - {"session_status"}
DENIES = {"homecompute_task_submit", "homecompute_task_status", "homecompute_task_cancel",
          "group:runtime", "group:ui", "group:nodes", "group:automation", "group:web",
          "group:messaging", "sessions_spawn", "sessions_send", "subagents", "github_publish"}
SECTIONS = {"agents", "tools", "memory", "browser", "hooks", "cron", "discovery",
            "commands", "logging", "acp", "update", "telemetry"}


class RecoveryRefused(Exception):
    """Fixed diagnostic codes only; no raw config/CLI output."""


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_safe_config(config: dict[str, Any]) -> None:
    tools = config.get("tools", {})
    allow, deny = set(tools.get("allow", [])), set(tools.get("deny", []))
    if "session_status" not in allow or not allow <= TOOLS or not BLOCKED_TOOLS | DENIES <= deny or "session_status" in deny:
        raise RecoveryRefused("tool-quarantine-required")
    if tools.get("alsoAllow"):
        raise RecoveryRefused("extra-tools-refused")
    agents = config.get("agents", {}).get("entries", {})
    if set(agents) != {"main"}:
        raise RecoveryRefused("agent-roster-refused")
    agent_tools = agents["main"].get("tools", {})
    if ("session_status" not in set(agent_tools.get("allow", [])) or
            not set(agent_tools.get("allow", [])) <= TOOLS or
            "session_status" in set(agent_tools.get("deny", [])) or
            not DENIES <= set(agent_tools.get("deny", []))):
        raise RecoveryRefused("agent-tools-refused")
    for section in ("hooks", "cron", "browser"):
        if config.get(section, {}).get("enabled") is not False:
            raise RecoveryRefused("optional-runtime-must-be-disabled")
    plugins = config.get("plugins", {})
    if not set(plugins.get("allow", [])) <= {"nemoclaw", "memory-core", "homecompute-broker"}:
        raise RecoveryRefused("plugin-allowlist-refused")
    for name, entry in plugins.get("entries", {}).items():
        if name not in {"nemoclaw", "memory-core"} and entry.get("enabled") is not False:
            raise RecoveryRefused("plugin-enablement-refused")
    provider = config.get("models", {}).get("providers", {})
    if (set(provider) != {"inference"} or provider["inference"].get("apiKey") != "unused" or
            provider["inference"].get("baseUrl") != "https://inference.local/v1"):
        raise RecoveryRefused("managed-inference-refused")


def merge_managed_config(old: dict[str, Any], native: dict[str, Any]) -> dict[str, Any]:
    validate_safe_config(old)
    merged = copy.deepcopy(native)
    for section in SECTIONS:
        if section in old:
            merged[section] = copy.deepcopy(old[section])
    for key in ("reload", "terminal", "cliAgents", "nodes"):
        if key in old.get("gateway", {}):
            merged.setdefault("gateway", {})[key] = copy.deepcopy(old["gateway"][key])
    # Keep the replacement's native install/proxy/auth/model records. Restore
    # only the disabled known dependency-free broker and native memory slot.
    plugins = merged.setdefault("plugins", {})
    plugins["allow"] = ["nemoclaw", "memory-core", "homecompute-broker"]
    plugins["slots"] = {"memory": "memory-core"}
    entries = plugins.setdefault("entries", {})
    for name in ("memory-core", "homecompute-broker"):
        if name in old.get("plugins", {}).get("entries", {}):
            entries[name] = copy.deepcopy(old["plugins"]["entries"][name])
    entries.setdefault("homecompute-broker", {})["enabled"] = False
    if "load" in old.get("plugins", {}):
        plugins["load"] = copy.deepcopy(old["plugins"]["load"])
    for key in ("models", "proxy"):
        if merged.get(key) != native.get(key):
            raise RecoveryRefused("native-ownership-drift")
    if merged.get("gateway", {}).get("auth") != native.get("gateway", {}).get("auth"):
        raise RecoveryRefused("native-auth-drift")
    validate_safe_config(merged)
    return merged


def verify_backup(directory: Path) -> dict[str, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise RecoveryRefused("backup-missing")
    for name in ("openclaw.json", "workspace", "agents"):
        path = directory / name
        if not path.exists() or path.is_symlink():
            raise RecoveryRefused("backup-required-state-missing")
    validate_safe_config(json.loads((directory / "openclaw.json").read_bytes()))
    broker = directory / "extensions/homecompute-broker"
    if broker.exists():
        validate_broker_links(broker)
    # Complete backup validation includes machine metadata DBs even though
    # only the qualified agent/workspace subset is restored automatically.
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            header = handle.read(16)
        if header == b"SQLite format 3\x00":
            try:
                with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
                    db.execute("PRAGMA query_only=ON")
                    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RecoveryRefused("backup-sqlite-integrity-failed")
            except sqlite3.Error:
                raise RecoveryRefused("backup-sqlite-integrity-failed") from None
    hashes: dict[str, str] = {}
    for root in (directory / "workspace", directory / "agents"):
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RecoveryRefused("restore-symlink-refused")
            if path.is_file():
                relative = str(path.relative_to(directory))
                with path.open("rb") as handle:
                    hashes[relative] = hashlib.file_digest(handle, "sha256").hexdigest()
    if not any(key.startswith("agents/") for key in hashes):
        raise RecoveryRefused("conversation-backup-empty")
    return hashes


def validate_broker_links(broker: Path) -> None:
    if broker.is_symlink():
        raise RecoveryRefused("broker-symlink-refused")
    for path in broker.rglob("*"):
        if path.is_symlink() and (path.relative_to(broker).as_posix() != "node_modules/openclaw" or
                                 str(path.readlink()) != "/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw"):
            raise RecoveryRefused("broker-symlink-refused")


def conversation_counts(directory: Path) -> dict[str, int]:
    path = directory / "agents/main/agent/openclaw-agent.sqlite"
    counts = {}
    if path.is_file():
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("session_nodes", "session_windows", "transcript_events", "memory_chunks"):
                if table in tables:
                    counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts


def logical_conversations(directory: Path) -> dict[str, Any]:
    path = directory / "agents/main/agent/openclaw-agent.sqlite"
    result: dict[str, Any] = {"counts": conversation_counts(directory)}
    if path.is_file():
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
            for table in ("session_nodes", "session_windows", "transcript_events"):
                if table not in result["counts"]:
                    continue
                columns = db.execute(f"PRAGMA table_info({table})").fetchall()
                positions = [index for index, column in enumerate(columns) if column[5]]
                if table != "transcript_events" and not positions:
                    raise RecoveryRefused("canonical-session-primary-key-missing")
                rows = db.execute(f"SELECT * FROM {table}").fetchall()
                # All immutable transcript fields; only stable session IDs for
                # session tables whose boot/lease metadata can legitimately move.
                if table != "transcript_events":
                    rows = [tuple(row[index] for index in positions) for row in rows]
                normalized = sorted(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                                default=lambda value: {"bytes": value.hex()}) for row in rows)
                result[table + "Digest"] = hashlib.sha256("\n".join(normalized).encode()).hexdigest()
    return result


class Transaction:
    def __init__(self, context: Any) -> None:
        self.ctx = context
        self.path = context.state / "error-recovery-transaction.json"
        self.record: dict[str, Any] = {}

    def preflight(self) -> None:
        if self.path.exists():
            previous = json.loads(self.path.read_bytes())
            if previous.get("status") not in ("complete", "refused"):
                raise RecoveryRefused("previous-transaction-needs-operator")
            if previous.get("status") == "complete" and time.time() - previous.get("startedAt", 0) < 3600:
                raise RecoveryRefused("replacement-hourly-budget-exhausted")
        self.ctx.check_registry()
        if self.ctx.observe() != "Error":
            raise RecoveryRefused("exact-error-phase-required")
        self.inspect(old=True)

    def command(self, argv: list[str], label: str, **options: Any) -> bytes:
        code, output = self.ctx.run(argv, label, **options)
        if code:
            raise RecoveryRefused(f"command-failed:{label}")
        return output

    def inspect(self, *, old: bool = False) -> dict[str, Any]:
        fields = '{"status":{{json .State.Status}},"image":{{json .Config.Image}},"memory":{{.HostConfig.Memory}},"swap":{{.HostConfig.MemorySwap}},"gpu":{{json .HostConfig.DeviceRequests}},"mounts":{{json .Mounts}}}'
        data = json.loads(self.command(["/usr/bin/docker", "inspect", "--format", fields,
                                       self.ctx.container], "transaction-identity"))
        if (data["image"] not in (IMAGE, "sha256:" + IMAGE.rsplit("sha256:", 1)[1]) or
                data["memory"] != MEMORY or data["swap"] != MEMORY or data["gpu"]):
            raise RecoveryRefused("container-image-memory-gpu-mismatch")
        mount = [item for item in data["mounts"] if item.get("Destination") == "/sandbox/.openclaw"]
        if len(mount) != 1 or mount[0].get("Type") != "volume" or mount[0].get("Source") != VOLUME:
            raise RecoveryRefused("exact-state-volume-required")
        if old and data["status"] not in ("exited", "dead"):
            raise RecoveryRefused("active-old-container-refused")
        return data

    def stage(self, stage: str, status: str = "active") -> None:
        self.record.setdefault("history", []).append({"stage": stage, "at": time.time()})
        self.record.update(stage=stage, status=status)
        atomic_json(self.path, self.record)

    def restore_files(self, backup: Path, candidate: Path) -> None:
        phase = self.ctx.observe()
        if phase == "Ready":
            self.command([self.ctx.cli, NAME, "stop"], "replacement-stop", timeout=180)
        elif phase != "Stopped":
            raise RecoveryRefused("replacement-must-be-ready-or-stopped")
        self.inspect()
        self.volume_command('import pathlib,os;p=pathlib.Path("/state/extensions");p.mkdir(exist_ok=True);os.chown(p,998,998)',
                            "prepare-extensions-directory")
        for name in ("workspace", "agents"):
            self.command(["/usr/bin/docker", "cp", str(backup / name),
                          f"{self.ctx.container}:/sandbox/.openclaw/"], f"restore-{name}", timeout=180)
        broker = backup / "extensions/homecompute-broker"
        if broker.exists():
            validate_broker_links(broker)
            self.command(["/usr/bin/docker", "cp", str(broker),
                          f"{self.ctx.container}:/sandbox/.openclaw/extensions/"], "restore-disabled-broker")
        self.command(["/usr/bin/docker", "cp", str(candidate),
                      f"{self.ctx.container}:/sandbox/.openclaw/openclaw.json"], "restore-config")
        # The Docker API copies operator files as root even when tar UID was
        # selected. Repair only this exact qualified state volume, offline.
        repair = '''import os,pathlib,stat,json
root=pathlib.Path("/state")
for name in ("workspace","agents","extensions/homecompute-broker"):
 p=root/name
 if not p.exists():continue
 for q in [p,*p.rglob("*")]:
  if q.is_symlink() and (q != root/"extensions/homecompute-broker/node_modules/openclaw" or str(q.readlink())!="/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw"):raise SystemExit("symlink-refused")
  os.chown(q,998,998,follow_symlinks=False)
p=root/"openclaw.json"
if p.is_symlink() or not p.is_file():raise SystemExit("config-refused")
os.chown(p,998,998,follow_symlinks=False);os.chmod(p,0o600)
s=p.stat()
assert (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==(998,998,0o600)
print("config-owner-mode-verified")'''
        self.volume_command(repair, "repair-exact-state-owner")

    def volume_command(self, code: str, label: str) -> None:
        self.command(["/usr/bin/docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                      "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                      "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
                      "--security-opt", "no-new-privileges", "--entrypoint", "/usr/bin/python3",
                      "--mount", f"type=bind,src={VOLUME},dst=/state", IMAGE, "-c", code],
                     label, timeout=180)

    def execute(self) -> tuple[str, str]:
        self.preflight()
        backup_root = self.ctx.state / f"error-backup-{int(time.time())}-{self.ctx.uuid}"
        backup_root.mkdir(mode=0o700)
        self.command(["/usr/bin/docker", "cp", f"{self.ctx.container}:/sandbox/.openclaw",
                      str(backup_root)], "capture-complete-state", timeout=300)
        backup = backup_root / ".openclaw"
        hashes = verify_backup(backup)
        atomic_json(backup_root / "conversation-hashes.json", hashes)
        old = json.loads((backup / "openclaw.json").read_bytes())
        conversations = logical_conversations(backup)
        self.record = {"schemaVersion": 1, "startedAt": time.time(), "oldId": self.ctx.uuid,
                       "oldGeneration": self.ctx.generation, "backup": str(backup_root),
                       "conversationCounts": conversation_counts(backup)}
        self.stage("backup-verified")
        mutated = False
        original = self.ctx.uuid, self.ctx.generation, self.ctx.container
        try:
            # Repeat identity immediately before committing the native delete.
            self.ctx.check_registry()
            self.inspect(old=True)
            if self.ctx.observe() != "Error":
                raise RecoveryRefused("error-phase-changed-before-delete")
            self.stage("native-destroy-starting")
            mutated = True
            self.command([self.ctx.cli, NAME, "destroy", "--yes", "--no-cleanup-gateway"],
                         "native-destroy", timeout=240)
            self.stage("native-onboard-starting")
            self.command([self.ctx.cli, "onboard", "--non-interactive",
                          "--yes-i-accept-third-party-software", "--agents",
                          "/etc/homecompute-openclaw/openclaw-nemoclaw-agents.json"],
                         "native-replacement-onboard", onboarding=True, timeout=900)
            output = self.command([self.ctx.openshell, "sandbox", "get", "-g", GATEWAY,
                                   NAME, "-o", "json"], "new-sandbox-metadata")
            metadata = json.loads(output)
            new_id = str(uuid.UUID(metadata["id"]))
            if metadata["name"] != NAME or metadata["phase"] != "Ready" or new_id == self.ctx.uuid:
                raise RecoveryRefused("replacement-identity-refused")
            registry = json.loads(self.ctx.registry.read_bytes())["sandboxes"][NAME]
            generation = str(uuid.UUID(registry["lifecycleGeneration"]))
            self.ctx.uuid, self.ctx.generation = new_id, generation
            self.ctx.container = f"openshell-default--{NAME}-{new_id}"
            self.ctx.check_registry()
            self.record.update(newId=new_id, newGeneration=generation)
            self.stage("replacement-created")
            # Prevent any extra swap allowance in the newly created container.
            self.command(["/usr/bin/docker", "update", "--memory", str(MEMORY),
                          "--memory-swap", str(MEMORY), self.ctx.container], "cap-replacement-memory")
            self.inspect()
            native_path = backup_root / "replacement-native.json"
            self.command(["/usr/bin/docker", "cp", f"{self.ctx.container}:/sandbox/.openclaw/openclaw.json",
                          str(native_path)], "capture-new-native-config")
            native = json.loads(native_path.read_bytes())
            candidate = backup_root / "restored-config.json"
            atomic_json(candidate, merge_managed_config(old, native))
            return self.complete_restore(backup_root, backup, candidate, hashes, conversations)
        except Exception as exc:
            self.record["failedStage"] = self.record.get("stage")
            self.record["failureCode"] = str(exc) if isinstance(exc, RecoveryRefused) else "private-exception"
            self.ctx.uuid, self.ctx.generation, self.ctx.container = original
            self.stage("failed-after-mutation" if mutated else "refused-before-mutation",
                       "failed" if mutated else "refused")
            raise RecoveryRefused("transaction-needs-operator" if mutated else "transaction-refused") from None


    def complete_restore(self, backup_root: Path, backup: Path, candidate: Path,
                         hashes: dict[str, str], conversations: dict[str, Any]) -> tuple[str, str]:
        self.stage("offline-restore-starting")
        self.restore_files(backup, candidate)
        verify_root = backup_root / "verification-offline"
        verify_root.mkdir(mode=0o700)
        self.command(["/usr/bin/docker", "cp", f"{self.ctx.container}:/sandbox/.openclaw",
                      str(verify_root)], "capture-offline-verification", timeout=300)
        restored = verify_backup(verify_root / ".openclaw")
        if any(restored.get(key) != value for key, value in hashes.items()):
            raise RecoveryRefused("offline-conversation-or-workspace-hash-drift")
        self.stage("offline-data-verified")
        self.stage("native-start-starting")
        self.command([self.ctx.cli, NAME, "start"], "native-restored-start", timeout=420)
        self.ctx.check_registry()
        self.inspect()
        if self.ctx.observe() != "Ready" or not self.ctx.ready():
            raise RecoveryRefused("restored-runtime-not-ready")
        # Physical SQLite bytes can change for boot/lease bookkeeping;
        # immutable transcripts and stable session identities must not.
        verify_root = backup_root / "verification-ready"
        verify_root.mkdir(mode=0o700)
        self.command(["/usr/bin/docker", "cp", f"{self.ctx.container}:/sandbox/.openclaw",
                      str(verify_root)], "capture-restored-verification", timeout=300)
        restored = verify_backup(verify_root / ".openclaw")
        if any(restored.get(key) != value for key, value in hashes.items() if key.startswith("workspace/")):
            raise RecoveryRefused("workspace-hash-drift")
        if logical_conversations(verify_root / ".openclaw") != conversations:
            raise RecoveryRefused("immutable-conversation-or-session-drift")
        self.stage("canonical-cli-scope-qualification")
        spec = importlib.util.spec_from_file_location("canonical_cli_pairing", Path(__file__).with_name("approve-native-openclaw-cli.py"))
        pairing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pairing)
        try:
            pairing.ensure_cli(self.ctx)
        except pairing.PairingRefused as exc:
            raise RecoveryRefused(str(exc)) from None
        self.stage("verified-before-identity-commit")
        identity = Path("/etc/homecompute-openclaw/supervisor-identity.env")
        temporary = identity.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(f"OPENCLAW_EXPECTED_SANDBOX_ID={self.ctx.uuid}\nOPENCLAW_EXPECTED_GENERATION={self.ctx.generation}\n")
            handle.flush(); os.fsync(handle.fileno())
        temporary.replace(identity)
        fsync_directory(identity.parent)
        self.stage("complete", "complete")
        return self.ctx.uuid, self.ctx.generation

    def resume(self) -> tuple[str, str]:
        """Operator-only continuation; automatic failed-transaction retries stop."""
        self.record = json.loads(self.path.read_bytes())
        if self.record.get("status") != "failed" or not self.record.get("newId"):
            raise RecoveryRefused("failed-replacement-journal-required")
        backup_root = Path(self.record["backup"])
        if backup_root.is_symlink() or backup_root.resolve().parent != self.ctx.state.resolve():
            raise RecoveryRefused("resume-backup-path-refused")
        backup = backup_root / ".openclaw"
        hashes = verify_backup(backup)
        conversations = logical_conversations(backup)
        native = json.loads((backup_root / "replacement-native.json").read_bytes())
        candidate = backup_root / "restored-config.json"
        if json.loads(candidate.read_bytes()) != merge_managed_config(json.loads((backup / "openclaw.json").read_bytes()), native):
            raise RecoveryRefused("resume-candidate-drift")
        self.ctx.uuid = str(uuid.UUID(self.record["newId"]))
        self.ctx.generation = str(uuid.UUID(self.record["newGeneration"]))
        self.ctx.container = f"openshell-default--{NAME}-{self.ctx.uuid}"
        self.ctx.check_registry()
        self.inspect(old=True)
        if self.ctx.observe() != "Stopped":
            raise RecoveryRefused("resume-stopped-identity-required")
        current = backup_root / "resume-native-current.json"
        self.command(["/usr/bin/docker", "cp", f"{self.ctx.container}:/sandbox/.openclaw/openclaw.json",
                      str(current)], "resume-native-ownership-check")
        actual = json.loads(current.read_bytes())
        if any(actual.get(key) != native.get(key) for key in ("models", "proxy")) or actual.get("gateway", {}).get("auth") != native.get("gateway", {}).get("auth"):
            raise RecoveryRefused("resume-native-auth-or-route-drift")
        try:
            return self.complete_restore(backup_root, backup, candidate, hashes, conversations)
        except Exception as exc:
            self.record["failedStage"] = self.record.get("stage")
            self.record["failureCode"] = str(exc) if isinstance(exc, RecoveryRefused) else "private-exception"
            self.stage("failed-after-mutation", "failed")
            raise RecoveryRefused("transaction-needs-operator") from None


def restore_error(context: Any) -> tuple[str, str]:
    fd = os.open(context.state / "error-recovery.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryRefused("transaction-already-running") from None
        return Transaction(context).execute()
    finally:
        os.close(fd)
