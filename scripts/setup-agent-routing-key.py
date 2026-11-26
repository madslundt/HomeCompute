#!/usr/bin/env python3
"""Verify or provision a dedicated key for a declared agent-routing profile."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/srv/state/control-plane/agent-routing-keys")
SPEC = importlib.util.spec_from_file_location("lan_catalog", ROOT / "scripts/setup-lan-catalog-key.py")
assert SPEC and SPEC.loader
LITELLM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LITELLM)


def reconcile(profile, instance, apply):
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "home-core":
        raise RuntimeError("Run as root on home-core.")
    policy = json.loads(LITELLM.ROUTES_FILE.read_text(encoding="utf-8"))["agent_routing"]
    if profile not in policy["consumer_profiles"]:
        raise RuntimeError("Unknown routing profile.")
    principal = f"agent-routing-{profile}-{instance}"
    key = LITELLM.read_key(STATE / principal)
    if not key:
        raise RuntimeError("The externally supplied credential file is missing.")
    token_id = hashlib.sha256(key.encode()).hexdigest()
    models = sorted(policy["consumer_profiles"][profile])
    permission = {"mcp_servers": ["no-mcp-servers"]}
    routes = ["/v1/models", "/v1/chat/completions", "/v1/responses"]
    desired = {
        "key_alias": principal, "user_id": principal, "models": models,
        "rpm_limit": policy["key_requests_per_minute"], "tpm_limit": policy["key_tokens_per_minute"],
        "allowed_routes": routes, "object_permission": permission,
    }

    def load_key():
        result = LITELLM.api("/key/info", query={"key": token_id})
        return None if result is None else result["info"]

    info = load_key()
    if info is None:
        if not apply:
            raise RuntimeError("The declared key still needs provisioning.")
        if not set(models) <= LITELLM.list_models():
            raise RuntimeError("One or more profile routes are missing from the gateway.")
        LITELLM.api("/key/generate", body={**desired, "key": key})
        info = load_key()
    if info is None or any(
        info.get(field) != principal for field in ("key_alias", "user_id")
    ) or any(info.get(field) for field in ("team_id", "blocked", "expires", "aliases", "router_settings")):
        raise RuntimeError("Refusing to change an unexpected virtual key.")

    def matches(value):
        grants = value.get("object_permission") or {}
        return (sorted(value.get("models") or []) == models
                and set(value.get("allowed_routes") or []) == set(routes)
                and grants.get("mcp_servers") == permission["mcp_servers"]
                and not any(grants.get(field) for field in ("mcp_access_groups", "mcp_tool_permissions"))
                and value.get("rpm_limit") == desired["rpm_limit"]
                and value.get("tpm_limit") == desired["tpm_limit"])

    if not matches(info):
        raise RuntimeError("The virtual key differs from the declared scope. Rotate the dedicated key.")
    if LITELLM.list_models(key=key) != set(models):
        raise RuntimeError("The credential did not return its declared model catalog.")
    print(json.dumps({"principal": principal, "models": models, "verified": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("instance", help="stable consumer instance name")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.instance):
        parser.error("instance must be a lowercase name using letters, numbers, and hyphens")
    try:
        reconcile(args.profile, args.instance, args.apply)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print("Invalid routing key setup state.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
