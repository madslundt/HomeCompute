#!/usr/bin/env python3
"""Admit Home Assistant zone transitions into the existing durable reminder queues."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
from typing import Any, Callable
import uuid

CONFIG_ROOT = Path('/var/lib/homecompute-openclaw/config')
CONFIG = CONFIG_ROOT / 'location-reminders.json'
STATE = Path('/var/lib/homecompute-openclaw/communication/adapter')
STATUS_NAME = 'location-reminder-status.json'


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def private_state(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Private location reminder state required')
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Private location reminder state required')


def status_write(state: Path, value: dict[str, Any]) -> None:
    from openclaw_household_requests import private_file
    private_state(state)
    target = state / STATUS_NAME
    if target.exists() or target.is_symlink():
        private_file(target)
    temporary = state / ('.location-status-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(json.dumps(value, allow_nan=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def task_stores(state: Path, policy: Any) -> list[tuple[str, Any]]:
    from openclaw_household_tasks import FamilyTasks, HouseholdTasks, configuration
    result = []
    for scope, name, cls in (('owner', 'household-tasks.json', HouseholdTasks),
                             ('family', 'family-tasks.json', FamilyTasks)):
        path = CONFIG_ROOT / name
        if path.exists():
            configuration(path, scope=scope)
            result.append((scope, cls(state, location_policy=policy)))
    if not result:
        raise ValueError('Configured reminder task stores required')
    return result


def worker_delivery(scope: str, state: Path) -> dict[str, Any]:
    path = Path(__file__).with_name('openclaw-household-task-reminders.py')
    spec = importlib.util.spec_from_file_location('location_reminder_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = CONFIG_ROOT / ('family-tasks.json' if scope == 'family' else 'household-tasks.json')
    return module.run_scope(scope, state, config)


def admit(event: dict[str, Any], stores: list[tuple[str, Any]], state: Path,
          now: datetime, deliver: Callable[[str, Path], Any] = worker_delivery) -> int:
    matched = 0
    for scope, tasks in stores:
        result = tasks.accept_location_event(event, now)
        count = result['matched']
        if type(count) is not int or count < 0:
            raise ValueError('Bounded event admission receipt required')
        matched += count
        if count:
            # The worker shares the minute timer's lock and durable delivery claim.
            # A delivery failure never removes the admitted event or retries a send.
            try:
                deliver(scope, state)
            except (OSError, ValueError, TypeError, sqlite3.Error):
                pass
    return matched


def check(policy: Any, client_factory: Callable[..., Any]) -> dict[str, Any]:
    with client_factory(policy) as client:
        count = client.subscribe()
    return {'ok': True, 'connected': True, 'subscription_count': count,
            'messages_sent': 0, 'events_admitted': 0}


def listen(policy: Any, state: Path, stores: list[tuple[str, Any]],
           client_factory: Callable[..., Any], stop: Callable[[], bool],
           *, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], datetime] = utc_now,
           deliver: Callable[[str, Path], Any] = worker_delivery) -> None:
    status = {'schema_version': 1, 'connected': False, 'subscription_count': 0,
              'last_connected_at': None, 'last_event_at': None, 'disconnected_at': None,
              'last_gap_seconds': 0, 'reconnects': 0, 'last_error': None}
    status_write(state, status)
    backoff = 1
    last_write = time.monotonic()
    while not stop():
        try:
            with client_factory(policy) as client:
                count = client.subscribe()
                now = clock()
                if status['disconnected_at']:
                    gap = now - datetime.fromisoformat(status['disconnected_at'])
                    status['last_gap_seconds'] = max(0, round(gap.total_seconds(), 3))
                status.update(connected=True, subscription_count=count,
                              last_connected_at=now.isoformat(), last_error=None)
                status_write(state, status)
                backoff = 1
                while not stop():
                    event = client.next_event(timeout_seconds=5)
                    if event is not None:
                        admit(event, stores, state, clock(), deliver)
                        status['last_event_at'] = event['occurred_at']
                    if event is not None or time.monotonic() - last_write >= 30:
                        status_write(state, status)
                        last_write = time.monotonic()
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            disconnected_at = (clock().isoformat() if status['connected'] or not status['disconnected_at']
                               else status['disconnected_at'])
            status.update(connected=False, subscription_count=0,
                          disconnected_at=disconnected_at, last_error='location_stream_unavailable')
            status['reconnects'] += 1
            status_write(state, status)
            # The next connection starts with new subscriptions. Current positions
            # and transitions missed while disconnected never become reminders.
            for _ in range(backoff):
                if stop():
                    break
                sleep(1)
            backoff = min(backoff * 2, 30)
    status.update(connected=False, subscription_count=0, last_error='stopped')
    status_write(state, status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    from openclaw_location_policy import Policy
    from openclaw_location_transport import LocationClient
    try:
        policy = Policy(args.config)
        if args.check:
            print(json.dumps(check(policy, LocationClient)))
            return 0
        private_state(args.state)
        from openclaw_household_requests import private_file
        lock_path = args.state / 'location-reminders.lock'
        private_file(lock_path)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stopped = False
            def halt(*_: Any) -> None:
                nonlocal stopped
                stopped = True
            signal.signal(signal.SIGTERM, halt)
            signal.signal(signal.SIGINT, halt)
            listen(policy, args.state, task_stores(args.state, policy), LocationClient, lambda: stopped)
        finally:
            os.close(descriptor)
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        print(json.dumps({'ok': False, 'reason': 'location_reminder_listener_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
