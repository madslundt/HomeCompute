#!/usr/bin/env python3
"""Plan or atomically refresh four reviewed notification renderers, preserving state.

This deliberately does not invoke the broader communication/access installers.
Only the fixed modules below are written. Units, configuration, credentials,
databases, monitoring sources and inactive services remain untouched.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid

BASELINE = '3c320ddee4beee08f5a055c765f4e230a6d1dd81'
TARGETS = {
    'openclaw_notifications.py': ('/opt/homecompute/openclaw-communication/scripts', '05b5c0d2af2080a6e73afba646bc9ad1ebd1e876d05fb9f101dc72fd3ad4325a'),
    'openclaw_machine_notifications.py': ('/opt/homecompute/openclaw-communication/scripts', 'a3fb88518b427d528e04c865e5d0df4f13aa6cf97ac9ff4993061f035480580f'),
    'openclaw-household-task-reminders.py': ('/opt/homecompute/openclaw-communication/scripts', 'b0a2a6adbad87dc87b1b685e50913c5f28caf93bb3b69a54608f3d899e6f2be2'),
    'openclaw_access_notifications.py': ('/opt/homecompute/openclaw-credentials', 'f1698fd1a1b3e68227e0b87cdb4e7eb9d27340072bf4f68dc204b2007e88a4cc'),
}
PREFIX = 'homecompute-openclaw-'
ADAPTER = PREFIX + 'adapter.service'
ACCESS = PREFIX + 'access.service'
PROXY = PREFIX + 'access-proxy.service'
WATCH = PREFIX + 'access-certificate.path'
CERTIFICATE = PREFIX + 'access-certificate.service'
MACHINE = PREFIX + 'machine-status.service'
STOP_ORDER = (ADAPTER, WATCH, PROXY, ACCESS)
START_ORDER = (ACCESS, PROXY, WATCH, ADAPTER)
BACKUPS = Path('/var/lib/homecompute-notification-presentation/backups')
LOCK = Path('/run/lock/homecompute-notification-presentation.lock')
OWNER = 0
PRESERVED_SOURCES = ('docs/openclaw-operations.md', 'docs/openclaw-tools.md', 'docs/homecompute-operations.md',
                     'docs/openclaw-integrations.md', 'config/system-monitoring.json', 'scripts/openclaw-machine-snapshot.py')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def protected(path: Path, *, directory: bool = False) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink in managed path')
    info = path.stat()
    if (info.st_uid != OWNER or info.st_mode & 0o022
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and (info.st_nlink != 1 or info.st_size > 1024 * 1024)):
        raise ValueError('Protected root-owned path required')
    return b'' if directory else path.read_bytes()


def git(source: Path, *arguments: str) -> bytes:
    result = subprocess.run(['git', '-C', str(source), *arguments], capture_output=True, check=True, timeout=20)
    return result.stdout


def service_state(name: str) -> dict:
    result = subprocess.run(['systemctl', 'show', name, '--property=LoadState,ActiveState,SubState,FragmentPath'],
                            capture_output=True, text=True, check=True, timeout=20)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if state.get('LoadState') != 'loaded' or state.get('ActiveState') not in {'active', 'inactive'}:
        raise ValueError('Owned service is unavailable or changing state')
    path = Path('/etc/systemd/system.attached') / name
    if state.get('FragmentPath') != str(path):
        raise ValueError('Owned service path differs')
    return {'active': state['ActiveState'] == 'active', 'unit_sha256': digest(protected(path))}


def in_flight() -> dict:
    queries = {
        'sending_notifications': ('/var/lib/homecompute-openclaw/communication/adapter/transport.sqlite3',
                                  "SELECT count(*) FROM events WHERE state='sending'"),
        'running_turns': ('/var/lib/homecompute-openclaw/communication/adapter/transport.sqlite3',
                          "SELECT count(*) FROM turns WHERE state='running'"),
        'sending_access_notifications': ('/var/lib/homecompute-access/notifications/notifications.sqlite',
                                         "SELECT count(*) FROM deliveries WHERE state!='delivered'"),
        'executing_transactions': ('/var/lib/homecompute-access/transactions/transactions.sqlite',
                                   "SELECT count(*) FROM transactions WHERE status IN ('executing','unknown')"),
    }
    counts = {}
    for label, (name, query) in queries.items():
        path = Path(name)
        if not path.is_file() or path.is_symlink():
            raise ValueError('Existing delivery ledger required')
        with sqlite3.connect('file:' + name + '?mode=ro', uri=True, timeout=3) as db:
            db.execute('PRAGMA query_only=ON')
            counts[label] = db.execute(query).fetchone()[0]
    return counts


def preserved_artifacts() -> dict:
    paths = [Path('/opt/homecompute/openclaw-communication') / name for name in PRESERVED_SOURCES]
    paths.extend(Path('/etc/systemd/system.attached').glob('homecompute-openclaw-*'))
    for directory in ('multi-user.target.wants', 'timers.target.wants'):
        paths.extend((Path('/etc/systemd/system.attached') / directory).glob('homecompute-openclaw-*'))
    paths.extend(Path('/opt/homecompute/openclaw-communication/config').glob('*'))
    paths.extend(Path('/opt/homecompute/openclaw-credentials') / name for name in ('access.Caddyfile', 'access-compose.yaml'))
    paths.extend((Path('/etc/tmpfiles.d/homecompute-openclaw.conf'),
                  Path('/var/lib/homecompute-openclaw/config/system-monitoring.json')))
    result = {}
    for path in sorted(set(paths)):
        if path.is_symlink():
            result[str(path)] = {'symlink': str(path.readlink())}
        elif path.is_file():
            # These fixed paths contain source, units, public CA/config or the
            # monitoring policy. Never hash credentials, sessions or databases.
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Preserved managed artifact exceeds budget')
            result[str(path)] = {'sha256': digest(path.read_bytes())}
    return result


def plan(source: Path, revision: str) -> tuple[dict, dict]:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Full reviewed Git revision required')
    source = source.absolute()
    protected(source, directory=True)
    if git(source, 'rev-parse', 'HEAD').decode().strip() != revision or git(source, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Clean checkout at the selected revision required')
    git(source, 'merge-base', '--is-ancestor', BASELINE, revision)
    git(source, 'merge-base', '--is-ancestor', revision, 'origin/master')
    own = protected(source / 'scripts/setup-notification-presentation.py')
    if own != Path(__file__).read_bytes() or own != git(source, 'show', revision + ':scripts/setup-notification-presentation.py'):
        raise ValueError('Refresh tool must match the selected committed source')
    rows, contents, bundle = [], {}, hashlib.sha256()
    for name, (directory, baseline_hash) in TARGETS.items():
        relative = 'scripts/' + name
        before_source = git(source, 'show', BASELINE + ':' + relative)
        if digest(before_source) != baseline_hash:
            raise ValueError('Baseline Git source does not match the approved manifest')
        after = protected(source / relative)
        if after != git(source, 'show', revision + ':' + relative):
            raise ValueError('Module differs from selected committed source')
        compile(after, relative, 'exec')
        target = Path(directory) / name
        protected(target.parent, directory=True)
        before = protected(target)
        if digest(before) not in {baseline_hash, digest(after)}:
            raise ValueError('Installed renderer differs from approved baseline and target')
        info = target.stat()
        contents[name] = {'path': target, 'before': before, 'after': after, 'mode': stat.S_IMODE(info.st_mode), 'gid': info.st_gid}
        rows.append({'source': relative, 'path': str(target), 'before_sha256': digest(before),
                     'after_sha256': digest(after), 'changed': before != after})
        bundle.update(relative.encode() + b'\0' + hashlib.sha256(after).digest())
    changed = {row['source'].split('/')[-1] for row in rows if row['changed']}
    names = []
    if changed & {'openclaw_notifications.py', 'openclaw_machine_notifications.py'}:
        names.append(ADAPTER)
    if 'openclaw_access_notifications.py' in changed:
        names.extend((ACCESS, PROXY, WATCH, CERTIFICATE))
    states = {name: service_state(name) for name in names}
    dependencies = []
    if states.get(ADAPTER, {}).get('active'):
        states[MACHINE] = service_state(MACHINE)
        if states[MACHINE]['active']:
            raise ValueError('Wait for the existing status projection to finish')
        dependencies.append({'unit': MACHINE, 'trigger': ADAPTER, 'invocation': 'read-only status projection',
                             'expected_final_active': False})
    if states.get(CERTIFICATE, {}).get('active'):
        raise ValueError('Wait for the existing certificate reload to finish')
    if states.get(PROXY, {}).get('active') and not states.get(ACCESS, {}).get('active'):
        raise ValueError('Access dependency state requires reconciliation')
    counts = in_flight() if changed else {}
    if any(counts.values()):
        raise ValueError('In-flight work requires reconciliation before refreshing renderers')
    result = {'source_revision': revision, 'baseline_revision': BASELINE, 'bundle_sha256': bundle.hexdigest(),
              'modules': rows, 'service_state': states, 'in_flight_counts': counts,
              'preserved_artifacts': preserved_artifacts(),
              'declared_start_dependencies': dependencies,
              'restart_only_previously_active': [name for name in START_ORDER if states.get(name, {}).get('active')]}
    result['plan_sha256'] = digest(canonical(result))
    return result, contents


def atomic(path: Path, raw: bytes, mode: int, gid: int) -> None:
    protected(path.parent, directory=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.notification-presentation-', dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, OWNER, gid)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def control(action: str, name: str) -> None:
    subprocess.run(['systemctl', action, name], capture_output=True, check=True, timeout=90)


def restore_services(states: dict) -> None:
    for name in START_ORDER:
        if states.get(name, {}).get('active'):
            control('start', name)
    for name, before in states.items():
        if service_state(name) != before:
            raise ValueError('Owned service activity differs after refresh')


def apply(source: Path, revision: str, expected_plan: str) -> dict:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('Apply requires root on home-core')
    protected(LOCK.parent, directory=True)
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        protected(LOCK)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report, contents = plan(source, revision)
        if not re.fullmatch(r'[0-9a-f]{64}', expected_plan) or report['plan_sha256'] != expected_plan:
            raise ValueError('Plan changed; inspect a fresh read-only plan')
        changed = {name: row for name, row in contents.items() if row['before'] != row['after']}
        if not changed:
            return {**report, 'applied': False, 'already_current': True}
        # This directory contains only reviewed source bytes and the public manifest.
        if BACKUPS.exists():
            protected(BACKUPS, directory=True)
        else:
            protected(BACKUPS.parent.parent, directory=True)
            BACKUPS.parent.mkdir(mode=0o700, exist_ok=True)
            protected(BACKUPS.parent, directory=True)
            BACKUPS.mkdir(mode=0o700)
        backup = BACKUPS / uuid.uuid4().hex
        backup.mkdir(mode=0o700)
        for name, row in changed.items():
            atomic(backup / name, row['before'], 0o600, os.getegid())
        atomic(backup / 'manifest.json', canonical(report) + b'\n', 0o600, os.getegid())
        touched = []
        handled = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous_handlers = {number: signal.getsignal(number) for number in handled}
        def interrupted(_number: int, _frame: object) -> None:
            raise RuntimeError('Notification renderer refresh interrupted')
        for number in handled:
            signal.signal(number, interrupted)
        try:
            for name in STOP_ORDER:
                if report['service_state'].get(name, {}).get('active'):
                    control('stop', name)
            # Stop active consumers before checking again; never clear delivery state.
            if any(in_flight().values()):
                raise ValueError('Work changed while pausing consumers; no refresh permitted')
            for name, row in changed.items():
                if protected(row['path']) != row['before']:
                    raise ValueError('Installed source changed after planning')
                touched.append(name)
                atomic(row['path'], row['after'], row['mode'], row['gid'])
                if protected(row['path']) != row['after']:
                    raise ValueError('Installed source verification failed')
            restore_services(report['service_state'])
            if any(protected(row['path']) != row['after'] for row in contents.values()):
                raise ValueError('Final source verification failed')
            if preserved_artifacts() != report['preserved_artifacts']:
                raise ValueError('Unrelated managed artifacts changed during refresh')
        except BaseException:
            # Finish rollback even if another ordinary termination signal arrives.
            for number in handled:
                signal.signal(number, signal.SIG_IGN)
            # Do not replace an unexpected concurrent edit, even during rollback.
            rollback_failed = False
            for name in STOP_ORDER:
                if report['service_state'].get(name, {}).get('active'):
                    try:
                        control('stop', name)
                    except Exception:
                        rollback_failed = True
            for name in reversed(touched):
                row = changed[name]
                try:
                    current = protected(row['path'])
                    if current not in {row['before'], row['after']}:
                        raise ValueError('Concurrent edit prevents rollback')
                    if current != row['before']:
                        atomic(row['path'], row['before'], row['mode'], row['gid'])
                    if protected(row['path']) != row['before']:
                        raise ValueError('Rollback verification failed')
                except Exception:
                    rollback_failed = True
            if not rollback_failed:
                try:
                    restore_services(report['service_state'])
                except Exception:
                    rollback_failed = True
            raise RuntimeError('Refresh failed; ' + ('manual reconciliation required; ' if rollback_failed else 'source and service activity restored; ') + 'backup: ' + str(backup)) from None
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        return {**report, 'applied': True, 'backup_directory': str(backup)}
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    try:
        report = apply(args.source, args.revision, args.expected_plan_sha256 or '') if args.apply else plan(args.source, args.revision)[0]
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        # Fixed diagnostics, file paths and hashes only; never subprocess output.
        print(json.dumps({'refused': True, 'reason': str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__}))
        return 2


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
