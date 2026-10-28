#!/usr/bin/env python3
"""Expose the finite maintenance MCP tools in the existing managed OpenClaw.

Preserves all credentials, shell policies, approved tools and conversation state.
Default validates only; --apply adds explicit tool names and the reviewed policy.
Run inside the managed sandbox after the trusted controller is installed.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import uuid

NAMES = {'homecompute__homecompute_maintenance_' + suffix for suffix in
         ('prepare', 'preparation', 'list', 'status', 'run')}


def update(apply=False, policy=None):
    path = Path('/sandbox/.openclaw/openclaw.json')
    before = json.loads(path.read_text())
    if (before['agents']['defaults']['workspace'] != '/sandbox/.openclaw/workspace'
            or before['tools']['elevated']['enabled'] is not False
            or 'homecompute' not in before.get('mcp', {}).get('servers', {})
            or 'bundle-mcp' not in before['tools'].get('alsoAllow', [])):
        raise ValueError('retained managed MCP boundary required')
    allowed = before['agents']['entries']['main']['tools']['allow']
    # Calendar writes use preflight, one mutation and readback; retain a longer
    # reviewed timeout when maintenance tools are reapplied later.
    request_timeout = before['mcp']['servers']['homecompute'].get('requestTimeoutMs', 15000)
    if type(request_timeout) is not int or not 1 <= request_timeout <= 300000:
        raise ValueError('bounded managed MCP timeout required')
    batch = [{'path': 'agents.entries.main.tools.allow', 'value': sorted(set(allowed) | NAMES)},
             {'path': 'mcp.servers.homecompute.requestTimeoutMs', 'value': max(15000, request_timeout)},
             {'path': 'mcp.servers.homecompute.connectionTimeoutMs', 'value': 5000}]
    command = ['openclaw', 'config', 'set', '--batch-json', json.dumps(batch)]
    result = subprocess.run(command + ['--dry-run'], capture_output=True, timeout=45)
    if result.returncode:
        raise ValueError('native configuration validation failed')
    if not apply:
        return {'validated': True, 'applied': False, 'tools_added': sorted(NAMES - set(allowed))}
    backup = path.parent / 'maintenance-config-backups'
    if backup.is_symlink():
        raise ValueError('private backup directory required')
    backup.mkdir(mode=0o700, exist_ok=True)
    with (backup / (uuid.uuid4().hex + '.json')).open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(before, stream)
    result = subprocess.run(command, capture_output=True, timeout=45)
    if result.returncode:
        raise ValueError('native configuration application failed')
    if policy is not None:
        target = Path('/sandbox/.openclaw/workspace/POLICY.md')
        if target.is_symlink():
            raise ValueError('fixed policy path required')
        temporary = target.with_name('.POLICY.' + uuid.uuid4().hex)
        with temporary.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(policy)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    after = json.loads(path.read_text())
    if not NAMES <= set(after['agents']['entries']['main']['tools']['allow']):
        raise ValueError('native tool persistence failed')
    return {'validated': True, 'applied': True, 'tools_added': sorted(NAMES - set(allowed))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true'); p.add_argument('--policy-base64')
    a = p.parse_args()
    try:
        policy = base64.b64decode(a.policy_base64, validate=True).decode() if a.policy_base64 else None
        if policy is not None and len(policy.encode()) > 16384:
            raise ValueError('bounded policy required')
        print(json.dumps(update(a.apply, policy))); return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Managed maintenance tool setup refused; preserve existing configuration.'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
