#!/usr/bin/env python3
"""Reconcile OpenBot's embedded Hermes CLI with the local Home AI endpoint."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


CONTAINER_DEFAULT = "homecompute-openbot"
HERMES = "/data/.local/bin/hermes"
PROVIDER = {
    "name": "Home AI (local)",
    "base_url": "http://ai.home.arpa:18080/v1",
    "model": "automation-moe",
    "api_mode": "chat_completions",
}
MODEL_SETTINGS = {
    "model.provider": "custom",
    "model.default": "automation-moe",
    "model.base_url": PROVIDER["base_url"],
    "model.api_mode": "chat_completions",
    "agent.reasoning_effort": "xhigh",
}


def run(*args: str) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def has_secret_fields(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if any(part in normalized for part in ("apikey", "token", "secret", "password", "header")):
                if child not in (None, "", [], {}):
                    return True
            if has_secret_fields(child):
                return True
    elif isinstance(value, list):
        return any(has_secret_fields(item) for item in value)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default=CONTAINER_DEFAULT)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.container):
        parser.error("container must be a simple Docker container name")

    try:
        state = run("docker", "inspect", "--format", "{{.State.Running}}", args.container)
        if state != "true":
            raise RuntimeError(f"Docker container {args.container} is not running")
        run("docker", "exec", args.container, HERMES, "--version")

        raw = run("docker", "exec", args.container, HERMES, "config", "get", "custom_providers", "--json")
        providers = json.loads(raw)
        if not isinstance(providers, list):
            raise RuntimeError("Hermes custom_providers must be a JSON list")

        matches = [row for row in providers if isinstance(row, dict) and row.get("name") == PROVIDER["name"]]
        if len(matches) > 1:
            raise RuntimeError("multiple Home AI (local) entries exist; resolve them manually")
        if matches:
            current = matches[0]
            if has_secret_fields(current):
                raise RuntimeError("Home AI (local) entry contains credential fields; refusing to rewrite it")
            if any(current.get(key) != value for key, value in PROVIDER.items()):
                others = [row for row in providers if row is not current]
                if has_secret_fields(others):
                    raise RuntimeError("other custom providers contain credentials; refusing to rewrite their configuration")
                current.update(PROVIDER)
                run("docker", "exec", args.container, HERMES, "config", "set", "custom_providers", json.dumps(providers))
        else:
            if has_secret_fields(providers):
                raise RuntimeError("other custom providers contain credentials; refusing to rewrite their configuration")
            providers.append(PROVIDER)
            run("docker", "exec", args.container, HERMES, "config", "set", "custom_providers", json.dumps(providers))

        for key, value in MODEL_SETTINGS.items():
            run("docker", "exec", args.container, HERMES, "config", "set", key, value)

        for key, expected in MODEL_SETTINGS.items():
            actual = run("docker", "exec", args.container, HERMES, "config", "get", key)
            if actual != expected:
                raise RuntimeError(f"Hermes did not retain the expected value for {key}")
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"configure-openbot-hermes: {exc}", file=sys.stderr)
        return 1

    print("OpenBot Hermes is configured for Home AI (local)/automation-moe at xhigh.")
    print("No API key is set; the endpoint uses the local Caddy gateway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
