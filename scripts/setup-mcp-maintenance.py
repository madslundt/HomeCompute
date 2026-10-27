#!/usr/bin/env python3
"""Install reviewed MCP recipes and a weekly routine-only maintenance schedule.

Default is a hash-only dry run. Installation starts the timer, never its update
service. Private state/candidates survive installation; prior recipes are backed
up. A failed or interrupted automatic attempt needs root reconciliation of
weekly-attempts.json before another automatic deployment of that component.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

DEST = Path('/opt/homecompute/mcp-maintenance')
CONTROLLER = Path('/opt/homecompute/infrastructure-maintenance')
STATE = Path('/var/lib/homecompute-mcp-maintenance')
PUBLIC = Path('/var/lib/homecompute-mcp-maintenance-public')
UNITS = Path('/etc/systemd/system.attached')
LIBEXEC = Path('/usr/local/libexec/homecompute-mcp-maintenance')
PYTHON = '/run/current-system/sw/bin/python3'
NAME = 'homecompute-mcp-update'
COMPONENTS = ('aula-mcp', 'tilbudstrolden-mcp')
RECIPES = ('compose.yaml', 'production.yaml', 'aula.Dockerfile', 'aula-n8n.patch',
           'tilbudstrolden.Dockerfile', 'tilbudstrolden-security.patch',
           'tilbudstrolden-gateway/package.json', 'tilbudstrolden-gateway/package-lock.json')
FILES = ('scripts/setup-mcp-maintenance.py', *(f'deploy/automation/{name}' for name in RECIPES))


def safe(path, *, directory=False, root=True):
    """Installed paths require root-owned ancestors; source bytes are hash-bound."""
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or (root or item == path) and info.st_mode & 0o022
                or root and info.st_uid != 0):
            raise ValueError('trusted non-writable path required')
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('trusted directory required')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('single-link regular file required')
    return path


def directory(path, mode=0o755):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        if not path.parent.exists():
            directory(path.parent)
        safe(path.parent, directory=True)
        path.mkdir(mode=mode)
    safe(path, directory=True)
    path.chmod(mode)


def write(path, data, mode=0o644):
    path = Path(path)
    safe(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        safe(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value, mode=0o600):
    write(path, (json.dumps(value, sort_keys=True) + '\n').encode(), mode)


def bundle(source):
    contents, digest = {}, hashlib.sha256()
    for name in FILES:
        path = safe(Path(source) / name, root=False)
        if path.stat().st_size > 524288:
            raise ValueError('bounded reviewed source required')
        data = path.read_bytes()
        if name.endswith('.py'):
            compile(data, name, 'exec')
        elif name.endswith('.json'):
            json.loads(data)
        contents[name] = data
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return contents, digest.hexdigest()


def service():
    return f'''[Unit]
Description=HomeCompute weekly reviewed MCP routine updates
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
ExecStart={PYTHON} -I {DEST}/setup-mcp-maintenance.py --weekly
Environment=PATH=/run/current-system/sw/bin:/usr/bin:/bin
Environment=HOME=/root
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
UMask=0077
TimeoutStartSec=3600
TimeoutStopSec=20
KillMode=control-group
Restart=no
NoNewPrivileges=yes
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectClock=yes
RestrictSUIDSGID=yes
LimitCORE=0
TasksMax=256
'''


def timer():
    return f'''[Unit]
Description=Weekly Aula and TilbudsTrolden updates
[Timer]
OnCalendar=Mon *-*-* 09:20:00 Europe/Copenhagen
Persistent=true
AccuracySec=1min
Unit={NAME}.service
[Install]
WantedBy=timers.target
'''


def helper():
    # Validate the import tree before exposing it to Python's module loader.
    return f'''#!{PYTHON} -I
import os, pathlib, stat, sys
if os.geteuid() != 0:
    raise SystemExit("root required")
p = pathlib.Path("{CONTROLLER}")
for q in [*reversed(p.parents), p, *p.glob("*.py")]:
    s = q.lstat()
    if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise SystemExit("trusted installed code required")
    if q.suffix == ".py" and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1):
        raise SystemExit("regular installed code required")
sys.path.insert(0, str(p))
from mcp_maintenance import main
raise SystemExit(main())
'''


def require_host():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('root on home-core required')


def runtime():
    safe(CONTROLLER, directory=True)
    for path in CONTROLLER.glob('*.py'):
        safe(path)
    safe(CONTROLLER / 'mcp_maintenance.py')
    sys.path.insert(0, str(CONTROLLER))
    from infrastructure_maintenance import Controller
    from mcp_maintenance import prepare
    return Controller(), prepare


def report(rows):
    return {'schema_version': 1, 'host': 'home-core',
            'generated_at': datetime.now(timezone.utc).isoformat(), 'components': rows}


def install(source, expected):
    require_host()
    files, actual = bundle(source)
    if actual != expected:
        raise ValueError('exact reviewed bundle hash required')
    safe(CONTROLLER / 'mcp_maintenance.py')
    # Reject existing untrusted targets before changing any reviewed recipe.
    targets = [DEST / ('setup-mcp-maintenance.py' if name.startswith('scripts/') else
                       'automation/' + name.removeprefix('deploy/automation/')) for name in files]
    targets += [LIBEXEC, PUBLIC / 'report.json', UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')]
    for target in targets:
        existing = target
        while not existing.exists() and not existing.is_symlink():
            existing = existing.parent
        safe(existing, directory=existing != target)
    link = UNITS / 'timers.target.wants' / (NAME + '.timer')
    if link.is_symlink() and os.readlink(link) != str(UNITS / (NAME + '.timer')) or link.exists() and not link.is_symlink():
        raise ValueError('unmanaged timer dependency')
    # Never replace code/templates while a scheduled attempt is active.
    active = subprocess.run(['systemctl', 'is-active', '--quiet', NAME + '.service'], check=False)
    if active.returncode == 0:
        raise ValueError('active maintenance must finish before installation')
    for path, mode in ((DEST, 0o755), (STATE, 0o700), (PUBLIC, 0o755),
                       (UNITS, 0o755), (UNITS / 'timers.target.wants', 0o755),
                       (LIBEXEC.parent, 0o755)):
        directory(path, mode)
    backup = STATE / 'recipe-backups' / uuid.uuid4().hex
    directory(backup, 0o700)
    for name, data in files.items():
        relative = 'setup-mcp-maintenance.py' if name.startswith('scripts/') else 'automation/' + name.removeprefix('deploy/automation/')
        target = DEST / relative
        directory(target.parent)
        if target.exists() or target.is_symlink():
            safe(target)
            previous = backup / relative
            directory(previous.parent, 0o700)
            write(previous, target.read_bytes(), 0o600)
        write(target, data)
    write(LIBEXEC, helper().encode(), 0o755)
    service_path, timer_path = UNITS / (NAME + '.service'), UNITS / (NAME + '.timer')
    write(service_path, service().encode()); write(timer_path, timer().encode())
    link = UNITS / 'timers.target.wants' / timer_path.name
    if link.is_symlink():
        if os.readlink(link) != str(timer_path):
            raise ValueError('unmanaged timer dependency')
    elif link.exists():
        raise ValueError('unmanaged timer dependency')
    else:
        link.symlink_to(timer_path)
    if (PUBLIC / 'report.json').exists() or (PUBLIC / 'report.json').is_symlink():
        safe(PUBLIC / 'report.json')
    else:
        write_json(PUBLIC / 'report.json', report([
            {'component': c, 'status': 'blocked', 'reason': 'not_checked'} for c in COMPONENTS]), 0o644)
    subprocess.run(['systemd-analyze', 'verify', str(service_path), str(timer_path)], check=True, capture_output=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'restart', timer_path.name], check=True, capture_output=True)
    return {'installed': True, 'bundle_sha256': actual, 'timer': timer_path.name,
            'updates_run': 0, 'state_preserved': True, 'recipe_backup': str(backup)}


def read_attempts():
    path = STATE / 'weekly-attempts.json'
    if not path.exists() and not path.is_symlink():
        return {}
    safe(path)
    if path.stat().st_size > 65536:
        raise ValueError('bounded scheduler ledger required')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) - set(COMPONENTS):
        raise ValueError('invalid scheduler ledger')
    for row in value.values():
        if not isinstance(row, dict) or row.get('status') not in {'executing', 'succeeded', 'failed', 'uncertain'}:
            raise ValueError('invalid scheduler attempt')
    return value


def run_weekly(controller, prepare):
    """The root ledger reserves an attempt before execution, including crashes."""
    attempts, rows = read_attempts(), []
    for component in COMPONENTS:
        row, attempted = {'component': component}, False
        previous = attempts.get(component, {})
        if previous.get('status') in {'executing', 'failed', 'uncertain'}:
            rows.append(dict(row, status='uncertain' if previous['status'] == 'executing' else 'blocked',
                             reason='operator_reconciliation_required'))
            continue
        try:
            output = STATE / ('weekly-' + uuid.uuid4().hex + '.json')
            metadata = prepare(controller, component, output)
            registration = controller.register(metadata['plan_file'])
            for key in ('installed', 'candidate'):
                value = metadata[key]
                if not isinstance(value, str) or not 1 <= len(value) <= 256:
                    raise ValueError('bounded public version required')
                row[key] = value
            row['plan_id'] = registration['plan_id']
            classification = registration['classification']
            row['classification'] = classification['classification']
            if (classification['classification'] != 'routine' or classification['approval_required']
                    or registration.get('requires_approval')):
                rows.append(dict(row, status='approval_required', reason='fresh_exact_plan_requires_approval'))
                continue
            attempts[component] = {'status': 'executing', 'plan_id': registration['plan_id'],
                                   'candidate': row['candidate']}
            write_json(STATE / 'weekly-attempts.json', attempts)
            attempted = True
            result = controller.run(registration['plan_id'])
            status = result['status']
            attempts[component]['status'] = 'succeeded' if status == 'succeeded' else 'uncertain' if status in {'executing', 'uncertain'} else 'failed'
            write_json(STATE / 'weekly-attempts.json', attempts)
            attempted = False
            row['status'] = attempts[component]['status']
            if row['status'] != 'succeeded':
                row['reason'] = 'operator_reconciliation_required'
            rows.append(row)
        except Exception as error:
            # No subprocess output, repository content, or arbitrary exception text is public.
            if attempted or attempts.get(component, {}).get('status') == 'executing':
                rows.append(dict(row, status='uncertain', reason='operator_reconciliation_required'))
            elif isinstance(error, ValueError) and str(error) == 'no increasing stable MCP release':
                rows.append(dict(row, status='current'))
            else:
                rows.append(dict(row, status='failed', reason='preparation_or_execution_refused'))
    result = report(rows)
    write_json(PUBLIC / 'report.json', result, 0o644)
    return result


def weekly():
    require_host()
    safe(STATE, directory=True); safe(PUBLIC, directory=True)
    lock = STATE / 'weekly.lock'
    if lock.exists() or lock.is_symlink():
        safe(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('private scheduler lock required')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller, prepare = runtime()
        return run_weekly(controller, prepare)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--weekly', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.weekly:
            if args.source or args.bundle_sha256:
                raise ValueError('weekly execution uses only installed paths')
            result = weekly()
        elif args.source:
            result = install(args.source, args.bundle_sha256) if args.bundle_sha256 else {
                'bundle_sha256': bundle(args.source)[1], 'installed': False, 'updates_run': 0}
        else:
            raise ValueError('source or fixed weekly selector required')
        print(json.dumps(result)); return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('MCP maintenance refused; preserve private state and reconcile the root ledger.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
