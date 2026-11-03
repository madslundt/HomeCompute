#!/usr/bin/env python3
"""Read-only probe inside a disposable, networkless restored VM only."""
from contextlib import closing
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import uuid

STATE = Path('/var/lib/docker/volumes/nemoclaw-openclaw-state-v1-agent-openclaw/_data')
SECTIONS = ('agents', 'tools', 'channels', 'cron', 'hooks', 'browser')
IMAGE = 'sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a'


def policy_digest(config):
    value = {key: config.get(key) for key in SECTIONS}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inspect(expected):
    config = json.loads((STATE / 'openclaw.json').read_bytes())
    if policy_digest(config) != expected:
        raise ValueError('restored-policy-digest-mismatch')
    entries = config['agents']['entries']
    if len(entries) != 12 or config.get('cron', {}).get('enabled') is not False:
        raise ValueError('restored-roster-or-cron-mismatch')
    databases = files = 0
    for path in STATE.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        files += 1
        if files > 200000:
            raise ValueError('restored-state-entry-limit')
        with path.open('rb') as stream:
            header = stream.read(16)
        if header == b'SQLite format 3\0':
            # SQLite reads adjacent restored WAL/SHM rather than a detached DB.
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
                db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('restored-native-sqlite-integrity')
            databases += 1
    if databases < 1 or not (STATE / 'agents/main').is_dir() or not (STATE / 'workspace').is_dir():
        raise ValueError('restored-native-state-missing')
    primary = STATE / 'agents/main/agent/openclaw-agent.sqlite'
    if primary.is_symlink() or not primary.is_file():
        raise ValueError('restored-main-conversations-missing')
    with closing(sqlite3.connect(primary.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.execute('PRAGMA query_only=ON')
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('session_nodes', 'session_windows', 'transcript_events')}
    if any(value < 1 for value in counts.values()):
        raise ValueError('restored-main-conversations-empty')
    return {'native_policy_verified': True, 'native_sqlite_integrity': True,
            'native_conversations_verified': True, 'native_conversation_counts': counts,
            'native_sqlite_databases': databases, 'native_state_files': files,
            'roles': len(entries), 'workspace_directories': sum(p.is_dir() for p in STATE.glob('workspace*'))}


def gateway_health():
    # Existing native credentials remain inside this isolated VM. No turn is sent.
    registry = json.loads(Path('/home/hermes-operator/.nemoclaw/gateways/9123/sandboxes.json').read_bytes())
    entry = registry['sandboxes']['agent-openclaw']
    if entry.get('agentVersion') != '2026.9.1' or entry.get('openshellVersion') != '0.0.116':
        raise ValueError('restored-runtime-version-mismatch')
    command = ['runuser', '-u', 'hermes-operator', '--', 'env',
               'PATH=/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:/usr/local/bin:/usr/bin:/bin',
               'NEMOCLAW_GATEWAY_PORT=9123', 'nemoclaw', 'agent-openclaw', 'exec', '--',
               'openclaw', 'gateway', 'health', '--timeout', '10000', '--json']
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(command, capture_output=True, timeout=35)
            if result.returncode == 0 and len(result.stdout) <= 1024**2:
                raw = result.stdout.decode()
                health, _ = json.JSONDecoder().raw_decode(raw[raw.index('{'):])
                if health.get('ok') is True:
                    containers = subprocess.run(['docker', 'ps', '-q', '--filter',
                        'name=openshell-default--agent-openclaw-', '--format', '{{.ID}}'],
                        capture_output=True, check=True, timeout=10).stdout.decode().splitlines()
                    if len(containers) != 1 or not re.fullmatch('[0-9a-f]{12,64}', containers[0]):
                        raise ValueError('restored-runtime-identity-unavailable')
                    image = subprocess.run(['docker', 'inspect', '--format', '{{.Image}}', containers[0]],
                        capture_output=True, check=True, timeout=10).stdout.decode().strip()
                    if image != IMAGE:
                        raise ValueError('restored-runtime-image-mismatch')
                    version = subprocess.run(['docker', 'exec', '--user', '998:998', containers[0],
                        '/usr/local/bin/openclaw', '--version'], capture_output=True, check=True, timeout=30)
                    line = version.stdout.decode().strip().splitlines()[-1]
                    if not re.fullmatch(r'(?:OpenClaw\s+)?v?2026\.9\.1(?:\s*\([^\n]*\))?', line):
                        raise ValueError('restored-actual-core-version-mismatch')
                    return {'authenticated_gateway_verified': True,
                            'runtime_version_verified': True,
                            'required_plugins_loaded': {'homecompute-safety', 'homecompute-agent-office',
                                'memory-core', 'mattermost', 'nemoclaw'} <= set(health.get('plugins', {}).get('loaded', [])),
                            'gateway_plugin_errors': len(health.get('plugins', {}).get('errors', [])),
                            'openclaw_version': entry['agentVersion'], 'image': image}
        except (subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError):
            pass
        time.sleep(5)
    raise ValueError('restored-authenticated-gateway-unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--expected-policy-sha256', required=True)
    args = parser.parse_args()
    nonce = str(uuid.UUID(args.nonce))
    result = {'native_policy_verified': False, 'native_sqlite_integrity': False,
              'authenticated_gateway_verified': False, 'required_plugins_loaded': False,
              'native_conversations_verified': False, 'runtime_version_verified': False}
    try:
        if os.geteuid() != 0 or socket.gethostname() != 'agents':
            raise ValueError('isolated-agents-root-required')
        result.update(inspect(args.expected_policy_sha256))
        result.update(gateway_health())
    except Exception as error:
        result['error_category'] = str(error) if isinstance(error, ValueError) else type(error).__name__
    with open('/dev/ttyS0', 'w') as serial:
        serial.write('\nHC_OPENCLAW_RESTORE_' + nonce + ' ' + json.dumps(result, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
