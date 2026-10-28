#!/usr/bin/env python3
"""Qualify only the local canonical CLI's native admin scope repair.

The initiating RPC is read only. No agent turn or pending foreign device is
submitted/approved. All native outputs, including credentials, stay private.
"""
from __future__ import annotations

import json
import re
from typing import Any
import uuid

NAME = "agent-openclaw"
RPC = ["openclaw", "gateway", "call", "exec.approvals.get", "--params", "{}", "--json"]
IDENTITY_QUERY = "import sqlite3,json;c=sqlite3.connect('file:/sandbox/.openclaw/state/openclaw.sqlite?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');print(json.dumps([r[0] for r in c.execute('SELECT device_id FROM device_identities')]));c.close()"


class PairingRefused(Exception):
    """Fixed safe error codes only."""


def request_from_probe(output: bytes) -> str | None:
    matches = re.findall(r"scope upgrade pending approval \(requestId:\s*([0-9a-f-]{36})\)",
                         output.decode(errors="replace"))
    requests = {str(uuid.UUID(request)) for request in matches}
    if len(requests) > 1:
        raise PairingRefused("ambiguous-canonical-request")
    return next(iter(requests)) if requests else None


def validate_request(devices: dict[str, Any], request: str, identities: list[str]) -> str:
    pending = [item for item in devices.get("pending", []) if item.get("requestId") == request]
    if len(pending) != 1:
        raise PairingRefused("canonical-request-missing-or-ambiguous")
    item = pending[0]
    if (item.get("clientId") != "cli" or item.get("clientMode") != "cli" or
            item.get("platform") != "linux" or item.get("role") != "operator" or
            set(item.get("roles", [])) != {"operator"} or
            set(item.get("scopes", [])) != {"operator.admin"} or
            item.get("isRepair") is not True or item.get("remoteIp") is not None):
        raise PairingRefused("canonical-request-contract-refused")
    paired = [entry for entry in devices.get("paired", []) if entry.get("deviceId") == item.get("deviceId")]
    if (len(paired) != 1 or paired[0].get("clientId") != "cli" or paired[0].get("clientMode") != "cli" or
            item.get("deviceId") not in identities):
        raise PairingRefused("canonical-local-identity-refused")
    return request


def ensure_cli(context: Any) -> None:
    def run(args: list[str], label: str) -> tuple[int, bytes]:
        context.check_registry()
        context.check_container()
        if context.observe() != "Ready":
            raise PairingRefused("native-runtime-not-ready")
        return context.run([context.cli, NAME, "exec", "--", *args], label, timeout=180)

    code, output = run(RPC, "canonical-cli-admin-read")
    if code == 0:
        return
    request = request_from_probe(output)
    if not request:
        raise PairingRefused("canonical-admin-read-failed")
    code, output = run(["openclaw", "devices", "list", "--json"], "canonical-device-review")
    if code:
        raise PairingRefused("canonical-device-review-failed")
    devices = json.loads(output)
    code, output = run(["python3", "-c", IDENTITY_QUERY], "canonical-local-identity")
    if code:
        raise PairingRefused("canonical-local-identity-read-failed")
    identities = json.loads(output)
    validate_request(devices, request, identities)
    code, _ = run(["openclaw", "devices", "approve", request, "--json"], "canonical-exact-scope-approval")
    if code:
        raise PairingRefused("canonical-scope-approval-failed")
    code, _ = run(RPC, "canonical-cli-admin-read-verified")
    if code:
        raise PairingRefused("canonical-admin-scope-verification-failed")
