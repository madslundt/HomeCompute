"""Private, scoped source event journal and current projections for the assistant.

The source systems remain canonical. This module accepts already-authorized,
bounded snapshots and provides a small lexical retrieval surface over the latest
non-deleted projection in one trusted scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata
from typing import Any
import uuid


SOURCES = frozenset({'home-assistant', 'daythread', 'aula', 'calendar'})
_ENVELOPE_KEYS = frozenset({
    'schema_version', 'source', 'object_id', 'revision', 'event_type',
    'operation', 'occurred_at', 'source_ref', 'payload',
})
_MAX_EVENT_BYTES = 96 * 1024
_MAX_PAYLOAD_BYTES = 8192
_MAX_PAYLOAD_DEPTH = 8
_MAX_PAYLOAD_NODES = 3000
_MAX_PAYLOAD_STRING_BYTES = 16 * 1024
# Cheap conservative guard, not a semantic classifier. The owner review gate
# remains mandatory for every inferred candidate.
_SENSITIVE_MEMORY_TERMS = re.compile(
    r'\b(?:health|diagnos\w*|illness|disease|medicat\w*|doctor|therap\w*|allerg\w*|'
    r'anxiet\w*|depress\w*|pregnan\w*|salary|income|debt|bank(?:ing|\s+account)?|credit|'
    r'mortgage|passport|social security|ssn|cpr|identity number|sexual|intimat\w*|employer|'
    r'employment|fired|sygdom\w*|diagnos\w*|medicin\w*|læge|allerg\w*|angst|gravid\w*|'
    r'løn|indkomst|gæld|bankkonto|pas|personnummer|sexliv|arbejdsgiver|ansættelse)\b', re.I
)


@dataclass(frozen=True)
class Scope:
    """Trusted scope supplied by the caller, never inferred from an event."""

    principal_scope: str
    data_domain: str
    visibility: str = 'private'

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.principal_scope, self.data_domain, self.visibility)):
            raise ValueError('Context scope fields must be strings')
        if (self.principal_scope, self.data_domain, self.visibility) not in {
            ('owner', 'personal', 'private'),
            ('family', 'household', 'shared'),
        }:
            raise ValueError('Context scope is not enabled')


def _scope_key(scope: Scope) -> tuple[str, str, str]:
    if not isinstance(scope, Scope):
        raise ValueError('Trusted ContextStore Scope required')
    # Revalidate in case an object was forged without invoking dataclass init.
    Scope(scope.principal_scope, scope.data_domain, scope.visibility)
    return scope.principal_scope, scope.data_domain, scope.visibility


def _bounded_text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f'Invalid bounded {field}')
    try:
        encoded = value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'Invalid bounded {field}') from error
    if len(encoded) > maximum or any(ord(char) < 0x20 and char not in '\t\n\r' for char in value):
        raise ValueError(f'Invalid bounded {field}')
    return value


def _validate_payload(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > _MAX_PAYLOAD_NODES or depth > _MAX_PAYLOAD_DEPTH:
        raise ValueError('Payload complexity budget exceeded')
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('Non-finite payload number refused')
        return
    if isinstance(value, str):
        _bounded_text(value, 'payload string', _MAX_PAYLOAD_STRING_BYTES, allow_empty=True)
        return
    if isinstance(value, list):
        for item in value:
            _validate_payload(item, depth + 1, budget)
        return
    if isinstance(value, dict):
        if len(value) > 500:
            raise ValueError('Payload object budget exceeded')
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('Payload keys must be strings')
            _bounded_text(key, 'payload key', 128)
            _validate_payload(item, depth + 1, budget)
        return
    raise ValueError('Payload must contain only JSON values')


def _canonical_event(event: Any) -> tuple[dict[str, Any], str, str]:
    if not isinstance(event, dict) or set(event) != _ENVELOPE_KEYS:
        raise ValueError('Exact context event envelope required')
    if type(event['schema_version']) is not int or event['schema_version'] != 1:
        raise ValueError('Unsupported context event schema')
    if not isinstance(event['source'], str) or event['source'] not in SOURCES:
        raise ValueError('Unsupported context source')
    source = _bounded_text(event['source'], 'source', 32)
    object_id = _bounded_text(event['object_id'], 'object_id', 256)
    if type(event['revision']) is not int or event['revision'] <= 0 or event['revision'] > 2**63 - 1:
        raise ValueError('Positive bounded source revision required')
    revision = event['revision']
    event_type = _bounded_text(event['event_type'], 'event_type', 128)
    if not isinstance(event['operation'], str) or event['operation'] not in {'upsert', 'delete'}:
        raise ValueError('Unsupported source operation')
    operation = event['operation']
    occurred_at = _bounded_text(event['occurred_at'], 'occurred_at', 64)
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)', occurred_at):
        raise ValueError('Timezone-aware RFC3339 occurred_at required')
    try:
        datetime.fromisoformat(occurred_at[:-1] + '+00:00' if occurred_at.endswith('Z') else occurred_at)
    except ValueError as error:
        raise ValueError('Invalid RFC3339 occurred_at') from error
    source_ref = _bounded_text(event['source_ref'], 'source_ref', 1024)
    if not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:[^\s]*$', source_ref):
        raise ValueError('Source reference URI required')
    payload = event['payload']
    if not isinstance(payload, dict):
        raise ValueError('Object payload required')
    _validate_payload(payload)
    if operation == 'delete' and payload:
        raise ValueError('Delete events must not carry content')
    canonical = {
        'schema_version': 1, 'source': source, 'object_id': object_id,
        'revision': revision, 'event_type': event_type, 'operation': operation,
        'occurred_at': occurred_at, 'source_ref': source_ref, 'payload': payload,
    }
    payload_encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(payload_encoded.encode('utf-8')) > _MAX_PAYLOAD_BYTES:
        raise ValueError('Context payload exceeds attention budget')
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > _MAX_EVENT_BYTES:
        raise ValueError('Context event exceeds byte budget')
    return canonical, encoded, hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class ContextStore:
    """Versioned private SQLite journal with scoped current-state projections."""

    def __init__(self, path: str | os.PathLike[str]):
        supplied = Path(path)
        if supplied.name in {'', '.', '..'} or supplied.is_symlink():
            raise ValueError('Context store symlinks refused')
        supplied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Resolve trusted parent aliases (for example macOS /var -> /private/var)
        # while still refusing a symlink at the database leaf.
        self.path = supplied.parent.resolve() / supplied.name
        parent = self.path.parent.stat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError('Private context store directory required')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('Private context database file required')
        finally:
            os.close(fd)
        with closing(self._connect()) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise ValueError('Context database schema is newer than this code')
            if version == 0:
                db.executescript('''
                    BEGIN IMMEDIATE;
                    CREATE TABLE context_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        source TEXT NOT NULL,
                        object_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        ingested_at TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        payload_json TEXT,
                        fingerprint TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('accepted','stale')),
                        UNIQUE(principal_scope, data_domain, visibility, source, object_id, revision)
                    );
                    CREATE TABLE context_current (
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        source TEXT NOT NULL,
                        object_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
                        occurred_at TEXT NOT NULL,
                        ingested_at TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        payload_json TEXT,
                        PRIMARY KEY(principal_scope, data_domain, visibility, source, object_id)
                    );
                    CREATE INDEX context_current_scope_order ON context_current
                        (principal_scope, data_domain, visibility, occurred_at DESC, source, object_id);
                    CREATE TABLE context_pending (
                        event_id INTEGER PRIMARY KEY,
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        source TEXT NOT NULL,
                        object_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        FOREIGN KEY(event_id) REFERENCES context_events(event_id) ON DELETE CASCADE
                    );
                    CREATE INDEX context_pending_scope ON context_pending
                        (principal_scope, data_domain, visibility, event_id);
                    PRAGMA user_version=1;
                    COMMIT;
                ''')
                version = 1
            if version == 1:
                db.executescript('''
                    BEGIN IMMEDIATE;
                    CREATE TABLE assistant_memories (
                        memory_id TEXT PRIMARY KEY,
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        statement TEXT,
                        source_refs_json TEXT NOT NULL,
                        provenance TEXT NOT NULL CHECK(provenance IN ('explicit','inferred','imported')),
                        sensitivity TEXT NOT NULL CHECK(sensitivity IN ('ordinary','sensitive','restricted')),
                        confidence REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        last_confirmed_at TEXT,
                        expires_at TEXT,
                        supersedes TEXT,
                        status TEXT NOT NULL CHECK(status IN ('candidate','active','corrected','expired','deleted'))
                    );
                    CREATE INDEX assistant_memories_scope_order ON assistant_memories
                        (principal_scope,data_domain,visibility,created_at DESC,memory_id);
                    CREATE TABLE assistant_memory_requests (
                        request_id TEXT PRIMARY KEY,
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        memory_id TEXT NOT NULL
                    );
                    CREATE TABLE assistant_memory_tombstones (
                        principal_scope TEXT NOT NULL,
                        data_domain TEXT NOT NULL,
                        visibility TEXT NOT NULL,
                        source_ref_hash TEXT NOT NULL,
                        deleted_at TEXT NOT NULL,
                        PRIMARY KEY(principal_scope,data_domain,visibility,source_ref_hash)
                    );
                    PRAGMA user_version=2;
                    COMMIT;
                ''')

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _where(scope: Scope) -> tuple[str, str, str]:
        return _scope_key(scope)

    def ingest(self, scope: Scope, event: dict[str, Any]) -> dict[str, Any]:
        principal, domain, visibility = self._where(scope)
        canonical, encoded, fingerprint = _canonical_event(event)
        source, object_id, revision = canonical['source'], canonical['object_id'], canonical['revision']
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                existing = db.execute(
                    'SELECT event_id,fingerprint,status FROM context_events WHERE principal_scope=? AND data_domain=? '
                    'AND visibility=? AND source=? AND object_id=? AND revision=?',
                    (principal, domain, visibility, source, object_id, revision),
                ).fetchone()
                if existing:
                    if existing[1] != fingerprint:
                        raise ValueError('Conflicting content for an existing source revision')
                    db.execute('COMMIT')
                    return {'event_id': existing[0], 'source': source, 'object_id': object_id,
                            'revision': revision, 'status': 'duplicate'}
                current = db.execute(
                    'SELECT revision,operation FROM context_current WHERE principal_scope=? AND data_domain=? AND visibility=? '
                    'AND source=? AND object_id=?', (principal, domain, visibility, source, object_id),
                ).fetchone()
                stale = current is not None and revision < current[0]
                status = 'stale' if stale else 'accepted'
                ingested_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                # Never let a late event repopulate content after a tombstone.
                content_allowed = not (stale and current is not None and current[1] == 'delete')
                payload_json = (json.dumps(canonical['payload'], ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':'), allow_nan=False)
                                if canonical['operation'] == 'upsert' and content_allowed else None)
                cursor = db.execute(
                    'INSERT INTO context_events(principal_scope,data_domain,visibility,source,object_id,revision,event_type,'
                    'operation,occurred_at,ingested_at,source_ref,payload_json,fingerprint,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (principal, domain, visibility, source, object_id, revision, canonical['event_type'],
                     canonical['operation'], canonical['occurred_at'], ingested_at, canonical['source_ref'],
                     payload_json, fingerprint, status),
                )
                event_id = cursor.lastrowid
                if not stale:
                    if canonical['operation'] == 'delete':
                        # Preserve replay metadata and fingerprints, but scrub every online copy of content.
                        prior_refs = db.execute('SELECT DISTINCT source_ref FROM context_events WHERE principal_scope=? '
                            'AND data_domain=? AND visibility=? AND source=? AND object_id=?',
                            (principal, domain, visibility, source, object_id)).fetchall()
                        db.execute('UPDATE context_events SET payload_json=NULL WHERE principal_scope=? AND data_domain=? '
                                   'AND visibility=? AND source=? AND object_id=?',
                                   (principal, domain, visibility, source, object_id))
                        db.execute('DELETE FROM context_pending WHERE principal_scope=? AND data_domain=? AND visibility=? '
                                   'AND source=? AND object_id=?',
                                   (principal, domain, visibility, source, object_id))
                        for (source_ref,) in prior_refs:
                            self._scrub_memory_source(db, (principal, domain, visibility), source_ref, ingested_at)
                    else:
                        db.execute('DELETE FROM context_pending WHERE principal_scope=? AND data_domain=? AND visibility=? '
                                   'AND source=? AND object_id=?',
                                   (principal, domain, visibility, source, object_id))
                    db.execute(
                        'INSERT INTO context_current(principal_scope,data_domain,visibility,source,object_id,revision,'
                        'event_type,operation,occurred_at,ingested_at,source_ref,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) '
                        'ON CONFLICT(principal_scope,data_domain,visibility,source,object_id) DO UPDATE SET '
                        'revision=excluded.revision,event_type=excluded.event_type,operation=excluded.operation,'
                        'occurred_at=excluded.occurred_at,ingested_at=excluded.ingested_at,'
                        'source_ref=excluded.source_ref,payload_json=excluded.payload_json',
                        (principal, domain, visibility, source, object_id, revision, canonical['event_type'],
                         canonical['operation'], canonical['occurred_at'], ingested_at, canonical['source_ref'], payload_json),
                    )
                    if canonical['operation'] == 'upsert':
                        db.execute('INSERT INTO context_pending(event_id,principal_scope,data_domain,visibility,source,'
                                   'object_id,revision) VALUES (?,?,?,?,?,?,?)',
                                   (event_id, principal, domain, visibility, source, object_id, revision))
                db.execute('COMMIT')
                return {'event_id': event_id, 'source': source, 'object_id': object_id,
                        'revision': revision, 'status': status}
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    @staticmethod
    def _projection(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {'source': row[0], 'object_id': row[1], 'revision': row[2], 'event_type': row[3],
                'operation': row[4], 'occurred_at': row[5], 'ingested_at': row[6],
                'source_ref': row[7], 'payload': json.loads(row[8]) if row[8] is not None else None}

    @staticmethod
    def _scrub_memory_source(db: sqlite3.Connection, key: tuple[str, str, str],
                             source_ref: str, deleted_at: str) -> None:
        """Apply a source deletion to all memory versions derived from its stable reference."""
        source_hash = hashlib.sha256(source_ref.encode('utf-8')).hexdigest()
        db.execute('INSERT OR IGNORE INTO assistant_memory_tombstones'
                   '(principal_scope,data_domain,visibility,source_ref_hash,deleted_at) VALUES (?,?,?,?,?)',
                   (*key, source_hash, deleted_at))
        rows = db.execute('SELECT memory_id,revision,source_refs_json FROM assistant_memories '
                          'WHERE principal_scope=? AND data_domain=? AND visibility=?', key).fetchall()
        affected_hashes = {source_hash}
        affected_ids = set()
        for memory_id, revision, refs_json in rows:
            refs = json.loads(refs_json)
            hashes = {hashlib.sha256(ref.encode('utf-8')).hexdigest() for ref in refs}
            if affected_hashes.intersection(hashes):
                affected_ids.add(memory_id)
                affected_hashes.update(hashes)
        # Include every correction/version linked to the same source even when a
        # correction added another reference to the chain.
        changed = True
        while changed:
            changed = False
            for memory_id, revision, refs_json in rows:
                refs = json.loads(refs_json)
                hashes = {hashlib.sha256(ref.encode('utf-8')).hexdigest() for ref in refs}
                if memory_id in affected_ids or affected_hashes.intersection(hashes):
                    if memory_id not in affected_ids:
                        affected_ids.add(memory_id)
                        changed = True
                    before = len(affected_hashes)
                    affected_hashes.update(hashes)
                    changed = changed or len(affected_hashes) != before
        revisions = {memory_id: revision for memory_id, revision, _ in rows}
        for memory_id in affected_ids:
            db.execute('UPDATE assistant_memories SET revision=?,statement=NULL,source_refs_json=?,status=? '
                       'WHERE memory_id=? AND principal_scope=? AND data_domain=? AND visibility=?',
                       (revisions[memory_id] + 1, '[]', 'deleted', memory_id, *key))

    def get(self, scope: Scope, source: str, object_id: str) -> dict[str, Any] | None:
        key = self._where(scope)
        if not isinstance(source, str) or source not in SOURCES:
            raise ValueError('Unsupported context source')
        object_id = _bounded_text(object_id, 'object_id', 256)
        with closing(self._connect()) as db:
            row = db.execute('SELECT source,object_id,revision,event_type,operation,occurred_at,ingested_at,source_ref,payload_json '
                             'FROM context_current WHERE principal_scope=? AND data_domain=? AND visibility=? '
                             'AND source=? AND object_id=? AND operation=?', (*key, source, object_id, 'upsert')).fetchone()
        return None if row is None else self._projection(row)

    @staticmethod
    def _sources(sources: Any) -> tuple[str, ...]:
        if sources is None:
            return tuple(sorted(SOURCES))
        if not isinstance(sources, (list, tuple, set, frozenset)) or not sources:
            raise ValueError('Finite source grant required')
        if any(not isinstance(source, str) or source not in SOURCES for source in sources):
            raise ValueError('Unsupported source grant')
        return tuple(sorted(set(sources)))

    def list_current(self, scope: Scope, limit: int = 50, *, sources: Any = None) -> list[dict[str, Any]]:
        key = self._where(scope)
        selected = self._sources(sources)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Context list limit must be between 1 and 100')
        with closing(self._connect()) as db:
            rows = db.execute('SELECT source,object_id,revision,event_type,operation,occurred_at,ingested_at,source_ref,payload_json '
                              'FROM context_current WHERE principal_scope=? AND data_domain=? AND visibility=? '
                              'AND operation=? AND source IN (' + ','.join('?' for _ in selected) + ') '
                              'ORDER BY occurred_at DESC,source,object_id LIMIT ?', (*key, 'upsert', *selected, limit)).fetchall()
        return [self._projection(row) for row in rows]

    def pending(self, scope: Scope, limit: int = 50) -> list[dict[str, Any]]:
        """Return pending source projections that are still current in this scope."""
        key = self._where(scope)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Context pending limit must be between 1 and 100')
        with closing(self._connect()) as db:
            rows = db.execute(
                'SELECT p.event_id,c.source,c.object_id,c.revision,c.event_type,c.operation,c.occurred_at,'
                'c.ingested_at,c.source_ref,c.payload_json FROM context_pending p JOIN context_current c '
                'ON c.principal_scope=p.principal_scope AND c.data_domain=p.data_domain AND c.visibility=p.visibility '
                'AND c.source=p.source AND c.object_id=p.object_id AND c.revision=p.revision '
                'WHERE p.principal_scope=? AND p.data_domain=? AND p.visibility=? AND c.operation=? '
                'ORDER BY p.event_id LIMIT ?', (*key, 'upsert', limit),
            ).fetchall()
        result = []
        for row in rows:
            projection = self._projection(row[1:])
            projection['event_id'] = row[0]
            result.append(projection)
        return result

    def ack(self, scope: Scope, event_id: int) -> bool:
        """Remove a pending reference after its downstream durable write succeeds."""
        key = self._where(scope)
        if type(event_id) is not int or event_id <= 0:
            raise ValueError('Positive event ID required')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                cursor = db.execute('DELETE FROM context_pending WHERE principal_scope=? AND data_domain=? '
                                    'AND visibility=? AND event_id=?', (*key, event_id))
                db.execute('COMMIT')
                return cursor.rowcount == 1
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    @staticmethod
    def _memory_scope(scope: Scope) -> tuple[str, str, str]:
        key = _scope_key(scope)
        if key != ('owner', 'personal', 'private'):
            raise ValueError('Personal memory controls are enabled only for owner/personal/private')
        return key

    @staticmethod
    def _request_id(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError('UUIDv4 request ID required')
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as error:
            raise ValueError('UUIDv4 request ID required') from error
        if parsed.version != 4 or str(parsed) != value.lower():
            raise ValueError('Canonical UUIDv4 request ID required')
        return str(parsed)

    @staticmethod
    def _memory_id(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError('Memory UUID required')
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as error:
            raise ValueError('Memory UUID required') from error
        if parsed.version != 4 or str(parsed) != value.lower():
            raise ValueError('Canonical memory UUIDv4 required')
        return str(parsed)

    @staticmethod
    def _memory_row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {
            'memory_id': row[0], 'schema_version': 1, 'principal_scope': row[1],
            'data_domain': row[2], 'visibility': row[3], 'revision': row[4],
            'kind': row[5], 'statement': row[6], 'source_refs': json.loads(row[7]),
            'provenance': row[8], 'sensitivity': row[9], 'confidence': row[10],
            'created_at': row[11], 'last_confirmed_at': row[12], 'expires_at': row[13],
            'supersedes': row[14], 'status': row[15],
        }

    @staticmethod
    def _memory_select(db: sqlite3.Connection, key: tuple[str, str, str], memory_id: str) -> tuple[Any, ...] | None:
        return db.execute(
            'SELECT memory_id,principal_scope,data_domain,visibility,revision,kind,statement,source_refs_json,'
            'provenance,sensitivity,confidence,created_at,last_confirmed_at,expires_at,supersedes,status '
            'FROM assistant_memories WHERE principal_scope=? AND data_domain=? AND visibility=? AND memory_id=?',
            (*key, memory_id),
        ).fetchone()

    @staticmethod
    def _memory_request(db: sqlite3.Connection, key: tuple[str, str, str], request_id: str,
                        operation: str, fingerprint: str, memory_id: str) -> dict[str, Any] | None:
        previous = db.execute('SELECT principal_scope,data_domain,visibility,operation,fingerprint,memory_id '
                              'FROM assistant_memory_requests WHERE request_id=?', (request_id,)).fetchone()
        if previous is None:
            db.execute('INSERT INTO assistant_memory_requests(request_id,principal_scope,data_domain,visibility,'
                       'operation,fingerprint,memory_id) VALUES (?,?,?,?,?,?,?)',
                       (request_id, *key, operation, fingerprint, memory_id))
            return None
        if previous[:5] != (*key, operation, fingerprint):
            raise ValueError('Memory request ID was reused with different content or scope')
        row = ContextStore._memory_select(db, key, previous[5])
        if row is None:
            raise ValueError('Memory request receipt is inconsistent')
        return ContextStore._memory_row(row)

    @staticmethod
    def _memory_fingerprint(operation: str, data: dict[str, Any]) -> str:
        encoded = json.dumps({'operation': operation, **data}, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    @staticmethod
    def _new_memory_id() -> str:
        return str(uuid.uuid4())

    def propose_memory(self, scope: Scope, statement: str, kind: str, source_ref: str,
                       request_id: str) -> dict[str, Any]:
        """Persist a bounded inferred proposal as a review candidate only."""
        key = self._memory_scope(scope)
        statement = _bounded_text(statement, 'memory statement', 2000)
        if _SENSITIVE_MEMORY_TERMS.search(statement):
            raise ValueError('Sensitive or restricted inferences are not retained as proposals')
        kinds = {'preference', 'goal', 'relationship', 'constraint', 'working_context'}
        if not isinstance(kind, str) or kind not in kinds:
            raise ValueError('Unsupported personal memory kind')
        source_ref = _bounded_text(source_ref, 'memory source_ref', 1024)
        if not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:[^\s]*$', source_ref):
            raise ValueError('Memory source reference URI required')
        request_id = self._request_id(request_id)
        fingerprint = self._memory_fingerprint('propose', {'statement': statement, 'kind': kind,
                                                            'source_ref': source_ref})
        memory_id = self._new_memory_id()
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                replay = self._memory_request(db, key, request_id, 'propose', fingerprint, memory_id)
                if replay is not None:
                    db.execute('COMMIT')
                    return replay
                source_hash = hashlib.sha256(source_ref.encode('utf-8')).hexdigest()
                if db.execute('SELECT 1 FROM assistant_memory_tombstones WHERE principal_scope=? AND data_domain=? '
                              'AND visibility=? AND source_ref_hash=?', (*key, source_hash)).fetchone():
                    raise ValueError('Deleted memory source is tombstoned; a newer source_ref is required')
                db.execute('INSERT INTO assistant_memories(memory_id,principal_scope,data_domain,visibility,revision,'
                           'kind,statement,source_refs_json,provenance,sensitivity,confidence,created_at,last_confirmed_at,'
                           'expires_at,supersedes,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (memory_id, *key, 1, kind, statement, json.dumps([source_ref]), 'inferred', 'ordinary',
                            0.5, now, None, None, None, 'candidate'))
                row = self._memory_select(db, key, memory_id)
                db.execute('COMMIT')
                return self._memory_row(row)
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def list_memories(self, scope: Scope, limit: int = 20) -> list[dict[str, Any]]:
        key = self._memory_scope(scope)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Memory list limit must be between 1 and 100')
        with closing(self._connect()) as db:
            rows = db.execute(
                'SELECT memory_id,principal_scope,data_domain,visibility,revision,kind,statement,source_refs_json,'
                'provenance,sensitivity,confidence,created_at,last_confirmed_at,expires_at,supersedes,status '
                'FROM assistant_memories WHERE principal_scope=? AND data_domain=? AND visibility=? '
                'ORDER BY created_at DESC,memory_id LIMIT ?', (*key, limit),
            ).fetchall()
        return [self._memory_row(row) for row in rows]

    def inspect_memory(self, scope: Scope, memory_id: str) -> dict[str, Any] | None:
        key = self._memory_scope(scope)
        memory_id = self._memory_id(memory_id)
        with closing(self._connect()) as db:
            row = self._memory_select(db, key, memory_id)
        return None if row is None else self._memory_row(row)

    def export_memories(self, scope: Scope) -> dict[str, Any]:
        key = self._memory_scope(scope)
        with closing(self._connect()) as db:
            rows = db.execute(
                'SELECT memory_id,principal_scope,data_domain,visibility,revision,kind,statement,source_refs_json,'
                'provenance,sensitivity,confidence,created_at,last_confirmed_at,expires_at,supersedes,status '
                'FROM assistant_memories WHERE principal_scope=? AND data_domain=? AND visibility=? '
                'ORDER BY created_at,memory_id LIMIT 10001', key,
            ).fetchall()
        if len(rows) > 10000:
            raise ValueError('Memory export exceeds bounded record limit')
        return {'schema_version': 1, 'scope': {'principal_scope': key[0], 'data_domain': key[1],
                                               'visibility': key[2]},
                'records': [self._memory_row(row) for row in rows]}

    def export_memories_page(self, scope: Scope, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Bounded HTTP export; the local trusted export operation can still write a full archive."""
        key = self._memory_scope(scope)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('Memory export page must be between 1 and 20')
        after = self._memory_id(cursor) if cursor is not None else ''
        with closing(self._connect()) as db:
            ids = db.execute('SELECT memory_id FROM assistant_memories WHERE principal_scope=? AND data_domain=? '
                'AND visibility=? AND memory_id>? ORDER BY memory_id LIMIT ?', (*key, after, limit + 1)).fetchall()
            records = [self._memory_row(self._memory_select(db, key, row[0])) for row in ids[:limit]]
        return {'schema_version': 1, 'records': records,
                'next_cursor': ids[limit - 1][0] if len(ids) > limit else None,
                'snapshot_consistent': False}

    def update_memory(self, scope: Scope, memory_id: str, operation: str,
                      expected_revision: int, request_id: str) -> dict[str, Any]:
        """Expire or delete a memory using optimistic revision control."""
        key = self._memory_scope(scope)
        if not isinstance(operation, str) or operation not in {'delete', 'expire'}:
            raise ValueError('Memory update operation must be delete or expire')
        memory_id = self._memory_id(memory_id)
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError('Positive expected memory revision required')
        request_id = self._request_id(request_id)
        fingerprint = self._memory_fingerprint(operation, {'memory_id': memory_id,
                                                            'expected_revision': expected_revision})
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                replay = self._memory_request(db, key, request_id, operation, fingerprint, memory_id)
                if replay is not None:
                    db.execute('COMMIT')
                    return replay
                row = self._memory_select(db, key, memory_id)
                if row is None:
                    raise ValueError('Memory record not found in this scope')
                current = self._memory_row(row)
                if current['revision'] != expected_revision:
                    raise ValueError('Memory revision changed; inspect the current record first')
                if current['status'] not in {'candidate', 'active'}:
                    raise ValueError('Only candidate or active memory can be changed')
                now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                if operation == 'delete':
                    ref_hashes = {hashlib.sha256(ref.encode('utf-8')).hexdigest() for ref in current['source_refs']}
                    for ref_hash in ref_hashes:
                        db.execute('INSERT OR IGNORE INTO assistant_memory_tombstones'
                                   '(principal_scope,data_domain,visibility,source_ref_hash,deleted_at) VALUES (?,?,?,?,?)',
                                   (*key, ref_hash, now))
                    # Scrub this record's entire source-linked correction/history chain.
                    with_refs = db.execute('SELECT memory_id,revision,source_refs_json FROM assistant_memories '
                                           'WHERE principal_scope=? AND data_domain=? AND visibility=?', key).fetchall()
                    for related_id, related_revision, refs_json in with_refs:
                        refs = json.loads(refs_json)
                        related_hashes = {hashlib.sha256(ref.encode('utf-8')).hexdigest() for ref in refs}
                        if related_id == memory_id or ref_hashes.intersection(related_hashes):
                            db.execute('UPDATE assistant_memories SET revision=?,statement=NULL,source_refs_json=?, '
                                       'status=? WHERE memory_id=? AND principal_scope=? AND data_domain=? AND visibility=?',
                                       (related_revision + 1, '[]', 'deleted', related_id, *key))
                else:
                    db.execute('UPDATE assistant_memories SET revision=revision+1,expires_at=?,status=? WHERE memory_id=? '
                               'AND principal_scope=? AND data_domain=? AND visibility=?',
                               (now, 'expired', memory_id, *key))
                self._memory_request(db, key, request_id, operation, fingerprint, memory_id)
                updated = self._memory_row(self._memory_select(db, key, memory_id))
                db.execute('COMMIT')
                return updated
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def confirm_memory(self, scope: Scope, memory_id: str, expected_revision: int,
                       request_id: str) -> dict[str, Any]:
        """Trusted application operation; keep this off model-callable tools."""
        key = self._memory_scope(scope)
        memory_id = self._memory_id(memory_id)
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError('Positive expected memory revision required')
        request_id = self._request_id(request_id)
        fingerprint = self._memory_fingerprint('confirm', {'memory_id': memory_id,
                                                            'expected_revision': expected_revision})
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                replay = self._memory_request(db, key, request_id, 'confirm', fingerprint, memory_id)
                if replay is not None:
                    db.execute('COMMIT')
                    return replay
                current_row = self._memory_select(db, key, memory_id)
                if current_row is None:
                    raise ValueError('Memory record not found in this scope')
                current = self._memory_row(current_row)
                if current['revision'] != expected_revision or current['status'] != 'candidate':
                    raise ValueError('Only the current candidate revision can be confirmed')
                now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                db.execute('UPDATE assistant_memories SET revision=revision+1,last_confirmed_at=?,status=? '
                           'WHERE memory_id=? AND principal_scope=? AND data_domain=? AND visibility=?',
                           (now, 'active', memory_id, *key))
                self._memory_request(db, key, request_id, 'confirm', fingerprint, memory_id)
                updated = self._memory_row(self._memory_select(db, key, memory_id))
                db.execute('COMMIT')
                return updated
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def correct_memory(self, scope: Scope, memory_id: str, statement: str,
                       expected_revision: int, request_id: str) -> dict[str, Any]:
        """Trusted application operation creating an explicit superseding version."""
        key = self._memory_scope(scope)
        statement = _bounded_text(statement, 'memory statement', 2000)
        memory_id = self._memory_id(memory_id)
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ValueError('Positive expected memory revision required')
        request_id = self._request_id(request_id)
        fingerprint = self._memory_fingerprint('correct', {'memory_id': memory_id, 'statement': statement,
                                                            'expected_revision': expected_revision})
        new_id = self._new_memory_id()
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                replay = self._memory_request(db, key, request_id, 'correct', fingerprint, new_id)
                if replay is not None:
                    db.execute('COMMIT')
                    return replay
                current_row = self._memory_select(db, key, memory_id)
                if current_row is None:
                    raise ValueError('Memory record not found in this scope')
                current = self._memory_row(current_row)
                if current['revision'] != expected_revision or current['status'] not in {'candidate', 'active'}:
                    raise ValueError('Memory revision changed or is no longer correctable')
                now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                refs_json = json.dumps(current['source_refs'], ensure_ascii=False, separators=(',', ':'))
                db.execute('UPDATE assistant_memories SET revision=revision+1,status=? WHERE memory_id=? '
                           'AND principal_scope=? AND data_domain=? AND visibility=?',
                           ('corrected', memory_id, *key))
                new_status = 'active'
                confirmed_at = now
                db.execute('INSERT INTO assistant_memories(memory_id,principal_scope,data_domain,visibility,revision,'
                           'kind,statement,source_refs_json,provenance,sensitivity,confidence,created_at,last_confirmed_at,'
                           'expires_at,supersedes,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (new_id, *key, 1, current['kind'], statement, refs_json, 'explicit', 'ordinary', 1.0,
                            now, confirmed_at, None, memory_id, new_status))
                self._memory_request(db, key, request_id, 'correct', fingerprint, new_id)
                corrected = self._memory_row(self._memory_select(db, key, new_id))
                db.execute('COMMIT')
                return corrected
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def search(self, scope: Scope, query: str, limit: int = 10, *, sources: Any = None) -> list[dict[str, Any]]:
        key = self._where(scope)
        selected = self._sources(sources)
        query = _bounded_text(query, 'search query', 512)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Context search limit must be between 1 and 50')
        query_tokens = set(re.findall(r'\w+', unicodedata.normalize('NFKC', query).casefold()))
        if not query_tokens:
            raise ValueError('Search query must contain searchable text')
        with closing(self._connect()) as db:
            rows = db.execute('SELECT source,object_id,revision,event_type,operation,occurred_at,ingested_at,source_ref,payload_json '
                              'FROM context_current WHERE principal_scope=? AND data_domain=? AND visibility=? '
                              'AND operation=? AND source IN (' + ','.join('?' for _ in selected) + ') '
                              'ORDER BY occurred_at DESC LIMIT 1000', (*key, 'upsert', *selected)).fetchall()
        results = []
        for row in rows:
            projection = self._projection(row)
            body = json.dumps(projection['payload'], ensure_ascii=False, sort_keys=True)
            normalized = unicodedata.normalize('NFKC', body).casefold()
            body_tokens = set(re.findall(r'\w+', normalized))
            overlap = len(query_tokens & body_tokens)
            exact = query.casefold() in normalized
            score = max(overlap / len(query_tokens), 1.0 if exact else 0.0)
            if score:
                results.append({**projection, 'search_score': score})
        return sorted(results, key=lambda item: (-item['search_score'], -item['revision'], item['source'], item['object_id']))[:limit]
