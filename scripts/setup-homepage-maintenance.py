#!/usr/bin/env python3
"""Install finite Homepage check service; starts no production update."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess

DEST = Path('/opt/homecompute/homepage-maintenance')
UNITS = Path('/etc/systemd/system.attached')
STATE = Path('/var/lib/homecompute/homepage-maintenance')
PUBLIC = Path('/var/lib/homecompute-homepage-maintenance-public')
REQUEST = Path('/var/lib/homecompute-openclaw/maintenance-requests')
NAME = 'homecompute-homepage-update-check'
PYTHON = '/run/current-system/sw/bin/python3'
FILES = ('homepage_maintenance.py', 'homepage_update_check.py', 'docker_image_updates.py')


def service() -> str:
    return f'''[Unit]
Description=Read-only Homepage update proposal check
After=network-online.target docker.service
StartLimitIntervalSec=60
StartLimitBurst=2
[Service]
Type=oneshot
ExecStart={PYTHON} {DEST}/homepage_update_check.py check
Environment=PATH=/run/current-system/sw/bin
Environment=HOME=/var/empty
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
UMask=0077
TimeoutStartSec=180
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
NoNewPrivileges=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths={STATE} {PUBLIC} /etc/homecompute/homepage-maintenance-candidate.json /var/lib/homecompute/deploy.lock
MemoryMax=256M
TasksMax=32
LimitCORE=0
'''


def path_unit() -> str:
    return f'''[Unit]
Description=Finite Homepage metadata check request
[Path]
PathChanged={REQUEST}/homepage.check
Unit={NAME}.service
TriggerLimitIntervalSec=60
TriggerLimitBurst=10
[Install]
WantedBy=multi-user.target
'''


def directory(path: Path, mode: int, uid: int = 0, gid: int = 0) -> None:
    if path.is_symlink():
        raise ValueError('directory symlink refused')
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.stat().st_uid != uid or path.stat().st_mode & 0o022:
        raise ValueError('existing directory ownership or mode is unsafe')
    os.chown(path, uid, gid)
    path.chmod(mode)


def write(path: Path, data: bytes, mode: int = 0o644) -> None:
    if path.is_symlink() or path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
        raise ValueError('unsafe installed file')
    temporary = path.with_suffix('.install-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path) -> None:
    for name in FILES:
        path = source / 'scripts' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('trusted source required')
        compile(path.read_bytes(), str(path), 'exec')
    operator = pwd.getpwnam('homecompute-openclaw')
    directory(DEST.parent, 0o755)
    directory(DEST, 0o755)
    directory(STATE.parent, 0o700)
    directory(STATE, 0o700)
    directory(PUBLIC, 0o755)
    # Account-owned request contains no arguments and grants only a metadata check.
    if not REQUEST.exists():
        REQUEST.mkdir(mode=0o700)
        os.chown(REQUEST, operator.pw_uid, operator.pw_gid)
    directory(REQUEST, 0o700, operator.pw_uid, operator.pw_gid)
    request = REQUEST / 'homepage.check'
    if not request.exists():
        fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd); os.chown(request, operator.pw_uid, operator.pw_gid)
    info = request.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != operator.pw_uid
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):
        raise ValueError('unsafe fixed request file')
    for path in (Path('/etc/homecompute/homepage-maintenance-candidate.json'), STATE.parent / 'deploy.lock'):
        if not path.exists():
            write(path, b'{}\n', 0o600)
    for name in FILES:
        write(DEST / name, (source / 'scripts' / name).read_bytes())
    directory(UNITS, 0o755)
    directory(UNITS / 'multi-user.target.wants', 0o755)
    write(UNITS / (NAME + '.service'), service().encode())
    write(UNITS / (NAME + '.path'), path_unit().encode())
    link = UNITS / 'multi-user.target.wants' / (NAME + '.path')
    target = UNITS / (NAME + '.path')
    if link.is_symlink() and link.resolve() != target:
        raise ValueError('unmanaged boot link')
    if not link.exists():
        link.symlink_to(target)
    subprocess.run(['systemd-analyze', 'verify', str(UNITS / (NAME + '.service')), str(target)], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    # NixOS /etc/systemd/system is immutable; the attached boot link above owns enablement.
    subprocess.run(['systemctl', 'start', NAME + '.path'], check=True)
    subprocess.run(['systemctl', 'start', NAME + '.service'], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        parser.error('root on home-core required')
    install(args.source.resolve(strict=True))


if __name__ == '__main__':
    main()
