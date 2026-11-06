"""Content-free deletion-ledger export and guarded assistant-context restore.

This module only prepares isolated restore targets. Applying a verified restore
to a service root remains an owning lifecycle operation outside this helper.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from assistant_context_store import ContextStore, Scope

SCOPES = {
    'owner/personal/private': Scope('owner', 'personal', 'private'),
    'family/household/shared': Scope('family', 'household', 'shared'),
}
FENCE_NAME = 'restore-fence.json'
FENCE_SCHEMA = 1
MAX_LEDGER_BYTES = 8 * 1024 * 1024


class RestoreFenceError(ValueError):
    """Restored state must not be served yet."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _private_dir(path: Path, *, create: bool = False) -> None:
    if path.is_symlink():
        raise RestoreFenceError('restore path symlink refused')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RestoreFenceError('private restore directory required')


def _private_file(path: Path, *, maximum: int = MAX_LEDGER_BYTES) -> bytes:
    if path.is_symlink():
        raise RestoreFenceError('restore metadata symlink refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > maximum):
            raise RestoreFenceError('private bounded restore metadata required')
        raw = os.read(fd, maximum + 1)
    finally:
        os.close(fd)
    if len(raw) > maximum:
        raise RestoreFenceError('restore metadata exceeds bound')
    return raw


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    _private_dir(path.parent)
    raw = _canonical(value)
    with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fence_path(state: Path) -> Path:
    return state / FENCE_NAME


def assert_restore_ready(state: str | os.PathLike[str]) -> None:
    """Refuse service startup while a restore is pending or its fence is unsafe.

    Legacy state without a fence remains compatible. A present fence must be a
    private, exact-schema `ready` record produced after trusted tombstone replay.
    """
    root = Path(state)
    if root.is_symlink():
        raise RestoreFenceError('context state symlink refused')
    fence = _fence_path(root)
    if not fence.exists() and not fence.is_symlink():
        return
    try:
        value = json.loads(_private_file(fence, maximum=16384))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreFenceError('restore fence is missing or malformed') from error
    if (not isinstance(value, dict) or value.get('schema_version') != FENCE_SCHEMA
            or value.get('phase') != 'ready'
            or not re.fullmatch(r'[0-9a-f]{64}', value.get('ledger_sha256', ''))
            or not re.fullmatch(r'[0-9a-f]{64}', value.get('admission_sha256', ''))
            or not isinstance(value.get('scopes'), list) or not value['scopes']
            or any(scope not in SCOPES for scope in value['scopes'])
            or value['scopes'] != sorted(set(value['scopes']))):
        raise RestoreFenceError('restored context is fenced pending trusted deletion replay')


def export_deletion_ledger(store: ContextStore, scopes: Iterable[Scope]) -> dict[str, Any]:
    """Export current content-free tombstones; this output is not freshness proof."""
    if not isinstance(store, ContextStore):
        raise ValueError('ContextStore required')
    rows = {}
    for scope in scopes:
        if not isinstance(scope, Scope):
            raise ValueError('explicit trusted scope required')
        key = '/'.join((scope.principal_scope, scope.data_domain, scope.visibility))
        if key not in SCOPES or key in rows:
            raise ValueError('explicit unique restore scopes required')
        rows[key] = store.export_tombstones(scope)
    if not rows:
        raise ValueError('at least one explicit restore scope required')
    return {'schema_version': 1, 'freshness': 'unverified', 'scopes': rows}


def write_deletion_ledger(store: ContextStore, scopes: Iterable[Scope], destination: str | os.PathLike[str]) -> str:
    """Write an unverified ledger and return its SHA-256 for separate admission."""
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise ValueError('new deletion-ledger destination required')
    _private_dir(target.parent)
    value = export_deletion_ledger(store, scopes)
    raw = _canonical(value)
    if len(raw) > MAX_LEDGER_BYTES:
        raise ValueError('deletion ledger exceeds bound')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def stage_restore(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify an archive into a fresh private scratch target and leave it fenced."""
    archive = Path(source)
    destination = Path(target)
    if archive.is_symlink() or not archive.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError('existing archive and fresh isolated restore target required')
    recovery = _load_recovery()
    result = recovery.verify(archive, destination)
    assistant = json.loads((destination / 'manifest.json').read_text()).get('assistant_context')
    if not isinstance(assistant, dict) or 'assistant-context/state' not in assistant.get('directories', []):
        raise ValueError('selected archive has no retained assistant context state')
    state = destination / 'state/assistant-context/state'
    for parent in (destination / 'state', destination / 'state/assistant-context', state):
        if parent.is_symlink() or not parent.is_dir():
            raise RestoreFenceError('staged assistant context directory is unsafe')
        parent.chmod(0o700)
    _private_dir(state)
    for name in ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3'):
        if (state / name).is_symlink() or not (state / name).is_file():
            raise ValueError('restored context database set is incomplete')
    _write_atomic(_fence_path(state), {'schema_version': FENCE_SCHEMA, 'phase': 'pending',
        'created_at': datetime.now(timezone.utc).isoformat(), 'reason': 'trusted deletion replay required'})
    return {'phase': 'pending', **result}


def _load_recovery():
    recovery_path = ROOT / 'scripts/application-recovery.py'
    spec = importlib.util.spec_from_file_location('application_recovery_for_context', recovery_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('application recovery verifier is unavailable')
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    return recovery


def _validate_ledger(raw: bytes, expected_sha256: str) -> tuple[dict[str, Any], list[str]]:
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RestoreFenceError('deletion ledger SHA-256 does not match external expected value')
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreFenceError('deletion ledger is malformed') from error
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'freshness', 'scopes'}
            or value.get('schema_version') != 1 or value.get('freshness') != 'unverified'
            or not isinstance(value.get('scopes'), dict) or not value['scopes']
            or any(key not in SCOPES for key in value['scopes'])):
        raise RestoreFenceError('content-free deletion ledger scope is invalid')
    scopes = sorted(value['scopes'])
    for key in scopes:
        row = value['scopes'][key]
        if (not isinstance(row, dict) or set(row) != {'schema_version', 'source_tombstones', 'memory_source_ref_hashes'}
                or row.get('schema_version') != 1 or not isinstance(row.get('source_tombstones'), list)
                or not isinstance(row.get('memory_source_ref_hashes'), list)):
            raise RestoreFenceError('content-free scope tombstones are malformed')
    return value, scopes


def _validate_admission(raw: bytes, *, ledger_sha256: str, scopes: list[str], expected_sha256: str) -> None:
    if (not re.fullmatch(r'[0-9a-f]{64}', expected_sha256)
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise RestoreFenceError('separate source-controller admission SHA-256 mismatch')
    try:
        proof = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreFenceError('source-controller admission is malformed') from error
    # The expected digest is supplied through the trusted restore lifecycle,
    # separately from the archive and ledger. No model-facing API can select it.
    if (not isinstance(proof, dict) or set(proof) != {'schema_version', 'issuer', 'status', 'ledger_sha256', 'scopes', 'reconciled_at'}
            or proof.get('schema_version') != 1 or proof.get('issuer') != 'source-controller'
            or proof.get('status') != 'current' or proof.get('ledger_sha256') != ledger_sha256
            or proof.get('scopes') != scopes or not isinstance(proof.get('reconciled_at'), str)):
        raise RestoreFenceError('fresh external source-controller reconciliation admission required')
    try:
        stamp = datetime.fromisoformat(proof['reconciled_at'].replace('Z', '+00:00'))
    except ValueError as error:
        raise RestoreFenceError('source-controller reconciliation timestamp is invalid') from error
    age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() if stamp.tzinfo else None
    if age is None or age < -300 or age > 86400:
        raise RestoreFenceError('source-controller reconciliation is stale')


def _derived_scrub(state_root: Path, scopes: list[str], tombstone_rows: list[tuple[str, str, str]]) -> None:
    """Apply only local metadata purges; never invoke delivery or native transports."""
    from assistant_attention import AttentionLedger
    from assistant_context_delivery import DeliveryOutbox
    from assistant_context_briefings import OccurrenceLedger
    from assistant_context_delegation import DelegationController, DelegationError

    attention = state_root / 'attention.sqlite3'
    delivery = state_root / 'delivery.sqlite3'
    routine = state_root / 'routines.sqlite3'
    delegation = state_root / 'delegation.sqlite3'
    for scope_name in scopes:
        scope = SCOPES[scope_name]
        if attention.exists():
            ledger = AttentionLedger(attention, dry_run=True)
        else:
            ledger = None
        outbox = DeliveryOutbox(delivery) if delivery.exists() else None
        occurrences = OccurrenceLedger(routine) if routine.exists() else None
        delegate = None
        if delegation.exists():
            config_path = ROOT / 'config/assistant-delegation.json'
            config = json.loads(config_path.read_text())
            class OfflineTransport:
                def call(self, *_args, **_kwargs):
                    raise DelegationError('restore must not contact the native worker')
            delegate = DelegationController(delegation, config, transport=OfflineTransport(), maintenance_mode=True)
        for tombstone_scope, source, object_id in tombstone_rows:
            if tombstone_scope != scope_name:
                continue
            if ledger is not None:
                ledger.purge_source(scope=scope, source=source, object_id=object_id)
            if outbox is not None:
                outbox.purge_source(scope=scope, source=source, object_id=object_id)
            if occurrences is not None:
                occurrences.purge_source(scope, source, object_id)
            if delegate is not None:
                delegate.purge_source(scope=scope, source=source, object_id=object_id)


def _stored_scopes(state_root: Path) -> set[str]:
    """Find every trusted scope represented in the archived service databases."""
    names = ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3',
             'retrieval.sqlite3', 'routines.sqlite3', 'delegation.sqlite3')
    scopes: set[str] = set()
    triples = (('principal_scope', 'data_domain', 'visibility'), ('principal', 'domain', 'visibility'))
    for name in names:
        path = state_root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise RestoreFenceError('archived scope database is unsafe')
        uri = path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                # Table names came from sqlite_master, but still quote them as SQL identifiers.
                quoted = '"' + table.replace('"', '""') + '"'
                columns = {row[1] for row in db.execute(f'PRAGMA table_info({quoted})')}
                key = next((candidate for candidate in triples if set(candidate) <= columns), None)
                if key is not None:
                    fields = ','.join('"' + field + '"' for field in key)
                    for principal, domain, visibility in db.execute(f'SELECT DISTINCT {fields} FROM {quoted}'):
                        scope_name = '/'.join((principal, domain, visibility))
                        if scope_name not in SCOPES:
                            raise RestoreFenceError('archived state contains an unsupported scope')
                        scopes.add(scope_name)
                elif 'scope_key' in columns:
                    for (scope_name,) in db.execute(f'SELECT DISTINCT "scope_key" FROM {quoted}'):
                        if scope_name not in SCOPES:
                            raise RestoreFenceError('archived delegation scope is unsupported')
                        scopes.add(scope_name)
    return scopes


def complete_restore(target: str | os.PathLike[str], ledger_path: str | os.PathLike[str], *,
                     expected_sha256: str, admission_path: str | os.PathLike[str],
                     expected_admission_sha256: str) -> dict[str, Any]:
    """Replay separately admitted current deletion ledgers, scrub derived data, then ready."""
    destination = Path(target)
    recovery = _load_recovery()
    if not destination.is_absolute() or destination.parent.resolve() != recovery.SCRATCH.resolve():
        raise RestoreFenceError('completion is confined to a direct child of isolated restore scratch')
    _private_dir(destination)
    for ancestor in (destination / 'state', destination / 'state/assistant-context'):
        _private_dir(ancestor)
    state_root = destination / 'state/assistant-context/state'
    _private_dir(state_root)
    fence_path = _fence_path(state_root)
    if not fence_path.is_file() or fence_path.is_symlink():
        raise RestoreFenceError('staged pending restore fence required')
    # Fail closed before opening any database if an earlier attempt completed.
    try:
        fence = json.loads(_private_file(fence_path, maximum=16384))
    except Exception as error:
        raise RestoreFenceError('pending restore fence is malformed') from error
    if not isinstance(fence, dict) or fence.get('schema_version') != FENCE_SCHEMA or fence.get('phase') != 'pending':
        raise RestoreFenceError('restore target is not pending')
    ledger_file, admission_file = Path(ledger_path), Path(admission_path)
    if ledger_file.resolve() == admission_file.resolve() or ledger_file.is_relative_to(destination) or admission_file.is_relative_to(destination):
        raise RestoreFenceError('trusted ledger and admission must be outside the restored archive target')
    ledger_raw = _private_file(ledger_file)
    ledger, scope_names = _validate_ledger(ledger_raw, expected_sha256)
    required_scopes = _stored_scopes(state_root)
    if not required_scopes <= set(scope_names):
        raise RestoreFenceError('trusted deletion ledger omits a scope stored in the archive')
    admission_raw = _private_file(admission_file, maximum=16384)
    _validate_admission(admission_raw, ledger_sha256=expected_sha256, scopes=scope_names,
                        expected_sha256=expected_admission_sha256)
    store_path = state_root / 'context.sqlite3'
    if store_path.is_symlink() or not store_path.is_file():
        raise RestoreFenceError('restored ContextStore database is absent')
    if store_path.stat().st_mode & 0o077:
        raise RestoreFenceError('restored ContextStore database is not private')
    # Persist progress before replay. A crash or any error leaves startup fenced.
    _write_atomic(fence_path, {'schema_version': FENCE_SCHEMA, 'phase': 'pending',
        'ledger_sha256': expected_sha256, 'admission_sha256': expected_admission_sha256,
        'scopes': scope_names, 'replay_started': True})
    store = ContextStore(store_path)
    tombstone_rows: list[tuple[str, str, str]] = []
    try:
        for scope_name in scope_names:
            scope = SCOPES[scope_name]
            row = ledger['scopes'][scope_name]
            store.replay_tombstones(scope, row)
            for item in row['source_tombstones']:
                tombstone_rows.append((scope_name, item['source'], item['object_id']))
        _derived_scrub(state_root, scope_names, tombstone_rows)
        # Archived vectors are untrusted even when their source rows remain current.
        from assistant_context_retrieval import AssistantContextRetrieval
        cache_path = state_root / 'retrieval.sqlite3'
        if cache_path.exists():
            retrieval = AssistantContextRetrieval(store, cache_path)
            retrieval.invalidate_all_after_restore()
    except Exception:
        raise
    ready = {'schema_version': FENCE_SCHEMA, 'phase': 'ready',
        'ledger_sha256': expected_sha256, 'admission_sha256': expected_admission_sha256,
        'scopes': scope_names, 'ready_at': datetime.now(timezone.utc).isoformat()}
    _write_atomic(fence_path, ready)
    assert_restore_ready(state_root)
    return {'phase': 'ready', 'scopes': scope_names,
            'ledger_sha256': expected_sha256, 'admission_sha256': expected_admission_sha256}
