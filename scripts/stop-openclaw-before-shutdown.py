#!/usr/bin/env python3
"""Preserve native Stopped intent before Docker/user-manager shutdown."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess


def stop_exact(context, record=lambda stage: None) -> None:
    record("registry-identity")
    context.check_registry()
    record("container-identity")
    context.check_container()
    # The shutdown unit precedes user-manager and Docker teardown. Prevent its
    # watchdog from interpreting this intentional native stop as an outage.
    record("watchdog-stop")
    subprocess.run(["/usr/bin/systemctl", "--user", "stop", "homecompute-openclaw-supervisor.service"],
                   check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    record("native-phase-observation")
    phase = context.observe()
    if phase == "Stopped":
        return
    if phase != "Ready":
        raise RuntimeError("native-runtime-not-ready-for-stop")
    record("native-stop")
    context.run([context.cli, "agent-openclaw", "stop"], "native-shutdown-stop", timeout=100)
    context.check_registry()
    context.check_container()
    # A pending operator onboarding marker may refuse the registry stop-intent
    # finalization after native stop succeeds. Exact native Stopped is the
    # shutdown proof; never bypass a failed identity fence or invent a phase.
    if context.observe() != "Stopped":
        raise RuntimeError("native-shutdown-stop-failed")


if __name__ == "__main__":
    os.umask(0o077)
    diagnostic = Path("/home/hermes-operator/.local/state/homecompute-openclaw-supervisor/shutdown-event.json")
    state = {"stage": "configuration", "status": "active"}
    def record(stage):
        state["stage"] = stage
        temporary = diagnostic.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle); handle.flush(); os.fsync(handle.fileno())
        temporary.replace(diagnostic)
        fd = os.open(diagnostic.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    try:
        record("configuration")
        path = Path(__file__).with_name("supervise-openclaw-nemoclaw.py")
        spec = importlib.util.spec_from_file_location("shutdown_supervisor", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = dict(os.environ)
        # Recovery can replace the exact sandbox while this system unit is
        # active; load its trusted current identity at shutdown, never cache it.
        for line in module.private_file(Path("/etc/homecompute-openclaw/supervisor-identity.env")).decode().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                if key in ("OPENCLAW_EXPECTED_SANDBOX_ID", "OPENCLAW_EXPECTED_GENERATION"):
                    environment[key] = value
        stop_exact(module.Supervisor(environment), record)
        state["status"] = "complete"
        record("native-stopped")
        print("native-stopped-for-vm-shutdown", flush=True)
    except Exception as exc:
        state["status"] = "failed"
        state["errorCode"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        try: record(state["stage"])
        except Exception: pass
        print("blocked:native-shutdown-stop", flush=True)
        raise SystemExit(1) from None
