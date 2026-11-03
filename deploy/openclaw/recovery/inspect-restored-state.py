#!/usr/bin/env python3
"""Read-only probe inside a disposable, networkless restored VM only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
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


def verify_policy(expected):
    config = json.loads((STATE / 'openclaw.json').read_bytes())
    if policy_digest(config) != expected:
        raise ValueError('restored-policy-digest-mismatch')
    entries = config['agents']['entries']
    if len(entries) != 12 or config.get('cron', {}).get('enabled') is not False:
        raise ValueError('restored-roster-or-cron-mismatch')
    return config


def inspect(expected):
    config = verify_policy(expected)
    entries = config['agents']['entries']
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


class GatewayUnavailable(ValueError):
    def __init__(self, details):
        super().__init__('restored-authenticated-gateway-unavailable')
        self.details = details


def redact_diagnostic(value):
    value = re.sub(r'(?i)(bearer\s+|(?:token|api[_-]?key|password)[\"\']?\s*[=:]\s*[\"\']?)[^\s\"\']+', r'\1[redacted]', value)
    return re.sub(r'https?://[^\s]+', '[endpoint]', value)


def private_diagnostics(last):
    # This bounded health-command diagnostic stays in the private restore store.
    # It contains no model turn, request body, config export or channel message.
    def query(args, both=False):
        try:
            completed = subprocess.run(args, capture_output=True, timeout=10)
            return (completed.stdout + (completed.stderr if both else b'')).decode(errors='replace')[:65536]
        except (OSError, subprocess.SubprocessError):
            return 'unavailable'
    status = {name: query(['systemctl', 'is-active', name]).strip() for name in
              ('docker.service', 'docker.socket', 'containerd.service')}
    status['shutdown-owner.service'] = query(['systemctl', 'is-active', 'homecompute-openclaw-shutdown.service']).strip()
    user = ['runuser', '-u', 'hermes-operator', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
            'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus']
    status['supervisor.service'] = query([*user, 'systemctl', '--user', 'is-active',
                                        'homecompute-openclaw-supervisor.service']).strip()
    journal = query(['journalctl', '-b', '--no-pager', '-o', 'cat', '-n', '80',
                     '_SYSTEMD_USER_UNIT=homecompute-openclaw-supervisor.service'])
    events = re.findall(r'^(?:blocked|waiting|recovery-attempt|backoff):[A-Za-z0-9-]+$', journal, re.MULTILINE)
    containers = query(['docker', 'ps', '-a', '--filter', 'name=openshell-default--agent-openclaw-',
                        '--format', '{{.State}}'])
    states = [line for line in containers.splitlines() if line in ('running', 'exited', 'created', 'dead', 'restarting', 'paused')]
    jobs = query(['systemctl', 'list-jobs', '--no-legend', '--plain'])
    units = re.findall(r'\b[A-Za-z0-9@_.-]+\.(?:service|socket|target|mount)\b', jobs)
    command_log = Path('/home/hermes-operator/.local/state/homecompute-openclaw-supervisor/last-command.log')
    previous = ''
    native_status = {}
    if not command_log.is_symlink() and command_log.is_file() and command_log.stat().st_size <= 1024**2:
        previous = command_log.read_text(errors='replace')
        try:
            data, _ = json.JSONDecoder().raw_decode(previous[previous.index('{'):])
            native_status = {'phase': data.get('phase')}
        except (ValueError, TypeError):
            pass
    # Preserve status/reason, never the full native policy/configuration body.
    previous_label = previous.splitlines()[0] if previous else ''
    state = {}
    errors = ''
    identity = Path('/etc/homecompute-openclaw/supervisor-identity.env')
    if identity.is_file() and not identity.is_symlink():
        match = re.search(r'^OPENCLAW_EXPECTED_SANDBOX_ID=([0-9a-f-]{36})$', identity.read_text(), re.MULTILINE)
        if match:
            identifier = str(uuid.UUID(match[1]))
            name = 'openshell-default--agent-openclaw-' + identifier
            try:
                observed = json.loads(query(['docker', 'inspect', '--format', '{{json .State}}', name]))
                state = {key: observed.get(key) for key in ('Status', 'OOMKilled', 'ExitCode', 'Error', 'StartedAt', 'FinishedAt')}
            except (ValueError, TypeError):
                pass
            # Exclude all pre-backup logs. This copy has no network/model turns.
            boot = datetime.now(timezone.utc) - timedelta(seconds=float(Path('/proc/uptime').read_text().split()[0]))
            logs = query(['docker', 'logs', '--since', boot.isoformat(), '--tail', '80', name], both=True)
            errors = '\n'.join(line for line in logs.splitlines() if re.search(r'error|failed|panic|fatal|killed', line, re.IGNORECASE))
    stderr = redact_diagnostic(last.get('stderr', ''))
    return {'units': status, 'pending_units': units, 'supervisor_events': events, 'container_states': states,
            'native_exit_code': last.get('returncode'),
            'native_exception': last.get('exception'), 'native_stderr': stderr[-2048:],
            'supervisor_command': previous_label,
            'sandbox_status': redact_diagnostic(json.dumps(native_status))[:4096],
            'container_exit': state, 'startup_errors': redact_diagnostic(errors)[-4096:]}


def native_ready(prefix):
    observed = subprocess.run([*prefix, 'openshell', 'sandbox', 'get', '-g', 'nemoclaw-9123',
                               'agent-openclaw', '-o', 'json'], capture_output=True, timeout=15)
    if observed.returncode or len(observed.stdout) > 1024**2:
        return False
    metadata = json.loads(observed.stdout)
    identity = Path('/etc/homecompute-openclaw/supervisor-identity.env').read_text()
    expected = re.search(r'^OPENCLAW_EXPECTED_SANDBOX_ID=([0-9a-f-]{36})$', identity, re.MULTILINE)
    if (not expected or metadata.get('name') != 'agent-openclaw'
            or metadata.get('id') != str(uuid.UUID(expected[1]))):
        raise ValueError('restored-native-runtime-identity-mismatch')
    return metadata.get('phase') == 'Ready'


def gateway_health(progress=lambda details: None):
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
    last = {}
    attempts = 0
    next_diagnostic = time.monotonic()
    while time.monotonic() < deadline:
        try:
            # Exec performs native permission-posture housekeeping. Only enter
            # it after exact native Ready, never during stopped/start transitions.
            if not native_ready(command[:7]):
                raise ValueError('restored-native-not-ready')
            result = subprocess.run(command, capture_output=True, timeout=35)
            last = {'returncode': result.returncode, 'stderr': result.stderr.decode(errors='replace')}
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
        except (subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as error:
            last['exception'] = type(error).__name__
        attempts += 1
        if time.monotonic() >= next_diagnostic:
            details = private_diagnostics(last)
            progress(details)
            if 'blocked:agent-roster-refused' in details['supervisor_events']:
                raise GatewayUnavailable(details)
            next_diagnostic = time.monotonic() + 20
        time.sleep(5)
    raise GatewayUnavailable(private_diagnostics(last))


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
        def progress(details):
            with open('/dev/ttyS0', 'w') as serial:
                serial.write('\nHC_OPENCLAW_STARTUP_' + nonce + ' ' + json.dumps(details, sort_keys=True) + '\n')
        progress({'stage': 'before-health', **private_diagnostics({})})
        result.update(gateway_health(progress))
        # Native recovery must retain authority/sharing after startup too.
        result['native_policy_verified'] = False
        verify_policy(args.expected_policy_sha256)
        result['native_policy_verified'] = True
    except Exception as error:
        if isinstance(error, GatewayUnavailable):
            result['private_diagnostics'] = error.details
        result['error_category'] = str(error) if isinstance(error, ValueError) else type(error).__name__
    with open('/dev/ttyS0', 'w') as serial:
        serial.write('\nHC_OPENCLAW_RESTORE_' + nonce + ' ' + json.dumps(result, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
