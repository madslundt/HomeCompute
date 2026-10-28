"""Private durable receipts fence household writes across retries and restarts."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import Any, Callable, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def private_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('Private household receipt file required')


class Requests:
    def __init__(self, path: Path):
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError('Private household receipt directory required')
        private_file(path)
        self.path, self.mutex = path, threading.Lock()
        self.lock_path = path.with_suffix('.lock')
        private_file(self.lock_path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, '
                       'operation TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, '
                       'updated_at TEXT NOT NULL, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, '
                       'request_id TEXT NOT NULL, state TEXT NOT NULL, recorded_at TEXT NOT NULL)')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        private_file(self.path)
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def transition(db: sqlite3.Connection, request_id: str, state: str, receipt: dict[str, Any] | None = None) -> None:
        recorded = now()
        encoded = json.dumps(receipt, allow_nan=False) if receipt is not None else None
        if encoded is not None and len(encoded.encode()) > 24000:
            raise ValueError('Household receipt exceeds budget')
        db.execute('UPDATE requests SET state=?,updated_at=?,receipt=? WHERE id=?',
                   (state, recorded, encoded, request_id))
        db.execute('INSERT INTO events(request_id,state,recorded_at) VALUES(?,?,?)', (request_id, state, recorded))
        db.commit()  # Sending must be durable before any mutating network frame.

    def status(self, request_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute('SELECT id,operation,state,created_at,updated_at FROM requests WHERE id=?',
                             (request_id,)).fetchone()
        if row is None:
            return {'request_id': request_id, 'state': 'not_found', 'changed': False}
        result = dict(row)
        result['request_id'] = result.pop('id')
        result['retry_allowed'] = result['state'] == 'prepared'
        if result['state'] in {'sending', 'uncertain'}:
            result['guidance'] = 'Read the current records. Do not repeat the write with a new request ID.'
        return result

    def run(self, operation: str, arguments: dict[str, Any],
            execute: Callable[[Callable[[], None]], dict[str, Any]]) -> dict[str, Any]:
        if not self.mutex.acquire(blocking=False):
            raise ValueError('Another household write is in progress')
        descriptor = None
        try:
            private_file(self.lock_path)
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.connect() as db:
                return self._run(db, operation, arguments, execute)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self.mutex.release()

    def _run(self, db: sqlite3.Connection, operation: str, arguments: dict[str, Any],
             execute: Callable[[Callable[[], None]], dict[str, Any]]) -> dict[str, Any]:
        request_id = arguments['request_id']
        encoded = json.dumps({'actor': 'openclaw-owner', 'operation': operation, 'arguments': arguments},
                             sort_keys=True, separators=(',', ':'), allow_nan=False)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        row = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is not None:
            if row['fingerprint'] != fingerprint:
                raise ValueError('Household request ID already belongs to another operation')
            if row['state'] == 'sending':
                receipt = {'outcome': 'uncertain', 'changed': None, 'request_id': request_id,
                           'guidance': 'Previous write was interrupted. Read current records; never repeat automatically.'}
                self.transition(db, request_id, 'uncertain', receipt)
                return receipt
            if row['state'] != 'prepared':
                return {**json.loads(row['receipt']), 'replayed': True}
        else:
            recorded = now()
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,NULL)',
                       (request_id, fingerprint, operation, 'prepared', recorded, recorded))
            self.transition(db, request_id, 'prepared')
        sent = False
        def before_write() -> None:
            nonlocal sent
            if sent:
                raise ValueError('Only one mutation per household request is permitted')
            self.transition(db, request_id, 'sending')
            sent = True
        try:
            receipt = execute(before_write)
            if (not isinstance(receipt, dict) or receipt.get('outcome') not in {'confirmed', 'uncertain'}
                    or (receipt.get('changed') is not None and type(receipt['changed']) is not bool)):
                raise ValueError('Verified household receipt required')
            receipt = {**receipt, 'request_id': request_id}
            self.transition(db, request_id, receipt['outcome'], receipt)
            return receipt
        except (OSError, ValueError, TypeError, KeyError, TimeoutError):
            state = 'uncertain' if sent else 'blocked'
            receipt = {'request_id': request_id, 'outcome': state, 'changed': None if sent else False,
                       'guidance': ('Read current records. Do not repeat this write with another request ID.' if sent else
                                    'No change was sent. Refresh the records and check the exact item and preconditions.')}
            self.transition(db, request_id, state, receipt)
            return receipt
