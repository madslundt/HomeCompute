#!/usr/bin/env python3
"""Reconcile the managed LAN inference key with the loaded LiteLLM catalog."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile


ROUTES_FILE = Path(__file__).resolve().parents[1] / 'config/capability-routes.json'
KEY_FILE = Path('/srv/state/control-plane/caddy-data/lan-catalog-key')
CONTAINER = 'homecompute-control-plane-litellm-1'
OWNER = 'lan-catalog-inference'
ROUTES = [
    '/v1/models', '/v1/responses', '/v1/chat/completions', '/v1/completions',
    '/v1/audio/transcriptions', '/v1/audio/speech', '/v1/embeddings', '/v1/videos',
]
MCP_POLICY = {'mcp_servers': ['no-mcp-servers']}

API_SCRIPT = '''import json, sys, urllib.error, urllib.parse, urllib.request
path, body, query, key = %r
if key is None:
    key = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
url = 'http://127.0.0.1:4000' + path
if query:
    url += '?' + urllib.parse.urlencode(query)
request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(json.dumps(json.load(response)))
except urllib.error.HTTPError as error:
    sys.exit(44 if path == '/key/info' and error.code == 404 else 1)
'''


def api(path, *, body=None, query=None, key=None):
    # Send both the interpreter source and request over stdin. Key values
    # never appear in the process arguments or the setup output.
    result = subprocess.run(
        ['docker', 'exec', '-i', CONTAINER, 'python3', '-'],
        input=API_SCRIPT % ((path, body, query, key),),
        text=True, capture_output=True, timeout=45,
    )
    if result.returncode == 44:
        return None
    if result.returncode:
        raise RuntimeError(f'LiteLLM refused LAN catalog provisioning at {path}.')
    return json.loads(result.stdout)


def named_models(models):
    return bool(models) and all(
        re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', model)
        and model not in {'all-proxy-models', 'all-team-models', 'no-default-models'}
        for model in models
    )


def list_models(*, key=None):
    models = {row['id'] for row in api('/v1/models', key=key)['data']}
    if not named_models(models):
        raise RuntimeError('LiteLLM must report a nonempty catalog of named model aliases.')
    return models


def key_scope(token_id):
    result = api('/key/info', query={'key': token_id})
    if result is None:
        return None
    info = result['info']
    permission = info.get('object_permission') or {}
    if (info.get('user_id') != OWNER or info.get('key_alias') != OWNER or
            info.get('expires') is not None or info.get('team_id') is not None or
            info.get('blocked') or set(info.get('allowed_routes') or []) != set(ROUTES) or
            permission.get('mcp_servers') != MCP_POLICY['mcp_servers'] or
            permission.get('mcp_access_groups') or permission.get('mcp_tool_permissions')):
        raise RuntimeError('The LAN catalog key has unexpected ownership or permissions.')
    models = set(info.get('models') or [])
    if not named_models(models):
        raise RuntimeError('The LAN catalog key must have an explicit model scope.')
    return models


def read_key(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError('The LAN catalog key must be a root-owned regular file with mode 0600.')
    key = path.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]+', key):
        raise RuntimeError('The LAN catalog key has an invalid format.')
    return key


def allowed_catalog(models):
    policy = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))['agent_routing']
    cloud_routes = {alias for alias, route in policy['routes'].items() if route['privacy'] == 'cloud_allowed'}
    return models - cloud_routes


def provision():
    if os.geteuid() != 0:
        raise RuntimeError('Run as root on home-core.')
    models = allowed_catalog(list_models())
    key = read_key(KEY_FILE)
    pending = KEY_FILE.with_name('.lan-catalog-key.pending')
    creating = key is None
    if creating:
        key = read_key(pending)
        if key is None:
            # Persist the candidate first so a failed response or interrupted
            # install can recover the same key without creating an orphan.
            key = 'sk-' + secrets.token_urlsafe(32)
            with tempfile.NamedTemporaryFile(mode='w', dir=pending.parent, encoding='utf-8') as handle:
                handle.write(key + '\n')
                handle.flush()
                os.fsync(handle.fileno())
                os.link(handle.name, pending)
    # LiteLLM accepts its stored SHA-256 token ID for key management. Keep
    # the bearer credential out of URLs and service access logs.
    token_id = hashlib.sha256(key.encode()).hexdigest()
    scope = key_scope(token_id)
    if scope is None and creating:
        api('/key/generate', body={
            'key': key, 'models': sorted(models), 'user_id': OWNER, 'key_alias': OWNER,
            'allowed_routes': ROUTES, 'object_permission': MCP_POLICY,
        })
        scope = key_scope(token_id)
    if scope is None:
        raise RuntimeError('The LAN catalog key is missing in LiteLLM.')
    if scope != models:
        api('/key/update', body={'key': token_id, 'models': sorted(models)})
        scope = key_scope(token_id)
    if scope != models or list_models(key=key) != models:
        raise RuntimeError('The LAN catalog key did not verify against the loaded models.')
    if creating:
        pending.rename(KEY_FILE)
    print(f'LAN inference credential verified for {len(models)} LiteLLM model aliases.')


if __name__ == '__main__':
    try:
        provision()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        # Unexpected responses may contain secret fields.
        raise SystemExit('Invalid LAN catalog setup state.') from None
