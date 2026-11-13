#!/usr/bin/env python3
"""Reconcile OpenBot's embedded Hermes CLI with the local Home AI endpoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


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
    # Git 2.43 in the OpenBot image does not support Hermes' GIT_NO_LAZY_FETCH
    # guard. Its passive startup update check can therefore fetch a large
    # missing ancestry range and block ACP startup. Explicit update commands
    # remain available when passive checks are disabled.
    "updates.check": "false",
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


def disable_passive_update_checks(config_path: Path) -> None:
    """Disable only Hermes' passive update check, preserving the rest of config.yaml."""
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("OpenBot Hermes config.yaml is missing or is not a regular file")

    original = config_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    section_starts = [i for i, line in enumerate(lines) if re.fullmatch(r"updates:\s*(?:#.*)?\r?\n?", line)]
    if len(section_starts) > 1:
        raise RuntimeError("Hermes config.yaml has multiple top-level updates sections")

    if section_starts:
        start = section_starts[0]
        end = next(
            (i for i in range(start + 1, len(lines))
             if re.match(r"^[A-Za-z0-9_-]+:\s*(?:#.*)?(?:\r?\n)?$", lines[i])),
            len(lines),
        )
        checks = [i for i in range(start + 1, end) if re.match(r"^  check:\s*", lines[i])]
        if len(checks) > 1:
            raise RuntimeError("Hermes config.yaml has multiple updates.check settings")
        if checks:
            i = checks[0]
            replacement, count = re.subn(
                r"^(  check:\s*)(?:true|false)(\s*(?:#.*)?\r?\n?)$",
                r"\g<1>false\g<2>",
                lines[i],
                count=1,
                flags=re.IGNORECASE,
            )
            if count != 1:
                raise RuntimeError("Hermes updates.check is not a simple YAML boolean")
            lines[i] = replacement
        else:
            newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
            lines.insert(start + 1, f"  check: false{newline}")
    else:
        newline = "\r\n" if "\r\n" in original else "\n"
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.extend((f"updates:{newline}", f"  check: false{newline}"))

    updated = "".join(lines)
    if updated == original:
        return

    mode = stat.S_IMODE(config_path.stat().st_mode)
    fd, temp_name = tempfile.mkstemp(prefix=".config.yaml.", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp_file:
            temp_file.write(updated)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, config_path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


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

        data_root = run(
            "docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}',
            args.container,
        )
        expected_data_root = Path.home() / ".local/share/homecompute-openbot/data"
        if not data_root or Path(data_root).resolve() != expected_data_root.resolve():
            raise RuntimeError("OpenBot /data mount does not match the managed data directory")
        disable_passive_update_checks(Path(data_root) / ".hermes/config.yaml")

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
