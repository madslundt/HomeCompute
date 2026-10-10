#!/usr/bin/env python3
"""Trusted scheduled metadata admission; the existing n8n owner sends notifications."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time
from typing import Any
import uuid

from openclaw_tasks import ADAPTER, private_json, request

MAX_REPORT = 1024 * 1024
MAINTENANCE_REPORT = Path("/var/lib/homecompute-maintenance/report.json")
PRIVATE_MODEL_REPORT = Path("/var/lib/homecompute-model-update-check/report.json")
MODEL_REPORT = Path("/run/homecompute-openclaw-reports/model-update-report.json")
SPARK_COMMAND = ["/run/current-system/sw/bin/ssh", "-F", "/dev/null",
                 "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                 "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=8",
                 "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=5",
                 "-o", "ServerAliveCountMax=2", "-o", "ForwardAgent=no",
                 "-o", "UserKnownHostsFile=/var/lib/homecompute-openclaw/.ssh/known_hosts",
                 "-i", "/var/lib/homecompute-openclaw/.ssh/id_ed25519",
                 "madslundt@192.168.30.126", "cat /var/lib/homecompute-maintenance/report.json"]


def parse_report(data: bytes, limit: int = MAX_REPORT) -> dict[str, Any]:
    if len(data) > limit:
        raise ValueError("report exceeds bounded metadata contract")
    try:
        value = json.loads(data)
    except RecursionError:
        raise ValueError("metadata nesting exceeds bounded contract") from None
    if not isinstance(value, dict):
        raise ValueError("metadata report must be an object")
    return value


def read_report(path: Path, limit: int = MAX_REPORT) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("metadata source must be a regular file")
    with os.fdopen(descriptor, "rb") as stream:
        return parse_report(stream.read(limit + 1), limit)


def model_projection(model: dict[str, Any]) -> dict[str, Any]:
    summary = model.get("summary")
    counters = ("changed", "pin_drift", "source_errors", "outperforms_active")
    if (type(model.get("schema_version")) is not int or model["schema_version"] != 1
            or model.get("document_type") != "model_update_report" or model.get("mode") != "review-only"
            or not isinstance(model.get("status"), str) or model["status"] not in {"attention", "baseline", "quiet"}
            or not isinstance(model.get("generated_at"), str) or not isinstance(summary, dict)
            or any(type(summary.get(key)) is not int or not 0 <= summary[key] <= 10000000 for key in counters)):
        raise ValueError("invalid finite model report")
    generated = datetime.fromisoformat(model["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("model report timestamp requires timezone")
    value = {key: model[key] for key in ("schema_version", "document_type", "generated_at", "mode", "status")}
    value["summary"] = {key: summary[key] for key in counters}
    return value


def project_core_model() -> bool:
    """Root copies only finite metadata into a public, boot-ephemeral projection."""
    if os.geteuid() != 0:
        raise PermissionError("model projection requires the trusted root preflight")
    directory = MODEL_REPORT.parent
    directory.mkdir(mode=0o755, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = ".model-update-report-" + uuid.uuid4().hex
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("projection directory must be root-owned without group/world writes")
        os.fchmod(descriptor, 0o755)
        try:
            existing = os.stat(MODEL_REPORT.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode) or existing.st_uid != 0:
                raise ValueError("existing projection must be root-owned and regular")
        except FileNotFoundError:
            pass
        try:
            value = model_projection(read_report(PRIVATE_MODEL_REPORT, 2 * MAX_REPORT))
        except (OSError, ValueError):
            # An unavailable source must never leave a prior fresh projection behind.
            try:
                os.unlink(MODEL_REPORT.name, dir_fd=descriptor)
            except FileNotFoundError:
                pass
            os.fsync(descriptor)
            return False
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=descriptor)
        with os.fdopen(output, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, MODEL_REPORT.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
        return True
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def bounded_command(argv: list[str], timeout: float = 25) -> bytes:
    """Bound both SSH output allocation and wall time; discard private stderr."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    deadline, chunks, size = time.monotonic() + timeout, [], 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise subprocess.TimeoutExpired(argv[0], timeout)
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_REPORT + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_REPORT:
                    raise ValueError("remote metadata exceeds bounded contract")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv[0], timeout)
            if process.wait(timeout=remaining):
                raise ValueError("remote metadata unavailable")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def core_snapshot(system: dict[str, Any]) -> dict[str, Any] | None:
    if (not system["enabled"] or system["collector"] != "homecompute-status"
            or system["id"] != system["target"] or system["id"] not in {"home-core", "home-spark"}):
        return None
    raw = {"schema_version": 1, "host": system["id"]}
    try:
        raw["maintenance_report"] = (read_report(MAINTENANCE_REPORT) if system["id"] == "home-core"
                                     else parse_report(bounded_command(SPARK_COMMAND)))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raw["maintenance_report"] = None
    if system["id"] == "home-core" and "model-update-report" in system["updates"]:
        try:
            raw["model_update_report"] = model_projection(read_report(MODEL_REPORT))
        except (OSError, ValueError):
            raw["model_update_report"] = None
    return raw


def load_observer() -> Any:
    spec = importlib.util.spec_from_file_location("hc_observe_feed", Path(__file__).with_name("observe-homecompute.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def feed(registry_path: Path, tokens_path: Path, destination: Path, *, core_host_mode: bool = False) -> dict:
    module = load_observer()
    registry = module.validate_registry(module.read_json(registry_path))
    snapshots = {}
    for system in registry["systems"]:
        try:
            snapshots[system["id"]] = core_snapshot(system) if core_host_mode else module.collect(system)
        except (OSError, ValueError, module.subprocess.TimeoutExpired, module.homecompute.OperatorError):
            snapshots[system["id"]] = None
    report = module.make_report(registry, snapshots, datetime.now(timezone.utc))
    # This feed covers only the authorized update extension. Health and HA
    # retain their separately configured collection/activation paths.
    report["observations"] = [x for x in report["observations"] if x["category"] == "updates"]
    report["findings"] = []
    report["changes"] = []
    token = private_json(tokens_path)["collector"]
    if request(ADAPTER, "/observations", token, report) != {"accepted": True, "actions_enabled": False}:
        raise ValueError("observation receipt unavailable")
    temporary = destination.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    return {"accepted": True, "observations": len(report["observations"]), "generated_at": report["generated_at"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--tokens", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--core-host-mode", action="store_true",
                        help="fixed core report reads and dedicated forced-command Spark SSH reader")
    parser.add_argument("--project-core-model", action="store_true",
                        help="root-only finite model report projection; no tokens or transport calls")
    args = parser.parse_args()
    os.umask(0o077)
    if args.project_core_model:
        print(json.dumps({"projected": project_core_model()}))
        return 0
    if not args.registry or not args.tokens or not args.state:
        parser.error("feed requires --registry, --tokens and --state")
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.state.is_symlink() or args.state.stat().st_mode & 0o077:
        raise ValueError("private feed state required")
    fd = os.open(args.state / "feed.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular lock required")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                print(json.dumps(feed(args.registry, args.tokens, args.state / "latest.json",
                                      core_host_mode=args.core_host_mode)), flush=True)
            except Exception:
                print(json.dumps({"accepted": False, "reason": "collection_or_admission_failed"}), flush=True)
                if not args.follow:
                    return 2
            if not args.follow:
                return 0
            time.sleep(300)
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
