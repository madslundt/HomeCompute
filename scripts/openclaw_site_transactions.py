"""Exact, owner-approved site transactions for a future trusted finalizer.

Only a trusted site driver may propose snapshots or claim execution. This is not
an agent tool and never accepts URLs, selectors, callbacks or credentials. A
claim consumes approval before any external effect; callers must never retry an
uncertain effect. The finalizer must enforce the returned deadline, recheck its
grant immediately before sending, and record only a bounded outcome/reference.
No production driver is qualified by default.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Callable
import unicodedata
from uuid import UUID, uuid4

from openclaw_bitwarden import open_directory

KINDS = frozenset({'purchase', 'booking'})
AuthorizeGrant = Callable[[str, str], dict]


class TransactionRefused(ValueError):
    """Fixed diagnostics only; never incorporate driver or credential text."""


def reference(value: object) -> str:
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise TransactionRefused('Transaction reference refused')
    return value


def actor_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value):
        raise TransactionRefused('Transaction actor refused')
    return value


def account_id(value: object) -> str:
    if (not isinstance(value, str) or len(value) > 253
            or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value)):
        raise TransactionRefused('Transaction account refused')
    return value


def plain(value: object, maximum: int) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum or value.strip() != value
            or any(unicodedata.category(char).startswith('C') or char in '<>' for char in value)):
        raise TransactionRefused('Transaction text refused')
    return value


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def validate_snapshot(value: object) -> dict:
    fields = {'account', 'kind', 'payee', 'amount_minor', 'currency', 'items', 'terms', 'expires_at'}
    if (not isinstance(value, dict) or set(value) != fields or not isinstance(value['kind'], str)
            or value['kind'] not in KINDS or type(value['amount_minor']) is not int
            or not 0 <= value['amount_minor'] <= 10**12 or not isinstance(value['currency'], str)
            or not re.fullmatch(r'[A-Z]{3}', value['currency']) or type(value['expires_at']) is not int
            or not 0 <= value['expires_at'] <= 2**53 or not isinstance(value['items'], list)
            or not 1 <= len(value['items']) <= 12 or not isinstance(value['terms'], list)
            or not 1 <= len(value['terms']) <= 12):
        raise TransactionRefused('Exact transaction snapshot refused')
    items = []
    for item in value['items']:
        if (not isinstance(item, dict) or set(item) != {'description', 'quantity', 'unit_amount_minor'}
                or type(item['quantity']) is not int or not 1 <= item['quantity'] <= 100
                or type(item['unit_amount_minor']) is not int or not 0 <= item['unit_amount_minor'] <= 10**12):
            raise TransactionRefused('Transaction line item refused')
        items.append({'description': plain(item['description'], 240), 'quantity': item['quantity'],
                      'unit_amount_minor': item['unit_amount_minor']})
    if sum(item['quantity'] * item['unit_amount_minor'] for item in items) != value['amount_minor']:
        raise TransactionRefused('Transaction total differs from line items')
    result = {'account': account_id(value['account']), 'kind': value['kind'], 'payee': plain(value['payee'], 160),
              'amount_minor': value['amount_minor'], 'currency': value['currency'], 'items': items,
              'terms': [plain(term, 600) for term in value['terms']], 'expires_at': value['expires_at']}
    if len(canonical(result).encode()) > 12288:
        raise TransactionRefused('Transaction snapshot budget refused')
    return result


class SiteTransactions:
    def __init__(self, directory: Path, authorize_grant: AuthorizeGrant, *, clock=time.time,
                 qualified_drivers: frozenset[tuple[str, str, str]] = frozenset()):
        if not callable(authorize_grant) or not isinstance(qualified_drivers, frozenset):
            raise TransactionRefused('Trusted finalizer configuration refused')
        for entry in qualified_drivers:
            if (not isinstance(entry, tuple) or len(entry) != 3 or not isinstance(entry[0], str)
                    or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', entry[0])
                    or account_id(entry[1]) != entry[1] or not isinstance(entry[2], str) or entry[2] not in KINDS):
                raise TransactionRefused('Qualified driver configuration refused')
        self.directory, self.authorize_grant, self.clock = directory, authorize_grant, clock
        self.qualified_drivers = qualified_drivers
        parent = open_directory(directory, create=True)
        try:
            try:
                descriptor = os.open('transactions.sqlite', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
            except FileExistsError:
                descriptor = os.open('transactions.sqlite', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise TransactionRefused('Private transaction ledger refused')
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
        self.path = directory / 'transactions.sqlite'
        with self._connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,actor TEXT NOT NULL,grant_id TEXT NOT NULL,account TEXT NOT NULL,
                kind TEXT NOT NULL,snapshot TEXT NOT NULL,digest TEXT NOT NULL,created INTEGER NOT NULL,
                expires INTEGER NOT NULL,last_seen INTEGER NOT NULL,status TEXT NOT NULL,
                approved INTEGER,qualified_at_approval INTEGER NOT NULL DEFAULT 0,qualified_set_digest TEXT,
                execution_id TEXT UNIQUE,claimed INTEGER,driver_id TEXT,outcome_at INTEGER,receipt TEXT);
              CREATE TABLE IF NOT EXISTS challenges (
                hash TEXT PRIMARY KEY,transaction_id TEXT NOT NULL,digest TEXT NOT NULL,
                action TEXT NOT NULL,expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY,transaction_id TEXT NOT NULL,action TEXT NOT NULL,at INTEGER NOT NULL);
            ''')

    @contextlib.contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction:
                db.execute('COMMIT')
        except TransactionRefused:
            # Keep fail-closed clock/expiry transitions even when an action is
            # refused. Authorization/effect mutations occur only after guards.
            if db.in_transaction:
                db.execute('COMMIT')
            raise
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _now(self) -> int:
        value = self.clock()
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise TransactionRefused('Transaction clock refused')
        return int(value)

    def _grant(self, actor: str, grant_id: str, account: str) -> dict:
        try:
            grant = self.authorize_grant(actor, grant_id)
            now = self._now()
            if (not isinstance(grant, dict) or grant.get('request_id') != grant_id or grant.get('actor') != actor
                    or grant.get('account') != account or grant.get('status') != 'active'
                    or type(grant.get('approved_at')) is not int or not 0 <= grant['approved_at'] <= now
                    or type(grant.get('expires_at')) is not int or grant['expires_at'] <= now):
                raise TransactionRefused('Active account grant required')
            return grant
        except Exception:
            raise TransactionRefused('Active account grant required') from None

    def _row(self, db, transaction_id: str):
        row = db.execute('SELECT * FROM transactions WHERE id=?', (reference(transaction_id),)).fetchone()
        if row is None:
            raise TransactionRefused('Transaction unavailable')
        now = self._now()
        if row['status'] in {'pending', 'approved', 'executing'} and (now < row['last_seen'] or now >= row['expires']):
            state = 'unknown' if row['status'] == 'executing' else 'expired'
            db.execute('UPDATE transactions SET status=? WHERE id=?', (state, transaction_id))
            db.execute('DELETE FROM challenges WHERE transaction_id=?', (transaction_id,))
            db.execute('INSERT INTO events(transaction_id,action,at) VALUES(?,?,?)', (transaction_id, state, now))
            row = db.execute('SELECT * FROM transactions WHERE id=?', (transaction_id,)).fetchone()
        elif now > row['last_seen']:
            db.execute('UPDATE transactions SET last_seen=? WHERE id=?', (now, transaction_id))
            row = db.execute('SELECT * FROM transactions WHERE id=?', (transaction_id,)).fetchone()
        return row

    def _available(self, row) -> bool:
        return any(account == row['account'] and kind == row['kind'] for _driver, account, kind in self.qualified_drivers)

    def _driver_digest(self, row) -> str:
        matching = sorted(entry for entry in self.qualified_drivers if entry[1:] == (row['account'], row['kind']))
        return hashlib.sha256(canonical({'qualified_drivers': matching}).encode()).hexdigest()

    def _public(self, row) -> dict:
        original = json.loads(row['snapshot'])
        return {'transaction_id': row['id'], 'actor': row['actor'], 'grant_ref': row['grant_id'],
                **original, 'quote_expires_at': original['expires_at'],
                'proposal_digest': row['digest'], 'created_at': row['created'],
                'expires_at': row['expires'], 'status': row['status'], 'approved_at': row['approved'],
                'execution_id': row['execution_id'], 'receipt_reference': row['receipt'],
                'driver_qualified': self._available(row),
                'execution_available': bool(self._available(row) and row['status'] == 'approved'
                    and row['qualified_at_approval'] and row['qualified_set_digest'] == self._driver_digest(row)),
                'reconciliation_required': row['status'] in {'executing', 'unknown'}}

    def propose(self, trusted_actor: str, grant_ref: str, snapshot: dict) -> dict:
        actor, grant_ref, value = actor_id(trusted_actor), reference(grant_ref), validate_snapshot(snapshot)
        grant, now = self._grant(actor, grant_ref, value['account']), self._now()
        expiry = min(value['expires_at'], grant['expires_at'], now + 900)
        if expiry <= now:
            raise TransactionRefused('Transaction quote expired')
        transaction_id = str(uuid4())
        immutable = {'transaction_id': transaction_id, 'actor': actor, 'grant_ref': grant_ref,
                     'snapshot': value, 'created_at': now, 'expires_at': expiry}
        proposal_digest = hashlib.sha256(canonical(immutable).encode()).hexdigest()
        with self._connect() as db:
            if db.execute("SELECT 1 FROM transactions WHERE account=? AND status IN ('executing','unknown') LIMIT 1",
                          (value['account'],)).fetchone() is not None:
                raise TransactionRefused('Account has an unresolved transaction; operator reconciliation required')
            if db.execute('SELECT count(*) FROM transactions').fetchone()[0] >= 10000:
                raise TransactionRefused('Transaction ledger capacity reached')
            db.execute('INSERT INTO transactions '
                       '(id,actor,grant_id,account,kind,snapshot,digest,created,expires,last_seen,status) '
                       "VALUES(?,?,?,?,?,?,?,?,?,?,'pending')",
                       (transaction_id, actor, grant_ref, value['account'], value['kind'], canonical(value),
                        proposal_digest, now, expiry, now))
            db.execute('INSERT INTO events(transaction_id,action,at) VALUES(?,?,?)', (transaction_id, 'proposed', now))
            return self._public(self._row(db, transaction_id))

    def review(self, transaction_id: str, *, actor: str | None = None) -> dict:
        if actor is not None:
            actor_id(actor)
        with self._connect() as db:
            row = self._row(db, transaction_id)
            if actor is not None and row['actor'] != actor:
                raise TransactionRefused('Transaction unavailable')
            return self._public(row)

    def challenge(self, transaction_id: str, action: str) -> str:
        if not isinstance(action, str) or action not in {'approve', 'deny', 'revoke'}:
            raise TransactionRefused('Transaction decision refused')
        with self._connect() as db:
            row, now = self._row(db, transaction_id), self._now()
            if ((action in {'approve', 'deny'} and row['status'] != 'pending')
                    or (action == 'revoke' and row['status'] != 'approved')):
                raise TransactionRefused('Transaction decision no longer applicable')
            db.execute('DELETE FROM challenges WHERE expires<=?', (now,))
            if db.execute('SELECT count(*) FROM challenges').fetchone()[0] >= 1000:
                raise TransactionRefused('Transaction review budget reached')
            nonce = secrets.token_urlsafe(32)
            db.execute('INSERT INTO challenges VALUES(?,?,?,?,?)',
                       (hashlib.sha256(nonce.encode()).hexdigest(), transaction_id, row['digest'], action, min(now + 300, row['expires'])))
            return nonce

    def decide(self, transaction_id: str, proposal_digest: str, action: str, nonce: str) -> dict:
        if (not isinstance(action, str) or action not in {'approve', 'deny', 'revoke'} or not isinstance(nonce, str) or not 1 <= len(nonce) <= 128
                or not isinstance(proposal_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', proposal_digest)):
            raise TransactionRefused('Transaction decision refused')
        with self._connect() as db:
            row, now = self._row(db, transaction_id), self._now()
            challenge = db.execute('SELECT * FROM challenges WHERE hash=?', (hashlib.sha256(nonce.encode()).hexdigest(),)).fetchone()
            if (challenge is None or challenge['transaction_id'] != transaction_id or challenge['action'] != action
                    or now >= challenge['expires'] or challenge['digest'] != proposal_digest
                    or not hmac.compare_digest(row['digest'], proposal_digest)):
                raise TransactionRefused('Exact transaction review required')
            if ((action in {'approve', 'deny'} and row['status'] != 'pending')
                    or (action == 'revoke' and row['status'] != 'approved')):
                raise TransactionRefused('Transaction decision no longer applicable')
            if action == 'approve':
                self._grant(row['actor'], row['grant_id'], row['account'])
                db.execute("UPDATE transactions SET status='approved',approved=?,qualified_at_approval=?,qualified_set_digest=? WHERE id=?",
                           (now, int(self._available(row)), self._driver_digest(row), transaction_id))
            else:
                db.execute('UPDATE transactions SET status=? WHERE id=?', ('denied' if action == 'deny' else 'revoked', transaction_id))
            db.execute('DELETE FROM challenges WHERE transaction_id=?', (transaction_id,))
            db.execute('INSERT INTO events(transaction_id,action,at) VALUES(?,?,?)', (transaction_id, action, now))
            return self._public(self._row(db, transaction_id))

    def claim(self, trusted_actor: str, transaction_id: str, current_snapshot: dict, driver_id: str) -> dict:
        actor, current = actor_id(trusted_actor), validate_snapshot(current_snapshot)
        with self._connect() as db:
            row, now = self._row(db, transaction_id), self._now()
            if row['actor'] != actor or canonical(current) != row['snapshot']:
                raise TransactionRefused('Transaction context changed; fresh approval required')
            if (not isinstance(driver_id, str) or (driver_id, row['account'], row['kind']) not in self.qualified_drivers
                    or not row['qualified_at_approval'] or row['qualified_set_digest'] != self._driver_digest(row)):
                raise TransactionRefused('Qualified transaction finalizer unavailable')
            if row['status'] != 'approved':
                raise TransactionRefused('Transaction approval unavailable or consumed')
            if db.execute("SELECT 1 FROM transactions WHERE account=? AND id!=? AND status IN ('executing','unknown') LIMIT 1",
                          (row['account'], transaction_id)).fetchone() is not None:
                raise TransactionRefused('Account has an unresolved transaction; operator reconciliation required')
            grant = self._grant(actor, row['grant_id'], row['account'])
            execution_id = str(uuid4())
            db.execute("UPDATE transactions SET status='executing',execution_id=?,claimed=?,driver_id=? WHERE id=?",
                       (execution_id, now, driver_id, transaction_id))
            db.execute('DELETE FROM challenges WHERE transaction_id=?', (transaction_id,))
            db.execute('INSERT INTO events(transaction_id,action,at) VALUES(?,?,?)', (transaction_id, 'claimed', now))
            result = self._public(self._row(db, transaction_id))
            result['claim_deadline'] = min(row['expires'], grant['expires_at'], now + 15)
            if result['status'] != 'executing' or self._now() >= result['claim_deadline']:
                raise TransactionRefused('Transaction execution window expired; operator reconciliation required')
            result['claim_authorized'] = True
            return result

    def execution_outcome(self, transaction_id: str, execution_id: str, outcome: str,
                          receipt_reference: str | None = None) -> dict:
        execution_id = reference(execution_id)
        if (not isinstance(outcome, str) or outcome not in {'succeeded', 'failed', 'unknown'} or receipt_reference is not None
                and (not isinstance(receipt_reference, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', receipt_reference))
                or outcome != 'succeeded' and receipt_reference is not None):
            raise TransactionRefused('Transaction outcome refused')
        with self._connect() as db:
            row, now = self._row(db, transaction_id), self._now()
            if row['execution_id'] != execution_id:
                raise TransactionRefused('Transaction execution reference refused')
            if row['outcome_at'] is not None:
                if row['status'] == outcome and row['receipt'] == receipt_reference:
                    return self._public(row)
                raise TransactionRefused('Terminal transaction outcome requires operator reconciliation')
            if row['status'] not in {'executing', 'unknown'}:
                raise TransactionRefused('Transaction execution reference refused')
            db.execute('UPDATE transactions SET status=?,outcome_at=?,receipt=? WHERE id=?',
                       (outcome, now, receipt_reference, transaction_id))
            db.execute('INSERT INTO events(transaction_id,action,at) VALUES(?,?,?)', (transaction_id, outcome, now))
            return self._public(self._row(db, transaction_id))
