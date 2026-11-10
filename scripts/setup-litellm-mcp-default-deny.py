#!/usr/bin/env python3
"""Deny MCP access to existing LiteLLM keys without an explicit MCP policy."""
from __future__ import annotations

import subprocess
import sys


CONTAINER = "homecompute-control-plane-litellm-1"
LITELLM_CODE = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
base = 'http://127.0.0.1:4000'
headers = {'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'}

def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

keys = []
page = 1
while True:
    result = request('/key/list?' + urllib.parse.urlencode({
        'page': page, 'size': 100, 'return_full_object': 'true'}))
    batch = result.get('keys')
    if not isinstance(batch, list):
        raise RuntimeError('LiteLLM key listing had an unexpected shape')
    keys.extend(batch)
    pages = result.get('total_pages')
    if not isinstance(pages, int) or pages < page:
        raise RuntimeError('LiteLLM key listing did not include valid pagination')
    if page >= pages:
        break
    page += 1

changed = 0
already_denied = 0
explicit = 0
for item in keys:
    if not isinstance(item, dict):
        raise RuntimeError('LiteLLM returned a malformed key record')
    # LiteLLM 1.108.4 returns the hashed key identifier as `token` from
    # /key/list; `token_id` is not present in that response shape.
    key_id = item.get('token')
    permission = item.get('object_permission') or {}
    if not isinstance(key_id, str) or not key_id:
        raise RuntimeError('LiteLLM key record did not include its hashed token id')
    if not isinstance(permission, dict):
        raise RuntimeError('LiteLLM returned an unexpected object permission')
    servers = permission.get('mcp_servers') or []
    groups = permission.get('mcp_access_groups') or []
    if not isinstance(servers, list) or not isinstance(groups, list):
        raise RuntimeError('LiteLLM returned malformed MCP permissions')
    if 'no-mcp-servers' in servers:
        already_denied += 1
        continue
    if servers or groups:
        explicit += 1
        continue

    updated = dict(permission)
    updated['mcp_servers'] = ['no-mcp-servers']
    request('/key/update', {'key': key_id, 'object_permission': updated})
    info = request('/key/info?' + urllib.parse.urlencode({'key': key_id})).get('info', {})
    verified = info.get('object_permission') or {}
    if not isinstance(verified, dict) or verified.get('mcp_servers') != ['no-mcp-servers']:
        raise RuntimeError('MCP default-deny update did not verify')
    changed += 1

print(json.dumps({'keys_seen': len(keys), 'keys_denied': changed,
                  'keys_already_denied': already_denied,
                  'keys_with_existing_explicit_policy': explicit}))
'''


def apply(runner=subprocess.run) -> None:
    result = runner(
        ["docker", "exec", "-i", CONTAINER, "python3", "-"],
        input=LITELLM_CODE,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("LiteLLM MCP default-deny provisioning failed; inspect protected service state")
    print("Applied LiteLLM MCP default-deny to unscoped keys: " + result.stdout.strip())


if __name__ == "__main__":
    try:
        apply()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"LiteLLM MCP access setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
