#!/usr/bin/env python3
"""Stage and activate the bounded Home Assistant location reminder listener."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import sys
from typing import Any, Callable
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
HOME = Path('/var/lib/homecompute-openclaw')
CONFIG = HOME / 'config/location-reminders.json'
TOKEN = HOME / 'config/household-ha-token'
STATE = HOME / 'communication/adapter'
SCRIPTS = Path('/opt/homecompute/openclaw-communication/scripts')
sys.path.append(str(SCRIPTS))  # Reuse the installed trusted dependencies during staged installation.
UNITS = Path('/etc/systemd/system.attached')
UNIT = 'homecompute-openclaw-location-reminders.service'
PYTHON = Path('/run/current-system/sw/bin/python3')
USER = 'homecompute-openclaw'
FILES = ('openclaw_location_policy.py', 'openclaw_location_transport.py', 'openclaw_task_location.py',
         'openclaw-location-reminders.py', 'setup-openclaw-location-reminders.py')
PERSONS = {'mads': {'name': 'Mads', 'entity_id': 'person.madslundt', 'notify_service': 'mobile_app_mads_iphone'},
           'kristina': {'name': 'Kristina', 'entity_id': 'person.kristinasorensen', 'notify_service': 'mobile_app_kristina_iphone'}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        raise ValueError('Home Assistant redirect refused')


def path_guard(path: Path, owners: set[int], *, directory: bool = False, private: bool = False) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Trusted nonsymlink path required')
    info = path.stat()
    if (info.st_uid not in owners or info.st_mode & (0o077 if private else 0o022)
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink != 1):
        raise ValueError('Trusted path ownership and permissions required')


def read_private(path: Path, uid: int, limit: int) -> bytes:
    path_guard(path, {0, uid}, private=True)
    if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_size > limit:
        raise ValueError('Private bounded configuration required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Private configuration exceeds budget')
    return data


def atomic(path: Path, data: bytes, uid: int, gid: int, mode: int = 0o600) -> None:
    temporary = path.with_name('.location-install-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def discover(uid: int, opener: Any = None) -> dict[str, Any]:
    token = read_private(TOKEN, uid, 4096).decode().strip()
    if not 32 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('Private household authentication required')
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request('http://192.168.30.30/api/states', headers={'Authorization': 'Bearer ' + token})
    with opener.open(request, timeout=12) as response:
        raw = response.read(8388609)
        if response.status != 200 or len(raw) > 8388608:
            raise ValueError('Bounded Home Assistant inventory required')
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError('Bounded entity inventory required')
    people, zones = {}, {}
    person_entities = {person['entity_id'] for person in PERSONS.values()}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('entity_id'), str):
            continue
        entity = row['entity_id']
        if entity not in person_entities and not entity.startswith('zone.'):
            continue
        attributes = row.get('attributes')
        if not isinstance(attributes, dict):
            raise ValueError('Bounded person and zone metadata required')
        if entity in person_entities:
            trackers = attributes.get('device_trackers')
            people[entity] = (isinstance(trackers, list) and bool(trackers)
                              and all(isinstance(item, str) and item.startswith('device_tracker.') for item in trackers))
        else:
            zones[entity[5:]] = {'entity_id': entity, 'name': attributes.get('friendly_name', entity[5:])}
    if any(not people.get(person['entity_id']) for person in PERSONS.values()):
        raise ValueError('Configured adult person tracking required')
    # Coordinates, state, users, histories and unrelated entities are discarded.
    value = {'schema_version': 1, 'ha_token_file': str(TOKEN), 'owner_person': 'mads',
             'persons': PERSONS, 'zones': zones}
    from openclaw_location_policy import Policy
    return Policy.from_value(value).value


def select_policy(existing: dict[str, Any] | None, discovered: dict[str, Any],
                  *, refresh_zones: bool = False) -> dict[str, Any]:
    from openclaw_location_policy import Policy
    found = Policy.from_value(discovered).value
    if existing is None:
        return found
    value = Policy.from_value(existing).value
    if value['persons'] != PERSONS or value['owner_person'] != 'mads' or value['ha_token_file'] != str(TOKEN):
        raise ValueError('Existing location identities require operator reconciliation')
    if refresh_zones:
        for alias, zone in found['zones'].items():
            value['zones'].setdefault(alias, zone)
    return Policy.from_value(value).value


def service() -> str:
    return f'''[Unit]
Description=HomeCompute Home Assistant location reminder listener
After=network-online.target
Wants=network-online.target
ConditionPathExists={CONFIG}
StartLimitIntervalSec=0
[Service]
Type=simple
User={USER}
Group={USER}
WorkingDirectory={SCRIPTS.parent}
Environment=HOME={HOME}
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
ExecStart={PYTHON} {SCRIPTS}/openclaw-location-reminders.py --config {CONFIG} --state {STATE}
Restart=always
RestartSec=5s
TimeoutStopSec=15s
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectKernelLogs=yes
ProtectClock=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_UNIX
ReadWritePaths={STATE}
MemoryMax=128M
TasksMax=32
LimitCORE=0
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
'''


def require_host() -> Any:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core' or not PYTHON.is_file():
        raise ValueError('Root on home-core with the installed Python runtime required')
    return pwd.getpwnam(USER)


def plan() -> dict[str, Any]:
    return {'service': UNIT, 'configuration': str(CONFIG), 'source_files': list(FILES),
            'install_starts_service': False, 'new_secrets': 0, 'messages_sent': 0,
            'trigger_source': 'Home Assistant zone transitions', 'unit': service()}


def install(source: Path, *, refresh_zones: bool = False,
            runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    account = require_host()
    source = source.absolute()
    path_guard(source, {0}, directory=True)
    path_guard(source / 'scripts', {0}, directory=True)
    sources = {}
    for name in FILES:
        path = source / 'scripts' / name
        path_guard(path, {0})
        content = path.read_bytes()
        if len(content) > 131072:
            raise ValueError('Trusted Python source exceeds budget')
        compile(content, str(path), 'exec')
        sources[name] = content
    for path, owners, private in ((SCRIPTS, {0}, False), (UNITS, {0}, False),
                                  (CONFIG.parent, {account.pw_uid}, True), (STATE, {account.pw_uid}, True)):
        path_guard(path, owners, directory=True, private=private)
    discovered = discover(account.pw_uid)
    existing = json.loads(read_private(CONFIG, account.pw_uid, 65536)) if CONFIG.exists() or CONFIG.is_symlink() else None
    value = select_policy(existing, discovered, refresh_zones=refresh_zones)
    config_bytes = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    backup = CONFIG.parent / ('location-reminder-backup-' + uuid.uuid4().hex)
    backup.mkdir(mode=0o700)
    if CONFIG.exists():
        shutil.copy2(CONFIG, backup / 'location-reminders.json')
        os.chown(backup / 'location-reminders.json', 0, 0)
    for name, content in sources.items():
        destination = SCRIPTS / name
        if destination.exists() or destination.is_symlink():
            path_guard(destination, {0})
            shutil.copy2(destination, backup / name)
        atomic(destination, content, 0, 0, 0o644)
    unit_path = UNITS / UNIT
    if unit_path.exists() or unit_path.is_symlink():
        path_guard(unit_path, {0})
        shutil.copy2(unit_path, backup / UNIT)
    atomic(CONFIG, config_bytes, account.pw_uid, account.pw_gid)
    atomic(unit_path, service().encode(), 0, 0, 0o644)
    wanted = UNITS / 'multi-user.target.wants'
    path_guard(wanted, {0}, directory=True)
    link = wanted / UNIT
    if link.is_symlink():
        if link.resolve() != unit_path:
            raise ValueError('Existing service enable link differs')
    elif link.exists():
        raise ValueError('Existing service enable path differs')
    else:
        link.symlink_to(unit_path)
    runner(['systemd-analyze', 'verify', str(unit_path)], check=True, capture_output=True, text=True)
    runner(['systemctl', 'daemon-reload'], check=True, capture_output=True, text=True)
    return {'installed': True, 'started': False, 'persons': len(value['persons']), 'zones': len(value['zones']),
            'policy_sha256': hashlib.sha256(config_bytes).hexdigest(), 'backup': str(backup), 'messages_sent': 0}


def activate(runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    account = require_host()
    path_guard(CONFIG, {account.pw_uid}, private=True)
    for name in FILES:
        path_guard(SCRIPTS / name, {0})
    path_guard(UNITS / UNIT, {0})
    runner(['runuser', '-u', USER, '--', str(PYTHON), str(SCRIPTS / 'openclaw-location-reminders.py'),
            '--config', str(CONFIG), '--state', str(STATE), '--check'],
           check=True, capture_output=True, text=True, timeout=45)
    runner(['systemctl', 'start', UNIT], check=True, capture_output=True, text=True)
    runner(['systemctl', 'is-active', '--quiet', UNIT], check=True, capture_output=True, text=True)
    return {'activated': True, 'subscription_check': 'passed', 'messages_sent_by_check': 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install', action='store_true')
    group.add_argument('--activate', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--refresh-zones', action='store_true', help='add newly discovered zones without replacing retained identities')
    args = parser.parse_args()
    try:
        if args.install:
            if args.source is None:
                parser.error('--install requires --source')
            result = install(args.source, refresh_zones=args.refresh_zones)
        elif args.activate:
            result = activate()
        else:
            result = plan()
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'reason': 'location_reminder_setup_refused'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
