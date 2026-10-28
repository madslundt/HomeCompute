#!/usr/bin/env python3
"""Prepare a private NemoClaw config candidate; never upload, install or deploy."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def prepare(base: dict[str, Any], overlay: dict[str, Any], *, gateway_port: int = 18789) -> dict[str, Any]:
    # These fields belong to the managed startup profile and OpenClaw native lifecycle.
    inference = base.get("models", {}).get("providers", {}).get("inference", {})
    if inference.get("baseUrl") != "https://inference.local/v1" or inference.get("apiKey") != "unused":
        raise ValueError("input is not the expected credential-free managed inference config")
    if set(base["models"]["providers"]) != {"inference"}:
        raise ValueError("unexpected additional model providers; review the managed input")
    if not 1024 <= gateway_port <= 65535:
        raise ValueError("expected gateway port must be a valid service port")
    if base.get("gateway", {}).get("mode") != "local" or base.get("gateway", {}).get("port") != gateway_port:
        raise ValueError("input does not preserve the managed internal gateway")
    if base.get("plugins", {}).get("entries", {}).get("nemoclaw", {}).get("enabled") is not True:
        raise ValueError("managed NemoClaw plugin is missing")
    result = merge(base, overlay)
    # Generated NemoClaw alsoAllow bundle-mcp conflicts with a finite allow list.
    result["tools"].pop("alsoAllow", None)
    # Managed main is implicit; 2026.9.1 treats explicit default markers as retired.
    result["agents"]["entries"]["main"].pop("default", None)
    if result["models"]["providers"]["inference"]["apiKey"] != "unused":
        raise ValueError("overlay introduces a real inference credential")
    for owned in ("auth", "port", "mode", "trustedProxies", "controlUi"):
        if result["gateway"].get(owned) != base["gateway"].get(owned):
            raise ValueError("overlay changed a NemoClaw-owned gateway field")
    if result.get("proxy") != base.get("proxy"):
        raise ValueError("overlay changed NemoClaw managed proxy routing")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-config", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, default=ROOT / "config/openclaw-nemoclaw.json")
    parser.add_argument("--output", type=Path, required=True, help="new private file; existing files are refused")
    parser.add_argument("--gateway-port", type=int, default=18789,
                        help="expected native gateway port from the reviewed managed profile")
    args = parser.parse_args()
    if args.managed_config.is_symlink() or args.overlay.is_symlink():
        parser.error("input symlinks are refused")
    candidate = prepare(json.loads(args.managed_config.read_text()), json.loads(args.overlay.read_text()),
                        gateway_port=args.gateway_port)
    # Exclusive creation avoids overwriting an active managed config accidentally.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(candidate, output, indent=2)
        output.write("\n")
    print("Private candidate prepared; no NemoClaw or OpenShell command was invoked.")


if __name__ == "__main__":
    main()
