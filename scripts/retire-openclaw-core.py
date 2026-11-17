#!/usr/bin/env python3
"""Stop only the dedicated OpenClaw Core units from a reviewed release."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ATTACHED = Path('/etc/systemd/system.attached')
STATE = Path('/var/lib/homecompute-openclaw-retirement')
LOCK = Path('/var/lib/homecompute/deploy.lock')
ROOT_UID = 0
ACCESS_PROXY = 'homecompute-openclaw-access-proxy.service'
RECOVERY_DROPIN = Path('/run/systemd/system') / (ACCESS_PROXY + '.d') / '50-restore-recovery.conf'
RECOVERY_DROPIN_SHA256 = 'ca4be90be912e3241da21c9f7289e48f6ff197b4ca76d9ba894017a3baa27eb4'
UNITS = (
    'homecompute-openclaw-family-task-reminders.timer',
    'homecompute-openclaw-household-task-reminders.timer',
    'homecompute-openclaw-access-certificate.path',
    'homecompute-openclaw-family-task-reminders.service',
    'homecompute-openclaw-household-task-reminders.service',
    'homecompute-openclaw-location-reminders.service',
    'homecompute-openclaw-task-feed.service',
    'homecompute-openclaw-sites.service',
    'homecompute-openclaw-site-egress.service',
    'homecompute-openclaw-access-certificate.service',
    'homecompute-openclaw-access-proxy.service',
    'homecompute-openclaw-access.service',
)


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=90).stdout.strip()


def protected(path: Path, *, private: bool = False) -> bytes:
    info = path.lstat()
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & forbidden or info.st_nlink != 1:
        raise ValueError('known root-owned configuration file required')
    return path.read_bytes()


def release(revision: str) -> None:
    if (os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core'
            or not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision):
        raise ValueError('root on home-core and exact release directory required')
    if command('git', '-C', str(ROOT), 'rev-parse', 'HEAD') != revision:
        raise ValueError('release revision changed')
    if command('git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('dirty release refused')
    command('git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master')
    protected(Path(__file__))


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def boot_link(unit: str) -> Path:
    suffix = {'timer': 'timers', 'path': 'multi-user', 'service': 'multi-user'}[unit.rsplit('.', 1)[1]]
    return ATTACHED / (suffix + '.target.wants') / unit


def inspect_unit(unit: str) -> dict:
    fragment = ATTACHED / unit
    link = boot_link(unit)
    for directory in (ATTACHED, link.parent):
        if not directory.exists() and directory == link.parent and not directory.is_symlink():
            continue
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise ValueError('managed systemd directory required')
    output = command('systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState',
                     '-p', 'FragmentPath', '-p', 'DropInPaths', '-p', 'UnitFileState')
    fields = dict(line.split('=', 1) for line in output.splitlines())
    if (fields.get('LoadState') != 'loaded' or fields.get('FragmentPath') != str(fragment)
            or fields.get('ActiveState') not in {'active', 'inactive'}
            or fields.get('UnitFileState') not in {'enabled', 'disabled', 'static'}):
        raise ValueError('unit differs from the managed attachment or is transitional: ' + unit)
    dropin = fields.get('DropInPaths')
    dropin_hash = None
    if dropin:
        if (unit != ACCESS_PROXY or dropin != str(RECOVERY_DROPIN)
                or hashlib.sha256(protected(RECOVERY_DROPIN)).hexdigest() != RECOVERY_DROPIN_SHA256):
            raise ValueError('unit differs from the managed attachment: ' + unit)
        dropin_hash = RECOVERY_DROPIN_SHA256
    target = str(link.readlink()) if link.is_symlink() else None
    if (target is None and link.exists()) or (target is not None and target not in (str(fragment), '../' + unit)):
        raise ValueError('unknown boot link: ' + unit)
    if fields.get('UnitFileState') == 'enabled' and target is None:
        raise ValueError('unexpected external boot link: ' + unit)
    return {'active': fields['ActiveState'] == 'active', 'boot_target': target,
            'unit_sha256': hashlib.sha256(protected(fragment)).hexdigest(),
            'recovery_dropin_sha256': dropin_hash}


def plan(revision: str) -> dict:
    return {'revision': revision, 'units': {unit: inspect_unit(unit) for unit in UNITS}}


def restore(reviewed: dict) -> None:
    if set(reviewed['units']) != set(UNITS):
        raise ValueError('unknown retirement receipt')
    for unit, previous in reviewed['units'].items():
        current = inspect_unit(unit)
        if (current['unit_sha256'] != previous['unit_sha256']
                or current['recovery_dropin_sha256'] != previous['recovery_dropin_sha256']
                or current['boot_target'] not in (None, previous['boot_target'])
                or current['active'] and not previous['active']):
            raise ValueError('state changed after retirement attempt: ' + unit)
    for unit in reversed(UNITS):
        previous = reviewed['units'][unit]
        link = boot_link(unit)
        target = previous['boot_target']
        if target is not None and target not in (str(ATTACHED / unit), '../' + unit):
            raise ValueError('unknown prior boot link: ' + unit)
        if target is not None and not link.is_symlink():
            link.symlink_to(target)
    command('systemctl', 'daemon-reload')
    for unit in reversed(UNITS):
        if reviewed['units'][unit]['active'] and not inspect_unit(unit)['active']:
            command('systemctl', 'start', unit)
    if plan(reviewed['revision']) != reviewed:
        raise ValueError('prior activity or startup links could not be restored')


def apply(reviewed: dict, expected: str | None) -> Path:
    if digest(reviewed) != expected or plan(reviewed['revision']) != reviewed:
        raise ValueError('exact current reviewed plan digest required')
    STATE.mkdir(mode=0o700, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o077:
        raise ValueError('private retirement receipt directory required')
    receipt = STATE / ('retirement-' + str(time.time_ns()) + '.json')
    with receipt.open('x') as stream:
        json.dump(reviewed, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for unit in UNITS:
            previous = reviewed['units'][unit]
            current = inspect_unit(unit)
            if (current['unit_sha256'] != previous['unit_sha256']
                    or current['recovery_dropin_sha256'] != previous['recovery_dropin_sha256']
                    or current['boot_target'] != previous['boot_target']
                    or current['active'] and not previous['active']):
                raise ValueError('unit changed before retirement: ' + unit)
            if current['active']:
                command('systemctl', 'stop', unit)
                if command('systemctl', 'show', unit, '-p', 'ActiveState', '--value') == 'failed':
                    command('systemctl', 'reset-failed', unit)
            if current['boot_target'] is not None:
                boot_link(unit).unlink()
        command('systemctl', 'daemon-reload')
        if any(row['active'] or row['boot_target'] is not None for row in plan(reviewed['revision'])['units'].values()):
            raise ValueError('retired unit still active or linked')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        raise ValueError(f'retirement interrupted; reconcile from receipt {receipt}') from error
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--rollback', type=Path)
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    release(args.revision)
    os.umask(0o077)
    with os.fdopen(os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW), 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022 or info.st_nlink != 1:
            raise ValueError('protected deployment lock required')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            if args.rollback.parent != STATE or not re.fullmatch(r'retirement-[0-9]+\.json', args.rollback.name):
                raise ValueError('use the exact protected retirement receipt')
            reviewed = json.loads(protected(args.rollback, private=True))
            if reviewed['revision'] != args.revision:
                raise ValueError('receipt owner revision differs')
            restore(reviewed)
            print(json.dumps({'restored': True}))
            return
        reviewed = plan(args.revision)
        if args.apply:
            receipt = apply(reviewed, args.expected_plan_sha256)
            print(json.dumps({'retired': True, 'receipt': str(receipt)}))
        else:
            print(json.dumps({**reviewed, 'plan_sha256': digest(reviewed)}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # External command output may contain private state. Error strings here
        # are drawn from our fixed guard failures only.
        detail = str(error) if type(error) is ValueError else type(error).__name__
        raise SystemExit('OpenClaw retirement held: ' + detail)
