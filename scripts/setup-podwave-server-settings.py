#!/usr/bin/env python3
"""Migrate known invalid PodWave Home AI server TTS defaults in place."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


HOME_AI_BASE = "https://ai.home.arpa:8443/v1"
LEGACY_TTS_VOICE = "Seeded synthetic household voice"
REMOTE_CONFIG_SCRIPT = r'''import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8080/api/config", timeout=10) as response:
    config = json.load(response)
print(json.dumps(config, separators=(",", ":")))
'''


def migrate_updates(config: dict) -> dict[str, str]:
    """Return only safe migrations for the known Home AI TTS settings."""
    tts = config.get("tts")
    if not isinstance(tts, dict):
        return {}
    if tts.get("prov") != "openai" or str(tts.get("base", "")).rstrip("/") != HOME_AI_BASE:
        return {}

    updates: dict[str, str] = {}
    model = str(tts.get("model", ""))
    if model.strip().casefold() == "omnivoice" and model != "omnivoice":
        updates["model"] = "omnivoice"
    if str(tts.get("voiceA", "")).strip() == LEGACY_TTS_VOICE:
        updates["voiceA"] = "danish-default"
    if not str(tts.get("voiceB", "")).strip() and (updates.get("model", model) == "omnivoice"):
        updates["voiceB"] = "english-default"
    return updates


def docker_exec(container: str, code: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", container, "/app/.venv/bin/python", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("PodWave settings API request failed")
    return result.stdout.strip()


def reconcile(container: str, runner=docker_exec) -> dict:
    raw = runner(container, REMOTE_CONFIG_SCRIPT)
    try:
        config = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("PodWave returned an invalid settings response") from exc
    updates = migrate_updates(config)
    if not updates:
        return {"changed": False, "fields": []}

    body = json.dumps({"tts": updates}, separators=(",", ":"))
    update_script = f'''import json, urllib.request
body = {body!r}.encode()
request = urllib.request.Request("http://127.0.0.1:8080/api/config", data=body,
    headers={{"Content-Type": "application/json"}}, method="PUT")
with urllib.request.urlopen(request, timeout=10) as response:
    json.load(response)
'''
    runner(container, update_script)
    verified = json.loads(runner(container, REMOTE_CONFIG_SCRIPT))
    verified_tts = verified.get("tts", {})
    if any(verified_tts.get(field) != value for field, value in updates.items()):
        raise RuntimeError("PodWave did not retain the migrated TTS settings")
    return {"changed": True, "fields": sorted(updates)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="PodWave container ID from Compose")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12,64}", args.container):
        raise SystemExit("PodWave container ID is invalid")
    print(json.dumps(reconcile(args.container), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"PodWave server settings migration refused ({exc})", file=sys.stderr)
        raise SystemExit(1) from None
