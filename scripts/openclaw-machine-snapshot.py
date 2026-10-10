#!/usr/bin/env python3
"""Fixed, read-only host observations for the trusted OpenClaw status feed.

The SSH entry point admits only homecompute-machine-snapshot and the legacy
fixed maintenance-report read. Raw reports go to the trusted normalizer, never
directly into assistant context. No inventory discovery or updates run here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import time
from typing import Any, Callable
import uuid

CONTAINERS = {
    "home-core": ("homecompute-control-plane-caddy-1", "homecompute-control-plane-litellm-1",
                  "homecompute-control-plane-postgres-1", "homecompute-automation-n8n-1"),
    "home-spark": ("qwen38-flash-ultrafast", "gb10-hviske-primary", "homecompute-plapre-plapre-primary-1"),
}
UNITS = {"home-core": ("homecompute-platform-health.service", "homecompute-model-update-monitor.service",
                       "homecompute-agents-vm.service"), "home-spark": ()}
MAINTENANCE = Path("/var/lib/homecompute-maintenance/report.json")
MODEL = Path("/run/homecompute-openclaw-reports/model-update-report.json")
PUBLIC_SNAPSHOT = Path("/run/homecompute-openclaw-reports/machine-status.json")
MAX_REPORT = 1024 * 1024
COMMAND_BUDGET = 8.0
FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "ExecMainStartTimestampMonotonic")
ENUMS = {"LoadState": {"loaded", "not-found", "error", "masked"},
         "ActiveState": {"active", "inactive", "failed", "activating", "deactivating", "reloading"},
         "SubState": {"running", "exited", "dead", "failed", "start", "stop", "auto-restart", "waiting"},
         "Result": {"success", "exit-code", "signal", "core-dump", "timeout", "watchdog", "resources", "start-limit-hit"}}


def bounded_file(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("report-file-refused")
        value = os.read(fd, limit + 1)
        if len(value) > limit:
            raise ValueError("report-budget-refused")
        return value
    finally:
        os.close(fd)


def read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(bounded_file(path, MAX_REPORT))
    except RecursionError:
        raise ValueError("report-nesting-refused") from None
    if not isinstance(value, dict):
        raise ValueError("report-object-required")
    return value


def bounded_command(argv: list[str], timeout: float, *, host: str) -> bytes:
    environment = {"HOME": "/", "LANG": "C.UTF-8", "PATH": "/run/current-system/sw/bin" if host == "home-core" else "/usr/bin:/bin"}
    child = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    output, deadline = bytearray(), time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("command-timeout")
                for key, _ in selector.select(min(remaining, 0.25)):
                    value = os.read(key.fd, 8192)
                    if not value:
                        selector.unregister(key.fileobj)
                    elif len(output) + len(value) > 32768:
                        raise ValueError("command-output-budget")
                    else:
                        output.extend(value)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or child.wait(timeout=remaining) != 0:
                raise ValueError("command-unavailable")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise ValueError("command-timeout") from None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()


def container_status(state: dict[str, Any]) -> str:
    if state.get("Restarting") is True:
        return "Restarting observed"
    if state.get("Dead") is True or state.get("Status") == "dead":
        return "Dead"
    if state.get("Paused") is True:
        return "Exited paused"
    if state.get("Running") is True:
        health = state.get("Health", {})
        status = health.get("Status") if isinstance(health, dict) else None
        return "Up observed" + (" (" + status + ")" if isinstance(status, str) and status in {"healthy", "unhealthy", "starting"} else "")
    return "Exited observed" if state.get("Status") == "exited" else "Unknown"


def unit_projection(data: bytes, allowed: tuple[str, ...]) -> dict[str, Any]:
    result = {}
    for block in data.decode(errors="replace").strip().split("\n\n"):
        values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        name = values.get("Id")
        if name not in allowed or name in result:
            continue
        record = {key: values[key] if values.get(key) in choices else "unknown" for key, choices in ENUMS.items()}
        for key, limit in (("ExecMainStatus", 255), ("ExecMainStartTimestampMonotonic", 10 ** 18)):
            try:
                value = int(values[key])
                if not 0 <= value <= limit:
                    raise ValueError()
                record[key] = value
            except (ValueError, KeyError):
                record[key] = None
        result[name] = record
    return result


def host_metrics() -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    try:
        uptime = float(bounded_file(Path("/proc/uptime"), 1024).split()[0])
        if math.isfinite(uptime) and 0 <= uptime <= 10 ** 12:
            values["uptime_seconds"] = round(uptime, 2)
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory = {}
        for line in bounded_file(Path("/proc/meminfo"), 16384).decode().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] in {"MemTotal:", "MemAvailable:"} and parts[2] == "kB":
                memory[parts[0]] = int(parts[1]) * 1024
        total, available = memory["MemTotal:"], memory["MemAvailable:"]
        if 0 <= available <= total <= 10 ** 16:
            values.update(memory_total_bytes=total, memory_available_bytes=available, memory_used_bytes=total - available)
    except (OSError, ValueError, KeyError, UnicodeError):
        pass
    try:
        disk = shutil.disk_usage("/")
        if all(type(x) is int and 0 <= x <= 10 ** 18 for x in disk):
            values.update(disk_total_bytes=disk.total, disk_used_bytes=disk.used, disk_free_bytes=disk.free)
    except OSError:
        pass
    return values


def model_projection(value: dict[str, Any]) -> dict[str, Any]:
    counters = ("changed", "pin_drift", "source_errors", "outperforms_active")
    summary = value.get("summary", {})
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("mode") != "review-only" or not isinstance(summary, dict)
            or not isinstance(value.get("generated_at"), str)
            or any(type(summary.get(k)) is not int or not 0 <= summary[k] <= 10000000 for k in counters)):
        raise ValueError("model-schema-refused")
    generated = datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("model-timezone-refused")
    return {"schema_version": 1, "mode": "review-only", "generated_at": generated.astimezone(timezone.utc).isoformat(),
            "summary": {k: summary[k] for k in counters}}


def snapshot(host: str, runner: Callable[..., bytes] = bounded_command) -> dict[str, Any]:
    if host not in CONTAINERS:
        raise ValueError("host-refused")
    deadline = time.monotonic() + COMMAND_BUDGET
    binary = "/run/current-system/sw/bin/" if host == "home-core" else "/usr/bin/"
    def command(arguments: list[str]) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("command-budget-exhausted")
        return runner(arguments, min(1.5, remaining), host=host)
    containers = []
    for name in CONTAINERS[host]:
        status = "Unknown"
        try:
            state = json.loads(command([binary + "docker", "--host", "unix:///var/run/docker.sock", "inspect", "--format", "{{json .State}}", name]))
            if isinstance(state, dict):
                status = container_status(state)
        except (OSError, ValueError, RecursionError):
            pass
        containers.append({"name": name, "status": status})
    units = {}
    if UNITS[host]:
        try:
            units = unit_projection(command([binary + "systemctl", "show", "--no-pager", *["-p" + x for x in FIELDS], *UNITS[host]]), UNITS[host])
        except (OSError, ValueError):
            pass
    failed = [name for name, state in units.items() if state.get("ActiveState") == "failed"]
    metrics = host_metrics()
    if len(units) == len(UNITS[host]):
        metrics["failed_unit_count"] = len(failed)
    result = {"schema_version": 1, "host": host, "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "containers": containers, "unit_states": units,
              "failed_units": failed, "host_metrics": metrics}
    try:
        result["maintenance_report"] = read_report(MAINTENANCE)
    except (OSError, ValueError):
        result["maintenance_report"] = None
    if host == "home-core":
        try:
            result["model_update_report"] = model_projection(read_report(MODEL))
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            result["model_update_report"] = None
    return result


def encode_snapshot(value: dict[str, Any]) -> bytes:
    output = json.dumps(value, allow_nan=False, sort_keys=True, ensure_ascii=False).encode()
    if len(output) > MAX_REPORT:
        value = dict(value, maintenance_report=None)
        output = json.dumps(value, allow_nan=False, sort_keys=True, ensure_ascii=False).encode()
    if len(output) > MAX_REPORT:
        raise ValueError("snapshot-budget-refused")
    return output


def publish_core() -> None:
    if os.geteuid() != 0:
        raise ValueError("root-publication-required")
    directory = PUBLIC_SNAPSHOT.parent
    directory.mkdir(mode=0o755, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = ".machine-status-" + uuid.uuid4().hex
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("publication-directory-refused")
        os.fchmod(descriptor, 0o755)
        try:
            existing = os.stat(PUBLIC_SNAPSHOT.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode) or existing.st_uid != 0:
                raise ValueError("publication-file-refused")
        except FileNotFoundError:
            pass
        output = encode_snapshot(snapshot("home-core"))
        target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=descriptor)
        with os.fdopen(target, "wb") as stream:
            stream.write(output); stream.flush(); os.fchmod(stream.fileno(), 0o644); os.fsync(stream.fileno())
        os.replace(temporary, PUBLIC_SNAPSHOT.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try: os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError: pass
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=tuple(CONTAINERS))
    parser.add_argument("--ssh", action="store_true")
    parser.add_argument("--publish-core", action="store_true")
    args = parser.parse_args()
    try:
        if args.publish_core:
            if args.ssh or args.host not in (None, "home-core"):
                raise ValueError("publication-mode-refused")
            publish_core()
            return 0
        if args.ssh:
            original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
            if original == "cat /var/lib/homecompute-maintenance/report.json":
                sys.stdout.buffer.write(bounded_file(MAINTENANCE, MAX_REPORT))
                return 0
            if original != "homecompute-machine-snapshot":
                raise ValueError("ssh-command-refused")
        sys.stdout.buffer.write(encode_snapshot(snapshot(args.host or ("home-spark" if args.ssh else "home-core"))) + b"\n")
        return 0
    except (OSError, ValueError):
        print("Machine observation unavailable or command refused.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
