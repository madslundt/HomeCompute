#!/usr/bin/env python3
"""Operator-only cold backup with bounded inference admission/drain; default plans."""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = ('scripts/backup-application-recovery.py', 'scripts/setup-nas-recovery-backup.py',
         'scripts/setup-speech-boundary.py')
CHAIN = 'HC-RECOVERY-DRAIN'
UNIT = 'homecompute-nas-recovery-backup.service'


def bundle(source: Path) -> str:
    digest = hashlib.sha256()
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('bounded regular recovery source required')
        data = path.read_bytes()
        compile(data, name, 'exec')
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def published(source: Path, revision: str, expected: str) -> None:
    if (not re.fullmatch('[0-9a-f]{40}', revision)
            or source.resolve() != Path('/srv/homecompute/releases') / revision
            or bundle(source) != expected):
        raise ValueError('reviewed immutable recovery release/hash required')
    def git(*args):
        return subprocess.run(['git', '-C', str(source), *args], check=True,
                              capture_output=True, timeout=15,
                              env={'PATH':'/run/current-system/sw/bin','HOME':'/var/empty',
                                   'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}).stdout
    if git('rev-parse', 'HEAD').decode().strip() != revision:
        raise ValueError('recovery release revision mismatch')
    git('merge-base', '--is-ancestor', revision, 'origin/master')
    for name in FILES:
        if git('show', revision + ':' + name) != (source / name).read_bytes():
            raise ValueError('unpublished recovery source refused')


def load(source: Path, name: str):
    spec = importlib.util.spec_from_file_location('recovery_' + name.replace('-', '_'), source / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state(nas) -> dict:
    result = nas.run(['systemctl', 'show', UNIT, '-p', 'LoadState', '-p', 'ActiveState',
                      '-p', 'SubState', '-p', 'ExecMainStatus', '-p', 'Result'])
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded':
        raise ValueError('existing backup unit required')
    return fields


def wait_phase(nas, started: float, deadline: float, *, until_cold: bool) -> dict | None:
    while time.monotonic() < deadline:
        current = state(nas)
        if current.get('SubState') == 'auto-restart' or current.get('ActiveState') == 'failed':
            raise RuntimeError('guarded backup failed or deferred; no automatic replay')
        if current.get('ActiveState') == 'inactive':
            receipt = nas.STATE / 'last-backup.json'
            if receipt.is_file() and receipt.stat().st_mtime >= started:
                if current.get('Result') != 'success' or current.get('ExecMainStatus') != '0':
                    raise RuntimeError('fresh receipt without successful backup settlement')
                if (nas.STATE / 'resume.json').exists() or Path('/run/homecompute/application-recovery-in-progress').exists():
                    raise RuntimeError('backup resumption still requires reconciliation')
                return json.loads(receipt.read_bytes())
            if time.time() - started >= 10:
                raise RuntimeError('backup settled without a fresh completed NAS receipt')
        elif current.get('ActiveState') not in {'active', 'activating', 'deactivating'}:
            raise RuntimeError('unknown backup state')
        if until_cold and current.get('ActiveState') in {'active', 'activating'}:
            running = nas.run(['docker', 'inspect', 'homecompute-control-plane-litellm-1',
                               '--format', '{{.State.Running}}']).stdout.strip()
            if running not in {'true', 'false'}:
                raise RuntimeError('inference source running state unavailable')
            if running == 'false':
                return None  # Source is stopped; release hold before resumption/NAS transfer.
        time.sleep(1)
    raise TimeoutError('bounded backup deadline exceeded')


def backup(source: Path, revision: str, expected: str, installed_hash: str,
           acknowledge_pause: bool, timeout: int) -> dict:
    if not acknowledge_pause or not 3 <= timeout <= 600:
        raise ValueError('explicit pause acknowledgement and bounded drain required')
    published(source, revision, expected)
    nas = load(source, 'setup-nas-recovery-backup')
    drain = load(source, 'setup-speech-boundary')
    drain.MAINTENANCE_CHAIN = CHAIN  # Same finite scopes; separate ownership from a speech cutover.
    record = nas.installed(installed_hash)
    environment = nas.environment(record['qemu_bin'])
    nas.run = partial(nas.run, env=environment)
    drain.run = partial(drain.run, environment=environment)
    nas.safe(nas.STATE / 'backup-authorized')  # Existing approved installation only; no new authorization.
    nas.run([nas.PYTHON, '-I', str(Path(record['version']) / 'scripts/nas-recovery-backup.py'), 'validate'],
            env=nas.environment(record['qemu_bin']))
    with nas.locked(nas.STATE / 'lifecycle.lock'):
        current = state(nas)
        deferred = ('activating', 'auto-restart', '75')
        if (current.get('ActiveState'), current.get('SubState'), current.get('ExecMainStatus')) != deferred:
            nas.busy()
        started, deadline, launched = time.time(), time.monotonic() + 7500, False
        signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        previous = {event: signal.getsignal(event) for event in signals}
        def interrupted(*_):
            raise InterruptedError('backup operator interrupted; resuming owning backend')
        for event in signals: signal.signal(event, interrupted)
        try:
            with drain.core_maintenance(timeout):
                try:
                    current = state(nas)
                    if (current.get('ActiveState'), current.get('SubState'), current.get('ExecMainStatus')) == deferred:
                        # Only cancel an idle retry after the drain succeeds.
                        # Failed drains therefore retain the original retry.
                        nas.run(['systemctl', 'stop', UNIT], timeout=600)
                    nas.busy()
                    masked = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
                    try:
                        nas.run(['systemctl', 'start', '--no-block', UNIT])
                        launched = True
                    finally: signal.pthread_sigmask(signal.SIG_SETMASK, masked)
                    result = wait_phase(nas, started, deadline, until_cold=True)
                except BaseException:
                    if launched: nas.run(['systemctl', 'stop', UNIT], timeout=600)
                    launched = False
                    raise
            if result is None:
                result = wait_phase(nas, started, deadline, until_cold=False)
        except BaseException:
            if launched:
                nas.run(['systemctl', 'stop', UNIT], timeout=600)
            raise
        finally:
            for event, handler in previous.items(): signal.signal(event, handler)
    return {'backup_completed': True, 'completed_at': result['time'],
            'production_configuration_changed': False, 'admission_gate_removed': True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('plan', 'backup'), default='plan')
    parser.add_argument('--revision')
    parser.add_argument('--expected-hash')
    parser.add_argument('--installed-hash')
    parser.add_argument('--acknowledge-pause', action='store_true')
    parser.add_argument('--drain-timeout', type=int, default=120)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if args.command == 'plan':
        print(json.dumps({'bundle_hash': bundle(ROOT), 'files': FILES, 'applied': False,
                          'admission_chain': CHAIN, 'scheduled_backup_policy_changed': False}))
        return
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise PermissionError('trusted home-core root operator required')
    if not args.revision or not args.expected_hash or not args.installed_hash:
        parser.error('backup requires published revision, reviewed source hash and installed NAS hash')
    print(json.dumps(backup(ROOT, args.revision, args.expected_hash, args.installed_hash,
                            args.acknowledge_pause, args.drain_timeout)))


if __name__ == '__main__':
    main()
