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
HERMES_CHATGPT_CATALOG_TARGET = "hermes-chatgpt-catalog"
HERMES_CHATGPT_BASE_URL = "https://llm.home.arpa/v1"
HERMES_CHATGPT_PROVIDERS = {
    f"homecompute-{family.lower()}": {
        "name": f"HomeCompute {family}",
        "base_url": HERMES_CHATGPT_BASE_URL,
        "default_model": f"chatgpt-{family.lower()}-latest",
        "api_mode": "chat_completions",
        "discover_models": False,
        "key_env": HERMES_SOL_KEY_ENV,
    }
    for family in ("Sol", "Luna", "Terra", "Astra")
}
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


def configure_owner_chatgpt_providers(mode="apply"):
    key_line = sys.stdin.buffer.read(4097)
    if not re.fullmatch(rb"sk-[A-Za-z0-9_-]{1,4092}\n", key_line):
        raise ValueError("expected the scoped OpenBot model credential on stdin")
    key = key_line[:-1]

    def hermes_config(*args):
        return subprocess.run(
            ["/data/.local/bin/hermes", "config", *args],
            text=True, capture_output=True, timeout=20, check=True,
        )

    def read_providers():
        try:
            result = hermes_config("get", "providers", "--json")
        except subprocess.CalledProcessError as error:
            if error.returncode == 1 and "Config key not set: providers" in error.stderr.splitlines():
                return {}
            raise
        return json.loads(result.stdout)

    declared = HERMES_CHATGPT_PROVIDERS
    current = read_providers()
    if any(name in current and current[name] != provider for name, provider in declared.items()):
        raise ValueError("existing Hermes ChatGPT provider differs from the source declaration")
    opener = build_opener(ProxyHandler({}), NoRedirect)
    request = Request(HERMES_CHATGPT_BASE_URL + "/models",
                      headers={"Authorization": f"Bearer {key.decode()}"})
    models = {provider["default_model"] for provider in declared.values()}
    with opener.open(request, timeout=10) as response:
        if models != {row["id"] for row in json.load(response)["data"]}:
            raise ValueError("the OpenBot credential must expose exactly the four ChatGPT aliases")

    missing = {name: provider for name, provider in declared.items() if name not in current}
    if mode == "apply":
        install_sol_key(HERMES_ENV, key)
        for name, provider in missing.items():
            hermes_config("set", f"providers.{name}", json.dumps(provider))
        verified = read_providers()
        if any(verified.get(name) != provider for name, provider in declared.items()):
            raise ValueError("Hermes did not retain the ChatGPT provider declarations")
    pending = {provider["default_model"] for provider in missing.values()} if mode == "plan" else set()
    return {"operation": mode, "configured": sorted(models - pending), "to_add": sorted(pending)}


def reconcile_hermes_sol(target, request, mode, setup_owner_provider=configure_owner_chatgpt_providers):
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
        if sys.argv[1] == HERMES_CHATGPT_CATALOG_TARGET:
            if sys.argv[2] == "rollback":
                raise ValueError("the catalog supports plan and apply")
            print(json.dumps(configure_owner_chatgpt_providers(sys.argv[2])), flush=True)
        elif sys.argv[1] in HERMES_SOL_TARGETS:
            reconcile_hermes_sol(sys.argv[1], client(), sys.argv[2])
        else:
            reconcile(json.loads(Path(sys.argv[1]).read_bytes()), client(), sys.argv[2])
    except HTTPError as exc:
        raise SystemExit(f"OpenBot returned HTTP {exc.code}; see its owner settings for details.") from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"OpenBot model reconciliation stopped ({type(exc).__name__}).") from None


if __name__ == "__main__":
    main()
