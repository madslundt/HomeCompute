#!/usr/bin/env python3
"""Render Paseo's host configuration and reconcile its dedicated LiteLLM key."""

import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/var/lib/homecompute/paseo")
KEY = STATE / "inference-key"
OWNER = "paseo-inference"
INFERENCE_UID = 12075
GATEWAY = runpy.run_path(str(ROOT / "scripts/setup-lan-catalog-key.py"))
ROUTES = [route for route in GATEWAY["ROUTES"] if route != "/v1/completions"]
ROUTES.extend(["/v1/videos/{video_id}", "/v1/videos/{video_id}/content"])


def read_key():
    metadata = KEY.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_gid != INFERENCE_UID or stat.S_IMODE(metadata.st_mode) != 0o640):
        raise ValueError("Unsafe Paseo LiteLLM credential file")
    key = KEY.read_text().strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]+", key):
        raise ValueError("Invalid Paseo LiteLLM credential file")
    return key


def write_private_file(path, contents, *, mode=0o600, gid=0, replace=True):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8") as temp:
        temp.write(contents)
        temp.flush()
        descriptor = temp.fileno()
        os.fsync(descriptor)
        os.fchown(descriptor, 0, gid)
        os.fchmod(descriptor, mode)
        if replace:
            # Keep the temporary file handle valid after renaming its directory entry.
            linked = Path(temp.name + ".ready")
            os.link(temp.name, linked)
            linked.replace(path)
        else:
            os.link(temp.name, path)


def provision(models):
    api = GATEWAY["api"]
    try:
        key = read_key()
    except FileNotFoundError:
        write_private_file(KEY, "sk-" + secrets.token_urlsafe(32) + "\n",
                           mode=0o640, gid=INFERENCE_UID, replace=False)
        key = read_key()
    token_id = hashlib.sha256(key.encode()).hexdigest()
    permission = GATEWAY["MCP_POLICY"]
    desired = {
        "key_alias": OWNER, "user_id": OWNER, "models": sorted(models),
        "allowed_routes": ROUTES, "object_permission": permission,
    }

    def load():
        result = api("/key/info", query={"key": token_id})
        return result["info"] if result else None

    info = load()
    if info is None:
        api("/key/generate", body={**desired, "key": key})
        info = load()
    if info is None or any(info.get(field) != OWNER for field in ("key_alias", "user_id")):
        raise RuntimeError("Paseo credential ownership could not be verified")
    grants = info.get("object_permission") or {}
    if (set(info.get("allowed_routes") or []) != set(ROUTES)
            or any(info.get(field) for field in ("blocked", "team_id", "expires", "aliases"))
            or grants.get("mcp_servers") != permission["mcp_servers"]
            or any(grants.get(field) for field in ("mcp_access_groups", "mcp_tool_permissions"))):
        raise RuntimeError("Paseo credential has unexpected route or MCP permissions")
    if set(info.get("models") or []) != models:
        api("/key/update", body={"key": token_id, "models": sorted(models)})
    if GATEWAY["list_models"](key=key) != models:
        raise RuntimeError("Paseo credentials did not return the expected catalog")


def model_groups(models):
    policy = json.loads((ROOT / "config/capability-routes.json").read_text())
    special = set().union(*(policy[group].keys() for group in (
        "transcription_routes", "tts_routes", "video_routes",
    )))
    special.add("embedding")
    return models - special, special & models


def render_settings(models):
    text, _ = model_groups(models)
    required = {"automation-moe", "local-only", "stt-danish", "omnivoice"}
    if not required <= models:
        raise ValueError("Paseo's default model routes are unavailable")
    settings = json.loads((ROOT / "deploy/paseo/settings.json").read_text())
    teams = json.loads((ROOT / "deploy/orca/teams.json").read_text())
    profiles = [
        {"id": "developer", "name": "Developer", "provider": "codex",
         "model": "automation-moe", "modeId": "auto",
         "notes": "Implement and test assigned issues in a dedicated worktree."},
        {"id": "hermes-review", "name": "Hermes Code Review", "provider": "hermes",
         "model": "automation-moe",
         "notes": "Read and search the implementation worktree for independent review findings."},
        {"id": "qa", "name": "QA", "provider": "opencode", "model": "homecompute/local-only",
         "modeId": "plan", "notes": "Review acceptance criteria, test evidence and missing cases."},
        {"id": "designer", "name": "Designer", "provider": "opencode", "model": "homecompute/local-only",
         "modeId": "plan", "notes": teams["specialists"]["designer"]["instructions"].replace(
             "Use Orca headless browser automation on a loopback fixture. ", "")},
    ]
    for project in teams["projects"]:
        profiles.append({
            "id": project["id"] + "-po", "name": project["po"], "provider": "opencode",
            "model": "homecompute/local-only", "modeId": "plan", "notes": project["context"],
        })
    settings["daemon"]["agentProfiles"] = profiles
    endpoint = "http://inference:18080/v1"
    chat = text - {alias for alias in text if alias.startswith("chatgpt-")}
    opencode = {
        "autoupdate": False, "share": "disabled", "model": "homecompute/local-only",
        "small_model": "homecompute/local-only",
        "providers": {"homecompute": {
            "name": "HomeCompute LiteLLM",
            "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"apiKey": "local", "baseURL": endpoint, "encoding": "none",
                         "headerTimeout": 610_000, "chunkTimeout": False},
            "models": {model: {"name": model, "tool_call": True} for model in sorted(chat)},
        }},
        "experimental": {"policies": [
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": "homecompute", "effect": "allow"},
        ]},
        "permissions": [
            {"action": "shell", "resource": "*", "effect": "ask"},
            {"action": "shell", "resource": "git push *", "effect": "deny"},
            {"action": "external_directory", "resource": "*", "effect": "ask"},
        ],
    }
    codex = json.loads((ROOT / "deploy/paseo/codex-local-models.json").read_text())
    base = codex["models"][0]
    codex["models"] = [
        {**base, "slug": model, "display_name": model, "priority": int(model != "automation-moe")}
        for model in sorted(text)
    ]
    return settings, opencode, codex


def main():
    models = GATEWAY["list_models"]()
    configs = render_settings(models)
    provision(models)
    for filename, config in zip(("config.json", "opencode.json", "codex-models.json"), configs, strict=True):
        write_private_file(STATE / filename, json.dumps(config, indent=2) + "\n",
                           mode=0o640, gid=12074)
    print(f"Verified the dedicated Paseo credential for {len(models)} LiteLLM aliases.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        raise SystemExit("Paseo configuration or LiteLLM credential verification failed.") from None
