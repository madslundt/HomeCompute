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
PARTNER_CONTAINER = "homecompute-openbot-partner"
PARTNER_DATA = Path("/var/lib/homecompute-openbot-partner/data")
PARTNER_MODEL_URL = "http://ai.home.arpa:18080/v1"
PARTNER_PROFILE_MAX_CHARS = 1300
HERMES = "/data/.local/bin/hermes"
COMPRESSION_KEY = "compression.threshold_tokens"
COMPRESSION_THRESHOLD_TOKENS = 128_000
PROVIDER = {
    "name": "Home AI (local)",
    "base_url": "https://llm.home.arpa/v1",
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


def reconcile_compression(container: str) -> None:
    cli = ("docker", "exec", container, HERMES, "config")
    current = json.loads(run(*cli, "get", COMPRESSION_KEY, "--json"))
    if current != COMPRESSION_THRESHOLD_TOKENS:
        run(*cli, "set", COMPRESSION_KEY, str(COMPRESSION_THRESHOLD_TOKENS))
        current = json.loads(run(*cli, "get", COMPRESSION_KEY, "--json"))
    if current != COMPRESSION_THRESHOLD_TOKENS:
        raise RuntimeError(f"Hermes did not retain the expected value for {COMPRESSION_KEY}")


def install_user_profile(home: Path, profile: str) -> None:
    memories = home / "memories"
    path = memories / "USER.md"
    if memories.is_symlink() or path.is_symlink():
        raise RuntimeError("Partner Hermes memory path must not contain symlinks")
    memories.mkdir(mode=0o700, exist_ok=True)
    descriptor, draft = tempfile.mkstemp(prefix=".USER.", dir=memories)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(profile)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(draft, path)
        except FileExistsError:
            if not path.is_file() or path.is_symlink() or path.read_text(encoding="utf-8") != profile:
                raise RuntimeError("Partner Hermes already has a user profile. Review changes in Hermes.") from None
    finally:
        os.unlink(draft)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default=CONTAINER_DEFAULT)
    parser.add_argument("--compression-only", action="store_true",
                        help="Reconcile only the embedded Hermes compression threshold.")
    parser.add_argument("--install-user-profile", action="store_true",
                        help="Read a reviewed partner Hermes profile from stdin.")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.container):
        parser.error("container must be a simple Docker container name")
    partner = args.container == PARTNER_CONTAINER
    if args.install_user_profile and (not partner or args.compression_only):
        parser.error("--install-user-profile requires the partner container and full model reconciliation")

    try:
        profile = None
        if args.install_user_profile:
            raw_profile = sys.stdin.read(PARTNER_PROFILE_MAX_CHARS + 2)
            profile = raw_profile.removesuffix("\n").strip()
            if not profile or len(raw_profile) > PARTNER_PROFILE_MAX_CHARS + 1 or "\x00" in profile:
                raise RuntimeError("Provide a reviewed nonempty UTF-8 profile of at most 1300 characters")
            profile += "\n"
        state = run("docker", "inspect", "--format", "{{.State.Running}}", args.container)
        if state != "true":
            raise RuntimeError(f"Docker container {args.container} is not running")

        data_root = run(
            "docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}',
            args.container,
        )
        expected_data_root = PARTNER_DATA if partner else Path.home() / ".local/share/homecompute-openbot/data"
        if not data_root or Path(data_root).resolve() != expected_data_root.resolve() or expected_data_root.is_symlink():
            raise RuntimeError("OpenBot /data mount does not match the managed data directory")
        if args.compression_only:
            reconcile_compression(args.container)
            print(f"OpenBot Hermes compression threshold: {COMPRESSION_THRESHOLD_TOKENS} tokens.")
            return 0

        provider = dict(PROVIDER)
        model_settings = dict(MODEL_SETTINGS)
        if partner:
            provider["base_url"] = model_settings["model.base_url"] = PARTNER_MODEL_URL

        # Check the actual container trust store and keyless path before changing settings.
        probe = f"""
import json, urllib.request
with urllib.request.urlopen({provider['base_url'] + '/models'!r}, timeout=8) as response:
    assert {provider['model']!r} in {{row['id'] for row in json.load(response)['data']}}
"""
        run("docker", "exec", args.container, "python3", "-c", probe)
        config_path = Path(data_root) / ".hermes/config.yaml"
        if partner:
            run("docker", "exec", args.container, "test", "-x", HERMES)
            if config_path.parent.is_symlink():
                raise RuntimeError("Partner Hermes home must be a private directory")
            config_path.parent.mkdir(mode=0o700, exist_ok=True)
            try:
                with os.fdopen(os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as config:
                    config.write("updates:\n  check: false\n")
            except FileExistsError:
                pass
        disable_passive_update_checks(config_path)

        run("docker", "exec", args.container, HERMES, "--version")

        raw = run("docker", "exec", args.container, HERMES, "config", "get", "custom_providers", "--json")
        providers = json.loads(raw)
        if not isinstance(providers, list):
            raise RuntimeError("Hermes custom_providers must be a JSON list")

        matches = [row for row in providers if isinstance(row, dict) and row.get("name") == provider["name"]]
        if len(matches) > 1:
            raise RuntimeError("multiple Home AI (local) entries exist; resolve them manually")
        if matches:
            current = matches[0]
            if has_secret_fields(current):
                raise RuntimeError("Home AI (local) entry contains credential fields; refusing to rewrite it")
            if any(current.get(key) != value for key, value in provider.items()):
                others = [row for row in providers if row is not current]
                if has_secret_fields(others):
                    raise RuntimeError("other custom providers contain credentials; refusing to rewrite their configuration")
                current.update(provider)
                run("docker", "exec", args.container, HERMES, "config", "set", "custom_providers", json.dumps(providers))
        else:
            if has_secret_fields(providers):
                raise RuntimeError("other custom providers contain credentials; refusing to rewrite their configuration")
            providers.append(provider)
            run("docker", "exec", args.container, HERMES, "config", "set", "custom_providers", json.dumps(providers))

        for key, value in model_settings.items():
            run("docker", "exec", args.container, HERMES, "config", "set", key, value)

        for key, expected in model_settings.items():
            actual = run("docker", "exec", args.container, HERMES, "config", "get", key)
            if actual != expected:
                raise RuntimeError(f"Hermes did not retain the expected value for {key}")

        reconcile_compression(args.container)
        if profile is not None:
            install_user_profile(config_path.parent, profile)
            print("Reviewed partner profile installed in private Hermes memory.")
    except (OSError, subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"configure-openbot-hermes: {exc}", file=sys.stderr)
        return 1

    print("OpenBot Hermes is configured for Home AI (local)/automation-moe at xhigh.")
    print("No API key is set; the endpoint uses the local Caddy gateway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
