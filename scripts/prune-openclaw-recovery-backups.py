#!/usr/bin/env python3
"""Prune redundant OpenClaw recovery copies after a verified successful restore.

Dry-run by default. Apply is refused unless the live sandbox is Ready and the
current recovery transaction points at a fully verified backup.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

STATE = Path('/home/hermes-operator/.local/state/homecompute-openclaw-supervisor')
TRANSACTION = 'error-recovery-transaction.json'
LOCK = 'error-recovery.lock'
KEEP_LATEST = 5
CLI = '/home/hermes-operator/.local/bin/nemohermes'
NAME = 'agent-openclaw'


class Refused(Exception):
    """Fixed operational refusal; never include user data."""


def require_verified_current(state: Path) -> Path:
    if state.is_symlink() or not state.is_dir():
        raise Refused('state-directory-refused')
    journal = state / TRANSACTION
    if journal.is_symlink() or not journal.is_file():
        raise Refused('recovery-journal-missing')
    try:
        record = json.loads(journal.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Refused('recovery-journal-invalid') from None
    if record.get('status') != 'complete' or record.get('stage') not in ('complete', 'complete-active-team'):
        raise Refused('recovery-not-complete')
    backup = Path(record.get('backup', ''))
    if (not backup.is_absolute() or backup.is_symlink() or backup.parent != state
            or not backup.name.startswith('error-backup-') or not backup.is_dir()):
        raise Refused('current-backup-path-refused')
    required = (
        backup / '.openclaw' / 'openclaw.json',
        backup / 'verification-ready' / '.openclaw' / 'openclaw.json',
        backup / 'replacement-native.json',
        backup / 'restored-config.json',
    )
    if any(path.is_symlink() or not path.is_file() for path in required):
        raise Refused('current-backup-not-verified')
    return backup


def runtime_ready(runner: Callable[..., Any] = subprocess.run) -> None:
    env = {key: value for key, value in os.environ.items()
           if key not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'SSH_AUTH_SOCK'}}
    env.update(PATH='/home/hermes-operator/.local/bin:/usr/local/bin:/usr/bin:/bin',
               NEMOCLAW_GATEWAY_PORT='9123')
    try:
        result = runner([CLI, NAME, 'status', '--json'], capture_output=True, text=True,
                        timeout=20, check=True, env=env)
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        raise Refused('runtime-status-unavailable') from None
    if (status.get('phase') != 'Ready' or status.get('inferenceHealth', {}).get('ok') is not True
            or status.get('recordedRoute', {}).get('model') != 'automation-moe'):
        raise Refused('runtime-not-ready')


def plan(state: Path, current: Path, keep: int = KEEP_LATEST) -> dict[str, Any]:
    if keep < 2:
        raise Refused('minimum-retention-refused')
    entries = []
    for path in state.iterdir():
        if not path.name.startswith('error-backup-'):
            continue
        if path.is_symlink() or not path.is_dir():
            raise Refused('backup-entry-type-refused')
        payload = path / '.openclaw'
        if payload.is_symlink():
            raise Refused('backup-payload-symlink-refused')
        if not payload.is_dir():
            continue
        entries.append(path)
    entries.sort(key=lambda item: (item.stat().st_mtime_ns, item.name), reverse=True)
    preserve = {item for item in entries[:keep]}
    preserve.add(current)
    candidates = [item for item in entries if item not in preserve]
    byte_counts = {item: sum(child.lstat().st_blocks * 512 for child in item.rglob('*')
                              if not child.is_symlink() and child.is_file())
                   for item in candidates}
    return {
        'current_backup': current.name,
        'keep_count': keep,
        'preserve': sorted(item.name for item in preserve),
        'remove': sorted(item.name for item in candidates),
        'remove_count': len(candidates),
        'reclaim_bytes': sum(byte_counts.values()),
    }


def apply(state: Path, result: dict[str, Any]) -> dict[str, Any]:
    removed = []
    for name in result['remove']:
        path = state / name
        if path.parent != state or path.is_symlink() or not path.is_dir() or not name.startswith('error-backup-'):
            raise Refused('backup-changed-during-prune')
        shutil.rmtree(path)
        removed.append(name)
    receipt = {
        'schema_version': 1,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'reason': 'redundant recovery snapshots pruned after verified restore',
        'current_backup': result['current_backup'],
        'kept_count': len(result['preserve']),
        'removed_count': len(removed),
        'reclaimed_bytes_estimate': result['reclaim_bytes'],
        'removed': removed,
    }
    path = state / f"recovery-prune-{int(time.time())}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    receipt['receipt'] = path.name
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete only redundant error-backup snapshots')
    args = parser.parse_args()
    lock_path = STATE / LOCK
    try:
        if os.geteuid() != 1000 or Path.home() != Path('/home/hermes-operator'):
            raise Refused('guest-operator-required')
        if lock_path.is_symlink():
            raise Refused('recovery-lock-refused')
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('recovery-transaction-active') from None
            current = require_verified_current(STATE)
            runtime_ready()
            result = plan(STATE, current)
            if args.apply:
                receipt = apply(STATE, result)
                result = {
                    'applied': True,
                    'current_backup': receipt['current_backup'],
                    'kept_count': receipt['kept_count'],
                    'removed_count': receipt['removed_count'],
                    'reclaimed_bytes_estimate': receipt['reclaimed_bytes_estimate'],
                    'receipt': receipt['receipt'],
                }
            else:
                result['applied'] = False
            print(json.dumps(result, sort_keys=True))
        finally:
            os.close(fd)
        return 0
    except Refused as error:
        print(f'OpenClaw recovery snapshot cleanup refused: {error}', file=sys.stderr)
        return 1
    except OSError:
        print('OpenClaw recovery snapshot cleanup failed; live state was preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
