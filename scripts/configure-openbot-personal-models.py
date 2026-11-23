#!/usr/bin/env python3
"""Reconcile model preferences in the owner container via OpenBot Local scripts."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

AUTOMATION = Path("/data/.config/OpenBot/automation")
OWNER_HERMES_ID = "agent-f1f9627d-4479-4c4e-8b26-927e11f1c740"
HERMES_SOL_TARGETS = ("hermes-sol-owner", "hermes-sol-partner")
SOL_SETTINGS = {
    "provider": "opencode", "model": "homecompute-chatgpt/chatgpt-sol-latest",
    "reasoningEffort": "high",
}
HERMES_SOL_KEY_ENV = "HOME_OPENBOT_CHATGPT_API_KEY"
HERMES_SOL_PROVIDER = {
    "name": "HomeCompute Sol",
    "base_url": "https://llm.home.arpa/v1",
    "default_model": "chatgpt-sol-latest",
    "api_mode": "chat_completions",
    "discover_models": False,
    "key_env": HERMES_SOL_KEY_ENV,
}
HERMES_SOL_PROVIDER_KEY = "providers.homecompute-sol"
HERMES_ENV = Path("/data/.hermes/.env")
HERMES_SOL_SETTINGS = {
    "provider": "hermes", "model": "custom:homecompute-sol:chatgpt-sol-latest",
    "reasoningEffort": "high",
}
LOCAL_SETTINGS = {
    "provider": "hermes", "model": "custom:automation-moe", "reasoningEffort": "xhigh",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def client():
    url = (AUTOMATION / "url").read_text().strip()
    if not re.fullmatch(r"http://127[.]0[.]0[.]1:[0-9]{1,5}", url):
        raise ValueError("expected OpenBot's loopback automation URL")
    token = (AUTOMATION / "token").read_text().strip()
    if not token or re.search(r"\s", token):
        raise ValueError("invalid automation credential")
    opener = build_opener(ProxyHandler({}), NoRedirect)

    def request(path, data=None):
        headers = {"Authorization": f"Bearer {token}"}
        payload = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode()
        with opener.open(Request(url + path, headers=headers, data=payload), timeout=15) as response:
            return json.load(response)

    return request


def reconcile(config, request, mode):
    roster = request("/v1/agents")
    targets = config["fast_local_agents"]
    model = config["model"]
    if config["container"] != "homecompute-openbot" or model != {
        "provider": "hermes", "model": "custom:automation-moe", "reasoningEffort": "none"
    }:
        raise ValueError("expected the owner local model configuration")
    selected = []
    for target in targets:
        matches = [row for row in roster if row["name"] in target["existing_names"]]
        if len(matches) != 1 or not re.fullmatch(r"agent-[0-9a-f-]{36}", matches[0]["id"]):
            raise ValueError("agent identity changed or Local scripts is unavailable")
        agent, = matches
        if agent["id"] == OWNER_HERMES_ID:
            raise ValueError("owner Hermes now has a separate model lifecycle")
        selected.append((agent, target))
    if len({agent["id"] for agent, _ in selected}) != len(targets):
        raise ValueError("the same agent matches two roles")
    for agent, target in selected:
        agent_id = agent["id"]
        settings = target["rollback_model"] if mode == "rollback" else model
        summary = {"agent": agent["name"], **settings}
        if mode != "plan":
            updated = request(f"/v1/agents/{agent_id}/settings", settings)
            if updated.get("id") != agent_id or any(updated.get(k) != v for k, v in settings.items()):
                raise ValueError("OpenBot did not confirm the updated preferences")
            summary["result"] = "verified"
        else:
            summary["result"] = "planned"
        print(json.dumps(summary), flush=True)


def install_sol_key(path, key):
    previous = b""
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("expected a private Hermes environment file")
        previous = path.read_bytes()
    name = HERMES_SOL_KEY_ENV.encode()
    assignment = name + b"=" + key
    lines = previous.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if re.match(rb"[ \t]*(?:export[ \t]+)?" + name + rb"[ \t]*=", line)]
    if len(matches) > 1:
        raise ValueError("multiple Hermes Sol credential bindings")
    if matches:
        if lines[matches[0]].rstrip(b"\r\n") == assignment:
            return
        lines[matches[0]] = assignment + b"\n"
        updated = b"".join(lines)
    else:
        updated = previous + (b"\n" if previous and not previous.endswith(b"\n") else b"")
        updated += assignment + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".env.", delete=False) as output:
        draft = Path(output.name)
        try:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
            draft.replace(path)
        finally:
            draft.unlink(missing_ok=True)


def configure_owner_sol_provider():
    key_line = sys.stdin.buffer.read(4097)
    if not re.fullmatch(rb"sk-[A-Za-z0-9_-]{1,4092}\n", key_line):
        raise ValueError("expected the scoped OpenBot model credential on stdin")
    key = key_line[:-1]

    def hermes_config(*args):
        return subprocess.run(
            ["/data/.local/bin/hermes", "config", *args],
            text=True, capture_output=True, timeout=20,
        )

    def read_provider():
        result = hermes_config("get", HERMES_SOL_PROVIDER_KEY, "--json")
        if result.returncode == 1 and f"Config key not set: {HERMES_SOL_PROVIDER_KEY}" in result.stderr.splitlines():
            return None
        result.check_returncode()
        return json.loads(result.stdout)

    current = read_provider()
    if current not in (None, HERMES_SOL_PROVIDER):
        raise ValueError("existing Hermes Sol provider differs from the source declaration")
    opener = build_opener(ProxyHandler({}), NoRedirect)
    request = Request(HERMES_SOL_PROVIDER["base_url"] + "/models",
                      headers={"Authorization": f"Bearer {key.decode()}"})
    with opener.open(request, timeout=10) as response:
        if HERMES_SOL_PROVIDER["default_model"] not in {row["id"] for row in json.load(response)["data"]}:
            raise ValueError("ChatGPT Sol is missing from the private gateway")
    install_sol_key(HERMES_ENV, key)
    if current is None:
        hermes_config("set", HERMES_SOL_PROVIDER_KEY, json.dumps(HERMES_SOL_PROVIDER)).check_returncode()
    if read_provider() != HERMES_SOL_PROVIDER:
        raise ValueError("Hermes did not retain the Sol provider declaration")


def reconcile_hermes_sol(target, request, mode, setup_owner_provider=configure_owner_sol_provider):
    roster = request("/v1/agents")
    matches = [row["id"] for row in roster if row["name"] == "Hermes"]
    if len(matches) != 1 or not re.fullmatch(r"agent-[0-9a-f-]{36}", matches[0]):
        raise ValueError("expected the existing Hermes on the selected server")
    agent_id, = matches
    owner = target == "hermes-sol-owner"
    if owner != (agent_id == OWNER_HERMES_ID):
        raise ValueError("the requested server does not match the Hermes identity")
    if owner:
        settings = SOL_SETTINGS if mode == "rollback" else HERMES_SOL_SETTINGS
        if mode == "apply":
            setup_owner_provider()
    else:
        settings = LOCAL_SETTINGS if mode == "rollback" else SOL_SETTINGS
    if mode != "plan":
        updated = request(f"/v1/agents/{agent_id}/settings", settings)
        if updated.get("id") != agent_id or any(updated.get(k) != v for k, v in settings.items()):
            raise ValueError("OpenBot did not confirm the updated Hermes model")
    print(json.dumps({"target": target, "operation": mode, **settings}), flush=True)


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in {"plan", "apply", "rollback"}:
        raise SystemExit("usage: configure-openbot-personal-models.py CONFIG plan|apply|rollback")
    try:
        if sys.argv[1] in HERMES_SOL_TARGETS:
            reconcile_hermes_sol(sys.argv[1], client(), sys.argv[2])
        else:
            reconcile(json.loads(Path(sys.argv[1]).read_bytes()), client(), sys.argv[2])
    except HTTPError as exc:
        raise SystemExit(f"OpenBot returned HTTP {exc.code}; see its owner settings for details.") from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"OpenBot model reconciliation stopped ({type(exc).__name__}).") from None


if __name__ == "__main__":
    main()
