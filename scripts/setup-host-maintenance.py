#!/usr/bin/env python3
"""Install reviewed daily Docker and host package maintenance on one compute host.

The default prints a bundle hash without staging files or starting services.
Installation preserves private state, backs up previous reviewed code/tracking,
and starts only the timer. --run uses fixed root-owned installed code; routine
update authorization, major holds and durable attempt policy belong to its runner.
No environment files, credentials, or live deployment configuration are copied.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

DEST = Path('/opt/homecompute/host-maintenance')
CONTROLLER = Path('/opt/homecompute/infrastructure-maintenance')
STATE = Path('/var/lib/homecompute-host-maintenance')
PUBLIC = Path('/var/lib/homecompute-host-maintenance-public')
NAME = 'homecompute-host-update'
HOSTS = {'home-core', 'home-spark'}
FILES = ('scripts/setup-host-maintenance.py', 'scripts/host_maintenance.py',
         'scripts/host_maintenance_sources.py', 'scripts/core_update_build.py',
         'config/docker-image-tracking.json')


def safe(path, *, directory=False, root=True):
    """Installed ancestors must be root-owned; exact hashes authorize source bytes."""
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
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bundle(source):
    contents, digest = {}, hashlib.sha256()
    for name in FILES:
        path = safe(Path(source) / name, root=False)
        if path.stat().st_size > 524288:
            raise ValueError('bounded reviewed source required')
        data = path.read_bytes()
        if name.endswith('.py'):
            compile(data, name, 'exec')
        elif not isinstance(json.loads(data), dict):
            raise ValueError('tracking object required')
        contents[name] = data
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return contents, digest.hexdigest()


def layout(host):
    if host not in HOSTS:
        raise ValueError('reviewed compute host required')
    return ((Path('/etc/systemd/system.attached'), '/run/current-system/sw/bin/python3')
            if host == 'home-core' else (Path('/etc/systemd/system'), '/usr/bin/python3'))


def service(host):
    _, python = layout(host)
    return f'''[Unit]
Description=HomeCompute daily routine Docker and host package updates
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
ExecStart={python} -I {DEST}/setup-host-maintenance.py --run --host {host}
Environment=PATH=/run/current-system/sw/bin:/usr/bin:/bin
Environment=HOME=/root
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
UMask=0077
TimeoutStartSec=7200
TimeoutStopSec=30
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
TasksMax=512
'''


def timer():
    return f'''[Unit]
Description=Daily HomeCompute routine updates
[Timer]
OnCalendar=*-*-* 05:55:00 Europe/Copenhagen
Persistent=true
AccuracySec=1min
Unit={NAME}.service
[Install]
WantedBy=timers.target
'''


def require_host(host):
    if host not in HOSTS or os.geteuid() != 0 or socket.gethostname().split('.')[0] != host:
        raise ValueError('root on the selected compute host required')


def preflight(paths):
    for target in paths:
        existing = target
        while not existing.exists() and not existing.is_symlink():
            existing = existing.parent
        safe(existing, directory=existing != target)


def dependency(link, target):
    if (link.is_symlink() and os.readlink(link) != str(target)
            or link.exists() and not link.is_symlink()):
        raise ValueError('unmanaged timer boot dependency')


@contextmanager
def installation_lock(exclusive=False):
    """Do not replace the import bundle while a scheduled wrapper is using it."""
    safe(STATE, directory=True)
    lock = STATE / 'installation.lock'
    if lock.exists() or lock.is_symlink():
        safe(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (info.st_uid != 0 or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077):
            raise ValueError('private installation lock required')
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install(source, expected, host):
    require_host(host)
    files, actual = bundle(source)
    if actual != expected:
        raise ValueError('exact reviewed bundle hash required')
    safe(CONTROLLER / 'infrastructure_maintenance.py')
    units, _ = layout(host)
    service_path, timer_path = units / (NAME + '.service'), units / (NAME + '.timer')
    link = units / 'timers.target.wants' / timer_path.name
    preflight([*(DEST / Path(name).name for name in files), service_path, timer_path,
               PUBLIC / 'report.json', STATE])
    dependency(link, timer_path)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', service_path.name], check=False)
    if active.returncode == 0:
        raise ValueError('active maintenance must finish before installation')
    directory(STATE, 0o700)
    with installation_lock(exclusive=True):
        return stage(files, actual, host, units, service_path, timer_path, link)


def stage(files, actual, host, units, service_path, timer_path, link):
    for path, mode in ((DEST, 0o755), (STATE, 0o700), (PUBLIC, 0o755),
                       (units, 0o755), (units / 'timers.target.wants', 0o755)):
        directory(path, mode)
    backup = STATE / 'installation-backups' / uuid.uuid4().hex
    directory(backup, 0o700)
    for name, data in files.items():
        target = DEST / Path(name).name
        if target.exists() or target.is_symlink():
            safe(target)
            write(backup / target.name, target.read_bytes(), 0o600)
        write(target, data)
    for target, data in ((service_path, service(host)), (timer_path, timer())):
        if target.exists() or target.is_symlink():
            safe(target)
            write(backup / target.name, target.read_bytes(), 0o600)
        write(target, data.encode())
    dependency(link, timer_path)
    if not link.is_symlink():
        link.symlink_to(timer_path)
    report = PUBLIC / 'report.json'
    if report.exists() or report.is_symlink():
        safe(report)
    else:
        initial = {'schema_version': 1, 'host': host, 'generated_at': datetime.now(timezone.utc).isoformat(),
                   'items': [], 'summary': {'blocked': 1}}
        write(report, (json.dumps(initial, sort_keys=True) + '\n').encode())
    subprocess.run(['systemd-analyze', 'verify', str(service_path), str(timer_path)], check=True, capture_output=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'restart', timer_path.name], check=True, capture_output=True)
    return {'installed': True, 'host': host, 'bundle_sha256': actual, 'timer': timer_path.name,
            'updates_run': 0, 'state_preserved': True, 'backup': str(backup)}


def load_runtime():
    # -I isolates the interpreter; both import trees must remain root-controlled.
    for parent in (CONTROLLER, DEST):
        safe(parent, directory=True)
        for path in parent.glob('*.py'):
            safe(path)
    for path in (CONTROLLER / 'infrastructure_maintenance.py', DEST / 'host_maintenance.py',
                 DEST / 'host_maintenance_sources.py', DEST / 'core_update_build.py',
                 DEST / 'docker-image-tracking.json'):
        safe(path)
    sys.path[:0] = [str(DEST), str(CONTROLLER)]
    return importlib.import_module('host_maintenance')


def run(host):
    require_host(host)
    safe(STATE, directory=True); safe(PUBLIC, directory=True)
    with installation_lock():
        return load_runtime().run(host)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--host', choices=sorted(HOSTS))
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    try:
        host = args.host or socket.gethostname().split('.')[0]
        if args.run:
            if args.source or args.bundle_sha256:
                raise ValueError('execution uses only installed paths')
            result = run(host)
        elif args.source:
            result = install(args.source, args.bundle_sha256, host) if args.bundle_sha256 else {
                'bundle_sha256': bundle(args.source)[1], 'installed': False, 'updates_run': 0}
        else:
            raise ValueError('source or fixed run selector required')
        print(json.dumps(result)); return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError):
        print('Host maintenance refused; preserve private state and reconcile the root ledger.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
