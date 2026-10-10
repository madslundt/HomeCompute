#!/usr/bin/env python3
"""Stage home-core communication services; activation is explicit and separate.

--install copies only trusted code and enables adapter/feeds for boot, without
starting processes. Private configuration, SSH identity and retained transport
state must be migrated separately. --activate-services publishes machine status
before starting the adapter/feeds and timers. --activate-telegram additionally requires an unpaused retained cursor
and no admission guard. No cursor, token, receipt or native session is reset.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any

DEST = Path('/opt/homecompute/openclaw-communication')
HOME = Path('/var/lib/homecompute-openclaw')
CONFIG = HOME / 'config'
STATE = HOME / 'communication'
UNIT_DIR = Path('/etc/systemd/system.attached')
REPORTS = Path('/run/homecompute-openclaw-reports')
TMPFILES = Path('/etc/tmpfiles.d/homecompute-openclaw.conf')
USER = 'homecompute-openclaw'
PREFIX = 'homecompute-openclaw-'
PYTHON = Path('/run/current-system/sw/bin/python3')
FILES = (
    'scripts/openclaw-communication.py', 'scripts/openclaw-telegram.py',
    'scripts/openclaw-chat.py', 'scripts/openclaw_notifications.py',
    'scripts/openclaw_machine_notifications.py', 'scripts/openclaw_tasks.py',
    'scripts/openclaw_infrastructure.py', 'scripts/openclaw-observation-feed.py',
    'scripts/observe-homecompute.py', 'scripts/homecompute.py',
    'scripts/maintenance_observations.py', 'config/system-monitoring.json',
    'scripts/openclaw-machine-snapshot.py',
)
NAMES = ('adapter', 'telegram', 'task-feed', 'update-feed', 'machine-status')
TIMERS = ('machine-status', 'update-feed')


def call(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def commands(python: Path = PYTHON) -> dict[str, list[str]]:
    scripts = DEST / 'scripts'
    return {
        'adapter': [str(python), str(scripts / 'openclaw-communication.py'),
                    '--config', str(CONFIG / 'communication.json'),
                    '--tokens', str(CONFIG / 'transport-tokens.json'),
                    '--state', str(STATE / 'adapter'),
                    '--task-transport', str(CONFIG / 'codex-task-transport.json'),
                    '--infrastructure-registry', str(DEST / 'config/system-monitoring.json'),
                    '--core-host-mode'],
        'telegram': [str(python), str(scripts / 'openclaw-telegram.py'), 'run',
                     '--config', str(CONFIG / 'telegram.json'),
                     '--tokens', str(CONFIG / 'telegram-tokens.json'),
                     '--state', str(STATE / 'telegram'), '--supervised'],
        'task-feed': [str(python), str(scripts / 'openclaw_tasks.py'),
                      '--transport', str(CONFIG / 'codex-task-transport.json'),
                      '--communication-config', str(CONFIG / 'communication.json'),
                      '--transport-tokens', str(CONFIG / 'transport-tokens.json'),
                      '--cursor', str(STATE / 'task-feed.cursor.json'), '--follow'],
        'update-feed': [str(python), str(scripts / 'openclaw-observation-feed.py'),
                        '--registry', str(DEST / 'config/system-monitoring.json'),
                        '--tokens', str(CONFIG / 'transport-tokens.json'),
                        '--state', str(STATE / 'update-feed'), '--core-host-mode'],
        'machine-status': [str(python), str(scripts / 'openclaw-machine-snapshot.py'), '--publish-core'],
    }


def service(name: str, python: Path = PYTHON) -> str:
    if name == 'machine-status':
        return machine_service(python)
    command = commands(python)[name]
    after = ('network-online.target homecompute-agents-vm.service ' + PREFIX + 'machine-status.service' if name == 'adapter'
             else PREFIX + 'adapter.service network-online.target')
    wants = 'network-online.target' + (' ' + PREFIX + 'machine-status.service' if name == 'adapter' else '')
    restart = 'on-failure' if name in {'telegram', 'update-feed'} else 'always'
    kind = 'oneshot' if name == 'update-feed' else 'simple'
    pre = (f'ExecStartPre=+{python} {DEST}/scripts/openclaw-observation-feed.py --project-core-model\n'
           if name == 'update-feed' else '')
    writable = f'{HOME} {REPORTS}' if name == 'update-feed' else str(HOME)
    return f'''[Unit]
Description=HomeCompute OpenClaw {name}
After={after}
Wants={wants}
StartLimitIntervalSec=0
[Service]
Type={kind}
User={USER}
Group={USER}
WorkingDirectory={DEST}
Environment=HOMECOMPUTE_OPENCLAW_TRANSPORT=home-core
Environment=HOME={HOME}
Environment=PATH=/run/current-system/sw/bin
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
{pre}ExecStart={' '.join(command)}
Restart={restart}
RestartSec=15s
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectClock=yes
ProtectControlGroups=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths={writable}
MemoryMax=256M
TasksMax=64
LimitCORE=0
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
'''


def machine_service(python: Path = PYTHON) -> str:
    return f'''[Unit]
Description=HomeCompute finite public machine status projection
After=docker.service
[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory={DEST}
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={' '.join(commands(python)['machine-status'])}
TimeoutStartSec=12s
UMask=0022
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectClock=yes
ProtectControlGroups=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX
ReadWritePaths={REPORTS}
MemoryMax=128M
TasksMax=32
LimitCORE=0
StandardOutput=journal
StandardError=journal
'''


def timer(name: str = 'update-feed') -> str:
    boot, interval = ('5s', '30s') if name == 'machine-status' else ('30s', '300s')
    return f'''[Unit]
Description=HomeCompute OpenClaw {name} observations
[Timer]
OnBootSec={boot}
OnUnitActiveSec={interval}
Persistent=true
Unit={PREFIX}{name}.service
[Install]
WantedBy=timers.target
'''


def protected_directory(path: Path, uid: int, private: bool, create: bool = False,
                        gid: int | None = None) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('directory symlinks prohibited')
    if not path.exists() and create:
        path.mkdir(mode=0o700 if private else 0o755)
        os.chown(path, uid, uid if gid is None else gid)
    info = path.lstat()
    prohibited = 0o077 if private else 0o022
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & prohibited:
        raise ValueError('directory owner or permissions differ from service boundary')


def private_file(path: Path, uid: int, *, json_object: bool = True) -> dict[str, Any]:
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('private file parent symlinks prohibited')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise ValueError('private files require service owner and mode0600')
        content = os.read(fd, 65537)
        if len(content) > 65536:
            raise ValueError('private file exceeds budget')
        value = json.loads(content) if json_object else {}
        if not isinstance(value, dict):
            raise ValueError('private JSON object required')
        return value
    finally:
        os.close(fd)


def account(create: bool = False) -> pwd.struct_passwd:
    try:
        info = pwd.getpwnam(USER)
    except KeyError:
        if not create:
            raise ValueError('dedicated service account unavailable') from None
        call('/run/current-system/sw/bin/useradd', '--system', '--user-group',
             '--no-create-home', '--home-dir', str(HOME), '--shell',
             '/run/current-system/sw/bin/nologin', USER)
        info = pwd.getpwnam(USER)
    if info.pw_uid == 0 or info.pw_dir != str(HOME) or info.pw_gid != grp.getgrnam(USER).gr_gid:
        raise ValueError('existing account differs from dedicated service boundary')
    return info


def validate_ready(uid: int, telegram: bool = False) -> None:
    for path in (HOME, CONFIG, STATE, STATE / 'adapter', STATE / 'telegram', STATE / 'update-feed'):
        protected_directory(path, uid, True)
    for name in ('communication.json', 'transport-tokens.json', 'codex-task-transport.json'):
        private_file(CONFIG / name, uid)
    transport = private_file(CONFIG / 'codex-task-transport.json', uid)
    for name in ('tokens_file', 'project_policy'):
        path = Path(transport.get(name, ''))
        if not path.is_absolute() or path.parent != CONFIG:
            raise ValueError('task transport must use the dedicated private config directory')
        private_file(path, uid)
    protected_directory(HOME / '.ssh', uid, True)
    for name in ('id_ed25519', 'known_hosts'):
        private_file(HOME / '.ssh' / name, uid, json_object=False)
    if telegram:
        private_file(CONFIG / 'telegram.json', uid)
        private_file(CONFIG / 'telegram-tokens.json', uid)
        cursor = private_file(STATE / 'telegram/telegram-cursor.json', uid)
        guard = STATE / 'telegram/telegram-admission.json'
        if cursor.get('paused') is not False or guard.exists() or guard.is_symlink():
            raise ValueError('receiver requires reconciliation; cursor and admission guard retained')


def source_bundle(source: Path) -> tuple[dict[str, bytes], str]:
    if any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError('source directory symlinks prohibited')
    if not source.is_dir() or source.stat().st_mode & 0o022:
        raise ValueError('protected source directory required')
    files, digest = {}, hashlib.sha256()
    for name in FILES:
        path = source / name
        if (any(p.is_symlink() for p in (path, path.parent)) or not path.is_file()
                or path.stat().st_mode & 0o022 or path.stat().st_size > 1024 * 1024):
            raise ValueError('unsafe source bundle file')
        content = path.read_bytes()
        if name.endswith('.py'):
            compile(content, name, 'exec')
        elif not isinstance(json.loads(content), dict):
            raise ValueError('registry object required')
        files[name] = content
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    return files, digest.hexdigest()


def write(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != 0)):
        raise ValueError('managed output must be a regular root-owned file')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def enable(name: str, enabled: bool, *, timer_unit: bool = False) -> None:
    directory = 'timers.target.wants' if timer_unit else 'multi-user.target.wants'
    suffix = '.timer' if timer_unit else '.service'
    link = UNIT_DIR / directory / (PREFIX + name + suffix)
    target = UNIT_DIR / link.name
    if link.is_symlink():
        if os.readlink(link) != str(target):
            raise ValueError('unmanaged boot dependency')
        if not enabled:
            link.unlink()
    elif link.exists():
        raise ValueError('boot dependency must be a managed symlink')
    elif enabled:
        link.symlink_to(target)


def install(files: dict[str, bytes]) -> None:
    unit_names = [PREFIX + name + '.service' for name in NAMES] + [PREFIX + name + '.timer' for name in TIMERS]
    for unit in unit_names:
        if call('systemctl', 'is-active', unit, check=False).stdout.strip() in {
                'active', 'activating', 'reloading'}:
            raise ValueError('stop existing communication units before replacing code')
    info = account(create=True)
    for path in (HOME, CONFIG, STATE, STATE / 'adapter', STATE / 'telegram', STATE / 'update-feed'):
        protected_directory(path, info.pw_uid, True, True, info.pw_gid)
    for path in (DEST.parent.parent, DEST.parent, DEST, DEST / 'scripts', DEST / 'config',
                 UNIT_DIR, UNIT_DIR / 'multi-user.target.wants', UNIT_DIR / 'timers.target.wants'):
        protected_directory(path, 0, False, True, 0)
    for name, content in files.items():
        write(DEST / name, content)
    for name in NAMES:
        write(UNIT_DIR / (PREFIX + name + '.service'), service(name).encode())
        enable(name, name in {'adapter', 'task-feed'})
    for name in TIMERS:
        write(UNIT_DIR / (PREFIX + name + '.timer'), timer(name).encode())
        enable(name, True, timer_unit=True)
    protected_directory(TMPFILES.parent, 0, False)
    write(TMPFILES, f'd {REPORTS} 0755 root root -\n'.encode())
    call('systemd-tmpfiles', '--create', str(TMPFILES))
    protected_directory(REPORTS, 0, False)
    call('systemd-analyze', 'verify', *(str(UNIT_DIR / (PREFIX + name + '.service')) for name in NAMES),
         *(str(UNIT_DIR / (PREFIX + name + '.timer')) for name in TIMERS))
    call('systemctl', 'daemon-reload')


def validate_units(unit_names: list[str]) -> None:
    for name in unit_names:
        path = UNIT_DIR / (PREFIX + name)
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ValueError('install trusted units before activation')


def activate(telegram: bool = False) -> None:
    validate_ready(account().pw_uid, telegram)
    selected = ('telegram',) if telegram else ('machine-status', 'adapter', 'task-feed', 'update-feed')
    timers = () if telegram else TIMERS
    validate_units([name + '.service' for name in selected] + [name + '.timer' for name in timers])
    for name in selected:
        if name not in {'update-feed', 'machine-status'}:
            enable(name, True)
    for name in timers:
        enable(name, True, timer_unit=True)
    call('systemctl', 'daemon-reload')
    for name in selected:
        call('systemctl', 'start', PREFIX + name + '.service', check=name != 'machine-status')
    for name in timers:
        call('systemctl', 'start', PREFIX + name + '.timer')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install', action='store_true', help='stage and enable adapter/feeds; start nothing; Telegram disabled')
    actions.add_argument('--activate-services', action='store_true', help='start adapter/task/update feeds after migration')
    actions.add_argument('--activate-telegram', action='store_true', help='enable/start receiver only after reconciliation')
    parser.add_argument('--source', type=Path, help='trusted checkout containing scripts/config; required for install')
    args = parser.parse_args(argv)
    try:
        if not any((args.install, args.activate_services, args.activate_telegram)):
            print(json.dumps({**{name: service(name) for name in NAMES},
                              **{name + '.timer': timer(name) for name in TIMERS}}, indent=2))
            return 0
        if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
            raise ValueError('requires root on home-core')
        if not PYTHON.is_file():
            raise ValueError('NixOS Python runtime unavailable')
        if args.install:
            if args.source is None:
                raise ValueError('--install requires --source')
            files, digest = source_bundle(args.source)
            install(files)
            result = {'installed': True, 'started': [], 'telegram_enabled': False, 'source_sha256': digest}
        else:
            activate(args.activate_telegram)
            result = {'started': ['telegram'] if args.activate_telegram else
                      ['machine-status', 'adapter', 'task-feed', 'update-feed', 'machine-status.timer', 'update-feed.timer']}
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Core communication setup refused; preserve private state and reconcile fixed inputs.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
