#!/usr/bin/env python3
"""Stage or activate only the approved daily metadata collector on its own host.

Dry-run never writes persistent host state. Installation requires the dry-run's
source digest; activation is a separate explicit flag. No application update,
image pull, application restart, host reboot, or remote SSH command is executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any

DEST = Path('/opt/homecompute/maintenance')
STATE = Path('/var/lib/homecompute-maintenance')
BACKUPS = STATE / 'deploy-backups'
NAME = 'homecompute-maintenance-monitor'
FILES = ('scripts/check-maintenance.py', 'scripts/package_updates.py',
         'scripts/docker_image_updates.py', 'config/docker-image-tracking.json')


def call(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def source_bundle(source: Path) -> tuple[dict[str, bytes], str]:
    if source.is_symlink() or not source.is_dir() or source.stat().st_mode & 0o022:
        raise ValueError('source must be a directory protected from group/other writes')
    files = {}
    digest = hashlib.sha256()
    for name in FILES:
        path = source / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022
                or path.stat().st_size > 1024 * 1024
                or any(p.is_symlink() for p in (path.parent, path.parent.parent))):
            raise ValueError('source bundle contains an unsafe file')
        content = path.read_bytes()
        if name.endswith('.json'):
            policy = json.loads(content)
            if not isinstance(policy, dict) or not isinstance(policy.get('hosts'), dict) or not {'home-core', 'home-spark'} <= policy['hosts'].keys():
                raise ValueError('tracking policy must include both reviewed hosts')
        else:
            compile(content, name, 'exec')
        files[name] = content
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    return files, digest.hexdigest()


def layout(host: str) -> tuple[Path, Path]:
    if host == 'home-core':
        return Path('/etc/systemd/system.attached'), Path('/run/current-system/sw/bin/python3')
    if host == 'home-spark':
        return Path('/etc/systemd/system'), Path('/usr/bin/python3')
    raise ValueError('unreviewed host')


def service(host: str, python: Path) -> str:
    executable_path = '/run/current-system/sw/bin' if host == 'home-core' else '/usr/bin:/bin'
    # systemd255 drops CAP_SETUID while applying seccomp unless it is ambient.
    # Only Spark refreshes public APT indexes under the unprivileged _apt UID.
    ambient = 'CAP_SETUID' if host == 'home-spark' else ''
    return f'''[Unit]
Description=HomeCompute daily package and Docker manifest review
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=oneshot
User=root
WorkingDirectory={DEST}
Environment=PATH={executable_path}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=HOME=/run/homecompute-maintenance
Environment=DOCKER_CONFIG=/run/homecompute-maintenance/docker
Environment=DOCKER_HOST=unix:///var/run/docker.sock
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy DOCKER_CONTEXT
ExecStart={python} {DEST}/scripts/check-maintenance.py --host {host} --tracking {DEST}/config/docker-image-tracking.json --state {STATE}
TimeoutStartSec=15min
StateDirectory=homecompute-maintenance
StateDirectoryMode=0755
RuntimeDirectory=homecompute-maintenance
RuntimeDirectoryMode=0700
UMask=0022
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_CHOWN CAP_DAC_OVERRIDE CAP_KILL
AmbientCapabilities={ambient}
DevicePolicy=closed
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths={STATE}
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
MemoryMax=512M
TasksMax=128
LimitCORE=0
StandardOutput=journal
StandardError=journal
'''


def timer() -> str:
    return f'''[Unit]
Description=Daily HomeCompute maintenance metadata collection
[Timer]
OnCalendar=*-*-* 05:30:00 Europe/Copenhagen
RandomizedDelaySec=15min
AccuracySec=1min
Persistent=yes
Unit={NAME}.service
[Install]
WantedBy=timers.target
'''


def unit_paths(host: str) -> tuple[Path, Path, Path]:
    directory, _ = layout(host)
    return directory / f'{NAME}.service', directory / f'{NAME}.timer', directory / 'timers.target.wants' / f'{NAME}.timer'


def managed_files(host: str) -> list[Path]:
    return [DEST / name for name in FILES] + list(unit_paths(host))


def snapshot(paths: list[Path], backup: Path) -> list[dict[str, Any]]:
    rows = []
    for index, path in enumerate(paths):
        row: dict[str, Any] = {'path': str(path), 'state': 'absent'}
        if path.is_symlink():
            row.update(state='symlink', target=os.readlink(path))
        elif path.exists():
            meta = path.stat()
            if not stat.S_ISREG(meta.st_mode):
                raise ValueError('managed target must be a file')
            filename = f'file-{index}'
            shutil.copyfile(path, backup / filename)
            (backup / filename).chmod(0o600)
            row.update(state='regular', backup=filename, mode=stat.S_IMODE(meta.st_mode), uid=meta.st_uid, gid=meta.st_gid)
        rows.append(row)
    return rows


def write(path: Path, content: bytes, mode: int) -> None:
    if path.is_symlink():
        raise ValueError('managed destination symlink prohibited')
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def safe_directories() -> None:
    for path in (DEST, DEST / 'scripts', DEST / 'config', STATE, BACKUPS):
        if path.is_symlink():
            raise ValueError('managed directory symlink prohibited')
        if path.exists() and (not path.is_dir() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
            raise ValueError('managed directory must be root-owned and protected')


def active(name: str) -> bool:
    return call('systemctl', 'is-active', name, check=False).stdout.strip() in {'active', 'activating', 'reloading'}


def verify_units(host: str, python: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='hc-maintenance-unit-check-') as directory:
        root = Path(directory)
        (root / f'{NAME}.service').write_text(service(host, python))
        (root / f'{NAME}.timer').write_text(timer())
        call('systemd-analyze', 'verify', str(root / f'{NAME}.service'), str(root / f'{NAME}.timer'))


def install(host: str, files: dict[str, bytes], digest: str, activate: bool) -> dict[str, Any]:
    safe_directories()
    directory, python = layout(host)
    real_python = python.resolve(strict=True)
    if host == 'home-core' and not str(real_python).startswith('/nix/store/'):
        raise ValueError('core Python must resolve to the reviewed Nix runtime')
    pinned_python = real_python if host == 'home-core' else python
    verify_units(host, pinned_python)
    for path in managed_files(host):
        if path.exists() and not path.is_symlink() and path.stat().st_uid != 0:
            raise ValueError('refuse replacing a non-root-owned managed target')
    STATE.mkdir(parents=True, mode=0o755, exist_ok=True)
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    BACKUPS.chmod(0o700)
    backup = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(mode=0o700)
    metadata = {'schema_version': 1, 'host': host, 'source_sha256': digest,
                'timer_active': active(NAME + '.timer'), 'unit': NAME,
                'files': snapshot(managed_files(host), backup)}
    # Preserve the old report privately, but never restore/overwrite a report:
    # collection freshness must not move backwards during a software rollback.
    report = STATE / 'report.json'
    if report.is_symlink():
        raise ValueError('report symlink prohibited')
    if report.is_file():
        shutil.copyfile(report, backup / 'report-before.json')
        (backup / 'report-before.json').chmod(0o600)
    write(backup / 'metadata.json', (json.dumps(metadata, indent=2) + '\n').encode(), 0o600)
    if metadata['timer_active']:
        call('systemctl', 'stop', NAME + '.timer')
    if active(NAME + '.service'):
        raise ValueError('collector is running; preserved backup and stopped its timer, wait for completion before staging')
    for name, content in files.items():
        write(DEST / name, content, 0o744 if name.endswith('.py') else 0o644)
    unit, timer_path, link = unit_paths(host)
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    write(unit, service(host, pinned_python).encode(), 0o644)
    write(timer_path, timer().encode(), 0o644)
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise ValueError('target dependency must be a symlink')
    if host == 'home-core':
        roots = Path('/nix/var/nix/gcroots/homecompute-maintenance')
        roots.mkdir(mode=0o755, exist_ok=True)
        root = roots / 'python'
        if root.is_symlink():
            root.unlink()
        elif root.exists():
            raise ValueError('runtime GC root must be a symlink')
        root.symlink_to(real_python.parent.parent)
    call('systemctl', 'daemon-reload')
    verify_units(host, pinned_python)
    if activate:
        link.parent.mkdir(mode=0o755, exist_ok=True)
        link.symlink_to(timer_path)
        call('systemctl', 'daemon-reload')
        call('systemctl', 'start', NAME + '.timer')
    return {'host': host, 'installed': True, 'timer_activated': activate, 'collector_started': False,
            'source_sha256': digest, 'backup': str(backup), 'report_preserved': True}


def rollback(host: str, backup: Path, activate: bool) -> dict[str, Any]:
    safe_directories()
    if backup.is_symlink() or backup.parent != BACKUPS or backup.stat().st_uid != 0 or backup.stat().st_mode & 0o077:
        raise ValueError('rollback requires a protected deployment backup for this monitor')
    metadata_file = backup / 'metadata.json'
    if metadata_file.is_symlink():
        raise ValueError('backup metadata symlink prohibited')
    metadata = json.loads(metadata_file.read_text())
    rows = metadata.get('files')
    allowed = {str(path) for path in managed_files(host)}
    if (metadata.get('schema_version') != 1 or metadata.get('host') != host or metadata.get('unit') != NAME
            or not isinstance(rows, list) or {row.get('path') for row in rows} != allowed or len(rows) != len(allowed)):
        raise ValueError('rollback metadata does not match this monitor')
    call('systemctl', 'stop', NAME + '.timer', check=False)
    if active(NAME + '.service'):
        raise ValueError('collector is running; timer stopped, wait for completion before rollback')
    # Validate every entry before changing any file; only the reviewed paths
    # from managed_files can be restored, and report.json is not among them.
    for row in rows:
        if row.get('state') not in {'absent', 'symlink', 'regular'}:
            raise ValueError('invalid rollback file state')
        if row['state'] == 'regular':
            filename = row.get('backup', '')
            if not re.fullmatch(r'file-[0-9]+', filename) or (backup / filename).is_symlink() or not (backup / filename).is_file():
                raise ValueError('invalid rollback content path')
        elif row['state'] == 'symlink':
            # Only a target dependency is managed as a symlink.
            if row['path'] != str(unit_paths(host)[2]) or row.get('target') != str(unit_paths(host)[1]):
                raise ValueError('unreviewed rollback symlink')
    for row in rows:
        path = Path(row['path'])
        if path.is_symlink() or path.is_file():
            path.unlink()
        if row['state'] == 'regular':
            write(path, (backup / row['backup']).read_bytes(), row['mode'])
            os.chown(path, row['uid'], row['gid'])
        elif row['state'] == 'symlink' and activate:
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            path.symlink_to(row['target'])
    call('systemctl', 'daemon-reload')
    if activate and metadata.get('timer_active') is True:
        call('systemctl', 'start', NAME + '.timer')
    return {'host': host, 'rolled_back': True, 'report_preserved': True, 'timer_reactivated': activate and metadata.get('timer_active') is True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=('home-core', 'home-spark'), required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--activate', action='store_true', help='explicitly start only this monitor timer after install/rollback')
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--dry-run', action='store_true')
    actions.add_argument('--install', action='store_true')
    actions.add_argument('--rollback', type=Path)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or socket.gethostname().split('.')[0] != args.host:
            raise ValueError('requires root on the selected host')
        if args.activate and args.dry_run:
            raise ValueError('dry-run cannot activate')
        if args.rollback:
            result = rollback(args.host, args.rollback, args.activate)
        else:
            if args.source is None:
                raise ValueError('source bundle required')
            files, digest = source_bundle(args.source)
            if args.expected_sha256 is not None and args.expected_sha256 != digest:
                raise ValueError('source digest differs from reviewed dry-run')
            if args.install and not args.expected_sha256:
                raise ValueError('installation requires --expected-sha256 from a reviewed dry-run')
            directory, python = layout(args.host)
            if args.dry_run:
                safe_directories()
                if not python.is_file():
                    raise ValueError('reviewed host Python unavailable')
                python = python.resolve(strict=True) if args.host == 'home-core' else python
                verify_units(args.host, python)
                result = {'host': args.host, 'dry_run': True, 'source_sha256': digest,
                          'destination': str(DEST), 'state': str(STATE), 'unit_directory': str(directory),
                          'python': str(python), 'schedule': '*-*-* 05:30:00 Europe/Copenhagen',
                          'randomized_delay_seconds': 900, 'persistent': True, 'timer_activated': False}
            else:
                result = install(args.host, files, digest, args.activate)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError):
        print('Maintenance monitor setup refused; preserve backups/state and review the fixed inputs.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
