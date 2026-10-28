#!/run/current-system/sw/bin/python3 -I
"""Root-only finite HA update transport, invoked by the trusted maintenance controller.

The parent controller owns exact approvals and consumes each attempt before
calling install. This helper never retries a service call or claims installation
has completed from a service receipt. The HA credential remains inside n8n.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import urllib.request

ENTITIES = frozenset({"update.home_assistant_core_update", "update.home_assistant_supervisor_update",
                     "update.home_assistant_operating_system_update", "update.house_battery_update",
                     "update.stromligning_update", "update.watts_live_update"})
TOKEN = Path("/var/lib/homecompute-ha-maintenance/executor-token")
ROUTE = "http://127.0.0.1:15678/webhook/homecompute-approved-ha-maintenance"
COMMAND = "/usr/local/libexec/homecompute-home-assistant-maintenance"
VERSION = re.compile(r"v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?")
# https://github.com/home-assistant/core/blob/dev/homeassistant/components/update/const.py
REQUIRED_FEATURES = 1 | 2 | 8  # INSTALL, SPECIFIC_VERSION, BACKUP
FIELDS = {"schema_version", "entity_id", "state", "installed_version", "latest_version",
          "supported_features", "in_progress", "observed_at"}


def entity(value: str) -> str:
    if not isinstance(value, str) or value not in ENTITIES:
        raise ValueError("update entity outside reviewed policy")
    return value


def version(value: str) -> str:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("stable explicit version required")
    return value


def metadata(value: object, selected: str, now: float) -> dict:
    if (not isinstance(value, dict) or set(value) != FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["entity_id"] != entity(selected) or value["state"] not in {"on", "off"}
            or type(value["supported_features"]) is not int or not 0 <= value["supported_features"] <= 65535
            or type(value["in_progress"]) is not bool
            or type(value["observed_at"]) not in {int, float} or not math.isfinite(value["observed_at"])
            or not now - 30 <= value["observed_at"] <= now + 2):
        raise ValueError("fresh finite HA update metadata required")
    version(value["installed_version"])
    version(value["latest_version"])
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Transport:
    def __init__(self, token_file: Path = TOKEN):
        for path in token_file.parents:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("root-owned fixed token directories required")
        fd = os.open(token_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError("root-owned mode0600 executor token required")
            self.token = os.read(fd, 256).decode().strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.token):
                raise ValueError("invalid executor token")
        finally:
            os.close(fd)

    def __call__(self, body: dict) -> dict:
        req = urllib.request.Request(ROUTE, data=json.dumps(body, allow_nan=False).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=300 if body.get("operation") == "execute" else 25) as response:
            if response.status != 200:
                raise ValueError("HA maintenance route unavailable")
            raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError("HA maintenance response exceeds budget")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("HA maintenance object required")
        return value


class Maintenance:
    def __init__(self, transport, clock=time.time):
        self.transport, self.clock = transport, clock

    def snapshot(self, selected: str) -> dict:
        selected = entity(selected)
        return metadata(self.transport({"operation": "snapshot", "entity_id": selected}), selected, self.clock())

    def install(self, selected: str, target: str, expected_installed: str) -> dict:
        selected, target, expected_installed = entity(selected), version(target), version(expected_installed)
        current = self.snapshot(selected)
        if (current["installed_version"] != expected_installed or current["latest_version"] != target
                or current["state"] != "on" or current["in_progress"] is not False
                or current["supported_features"] & REQUIRED_FEATURES != REQUIRED_FEATURES
                or target == expected_installed):
            raise ValueError("update baseline/candidate/capability changed; new review required")
        # n8n independently re-reads and enforces this same baseline immediately
        # before calling the single fixed update.install action.
        receipt = self.transport({"operation": "execute", "entity_id": selected,
                                  "version": target, "expected_installed": expected_installed, "backup": True})
        expected = {"accepted": True, "entity_id": selected, "version": target, "backup": True}
        if receipt != expected or receipt.get("accepted") is not True or receipt.get("backup") is not True:
            raise ValueError("uncertain HA service receipt; reconcile without retry")
        return receipt

    def verify(self, selected: str, target: str, *, timeout: int = 600, sleep=time.sleep, monotonic=time.monotonic) -> dict:
        selected, target = entity(selected), version(target)
        deadline = monotonic() + timeout
        while True:
            try:
                current = self.snapshot(selected)
                if current["installed_version"] == target and not current["in_progress"]:
                    return current
            except (ValueError, OSError):
                pass  # A restart can temporarily remove the read route; only GET-equivalent snapshots repeat.
            if monotonic() >= deadline:
                raise ValueError("HA installation not independently verified; reconcile without retrying install")
            sleep(min(5, max(0, deadline - monotonic())))

    def prepare(self, selected: str) -> dict:
        current = self.snapshot(selected)
        if (current["state"] != "on" or current["in_progress"]
                or current["installed_version"] == current["latest_version"]
                or current["supported_features"] & REQUIRED_FEATURES != REQUIRED_FEATURES):
            raise ValueError("no supported, idle update with exact version and backup capability")
        selected = current["entity_id"]
        installed, candidate = current["installed_version"], current["latest_version"]
        scheme = "calendar" if selected in {"update.home_assistant_core_update", "update.home_assistant_supervisor_update"} else "semver"
        return {"schema_version": 1,
                "policy": {"operation": "update", "target": "home-assistant", "updates": [
                    {"component": selected, "scheme": scheme, "installed": installed, "candidate": candidate}]},
                "prechecks": [{"argv": [COMMAND, "snapshot", selected],
                               "stdout_sha256": hashlib.sha256(stable_output(current)).hexdigest()}],
                "steps": [[COMMAND, "install", selected, candidate, "--expected-installed", installed]],
                "postchecks": [[COMMAND, "verify", selected, candidate]],
                "timeout_seconds": 900, "expires_at": self.clock() + 3600, "requires_approval": False}


def stable_output(current: dict) -> bytes:
    """Freshness is verified internally; invariants alone bind controller prechecks."""
    return (json.dumps({key: value for key, value in current.items() if key != "observed_at"}, sort_keys=True) + "\n").encode()


def write_plan(path: Path, plan: dict) -> None:
    # Keep the standalone -I executable independent of importable controller code.
    path = Path(os.path.abspath(path))
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("root-owned fixed plan directory required")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(plan, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["snapshot", "install", "verify", "prepare"])
    parser.add_argument("entity_id")
    parser.add_argument("version", nargs="?")
    parser.add_argument("--expected-installed")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "home-core":
        parser.error("trusted root operator on home-core required")
    try:
        entity(args.entity_id)
        if args.operation in {"snapshot", "prepare"}:
            if args.version is not None or args.expected_installed is not None:
                raise ValueError("snapshot takes only one entity")
        elif args.version is None or (args.operation == "install") != (args.expected_installed is not None):
            raise ValueError("install requires version and expected installed baseline")
        helper = Maintenance(Transport())
        if (args.operation == "prepare") != (args.output is not None):
            raise ValueError("prepare requires a new root-owned output plan")
        if args.operation == "prepare":
            write_plan(args.output, helper.prepare(args.entity_id))
            print(json.dumps({"plan_file": str(args.output), "installed_changes": False}, sort_keys=True))
            return 0
        values = [args.entity_id] + ([args.version] if args.version is not None else [])
        if args.expected_installed is not None:
            values.append(args.expected_installed)
        result = getattr(helper, args.operation)(*values)
        if args.operation == "snapshot":
            sys.stdout.write(stable_output(result).decode())
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError):
        print("HA maintenance unavailable or uncertain; reconcile through the trusted operator without retry.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
