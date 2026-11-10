#!/usr/bin/env python3
"""Connect the owner Hermes sandbox through native managed MCP, with a private pipe."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://homecompute-capabilities.home.arpa:19445/mcp'
KEY = 'HOMECOMPUTE_HERMES_MCP_TOKEN'
SKILL = ROOT / 'deploy/hermes/skills/homecompute-household'
ANDROID_SKILL = ROOT / 'deploy/hermes/skills/android-gateway'
GATES = Path('/var/lib/homecompute-hermes/gates')
PYTHON = '/opt/hermes/python-runtime/bin/python3.14'
BACKUPS = Path.home() / '.local/state/homecompute/hermes-mcp-registration-backups'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def status_row(status: dict) -> dict:
    rows = status.get('bridges', [status.get('status', status)]) if isinstance(status, dict) else status
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('one native MCP status required')
    return rows[0]


def legacy_boundary_conflict(list_row: dict, status: dict, sandbox: str) -> bool:
    """Recognize only the verified stale host-only credential provider state."""
    current = status_row(status)
    env = list_row.get('env') or {}
    provider = current.get('provider') or {}
    warning_text = '\n'.join(str(item) for item in current.get('warnings', []))
    return (
        list_row.get('server') == 'homecompute'
        and list_row.get('url') == URL
        and env.get('names') == []
        and (list_row.get('trustedPrivateTarget') or {}).get('state') == 'match'
        and current.get('server') == 'homecompute'
        and current.get('url') == URL
        and (current.get('env') or {}).get('names') == []
        and (current.get('env') or {}).get('ready') is False
        and provider.get('name') == sandbox + '-mcp-homecompute'
        and provider.get('state') == 'conflict'
        and provider.get('present') is True
        and provider.get('credentialReady') is False
        and provider.get('credentialResolution', {}).get('ok') is not True
        and (current.get('adapter') or {}).get('registered') is not True
        and (current.get('trustedPrivateTarget') or {}).get('state') == 'match'
        and 'credential name no longer satisfies the host-only credential boundary' in warning_text
        and 'remove this server, then add it again with a dedicated service credential name' in warning_text
    )


def save_legacy_registration_backup(sandbox: str, list_row: dict, status: dict) -> str:
    current = status_row(status)
    provider = current.get('provider') or {}
    backup_directory = BACKUPS
    home = Path.home()
    try:
        relative = backup_directory.relative_to(home)
    except ValueError as error:
        raise ValueError('private nonsymlink MCP registration backup directory required') from error
    cursor = home
    if cursor.is_symlink():
        raise ValueError('private nonsymlink MCP registration backup directory required')
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('private nonsymlink MCP registration backup directory required')
    backup_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = backup_directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('private operator-owned MCP registration backup directory required')
    backup = backup_directory / (str(time.time_ns()) + '-homecompute.json')
    record = {
        'schema_version': 1,
        'sandbox': sandbox,
        'server': 'homecompute',
        'url': URL,
        'env_names': (list_row.get('env') or {}).get('names', []),
        'provider_name': provider.get('name'),
        'provider_state': provider.get('state'),
        'provider_present': provider.get('present'),
        'credential_ready': provider.get('credentialReady'),
        'adapter_registered': (current.get('adapter') or {}).get('registered'),
        'private_target_state': (current.get('trustedPrivateTarget') or {}).get('state'),
        'warnings': list(current.get('warnings') or []),
    }
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return str(backup)


def private_legacy_backup(sandbox: str) -> str | None:
    try:
        relative = BACKUPS.relative_to(Path.home())
    except ValueError:
        return None
    cursor = Path.home()
    if cursor.is_symlink():
        return None
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        info = BACKUPS.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            return False
        for path in BACKUPS.glob('*-homecompute.json'):
            item = path.lstat()
            if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or item.st_uid != os.getuid()
                    or stat.S_IMODE(item.st_mode) != 0o600 or item.st_size > 16384):
                continue
            value = json.loads(path.read_bytes())
            warning_text = '\n'.join(value.get('warnings') or [])
            if (value.get('schema_version') == 1 and value.get('sandbox') == sandbox
                    and value.get('server') == 'homecompute'
                    and value.get('url') == URL
                    and value.get('env_names') == []
                    and value.get('provider_state') == 'conflict'
                    and value.get('private_target_state') == 'match'
                    and 'credential name no longer satisfies the host-only credential boundary' in warning_text
                    and 'remove this server, then add it again with a dedicated service credential name' in warning_text):
                return str(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def clear_stale_provider(sandbox: str, env: dict) -> None:
    cli = ['openshell', '-g', 'nemoclaw']
    providers = json.loads(call(cli + ['provider', 'list', '--output', 'json'], env=env))
    if not isinstance(providers, list):
        raise ValueError('native provider inventory has an invalid shape')
    matches = [row for row in providers if row.get('name') == sandbox + '-mcp-homecompute']
    if not matches:
        return
    if len(matches) != 1:
        raise ValueError('stale MCP provider identity is ambiguous; preserve it for review')
    provider = matches[0]
    if (provider.get('type') != 'nemoclaw-mcp-v1'
            or provider.get('credential_keys') != [KEY]
            or not provider.get('id') or not provider.get('resource_version')):
        raise ValueError('stale MCP provider shape differs; preserve it for review')
    names = call(cli + ['sandbox', 'list', '--names'], env=env).splitlines()
    attached = []
    for candidate in names:
        candidate = candidate.strip()
        if not candidate:
            continue
        table = ANSI.sub('', call(cli + ['sandbox', 'provider', 'list', candidate], env=env))
        if any(line.split() and line.split()[0] == provider['name'] for line in table.splitlines()):
            attached.append(candidate)
    if any(name != sandbox for name in attached):
        raise ValueError('stale MCP provider is attached outside its owner sandbox; preserve it for review')
    if sandbox in attached:
        call(cli + ['sandbox', 'provider', 'detach', sandbox, provider['name']], env=env)
        verify = ANSI.sub('', call(cli + ['sandbox', 'provider', 'list', sandbox], env=env))
        if any(line.split() and line.split()[0] == provider['name'] for line in verify.splitlines()):
            raise ValueError('native stale MCP provider detach did not verify')
    call(cli + ['provider', 'delete', provider['name']], env=env)
    after = json.loads(call(cli + ['provider', 'list', '--output', 'json'], env=env))
    if any(row.get('name') == provider['name'] for row in after):
        raise ValueError('native stale MCP provider deletion did not verify')


def network_candidate(before: dict, sandbox: str) -> dict:
    """Add the release-pinned real interpreter to only the existing MCP route."""
    after = copy.deepcopy(before)
    entry = after['network_policies']['mcp_bridge_homecompute']
    endpoints = entry['endpoints']
    if len(endpoints) != 1:
        raise ValueError('one exact generated MCP endpoint required')
    endpoint = endpoints[0]
    expected = {'host': 'homecompute-capabilities.home.arpa', 'port': 19445, 'path': '/mcp',
                'protocol': 'mcp', 'enforcement': 'enforce', 'allowed_ips': ['10.77.20.1'],
                'credential_binding': {'provider': sandbox + '-mcp-homecompute'},
                'mcp': {'max_body_bytes': 131072, 'strict_tool_names': True, 'allow_all_known_mcp_methods': False}}
    if any(endpoint.get(key) != value for key, value in expected.items()):
        raise ValueError('generated MCP route differs from the trusted endpoint')
    binaries = [{'path': '/usr/local/bin/hermes'}, {'path': '/usr/bin/python3*'},
                {'path': '/opt/hermes/.venv/bin/python*'}]
    if entry.get('binaries') not in (binaries, binaries + [{'path': PYTHON}]):
        raise ValueError('unexpected MCP executable grant')
    entry['binaries'] = binaries + [{'path': PYTHON}]
    return after


def network_setup(sandbox: str, env: dict) -> None:
    if sandbox != 'hermes-personal-v23':
        return
    observed = call(['nemohermes', sandbox, 'exec', '--', 'python3', '-c',
                     'import os; print(os.readlink("/proc/self/exe"))'], env=env).strip()
    if observed != PYTHON:
        raise ValueError('selected Hermes interpreter differs from the source pin')
    cli = ['openshell', '-g', 'nemoclaw', 'policy']
    arguments = ['get', sandbox, '--base', '-o', 'json']
    before = json.loads(call(cli + arguments, env=env))['policy']
    after = network_candidate(before, sandbox)
    if before == after:
        return
    root = Path.home() / '.local/state/homecompute/hermes-mcp-policy'
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('private nonsymlink policy backup directory required')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('private operator-owned policy backup directory required')
    backup = root / str(time.time_ns()); backup.mkdir(mode=0o700)
    for name, value in (('before', before), ('after', after)):
        path = backup / ('policy-' + name + '.json')
        with path.open('x') as stream:
            os.chmod(path, 0o600); stream.write(json.dumps(value, sort_keys=True))
    if json.loads(call(cli + arguments, env=env))['policy'] != before:
        raise ValueError('native policy changed during preparation')
    call(cli + ['set', sandbox, '--policy', str(backup / 'policy-after.json'), '--wait', '--timeout', '45'], env=env)
    if json.loads(call(cli + arguments, env=env))['policy'] != after:
        raise ValueError('native policy readback differs; preserve backups and inspect before retry')


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


def connect(sandbox: str, token: str, *, repair_legacy_registration: bool = False) -> dict:
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
    repair_backup = None
    if existing:
        if len(existing) != 1:
            raise ValueError('existing MCP registration differs; preserve it for review')
        list_row = existing[0]
        if list_row.get('url') != URL:
            raise ValueError('existing MCP registration differs; preserve it for review')
        if list_row.get('env', {}).get('names') != [KEY]:
            if not repair_legacy_registration:
                raise ValueError('existing MCP registration differs; preserve it for review')
            old_status = json.loads(call(cli + ['mcp', 'status', 'homecompute', '--json'], env=env))
            if not legacy_boundary_conflict(list_row, old_status, sandbox):
                raise ValueError('existing MCP registration is outside the reviewed legacy boundary-conflict case')
            repair_backup = save_legacy_registration_backup(sandbox, list_row, old_status)
            call(cli + ['mcp', 'remove', 'homecompute', '--force'], env=env)
            after_remove = json.loads(call(cli + ['mcp', 'list', '--json'], env=env))['bridges']
            if any(row.get('server') == 'homecompute' for row in after_remove):
                raise ValueError('native MCP removal did not verify; preserve its backup and inspect status')
            clear_stale_provider(sandbox, env)
            call(cli + ['mcp', 'add', 'homecompute', '--url', URL, '--env', KEY,
                        '--trusted-private-host', 'homecompute-capabilities.home.arpa'],
                 env={**env, KEY: token})
        elif list_row.get('url') != URL or list_row.get('env', {}).get('names') != [KEY]:
            raise ValueError('existing MCP registration differs; preserve it for review')
    else:
        if repair_legacy_registration:
            repair_backup = private_legacy_backup(sandbox)
        if repair_backup:
            clear_stale_provider(sandbox, env)
        # The native CLI owns policy, provider, opaque handles and gateway reload.
        # An unknown add outcome is left untouched for native reconciliation.
        call(cli + ['mcp', 'add', 'homecompute', '--url', URL, '--env', KEY,
                    '--trusted-private-host', 'homecompute-capabilities.home.arpa'], env={**env, KEY: token})
    token = ''
    network_setup(sandbox, env)
    status = json.loads(call(cli + ['mcp', 'status', 'homecompute', '--json'], env=env))
    row = status_row(status)
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
    android_expected = hashlib.sha256((ANDROID_SKILL / 'SKILL.md').read_bytes()).hexdigest()
    android_program = '''import hashlib,pathlib
p=pathlib.Path('/sandbox/.hermes/skills/android-gateway/SKILL.md')
assert not p.is_symlink() and not p.parent.is_symlink()
print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent')
'''
    android_current = call(cli + ['exec', '--stdin', '--', 'python3', '-'], input=android_program, env=env).strip()
    if android_current not in {'absent', android_expected}:
        raise ValueError('existing Android skill differs; preserve private edits')
    if android_current == 'absent':
        call(cli + ['skill', 'install', str(ANDROID_SKILL)], env=env)
    android_installed = call(cli + ['exec', '--stdin', '--', 'python3', '-'], input=android_program, env=env).strip()
    if android_installed != android_expected:
        raise ValueError('native Android skill readback differs')
    result = {'sandbox': sandbox, 'server': 'homecompute', 'credential_verified': True,
              'skill_sha256': expected, 'android_skill_sha256': android_expected}
    if repair_backup:
        result['replaced_legacy_registration_backup'] = repair_backup
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--sandbox', choices=('hermes-personal', 'hermes-personal-v23'),
                        default='hermes-personal-v23')
    parser.add_argument('--repair-legacy-registration', action='store_true',
                        help='replace only the verified stale host-only credential conflict, keeping a private metadata backup')
    args = parser.parse_args()
    try:
        spec = importlib.util.spec_from_file_location('setup_hermes_mcp', ROOT / 'scripts/setup-hermes-mcp.py')
        setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
        setup.published(args.revision)
        print(json.dumps(connect(args.sandbox, sys.stdin.read(4097).strip(),
                                 repair_legacy_registration=args.repair_legacy_registration)))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if isinstance(error, ValueError):
            print(str(error), file=sys.stderr)
        print('Hermes MCP connection refused or unverified; inspect native status without removing its state.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
