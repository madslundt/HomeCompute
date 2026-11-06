#!/usr/bin/env python3
"""Create a Hermes-only LiteLLM key and stream it directly into the guest.

Run from the operator workstation. The master key remains inside LiteLLM,
the generated virtual key is held only in process memory, and the guest file
is created without printing the key or placing it in an argument.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

HOST_KEY = str(Path.home() / ".ssh/id_ed25519_ai-services-01")
GUEST = "hermes-operator@10.77.20.2"
MODEL = "automation-moe"
OWNER = "hermes-agent"
DESTINATION = "/home/hermes-operator/.config/homecompute-hermes/secrets/litellm-agent-owner"

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
body = {'models': ['automation-moe'], 'key_alias': 'hermes-agent',
        'user_id': 'hermes-agent', 'duration': None,
        'metadata': {'purpose': 'Hermes household agent; automation-moe only'}}
request = urllib.request.Request('http://127.0.0.1:4000/key/generate',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
result = json.load(urllib.request.urlopen(request, timeout=30))
key = result.get('key')
query = urllib.parse.urlencode({'key': key})
check = urllib.request.Request('http://127.0.0.1:4000/key/info?' + query,
    headers={'Authorization': 'Bearer ' + master})
info = json.load(urllib.request.urlopen(check, timeout=30)).get('info', {})
print(json.dumps({'key': key, 'models': info.get('models'),
                  'user_id': info.get('user_id'), 'expires': info.get('expires')}))
'''


def run() -> None:
    host = subprocess.run(
        ["ssh", "home-core", "sudo -n docker exec -i homecompute-control-plane-litellm-1 python3 -"],
        input=LITELLM_SCRIPT, text=True, capture_output=True, timeout=45, check=False,
    )
    if host.returncode:
        raise RuntimeError("LiteLLM key creation failed; no credential was delivered")
    try:
        value = json.loads(host.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("LiteLLM returned an unreadable key receipt") from None
    key = value.get("key")
    if (not isinstance(key, str) or not key.startswith("sk-") or any(ch.isspace() for ch in key)
            or value.get("models") != [MODEL] or value.get("user_id") != OWNER
            or value.get("expires") is not None):
        raise RuntimeError("LiteLLM returned a key with an unexpected scope")

    receiver = (
        "set -eu; install -d -m 0700 ~/.config/homecompute-hermes/secrets; "
        f"test ! -e {DESTINATION} && test ! -L {DESTINATION}; "
        f"umask 077; cat > {DESTINATION}; chmod 0600 {DESTINATION}; "
        f"test -f {DESTINATION} && test ! -L {DESTINATION} && "
        f"test \"$(stat -c %a {DESTINATION})\" = 600 && "
        f"test \"$(wc -l < {DESTINATION})\" = 1 && echo key-installed"
    )
    guest = subprocess.run(
        ["ssh", "-J", "home-core", "-o", "BatchMode=yes", "-o",
         "StrictHostKeyChecking=yes", "-i", HOST_KEY, GUEST, receiver],
        input=key + "\n", text=True, capture_output=True, timeout=30, check=False,
    )
    if guest.returncode or guest.stdout.strip() != "key-installed":
        raise RuntimeError("Guest key installation failed; inspect protected guest state")
    print("Created a non-expiring LiteLLM key scoped only to automation-moe and installed it in the guest.")


if __name__ == "__main__":
    try:
        run()
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Hermes key provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
