#!/usr/bin/env python3
"""Connect the owner Hermes sandbox through native managed MCP, with a private pipe."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://homecompute-capabilities.home.arpa:19445/mcp'
KEY = 'HOMECOMPUTE_HERMES_MCP_TOKEN'
SKILL = ROOT / 'deploy/hermes/skills/homecompute-household'
GATES = Path('/var/lib/homecompute-hermes/gates')


def call(args: list[str], **kwargs) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=240, **kwargs)
    if result.returncode:
        detail = (result.stdout + '\n' + result.stderr).strip()
        secret = kwargs.get('env', {}).get(KEY)
        if secret:
            detail = detail.replace(secret, '[redacted]')
        raise ValueError('native ' + ' '.join(args[:4]) + ' failed or is uncertain: ' + detail[-4000:])
    return result.stdout


def gates() -> None:
    for name, gate in (('backup-readiness.json', 'off-host-backup'), ('agents-network.json', 'agents-network')):
        path = GATES / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
            raise ValueError('private household readiness record required')
        value = json.loads(path.read_bytes())
        observed = datetime.strptime(value['observed_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        if (value.get('schema_version') != 1 or value.get('gate') != gate or value.get('status') != 'ready'
                or value.get('scope') != 'hermes-household' or not value.get('evidence')
                or not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 7 * 86400):
            raise ValueError('fresh household backup and network readiness required')


def connect(sandbox: str, token: str) -> dict:
    gates()
    if not 32 <= len(token) <= 4096 or any(c.isspace() for c in token) or token.startswith(('replace', 'openshell:resolve:')):
        raise ValueError('dedicated owner MCP credential required')
    env = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
           'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
    cli = ['nemohermes', sandbox]
    state = json.loads(call(cli + ['status', '--json'], env=env))
    if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
        raise ValueError('ready isolated Hermes sandbox required')
    entries = json.loads(call(cli + ['mcp', 'list', '--json'], env=env))['bridges']
    existing = [row for row in entries if row.get('server') == 'homecompute']
    if existing:
        if len(existing) != 1 or existing[0].get('url') != URL or existing[0].get('env', {}).get('names') != [KEY]:
            raise ValueError('existing MCP registration differs; preserve it for review')
    else:
        # The native CLI owns policy, provider, opaque handles and gateway reload.
        # An unknown add outcome is left untouched for native reconciliation.
        call(cli + ['mcp', 'add', 'homecompute', '--url', URL, '--env', KEY,
                    '--trusted-private-host', 'homecompute-capabilities.home.arpa'], env={**env, KEY: token})
    token = ''
    status = json.loads(call(cli + ['mcp', 'status', 'homecompute', '--json'], env=env))
    rows = status.get('bridges', [status.get('status', status)]) if isinstance(status, dict) else status
    if len(rows) != 1:
        raise ValueError('one native MCP status required')
    row = rows[0]
    if (row.get('url') != URL or row.get('provider', {}).get('credentialResolution', {}).get('ok') is not True
            or row.get('adapter', {}).get('registered') is not True
            or row.get('trustedPrivateTarget', {}).get('state') != 'match'):
        raise ValueError('native MCP credential, adapter and private pin verification required')
    # Refuse to overwrite operator-edited instructions. The native skill lifecycle
    # owns installation; the read only guard merely recognizes this exact source.
    expected = hashlib.sha256((SKILL / 'SKILL.md').read_bytes()).hexdigest()
    program = '''import hashlib,pathlib
p=pathlib.Path('/sandbox/.hermes/skills/homecompute-household/SKILL.md')
assert not p.is_symlink() and not p.parent.is_symlink()
print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent')
'''
    current = call(cli + ['exec', '--stdin', '--', 'python3', '-'], input=program, env=env).strip()
    if current not in {'absent', expected}:
        raise ValueError('existing household skill differs; preserve private edits')
    if current == 'absent':
        call(cli + ['skill', 'install', str(SKILL)], env=env)
    installed = call(cli + ['exec', '--stdin', '--', 'python3', '-'], input=program, env=env).strip()
    if installed != expected:
        raise ValueError('native household skill readback differs')
    return {'sandbox': sandbox, 'server': 'homecompute', 'credential_verified': True, 'skill_sha256': expected}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--sandbox', choices=('hermes-personal', 'hermes-personal-v23'),
                        default='hermes-personal-v23')
    args = parser.parse_args()
    try:
        spec = importlib.util.spec_from_file_location('setup_hermes_mcp', ROOT / 'scripts/setup-hermes-mcp.py')
        setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
        setup.published(args.revision)
        print(json.dumps(connect(args.sandbox, sys.stdin.read(4097).strip())))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if isinstance(error, ValueError):
            print(str(error), file=sys.stderr)
        print('Hermes MCP connection refused or unverified; inspect native status without removing its state.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
