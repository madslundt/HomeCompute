#!/usr/bin/env python3
"""Supervise one pinned native NemoClaw sandbox without owning its gateway.

No conversations, turn replays, or runtime upgrades are submitted. An approved
transaction can replace an exact failed sandbox after verifying its backup.
CLI diagnostics remain private; the journal receives only fixed event names.
Stop/disable this service before an intentional `nemoclaw ... stop`.
"""
from __future__ import annotations

from contextlib import closing
import json
import importlib.util
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import subprocess
import threading
import time
import urllib.request
import uuid

NAME = "agent-openclaw"
GATEWAY = "nemoclaw-9123"
REVISION = "26922313bba96184e65c3663b351683ebae9504d"
IMAGE = "ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a"
INTERVAL = 60
WINDOW = 1800
MAX_ATTEMPTS = 3
ENV_KEYS = {
    "NEMOCLAW_INSTALL_REF", "NEMOCLAW_INSTALL_TAG", "NEMOCLAW_AGENT",
    "NEMOCLAW_SANDBOX_NAME", "NEMOCLAW_GATEWAY_PORT",
    "NEMOCLAW_GATEWAY_BIND_ADDRESS", "NEMOCLAW_DASHBOARD_PORT",
    "NEMOCLAW_PROVIDER", "NEMOCLAW_ENDPOINT_URL", "NEMOCLAW_TRUSTED_PRIVATE_HOSTS",
    "NEMOCLAW_MODEL", "NEMOCLAW_POLICY_TIER", "NEMOCLAW_POLICY_MODE",
    "NEMOCLAW_WEB_SEARCH_PROVIDER", "NEMOCLAW_TOOL_DISCLOSURE",
    "NEMOCLAW_CONTEXT_WINDOW", "NEMOCLAW_MAX_TOKENS", "NEMOCLAW_REASONING",
    "NEMOCLAW_AGENT_TIMEOUT", "NEMOCLAW_AGENT_HEARTBEAT_EVERY",
    "NEMOCLAW_CPU", "NEMOCLAW_RAM", "NEMOCLAW_SANDBOX_GPU",
    "NEMOCLAW_CORPORATE_CA_BUNDLE",
}
REQUIRED_ENV = {
    "NEMOCLAW_INSTALL_REF": REVISION, "NEMOCLAW_INSTALL_TAG": "v0.0.129",
    "NEMOCLAW_AGENT": "openclaw", "NEMOCLAW_SANDBOX_NAME": NAME,
    "NEMOCLAW_GATEWAY_PORT": "9123", "NEMOCLAW_DASHBOARD_PORT": "18791",
    "NEMOCLAW_MODEL": "automation-moe", "NEMOCLAW_RAM": "8Gi",
    "NEMOCLAW_PROVIDER": "custom",
    "NEMOCLAW_ENDPOINT_URL": "http://ai.home.arpa:18080/v1",
}


class Refused(Exception):
    """Unsafe identity or lifecycle; never include private source data."""


def event(message: str) -> None:
    print(message, flush=True)


