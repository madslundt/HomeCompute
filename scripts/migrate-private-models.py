#!/usr/bin/env python3
"""Plan, apply, or reverse the reviewed private model bindings."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


LEGACY = "automation-moe"
MODEL = "local-only"
HERMES = Path("/sandbox/.hermes/config.yaml")
RECEIPT = HERMES.with_name(".homecompute-private-model-preimage")
OPENBOT = Path("/data/.config/OpenBot")
AGENTS = {
    "agent-6bb5d9d0-3444-4291-82cf-da70c8a42037": ("Home Planner", "custom:automation-moe", "xhigh"),
    "agent-f1f9627d-4479-4c4e-8b26-927e11f1c740": ("Hermes", "custom:homecompute-sol:chatgpt-sol-latest", "high"),
}
AULA_WORKFLOW = "O0JTIc9aqA7OkrQS"
AULA_NODE = "d32b918c-53b9-44f5-9e2a-2c927e4e0002"
AULA_CREDENTIAL = "HomeCompute LiteLLM OpenAI"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_private(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError("Expected an owner-private regular file.")
    return path.read_bytes()


def atomic_write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent) as staged:
        staged.write(value)
        staged.flush()
        os.fsync(staged.fileno())
        # The staged inode stays private even when the destination is replaced.
        draft = Path(staged.name + ".ready")
        os.link(staged.name, draft)
        try:
            os.replace(draft, path)
        finally:
            draft.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def hermes_candidate(original):
    import yaml
    import re

    data = yaml.safe_load(original)
    model = data["model"]
    if (model.get("provider") != "custom" or model.get("default") not in {LEGACY, MODEL}
            or model.get("base_url") != "http://ai.home.arpa:18080/v1"
            or model.get("api_mode") != "chat_completions"
            or data.get("fallback_model") or data.get("fallback_models")):
        raise ValueError("Unexpected Hermes model binding or fallback.")
    lines = original.decode().splitlines(keepends=True)
    section = lines.index("model:\n")
    for i in range(section + 1, len(lines)):
        if re.match(r"^[^ #\r\n]", lines[i]):
            break
        if re.fullmatch(r"  default: (automation-moe|local-only)\n", lines[i]):
            lines[i] = f"  default: {MODEL}\n"
            result = "".join(lines).encode()
            expected = yaml.safe_load(original)
            expected["model"]["default"] = MODEL
            if yaml.safe_load(result) == expected:
                return result
            break
    raise ValueError("Cannot locate the exact Hermes model field.")


def hermes(mode, expected):
    old = read_private(HERMES)
    new = hermes_candidate(old)
    if mode == "plan":
        return {"preimage": digest(old), "changed": old != new}
    if mode == "rollback":
        prior = read_private(RECEIPT)
        if digest(old) != expected or hermes_candidate(prior) != old:
            raise ValueError("Hermes changed since application.")
        atomic_write(HERMES, prior)
        RECEIPT.unlink()
        return {"model": LEGACY}
    if old == new:
        return {"model": MODEL, "postimage": digest(new)}
    if digest(old) != expected:
        raise ValueError("Reviewed Hermes preimage required.")
    if RECEIPT.exists() or RECEIPT.is_symlink():
        if read_private(RECEIPT) != old:
            raise ValueError("Existing Hermes rollback preimage differs.")
    else:
        with os.fdopen(os.open(RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as output:
            output.write(old)
            output.flush()
            os.fsync(output.fileno())
    atomic_write(HERMES, new)
    return {"model": MODEL, "postimage": digest(new)}


def openbot_request(path, body=None):
    import re

    home = OPENBOT / "automation"
    url = (home / "url").read_text().strip()
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", url):
        raise ValueError("Expected the OpenBot loopback automation endpoint.")
    token = (home / "token").read_text().strip()
    opener = build_opener(ProxyHandler({}), NoRedirect)
    req = Request(url + path, None if body is None else json.dumps(body).encode(),
                  {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with opener.open(req, timeout=15) as handle:
        return json.load(handle)


def openbot(mode, agent_id):
    name, old_model, effort = AGENTS[agent_id]
    if {"id": agent_id, "name": name} not in (
        {k: a[k] for k in ("id", "name")} for a in openbot_request("/v1/agents")
    ):
        raise ValueError("OpenBot agent identity or Local scripts grant differs.")
    before = {"provider": "hermes", "model": old_model, "reasoningEffort": effort}
    local = {"provider": "hermes", "model": "custom:" + LEGACY, "reasoningEffort": "xhigh"}
    after = {**local, "model": "custom:" + MODEL}
    with sqlite3.connect(f"file:{OPENBOT / 'openbot.db'}?mode=ro", uri=True) as db:
        row = db.execute("SELECT agent_json FROM projection_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    raw = json.loads(row[0])
    current = {k: raw.get(k) for k in before}
    if current not in (before, local, after):
        raise ValueError("OpenBot model settings differ from the reviewed binding.")
    target = local if mode == "rollback" else after
    if mode != "plan" and target != current:
        reply = openbot_request(f"/v1/agents/{agent_id}/settings", target)
        if reply.get("id") != agent_id or any(reply.get(k) != v for k, v in target.items()):
            raise ValueError("OpenBot did not confirm the requested model.")
    return {"agent": name, "model": current["model"] if mode == "plan" else target["model"]}


def aula_operations(workflow, mode):
    if (workflow.get("id") != AULA_WORKFLOW or workflow.get("versionId") != workflow.get("activeVersionId")
            or (workflow.get("activeVersion") or {}).get("sameAsDraft") is False):
        raise ValueError("Expected the published Aula workflow with no pending draft.")
    selected = [node for node in workflow["nodes"] if node.get("id") == AULA_NODE]
    if len(selected) != 1:
        raise ValueError("Expected one Aula reviewer.")
    node, = selected
    models = node["parameters"]["model"]
    if (node.get("name") != "Local Aula summary reviewer"
            or node.get("type") != "@n8n/n8n-nodes-langchain.lmChatOpenAi"
            or node["credentials"]["openAiApi"]["name"] != AULA_CREDENTIAL
            or node["parameters"]["options"].get("maxRetries") != 0
            or models.get("mode") != "id" or models.get("value") not in (LEGACY, MODEL)
            or models.get("cachedResultName") != models["value"]):
        raise ValueError("Aula reviewer credential, node or model settings differ.")
    options = node["parameters"]["options"]
    provider_options = options.get("extraBody")
    if not isinstance(provider_options, str):
        raise ValueError("Expected Aula reviewer extraBody JSON.")
    overrides = json.loads(provider_options)
    legacy_options = {"chat_template_kwargs": {"enable_thinking": False}}
    expected = legacy_options if models["value"] == LEGACY else {}
    if overrides != expected:
        raise ValueError("Unexpected Aula reviewer provider overrides.")
    target = LEGACY if mode == "rollback" else MODEL
    if models["value"] == target:
        return []
    return [
        {"type": "setNodeParameter", "nodeName": node["name"], "path": "model",
         "value": {**models, "value": target, "cachedResultName": target}},
        {"type": "setNodeParameter", "nodeName": node["name"], "path": "options.extraBody",
         "value": json.dumps(legacy_options if mode == "rollback" else {})},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("consumer", choices=("hermes", "openbot", "aula"))
    parser.add_argument("mode", choices=("plan", "apply", "rollback"))
    parser.add_argument("--expected")
    parser.add_argument("--agent", choices=AGENTS)
    args = parser.parse_args()
    try:
        if args.consumer == "hermes":
            value = hermes(args.mode, args.expected)
        elif args.consumer == "openbot":
            if args.agent is None:
                parser.error("--agent is required for OpenBot")
            value = openbot(args.mode, args.agent)
        else:
            value = aula_operations(json.load(sys.stdin), args.mode)
        print(json.dumps(value))
    except HTTPError as error:
        raise SystemExit(f"Consumer API refused the operation with HTTP {error.code}.") from None
    except (ValueError, KeyError, IndexError, OSError, sqlite3.Error) as error:
        # Do not render service payloads or YAML parsing exceptions.
        msg = str(error) if type(error) is ValueError else type(error).__name__
        raise SystemExit(f"Private model migration refused. {msg}") from None


if __name__ == "__main__":
    main()
