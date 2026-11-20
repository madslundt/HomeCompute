#!/usr/bin/env python3
"""Grant the full LiteLLM MCP catalog to known harness virtual keys."""
from __future__ import annotations

import argparse
import subprocess
import sys


CONTAINER = "homecompute-control-plane-litellm-1"
LITELLM_CODE = r'''import json, sys, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
base = 'http://127.0.0.1:4000'
headers = {'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'}
openbot_only = sys.argv[1:] == ['--openbot-nod-only']
targets = {'openbot-agent'} if openbot_only else {
    'hermes-desktop-mcp', 'hermes-agent', 'openbot-agent', 'opencode-home-core'}
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
    batch, pages = result.get('keys'), result.get('total_pages')
    if not isinstance(batch, list) or not isinstance(pages, int) or pages < page:
        raise RuntimeError('LiteLLM key listing had an unexpected shape')
    keys.extend(batch)
    if page >= pages:
        break
    page += 1

verified = []
for alias in sorted(targets):
    matches = [item for item in keys if isinstance(item, dict) and item.get('key_alias') == alias]
    if len(matches) > 1:
        raise RuntimeError('A harness key alias is not unique')
    if not matches:
        if openbot_only:
            raise RuntimeError('The owner OpenBot key does not exist')
        continue
    item = matches[0]
    key_id = item.get('token')
    permission = item.get('object_permission') or {}
    if not isinstance(key_id, str) or not key_id or not isinstance(permission, dict):
        raise RuntimeError('LiteLLM harness key permission had an unexpected shape')
    groups = permission.get('mcp_access_groups') or []
    servers = permission.get('mcp_servers') or []
    if (not isinstance(groups, list) or any(not isinstance(group, str) for group in groups)
            or not isinstance(servers, list) or any(not isinstance(server, str) for server in servers)):
        raise RuntimeError('LiteLLM harness MCP permissions were malformed')
    changed = dict(permission)
    required_groups = {'all-harness-mcps'}
    if alias == 'openbot-agent':
        required_groups.add('openbot-nod')
    changed['mcp_access_groups'] = sorted(set(groups) | required_groups)
    if 'no-mcp-servers' in servers:
        remaining = [server for server in servers if server != 'no-mcp-servers']
        # LiteLLM merges object_permission updates; omission does not clear a
        # stored sentinel. Persist [] explicitly so the access group can grant.
        changed['mcp_servers'] = remaining
    if changed != permission:
        request('/key/update', {'key': key_id, 'object_permission': changed})
    info = request('/key/info?' + urllib.parse.urlencode({'key': key_id})).get('info', {})
    actual = info.get('object_permission') or {}
    actual_groups = actual.get('mcp_access_groups') if isinstance(actual, dict) else None
    actual_servers = actual.get('mcp_servers') if isinstance(actual, dict) else None
    if (not isinstance(actual_groups, list) or not required_groups.issubset(actual_groups)
            or isinstance(actual_servers, list) and 'no-mcp-servers' in actual_servers):
        raise RuntimeError('A LiteLLM full harness MCP grant did not verify')
    verified.append(alias)
print(json.dumps({'all_mcp_access': verified, 'missing_harness_keys': sorted(targets - set(verified))}))
'''


def apply(runner=subprocess.run, *, openbot_nod_only: bool = False) -> None:
    args = ["docker", "exec", "-i", CONTAINER, "python3", "-"]
    if openbot_nod_only:
        args.append("--openbot-nod-only")
    result = runner(
        args,
        input=LITELLM_CODE,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("LiteLLM harness MCP access provisioning failed; inspect protected service state")
    print("Verified LiteLLM full-catalog grants: " + result.stdout.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openbot-nod-only", action="store_true", help="reconcile only the owner OpenBot key")
    args = parser.parse_args()
    try:
        apply(openbot_nod_only=args.openbot_nod_only)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"LiteLLM harness MCP access setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