def private_file(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise Refused("private-file-permissions")
    if info.st_uid not in (0, os.getuid()):
        raise Refused("private-file-owner")
    return path.read_bytes()


def protobuf_field(data: bytes, number: int, wire: int) -> bytes | int:
    """Read one pinned protobuf field; malformed/ambiguous payloads fail closed."""
    offset = 0
    found: list[bytes | int] = []

    def varint() -> int:
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise Refused("native-database-payload-invalid")
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise Refused("native-database-payload-invalid")

    while offset < len(data):
        tag = varint()
        kind = tag & 7
        if kind == 0:
            value = varint()
        elif kind in (1, 2, 5):
            length = varint() if kind == 2 else (8 if kind == 1 else 4)
            if offset + length > len(data):
                raise Refused("native-database-payload-invalid")
            value = data[offset:offset + length]
            offset += length
        else:
            raise Refused("native-database-payload-invalid")
        if tag >> 3 == number:
            if kind != wire:
                raise Refused("native-database-payload-invalid")
            found.append(value)
    if len(found) != 1:
        raise Refused("native-database-payload-invalid")
    return found[0]


class Supervisor:
    def __init__(self, environment: dict[str, str], *, operator_resume: bool = False) -> None:
        self.env = {key: environment[key] for key in ENV_KEYS if key in environment}
        for key, value in REQUIRED_ENV.items():
            if self.env.get(key) != value:
                raise Refused("environment-tuple-mismatch")
        self.env.update({key: environment[key] for key in ("HOME", "PATH", "USER", "LOGNAME")
                         if key in environment})
        self.env.update({"NO_COLOR": "1", "CI": "1"})
        self.uuid = str(uuid.UUID(environment["OPENCLAW_EXPECTED_SANDBOX_ID"]))
        self.generation = str(uuid.UUID(environment["OPENCLAW_EXPECTED_GENERATION"]))
        self.home = Path(self.env["HOME"])
        self.cli = str(self.home / ".local/bin/nemoclaw")
        self.openshell = str(self.home / ".local/bin/openshell")
        self.registry = self.home / ".nemoclaw/gateways/9123/sandboxes.json"
        self.keyfile = Path(environment["OPENCLAW_NEMOCLAW_MODEL_KEY_FILE"])
        if self.keyfile != Path("/etc/homecompute-openclaw/secrets/litellm-agent-openclaw"):
            raise Refused("keyfile-path-mismatch")
        self.container = f"openshell-default--{NAME}-{self.uuid}"
        self.state = self.home / ".local/state/homecompute-openclaw-supervisor"
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state.is_symlink() or self.state.stat().st_uid != os.getuid():
            raise Refused("diagnostics-directory-owner")
        self.state.chmod(0o700)
        transaction = self.state / "error-recovery-transaction.json"
        if not operator_resume and transaction.exists() and json.loads(transaction.read_bytes()).get("status") not in ("complete", "refused"):
            raise Refused("error-recovery-needs-operator")
        self.stopping = threading.Event()
        self.child: subprocess.Popen | None = None
        self.attempts: list[float] = []
        self.failures = 0
        self.last_event = ""

    def stop(self, *_: object) -> None:
        self.stopping.set()
        if self.child is not None and self.child.poll() is None:
            # Only the current CLI command. Detached managed gateway survives.
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def report(self, message: str) -> None:
        if message != self.last_event:
            event(message)
            self.last_event = message

    def run(self, argv: list[str], label: str, *, onboarding: bool = False,
            timeout: int = 30) -> tuple[int, bytes]:
        env = self.env.copy()
        if onboarding:
            key = private_file(self.keyfile).decode().strip()
            if not key or "\n" in key:
                raise Refused("invalid-keyfile")
            env["COMPATIBLE_API_KEY"] = key
        if self.stopping.is_set():
            raise Refused("supervisor-stopping")
        self.child = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.stopping.is_set() or time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    output, _ = self.child.communicate(timeout=min(5, deadline - time.monotonic()))
                    break
                except subprocess.TimeoutExpired:
                    continue
            code = self.child.returncode
        except subprocess.TimeoutExpired:
            os.killpg(self.child.pid, signal.SIGTERM)
            try:
                output, _ = self.child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                output, _ = self.child.communicate()
            code = 124
        finally:
            self.child = None
        # Atomic replacement prevents symlink following and bounds disk usage.
        temporary = self.state / "diagnostics.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(f"{label} exit={code}\n".encode() + output[-1024 * 1024:])
        temporary.replace(self.state / "last-command.log")
        return code, output

    def check_registry(self) -> None:
        try:
            entry = json.loads(self.registry.read_bytes())["sandboxes"][NAME]
            expected = {"name": NAME, "agent": "openclaw", "gatewayName": GATEWAY,
                        "gatewayPort": 9123, "dashboardPort": 18791,
                        "provider": "compatible-endpoint", "model": "automation-moe",
                        "endpointUrl": "http://ai.home.arpa:18080/v1",
                        "agentVersion": "2026.9.1", "openshellVersion": "0.0.116",
                        "lifecycleGeneration": self.generation}
            if any(entry.get(key) != value for key, value in expected.items()):
                raise Refused("registry-identity-mismatch")
            workload = entry["workload"]
            if (workload.get("reference") != IMAGE or
                    workload.get("sourceRevision") != REVISION):
                raise Refused("registry-workload-mismatch")
        except (ValueError, KeyError, OSError, TypeError):
            raise Refused("registry-unavailable") from None

    def check_container(self) -> None:
        # Read only exact identity. No full inspect/environment/key output.
        code, output = self.run(["/usr/bin/docker", "inspect", "--format", "{{.Name}}",
                                 self.container], "container-identity")
        if code or output.decode().strip() != f"/{self.container}":
            raise Refused("sandbox-container-missing")

    def observe(self) -> str:
        code, output = self.run([self.openshell, "sandbox", "get", "-g", GATEWAY,
                                 NAME, "-o", "json"], "sandbox-metadata")
        if code:
            # Restore a missing host listener only; refuse auth/RPC failures on
            # an existing listener rather than introducing a second owner.
            try:
                with socket.create_connection(("127.0.0.1", 9123), timeout=3):
                    return "unavailable"
            except OSError:
                return "host-gateway-down"
        try:
            sandbox = json.loads(output)
            if sandbox["name"] != NAME or sandbox["id"] != self.uuid:
                raise Refused("sandbox-identity-mismatch")
            return sandbox["phase"]
        except (ValueError, KeyError, TypeError):
            raise Refused("sandbox-metadata-invalid") from None

    def ready(self) -> bool:
        # The dashboard is optional for canonical CLI/Telegram traffic. Prefer
        # its readiness signal, then prove the native gateway with an exact,
        # authenticated read-only RPC. Outputs remain in private diagnostics.
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18791/readyz", timeout=5) as response:
                body = json.loads(response.read(65536))
                if (response.status == 200 and isinstance(body, dict) and
                        (body.get("ready") is True or body.get("ok") is True)):
                    return True
        except (OSError, ValueError):
            pass
        try:
            code, _ = self.run([self.cli, NAME, "exec", "--", "openclaw", "gateway", "call",
                                "exec.approvals.get", "--params", "{}", "--json"],
                               "native-readiness-rpc", timeout=30)
            return code == 0
        except OSError:
            return False

    def recover(self, phase: str) -> None:
        if phase == "Error":
            path = Path(__file__).with_name("restore-openclaw-nemoclaw.py")
            spec = importlib.util.spec_from_file_location("openclaw_error_recovery", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            try:
                module.restore_error(self)
            except module.RecoveryRefused as exc:
                raise Refused(str(exc)) from None
            self.failures = 0
            self.report("ready-after-state-preserving-replacement")
            return
        if phase == "host-gateway-down":
            self.check_native_database()
            # The supported credentials query restores the named managed
            # gateway through recoverNamedGatewayRuntime. Onboarding instead
            # reserves an inference route and cannot finalize a Stopped agent.
            argv = [self.cli, "credentials", "list"]
        else:
            argv = [self.cli, NAME, "start" if phase == "Stopped" else "recover"]
        event(f"recovery-attempt:{phase}")
        code, _ = self.run(argv, "native-recovery", timeout=420)
        self.check_registry()
        self.check_container()
        observed = self.observe()
        if self.stopping.is_set():
            return
        # Restoring a retained host gateway preserves intentional Stopped
        # state. Complete the ordinary boot sequence under the same fences;
        # command exit codes alone do not invalidate positive native state.
        if phase == "host-gateway-down" and observed == "Stopped":
            event("recovery-attempt:retained-sandbox-start")
            code, _ = self.run([self.cli, NAME, "start"], "native-boot-start", timeout=420)
            self.check_registry()
            self.check_container()
            observed = self.observe()
        if phase in ("host-gateway-down", "Stopped") and observed == "Ready" and not self.ready():
            event("recovery-attempt:retained-forward-recovery")
            code, _ = self.run([self.cli, NAME, "recover"], "native-boot-recover", timeout=420)
            self.check_registry()
            self.check_container()
            observed = self.observe()
        if phase == "Ready" and observed == "Ready" and not self.ready():
            # Some pinned native recovery paths report a stopped gateway but
            # require explicit restart to relaunch it. A missing host forward
            # alone must never cause the running main gateway to be restarted.
            process_code, processes = self.run(
                ["/usr/bin/docker", "exec", self.container, "/usr/bin/pgrep", "-x", "openclaw-gatewa"],
                "native-gateway-process")
            if process_code == 1 and not processes.strip():
                event("recovery-attempt:native-gateway-restart")
                code, _ = self.run([self.cli, NAME, "gateway", "restart", "--quiet"],
                                   "native-gateway-restart", timeout=420)
                self.check_registry()
                self.check_container()
                observed = self.observe()
        # Native Start may return nonzero after successful startup when an
        # operator checkpoint blocks optional registry/forward finalization.
        # Positive exact Ready plus gateway readiness is the runtime proof.
        if observed != "Ready" or not self.ready():
            self.report("recovery-incomplete")
        else:
            self.failures = 0
            self.report("ready")

    def check_native_database(self) -> None:
        # An observation fence only: no native DB or phase mutations. Refuse
        # missing/corrupt/unknown records before host gateway recovery can run.
        # Error is retained here: the credential query cannot recreate it, and
        # its next observed phase enters the separately gated backup transaction.
        # Schema: OpenShell v0.0.116 proto/openshell.proto (Sandbox.status=3,
        # SandboxStatus.phase=6) and proto/datamodel.proto (id=1, name=2).
        path = self.home / ".local/state/nemoclaw/openshell-docker-gateway-9123/openshell.db"
        try:
            if path.is_symlink() or not path.is_file():
                raise Refused("native-database-unavailable")
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=3)) as db:
                db.execute("PRAGMA query_only=ON")
                rows = db.execute(
                    "SELECT id, payload FROM objects WHERE object_type=? AND name=? AND workspace=?",
                    ("sandbox", NAME, "default")).fetchall()
            if len(rows) != 1 or rows[0][0] != self.uuid:
                raise Refused("native-database-identity-mismatch")
            payload = rows[0][1]
            if not isinstance(payload, bytes) or len(payload) > 1024 * 1024:
                raise Refused("native-database-payload-invalid")
            metadata = protobuf_field(payload, 1, 2)
            if (protobuf_field(metadata, 1, 2).decode() != self.uuid or
                    protobuf_field(metadata, 2, 2).decode() != NAME):
                raise Refused("native-database-identity-mismatch")
            phase = protobuf_field(protobuf_field(payload, 3, 2), 6, 0)
            if phase not in (2, 3, 7):
                raise Refused("native-database-terminal-or-transitioning-phase")
        except (OSError, sqlite3.Error, UnicodeError, TypeError):
            raise Refused("native-database-unavailable") from None

    def tick(self, now: float) -> None:
        self.check_registry()
        self.check_container()
        phase = self.observe()
        if phase == "Ready" and self.ready():
            self.failures = 0
            self.report("ready")
            return
        if phase not in ("Ready", "Stopped", "host-gateway-down", "Error"):
            self.failures = 0
            self.report("blocked:terminal-or-unavailable-runtime")
            return
        self.failures += 1
        if self.failures < 2:
            self.report("waiting:confirming-runtime-failure")
            return
        self.attempts = [attempt for attempt in self.attempts if now - attempt < WINDOW]
        if len(self.attempts) >= MAX_ATTEMPTS:
            self.report("backoff:recovery-budget-exhausted")
            return
        delay = (60, 120, 300)[len(self.attempts)]
        if self.attempts and now - self.attempts[-1] < delay:
            return
        self.attempts.append(now)
        self.recover(phase)

    def serve(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        event("supervisor-started")
        while not self.stopping.is_set():
            try:
                self.tick(time.monotonic())
            except Refused as exc:
                self.report(f"blocked:{exc}")
            except Exception:
                # Never emit exceptions containing environment or CLI output.
                self.report("blocked:unexpected-supervisor-error")
            self.stopping.wait(INTERVAL)
        event("supervisor-stopped")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        Supervisor(dict(os.environ)).serve()
    except Exception:
        event("blocked:supervisor-configuration")
        raise SystemExit(1) from None
