#!/usr/bin/env python3
"""Create or verify OpenCode's Home Core model and MCP LiteLLM key.

Run as root on home-core after LiteLLM is healthy. The key is scoped to
local-only, automation-moe and all-harness-mcps, written to private persistent state, and
never printed.
Without --apply this command only validates an existing key or reports that
one still needs provisioning.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable

KEY_FILE = Path('/srv/state/opencode/litellm.env')
CONTAINER = 'homecompute-control-plane-litellm-1'
OWNER = 'opencode-home-core'
LEGACY_MODELS = ['automation-moe']
MODELS = [*LEGACY_MODELS, 'local-only']
AGENT_UID = pwd.getpwnam('agent').pw_uid
USERS_GID = grp.getgrnam('users').gr_gid

LITELLM_SCRIPT = r'''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
payload = %r
path = %r
query = %r
url = 'http://127.0.0.1:4000' + path
if query:
    url += '?' + urllib.parse.urlencode(payload)
request = urllib.request.Request(url,
    data=None if query else json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
print(json.dumps(json.load(urllib.request.urlopen(request, timeout=30))))
'''


class Refused(Exception):
    """Operator-safe diagnostics without credential values."""


def api(path: str, payload: dict[str, Any], *, query: bool = False,
        runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    source = LITELLM_SCRIPT % (payload, path, query)
    try:
        result = runner(
            ['docker', 'exec', '-i', CONTAINER, 'python3', '-'],
            input=source, capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('litellm-key-request-unconfirmed') from None
    if result.returncode:
        raise Refused('litellm-key-request-refused')
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Refused('litellm-key-response-refused') from None
    if not isinstance(value, dict):
        raise Refused('litellm-key-response-refused')
    return value


def validate_key(key: str, runner: Callable[..., Any], *, target_models: list[str], apply: bool) -> None:
    token_id = hashlib.sha256(key.encode()).hexdigest()

    def checked_scope():
        info = api('/key/info', {'key': token_id}, query=True, runner=runner).get('info', {})
        permission = info.get('object_permission') or {}
        if (info.get('user_id') != OWNER or info.get('key_alias') != OWNER or info.get('team_id')
                or info.get('expires') is not None or info.get('blocked') or info.get('aliases')
                or permission.get('mcp_access_groups') != ['all-harness-mcps']
                or permission.get('mcp_servers') or permission.get('mcp_tool_permissions')):
            raise Refused('opencode-key-scope-refused')
        return info.get('models')

    models = checked_scope()
    previous_models = LEGACY_MODELS if target_models == MODELS else MODELS
    if apply and models == previous_models:
        api('/key/update', {'key': token_id, 'models': target_models}, runner=runner)
        models = checked_scope()
    if models != target_models:
        raise Refused('opencode-key-models-refused')


def read_key(runner: Callable[..., Any], *, target_models: list[str], apply: bool = False) -> bool:
    parent = KEY_FILE.parent
    if parent.is_symlink() or not parent.is_dir():
        raise Refused('opencode-state-directory-required')
    parent_stat = parent.stat()
    if (parent_stat.st_uid != AGENT_UID or stat.S_IMODE(parent_stat.st_mode) != 0o700):
        raise Refused('opencode-state-permissions-refused')
    if KEY_FILE.is_symlink():
        raise Refused('opencode-key-symlink-refused')
    if not KEY_FILE.exists():
        return False
    metadata = KEY_FILE.stat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != AGENT_UID
            or metadata.st_gid != USERS_GID or stat.S_IMODE(metadata.st_mode) != 0o400):
        raise Refused('opencode-key-file-permissions-refused')
    text = KEY_FILE.read_text(encoding='utf-8')
    match = re.fullmatch(r'OPENCODE_LITELLM_API_KEY=(sk-[^\s=]+)\n?', text)
    if match is None:
        raise Refused('opencode-key-file-format-refused')
    validate_key(match.group(1), runner, target_models=target_models, apply=apply)
    return True


def write_key(key: str) -> None:
    parent = KEY_FILE.parent
    descriptor, temporary = tempfile.mkstemp(prefix='.litellm-', dir=parent)
    try:
        os.fchown(descriptor, AGENT_UID, USERS_GID)
        os.fchmod(descriptor, 0o400)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write('OPENCODE_LITELLM_API_KEY=' + key + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        if KEY_FILE.exists() or KEY_FILE.is_symlink():
            raise Refused('opencode-key-appeared-during-provisioning')
        os.link(temporary, KEY_FILE, follow_symlinks=False)
        os.unlink(temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def provision(*, apply: bool, rollback: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise Refused('run-as-root-on-home-core')
    target_models = LEGACY_MODELS if rollback else MODELS
    exists = read_key(runner, target_models=target_models, apply=apply)
    if exists or not apply or rollback:
        return {'key_exists': exists, 'scope': target_models}

    data = api('/key/generate', {
        'models': MODELS,
        'key_alias': OWNER,
        'user_id': OWNER,
        'duration': None,
        'metadata': {'purpose': 'OpenCode harness; automation-moe and full LiteLLM MCP catalog'},
        'object_permission': {'mcp_access_groups': ['all-harness-mcps']},
    }, runner=runner)
    key = data.get('key')
    if (not isinstance(key, str) or not key.startswith('sk-') or re.search(r'\s', key)
            or data.get('models') != MODELS or data.get('user_id') != OWNER
            or data.get('expires') is not None):
        raise Refused('generated-opencode-key-scope-refused')
    validate_key(key, runner, target_models=MODELS, apply=False)
    write_key(key)
    if not read_key(runner, target_models=MODELS):
        raise Refused('opencode-key-installation-refused')
    return {'key_exists': True, 'scope': MODELS, 'mcp_access_group': 'all-harness-mcps', 'applied': True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='reconcile the managed model scope or create a missing key')
    parser.add_argument('--rollback', action='store_true', help='restore the previous model scope')
    args = parser.parse_args()
    result = provision(apply=args.apply, rollback=args.rollback)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, Refused) as exc:
        print(f'OpenCode LiteLLM key provisioning refused ({exc})', file=sys.stderr)
        raise SystemExit(1) from None
