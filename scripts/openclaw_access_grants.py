"""Trusted-host access requests and expiring grants; no credential/browser API.

Only the trusted bridge supplies actor identity. This ledger is not an execution
engine: a qualified site adapter must check authorize before every effect, and
must close its owned browser session on expiry/revocation. Book/payment execution
is deliberately unavailable. Production bridge, notification and browser wiring
require separate qualification.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from uuid import UUID, uuid4

from openclaw_bitwarden import open_directory
from openclaw_site_registry import SiteRegistry, SiteRefused, account_name, SCOPES

ACCOUNT = 'hotels.com'


class GrantRefused(ValueError):
    """Only fixed diagnostics; never use untrusted text in this exception."""


def identity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value):
        raise GrantRefused('Actor refused')
    return value


def request_id(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise GrantRefused('Request reference refused')
    return value


def minutes(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 1440:
        raise GrantRefused('Duration refused')
    return value


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class AccessGrants:
    def __init__(self, directory: Path, *, clock=time.time, registry: SiteRegistry | None = None,
                 execution_available=None):
        self.directory, self.clock = directory, clock
        self.registry = SiteRegistry() if registry is None else registry
        self.execution_available = execution_available
        parent = open_directory(directory, create=True)
        try:
            try:
                descriptor = os.open('access.sqlite', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
            except FileExistsError:
                descriptor = os.open('access.sqlite', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise GrantRefused('Private access ledger refused')
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
        self.path = directory / 'access.sqlite'
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_key TEXT NOT NULL,
                scope TEXT NOT NULL, requested_minutes INTEGER NOT NULL, created INTEGER NOT NULL,
                digest TEXT NOT NULL, status TEXT NOT NULL, approved INTEGER, expires INTEGER,
                last_seen INTEGER NOT NULL,
                UNIQUE(actor, request_key));
              CREATE TABLE IF NOT EXISTS challenges (
                nonce_hash TEXT PRIMARY KEY, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                action TEXT NOT NULL, expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL, action TEXT NOT NULL, at INTEGER NOT NULL);
            ''')
            columns = {entry[1] for entry in db.execute('PRAGMA table_info(requests)')}
            if 'account' not in columns:
                # Historical immutable request digests already included hotels.com.
                # Preserve every reference, digest, grant state and expiry verbatim.
                db.execute("ALTER TABLE requests ADD COLUMN account TEXT NOT NULL DEFAULT 'hotels.com'")

    @contextlib.contextmanager
    def connect(self):
        # Only the trusted service owns this 0700 directory. The DB and journals
        # are never mounted into agent sandboxes. Startup validates the DB file.
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction:
                db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def now(self) -> int:
        value = int(self.clock())
        if value < 0:
            raise GrantRefused('Clock refused')
        return value

    def row(self, db, reference: str):
        reference = request_id(reference)
        row = db.execute('SELECT * FROM requests WHERE id=?', (reference,)).fetchone()
        if row is None:
            raise GrantRefused('Request unavailable')
        now = self.now()
        expired = (row['status'] in {'pending', 'active'} and now < row['last_seen']
                   or row['status'] == 'pending' and (now < row['created'] or now >= row['created'] + 3600)
                   or row['status'] == 'active' and (now < row['approved'] or now >= row['expires']))
        if expired:
            db.execute("UPDATE requests SET status='expired' WHERE id=?", (reference,))
            db.execute('DELETE FROM challenges WHERE request_id=?', (reference,))
            db.execute('INSERT INTO events(request_id,action,at) VALUES(?,?,?)', (reference, 'expired', now))
            row = db.execute('SELECT * FROM requests WHERE id=?', (reference,)).fetchone()
        elif now > row['last_seen']:
            db.execute('UPDATE requests SET last_seen=? WHERE id=?', (now, reference))
            row = db.execute('SELECT * FROM requests WHERE id=?', (reference,)).fetchone()
        return row

    def public(self, row) -> dict:
        account = account_name(row['account'])
        available = False
        try:
            profile = self.registry.profile(account)
            self.registry.validate_scope(account, row['scope'])
            available = (profile['qualified'] and self.execution_available is not None
                         and self.execution_available(account, row['scope']) is True)
        except Exception:
            # Removed or unqualified sites retain reviewable historical records.
            available = False
        return {'request_id': row['id'], 'actor': row['actor'], 'account': account,
                'scope': row['scope'], 'requested_minutes': row['requested_minutes'],
                'requested_at': row['created'], 'request_digest': row['digest'],
                'status': row['status'], 'approved_at': row['approved'], 'expires_at': row['expires'],
                'account_execution_available': available,
                'transaction_execution_available': False}

    def request(self, actor: str, values: dict) -> tuple[dict, bool]:
        identity(actor)
        if (not isinstance(values, dict) or set(values) != {'account', 'scope', 'minutes', 'request_key'}
                or not isinstance(values['scope'], str) or values['scope'] not in SCOPES
                or not isinstance(values['request_key'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', values['request_key'])):
            raise GrantRefused('Access request refused')
        try:
            self.registry.validate_scope(values['account'], values['scope'])
        except SiteRefused:
            raise GrantRefused('Website access is not registered') from None
        duration = minutes(values['minutes'])
        with self.connect() as db:
            # Sweep the bounded actor queue before applying its pending budget.
            # An abandoned request must not require an operator/status read to expire.
            pending = db.execute("SELECT id FROM requests WHERE actor=? AND status='pending'", (actor,)).fetchall()
            for entry in pending:
                self.row(db, entry['id'])
            old = db.execute('SELECT * FROM requests WHERE actor=? AND request_key=?',
                             (actor, values['request_key'])).fetchone()
            if old is not None:
                if old['account'] != values['account'] or old['scope'] != values['scope'] or old['requested_minutes'] != duration:
                    raise GrantRefused('Request key already bound')
                return self.public(self.row(db, old['id'])), False
            if db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 10000:
                raise GrantRefused('Access ledger capacity reached')
            if db.execute("SELECT count(*) FROM requests WHERE actor=? AND status='pending'",
                          (actor,)).fetchone()[0] >= 20:
                raise GrantRefused('Pending request budget reached')
            reference, now = str(uuid4()), self.now()
            immutable = {'request_id': reference, 'actor': actor, 'account': values['account'],
                         'scope': values['scope'], 'requested_minutes': duration, 'requested_at': now}
            db.execute('INSERT INTO requests(id,actor,request_key,scope,requested_minutes,created,digest,status,approved,expires,last_seen,account) '
                       "VALUES(?,?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                       (reference, actor, values['request_key'], values['scope'], duration, now, digest(immutable), now, values['account']))
            db.execute('INSERT INTO events(request_id,action,at) VALUES(?,?,?)', (reference, 'requested', now))
            return self.public(self.row(db, reference)), True

    def status(self, reference: str, *, actor: str | None = None) -> dict:
        if actor is not None:
            identity(actor)
        with self.connect() as db:
            row = self.row(db, reference)
            if actor is not None and actor != row['actor']:
                raise GrantRefused('Request unavailable')
            return self.public(row)

    def listing(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute('SELECT id FROM requests ORDER BY created DESC,id LIMIT 50').fetchall()
            return [self.public(self.row(db, row['id'])) for row in rows]

    def challenge(self, reference: str, action: str) -> str:
        if action not in {'approve', 'deny', 'revoke'}:
            raise GrantRefused('Decision refused')
        with self.connect() as db:
            row, now = self.row(db, reference), self.now()
            db.execute('DELETE FROM challenges WHERE expires<=?', (now,))
            if db.execute('SELECT count(*) FROM challenges').fetchone()[0] >= 1000:
                raise GrantRefused('Review challenge budget reached')
            nonce = secrets.token_urlsafe(32)
            db.execute('INSERT INTO challenges VALUES(?,?,?,?,?)',
                       (hashlib.sha256(nonce.encode()).hexdigest(), reference, row['digest'], action, now + 300))
            return nonce

    def decide(self, reference: str, request_digest: str, action: str, duration: int, nonce: str) -> dict:
        if action not in {'approve', 'deny', 'revoke'} or not isinstance(nonce, str) or len(nonce) > 128:
            raise GrantRefused('Decision refused')
        duration = minutes(duration)
        if not isinstance(request_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', request_digest):
            raise GrantRefused('Review digest refused')
        with self.connect() as db:
            row, now = self.row(db, reference), self.now()
            challenge = db.execute('SELECT * FROM challenges WHERE nonce_hash=?',
                                   (hashlib.sha256(nonce.encode()).hexdigest(),)).fetchone()
            if (not hmac.compare_digest(row['digest'], request_digest) or challenge is None
                    or challenge['request_id'] != reference or challenge['digest'] != request_digest
                    or challenge['action'] != action or now >= challenge['expires']):
                raise GrantRefused('Review challenge refused')
            if (action in {'approve', 'deny'} and row['status'] != 'pending'
                    or action == 'revoke' and row['status'] != 'active'):
                raise GrantRefused('Decision no longer applicable')
            if action == 'approve':
                db.execute("UPDATE requests SET status='active',approved=?,expires=? WHERE id=?",
                           (now, now + duration * 60, reference))
            else:
                db.execute('UPDATE requests SET status=? WHERE id=?',
                           ('denied' if action == 'deny' else 'revoked', reference))
            db.execute('DELETE FROM challenges WHERE request_id=?', (reference,))
            db.execute('INSERT INTO events(request_id,action,at) VALUES(?,?,?)', (reference, action, now))
            return self.public(self.row(db, reference))

    def authorize(self, actor: str, reference: str, scope: str, *, account: str | None = None) -> dict:
        if not isinstance(scope, str) or scope not in SCOPES:
            raise GrantRefused('Execution scope refused')
        value = self.status(reference, actor=actor)
        try:
            self.registry.validate_scope(value['account'], scope)
        except SiteRefused:
            raise GrantRefused('Website access is not registered') from None
        if account is not None and value['account'] != account:
            raise GrantRefused('Grant belongs to another website')
        if value['status'] != 'active' or value['scope'] != scope:
            raise GrantRefused('Account access not active')
        return value
