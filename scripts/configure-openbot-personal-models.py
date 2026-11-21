#!/usr/bin/env python3
"""Reconcile model preferences in the owner container via OpenBot Local scripts."""

import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

AUTOMATION = Path("/data/.config/OpenBot/automation")


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


def set_embedded_effort(effort):
    command = ["/data/.local/bin/hermes", "config"]
    key = "agent.reasoning_effort"
    current = subprocess.check_output([*command, "get", key, "--json"], timeout=15).decode().strip()
    if current.strip('"') != effort:
        subprocess.run([*command, "set", key, effort], check=True, timeout=15,
                       stdout=subprocess.DEVNULL)
        current = subprocess.check_output([*command, "get", key, "--json"], timeout=15).decode().strip()
    if current.strip('"') != effort:
        raise ValueError("embedded Hermes did not retain its effort value")


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
        selected.append((agent, target))
    if len({agent["id"] for agent, _ in selected}) != len(targets):
        raise ValueError("the same agent matches two roles")
    if mode != "plan":
        set_embedded_effort("xhigh" if mode == "rollback" else model["reasoningEffort"])
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


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in {"plan", "apply", "rollback"}:
        raise SystemExit("usage: configure-openbot-personal-models.py CONFIG plan|apply|rollback")
    try:
        reconcile(json.loads(Path(sys.argv[1]).read_bytes()), client(), sys.argv[2])
    except HTTPError as exc:
        raise SystemExit(f"OpenBot returned HTTP {exc.code}; see its owner settings for details.") from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"OpenBot model reconciliation stopped ({type(exc).__name__}).") from None


if __name__ == "__main__":
    main()
