"""Finite controlled actions in the broker database. HTTP roles belong to broker.

Only the operator/trusted collector may ingest evidence and approve. Assistants
may propose/status/cancel by opaque IDs. No external executor is provisioned.
One broker process owns execution; the database must stay outside assistant access.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Any, Callable, Iterator
import uuid
from zoneinfo import ZoneInfo

ID = re.compile(r'^[a-zA-Z0-9_.:-]{1,128}$')
POLICY_KEYS = {'id', 'target', 'environment', 'enabled', 'executor', 'check_id', 'category', 'status', 'gates',
               'approval', 'evidence_ttl_seconds', 'approval_ttl_seconds', 'maintenance_window',
               'cooldown_seconds', 'max_attempts', 'postcheck', 'rollback', 'mechanism_refs'}
OBS_KEYS = {'system_id', 'check_id', 'stable_key', 'category', 'status', 'severity', 'evidence',
            'observed_at', 'expires_at', 'action', 'approval_required', 'physical_device_actions'}
SYNTHETIC = {'synthetic-core', 'synthetic-spark'}
PRODUCTION = {'guarded-home-core-deploy': 'home-core', 'guarded-home-spark-deploy': 'home-spark',
              'approved-compose-recovery': 'home-core', 'approved-spark-compose-recovery': 'home-spark',
              'dgx-os-maintenance': 'home-spark'}
GATES = {'reviewed-change', 'backup', 'model-qualification', 'promotion-approval',
         'recovery-runbook-reviewed', 'vendor-maintenance-reviewed'}
MAX_EVIDENCE_RECORDS = 10000
SAFE_ERRORS = {None, 'broker restarted; operator reconciliation required',
               'effect or postcheck failed; bounded rollback started', 'effect failed; rollback verified',
               'rollback uncertain; operator reconciliation required'}


class OwnedConnection(sqlite3.Connection):
    """Connection lifetime owns the process lock, including ordinary db.close()."""
    owner_fd: int | None = None

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self.owner_fd is not None:
                os.close(self.owner_fd)
                self.owner_fd = None

    def __del__(self) -> None:
        try: self.close()
        except Exception: pass


def owned_connection(path: Path) -> OwnedConnection:
    lock_path = path.with_name(path.name + '.actions.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('action ownership lock must be a private regular operator-owned file')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('another broker owns this action database') from None
        connection = sqlite3.connect(path, check_same_thread=False, timeout=10, factory=OwnedConnection)
        connection.owner_fd = fd
        return connection
    except BaseException:
        os.close(fd)
        raise


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_actions(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 131072:
        raise ValueError('action policy exceeds size budget')
    data = json.loads(path.read_text())
    if not isinstance(data, dict): raise ValueError('action policy must be an object')
    return data


def stamp(value: Any) -> float:
    if not isinstance(value, str):
        raise ValueError('timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.timestamp()


def minute(value: Any, *, end: bool = False) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', value):
        raise ValueError('invalid window time')
    hour, mins = map(int, value.split(':'))
    if mins > 59 or hour > 23 and not (end and hour == 24 and mins == 0):
        raise ValueError('invalid window time')
    return hour * 60 + mins


def validate_policies(data: dict[str, Any], monitoring: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'mode', 'actions'}
            or data['schema_version'] != 1 or data['mode'] != 'synthetic-only'
            or not isinstance(data['actions'], list) or not 1 <= len(data['actions']) <= 64):
        raise ValueError('unsupported action registry')
    systems = {s['id']: s for s in monitoring['systems']}
    result = {}
    for p in data['actions']:
        if not isinstance(p, dict) or set(p) != POLICY_KEYS:
            raise ValueError('unsupported action fields')
        for key in ('id', 'target', 'executor', 'check_id'):
            if not isinstance(p[key], str) or not ID.fullmatch(p[key]):
                raise ValueError('invalid action identifier')
        if p['id'] in result or type(p['enabled']) is not bool or p['approval'] != 'operator':
            raise ValueError('duplicate action or missing operator approval')
        if p['category'] not in {'health', 'updates'} or p['status'] != 'degraded':
            raise ValueError('unsupported precondition')
        if (not isinstance(p['gates'], list) or not set(p['gates']) <= GATES
                or len(set(p['gates'])) != len(p['gates'])):
            raise ValueError('invalid gates')
        if p['category'] == 'updates' and not {'reviewed-change', 'backup', 'model-qualification', 'promotion-approval'} <= set(p['gates']):
            raise ValueError('update qualification and promotion gates required')
        if p['environment'] == 'synthetic':
            allowed = {'synthetic-recover': ('synthetic.health', 'health', 'synthetic-healthy'),
                       'synthetic-update': ('synthetic.updates', 'updates', 'synthetic-next-version-healthy')}
            if (p['target'] not in SYNTHETIC or p['executor'] not in allowed
                    or (p['check_id'], p['category'], p['postcheck']) != allowed[p['executor']]
                    or p['rollback'] != 'synthetic-snapshot-once' or p['mechanism_refs'] != []):
                raise ValueError('invalid synthetic executor binding')
        elif p['environment'] == 'production':
            if p['enabled'] or PRODUCTION.get(p['executor']) != p['target'] or p['target'] not in systems:
                raise ValueError('production executors are not provisioned')
            s = systems[p['target']]
            checks = {'container.' + x['name'] for x in s['containers']} | {'unit.' + x['name'] for x in s['units']}
            checks |= {x for x in s['updates']} | {'model-update-report.changed'}
            if p['check_id'] not in checks or p['rollback'] != 'operator-reconcile':
                raise ValueError('action not registered in monitoring')
            if p['postcheck'] not in {'registered-health', 'registered-health-and-revision', 'registered-health-and-driver-qualification'}:
                raise ValueError('invalid production postcheck')
            if not isinstance(p['mechanism_refs'], list) or not p['mechanism_refs']:
                raise ValueError('existing maintenance reference required')
            for ref in p['mechanism_refs']:
                if not isinstance(ref, str) or ref.startswith('/') or '..' in Path(ref).parts or not ref.startswith(('scripts/', 'docs/', 'deploy/')):
                    raise ValueError('invalid maintenance reference')
        else:
            raise ValueError('unsupported environment')
        for key, low, high in (('evidence_ttl_seconds', 1, 900), ('approval_ttl_seconds', 1, 900),
                               ('cooldown_seconds', 1, 604800), ('max_attempts', 1, 3)):
            if type(p[key]) is not int or not low <= p[key] <= high:
                raise ValueError('invalid execution budget')
        w = p['maintenance_window']
        if (not isinstance(w, dict) or set(w) != {'timezone', 'weekdays', 'start', 'end'}
                or not isinstance(w['weekdays'], list) or not w['weekdays']
                or any(type(day) is not int or day not in range(7) for day in w['weekdays'])):
            raise ValueError('invalid maintenance window')
        try: ZoneInfo(w['timezone'])
        except (KeyError, TypeError): raise ValueError('invalid window timezone') from None
        if minute(w['start']) >= minute(w['end'], end=True):
            raise ValueError('windows must use an explicit same-day interval')
        result[p['id']] = json.loads(json.dumps(p))
    return result


class SyntheticExecutor:
    """Deterministic DB fixtures: no shell, network, credential or host writes."""
    def __init__(self, ledger: ActionLedger, policy: dict[str, Any]):
        self.ledger, self.policy = ledger, policy

    def prepare(self) -> dict[str, Any]:
        state = self.ledger.synthetic_state(self.policy['target'])
        if self.policy['executor'] == 'synthetic-recover' and state['healthy']:
            raise ValueError('target is already healthy')
        if self.policy['executor'] == 'synthetic-update' and not state['healthy']:
            raise ValueError('update requires a healthy baseline')
        return state

    def apply(self, snapshot: dict[str, Any]) -> None:
        self.ledger.seed_synthetic(self.policy['target'], healthy=True,
                                   version=snapshot['version'] + (self.policy['executor'] == 'synthetic-update'))

    def postcheck(self, snapshot: dict[str, Any]) -> bool:
        expected = {'healthy': True, 'version': snapshot['version'] + (self.policy['executor'] == 'synthetic-update')}
        return self.ledger.synthetic_state(self.policy['target']) == expected

    def rollback(self, snapshot: dict[str, Any]) -> None:
        self.ledger.seed_synthetic(self.policy['target'], **snapshot)

    def rollback_postcheck(self, snapshot: dict[str, Any]) -> bool:
        return self.ledger.synthetic_state(self.policy['target']) == snapshot


class ActionLedger:
    def __init__(self, path: Path, policies: dict[str, Any], monitoring: dict[str, Any],
                 *, clock: Callable[[], float] = time.time):
        self.policies = validate_policies(policies, monitoring)
        self.registry_hash = digest(monitoring)
        self.clock, self.lock = clock, threading.RLock()
        self.db = owned_connection(path)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self) -> None:
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS action_evidence(id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, body TEXT,
            system_id TEXT, check_id TEXT, observed_at REAL);
          CREATE INDEX IF NOT EXISTS action_evidence_check ON action_evidence(system_id,check_id,observed_at);
          CREATE TABLE IF NOT EXISTS action_requests(
            id TEXT PRIMARY KEY, action_id TEXT, target TEXT, incident_key TEXT, evidence_id TEXT,
            state TEXT, created REAL, updated REAL, policy_sha256 TEXT, approval_sha256 TEXT,
            approved_at REAL, started_at REAL, snapshot TEXT, error TEXT, policy_json TEXT NOT NULL,
            UNIQUE(action_id,target,incident_key));
          CREATE TABLE IF NOT EXISTS action_events(seq INTEGER PRIMARY KEY, action_id TEXT, state TEXT, at REAL, metadata TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS action_synthetic(target TEXT PRIMARY KEY, healthy INTEGER, version INTEGER);
        ''')
        for table, required in (('action_requests', 'policy_json'), ('action_events', 'metadata')):
            if required not in {row['name'] for row in self.db.execute('PRAGMA table_info(' + table + ')')}:
                raise ValueError('old action audit schema requires operator migration; no state reset')
        with self.transaction():
            for row in self.db.execute("SELECT id FROM action_requests WHERE state IN ('running','postchecking','rolling_back')").fetchall():
                self.transition(row['id'], 'reconcile_required', error='broker restarted; operator reconciliation required')

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def transition(self, action_id: str, state: str, **fields: Any) -> None:
        allowed = {'approval_sha256', 'approved_at', 'started_at', 'snapshot', 'error'}
        if not set(fields) <= allowed: raise ValueError('invalid transition fields')
        if 'error' in fields and fields['error'] not in SAFE_ERRORS:
            fields['error'] = 'action failed; operator reconciliation required'
        self.db.execute('UPDATE action_requests SET state=?,updated=?' + ''.join(',' + key + '=?' for key in fields) + ' WHERE id=?',
                        [state, self.clock(), *fields.values(), action_id])
        task = self.get(action_id)
        metadata = {key: task[key] for key in ('action_id', 'target', 'incident_key', 'evidence_id', 'policy_sha256',
                                              'approval_sha256', 'approved_at', 'started_at', 'error')}
        metadata['evidence_sha256'] = digest(self.evidence(task['evidence_id']))
        self.db.execute('INSERT INTO action_events(action_id,state,at,metadata) VALUES (?,?,?,?)',
                        (action_id, state, self.clock(), json.dumps(metadata)))

    def get(self, action_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute('SELECT * FROM action_requests WHERE id=?', (action_id,)).fetchone()
            if row is None: raise KeyError('action not found')
            return {k: row[k] for k in row.keys() if k not in {'snapshot', 'policy_json'}}

    def list_actions(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.get(row['id']) for row in self.db.execute('SELECT id FROM action_requests ORDER BY created DESC LIMIT 100').fetchall()]

    def audit(self, action_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [{**dict(row), 'metadata': json.loads(row['metadata'])} for row in self.db.execute(
                'SELECT * FROM action_events WHERE action_id=? ORDER BY seq', (action_id,))]

    def ingest_evidence(self, body: dict[str, Any]) -> dict[str, str]:
        if not isinstance(body, dict) or set(body) != {'observation', 'incident_key', 'gates'}:
            raise ValueError('trusted projected observation required')
        o = body['observation']
        if not isinstance(o, dict) or set(o) != OBS_KEYS:
            raise ValueError('unsupported observation contract')
        for key in ('system_id', 'check_id'):
            if not isinstance(o[key], str) or not ID.fullmatch(o[key]): raise ValueError('invalid evidence identifier')
        if (not isinstance(body['incident_key'], str) or not ID.fullmatch(body['incident_key'])
                or o['stable_key'] != o['system_id'] + ':' + o['check_id']
                or o['status'] not in {'healthy', 'degraded', 'unknown'} or o['category'] not in {'health', 'updates'}
                or o['severity'] != ('warning' if o['status'] == 'degraded' else 'info') or o['action'] != 'review_only'
                or o['approval_required'] is not True or o['physical_device_actions'] is not False):
            raise ValueError('invalid observation boundary')
        if not any(p['target'] == o['system_id'] and p['check_id'] == o['check_id'] for p in self.policies.values()):
            raise ValueError('unregistered observation')
        values = o['evidence']
        if (not isinstance(values, dict) or not values or not set(values) <= {'state', 'count', 'candidate_count', 'reason', 'source_report_generated_at', 'failed'}
                or any(type(v) not in (str, int, bool) or isinstance(v, str) and len(v) > 128 for v in values.values())):
            raise ValueError('unprojected evidence')
        if 'state' in values and values['state'] not in {'healthy', 'unhealthy', 'running', 'missing', 'unknown'}:
            raise ValueError('unprojected state')
        for key in ('count', 'candidate_count'):
            if key in values and (type(values[key]) is not int or not 0 <= values[key] <= 10000000):
                raise ValueError('invalid projected count')
        if 'failed' in values and type(values['failed']) is not bool: raise ValueError('invalid failure flag')
        if 'reason' in values and values['reason'] not in {'collection_failed', 'not_provisioned', 'missing', 'stale',
                'unsupported_schema', 'health_not_reported', 'update_metadata_unavailable', 'cached_inventory_only', 'never_started'}:
            raise ValueError('unprojected reason')
        if 'source_report_generated_at' in values: stamp(values['source_report_generated_at'])
        observed, expires, now = stamp(o['observed_at']), stamp(o['expires_at']), self.clock()
        if not math.isfinite(now) or observed > now or expires <= observed:
            raise ValueError('invalid evidence lifetime or future observation')
        if (not isinstance(body['gates'], dict) or not set(body['gates']) <= GATES
                or any(type(v) is not bool for v in body['gates'].values())):
            raise ValueError('invalid trusted gates')
        sha = digest(body)
        with self.transaction():
            row = self.db.execute('SELECT id FROM action_evidence WHERE sha256=?', (sha,)).fetchone()
            eid = row['id'] if row else str(uuid.uuid4())
            if row is None:
                if self.db.execute('SELECT count(*) FROM action_evidence').fetchone()[0] >= MAX_EVIDENCE_RECORDS:
                    raise ValueError('evidence budget exhausted; operator archival required')
                self.db.execute('INSERT INTO action_evidence VALUES (?,?,?,?,?,?)',
                                (eid, sha, json.dumps(body), o['system_id'], o['check_id'], observed))
            return {'id': eid, 'sha256': sha}

    def evidence(self, evidence_id: str) -> dict[str, Any]:
        row = self.db.execute('SELECT body FROM action_evidence WHERE id=?', (evidence_id,)).fetchone()
        if row is None: raise ValueError('trusted evidence not found')
        return json.loads(row['body'])

    def policy_hash(self, policy: dict[str, Any]) -> str:
        return digest({'policy': policy, 'monitoring_registry_sha256': self.registry_hash})

    def preconditions(self, task: dict[str, Any]) -> dict[str, Any]:
        p = self.policies.get(task['action_id'])
        if not p or not p['enabled'] or p['environment'] != 'synthetic' or task['target'] != p['target']:
            raise ValueError('executor disabled or target not approved')
        if task.get('policy_sha256', self.policy_hash(p)) != self.policy_hash(p):
            raise ValueError('policy changed; new proposal required')
        e, now = self.evidence(task['evidence_id']), self.clock()
        o = e['observation']
        observed, expires = stamp(o['observed_at']), stamp(o['expires_at'])
        latest = self.db.execute('SELECT id FROM action_evidence WHERE system_id=? AND check_id=? ORDER BY observed_at DESC,rowid DESC LIMIT 1',
                                 (p['target'], p['check_id'])).fetchone()
        if (not math.isfinite(now) or not 0 <= now - observed < p['evidence_ttl_seconds'] or now >= expires
                or not latest or latest['id'] != task['evidence_id']
                or e['incident_key'] != task['incident_key'] or o['system_id'] != p['target']
                or o['check_id'] != p['check_id'] or o['category'] != p['category'] or o['status'] != p['status']
                or any(e['gates'].get(gate) is not True for gate in p['gates'])):
            raise ValueError('fresh evidence and trusted preconditions required')
        return p

    def propose(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) != {'action_id', 'target', 'incident_key', 'evidence_id'}:
            raise ValueError('exact action, target, incident and evidence IDs required')
        if any(not isinstance(x, str) or not ID.fullmatch(x) for x in body.values()): raise ValueError('invalid IDs')
        with self.transaction():
            existing = self.db.execute('SELECT id FROM action_requests WHERE action_id=? AND target=? AND incident_key=?',
                                       [body[k] for k in ('action_id', 'target', 'incident_key')]).fetchone()
            if existing: return self.get(existing['id'])
            p = self.preconditions(body)
            if self.db.execute("SELECT count(*) FROM action_requests WHERE state IN ('pending','approved','running','postchecking','rolling_back')").fetchone()[0] >= 32:
                raise ValueError('action queue budget exhausted')
            aid, now = str(uuid.uuid4()), self.clock()
            policy_snapshot = {'policy': p, 'monitoring_registry_sha256': self.registry_hash}
            self.db.execute('INSERT INTO action_requests(id,action_id,target,incident_key,evidence_id,state,created,updated,policy_sha256,policy_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (aid, body['action_id'], body['target'], body['incident_key'], body['evidence_id'], 'pending', now, now,
                             self.policy_hash(p), json.dumps(policy_snapshot)))
            self.transition(aid, 'pending')
            return self.get(aid)

    def review(self, action_id: str) -> dict[str, Any]:
        with self.lock:
            task = self.get(action_id)
            stored = json.loads(self.db.execute('SELECT policy_json FROM action_requests WHERE id=?', (action_id,)).fetchone()['policy_json'])
            if digest(stored) != task['policy_sha256']: raise ValueError('stored policy snapshot corrupt')
            evidence = self.evidence(task['evidence_id'])
            bound = {k: task[k] for k in ('id', 'action_id', 'target', 'incident_key', 'evidence_id', 'policy_sha256')}
            bound['evidence_sha256'] = digest(evidence)
            return {'action': task, 'policy': stored['policy'], 'monitoring_registry_sha256': stored['monitoring_registry_sha256'],
                    'evidence': evidence, 'approval_sha256': digest(bound), 'audit': self.audit(action_id)}

    def approve(self, action_id: str, approval_sha256: str) -> dict[str, Any]:
        with self.transaction():
            task = self.get(action_id)
            self.preconditions(task)
            if task['state'] != 'pending' or not isinstance(approval_sha256, str) or not hmac.compare_digest(approval_sha256, self.review(action_id)['approval_sha256']):
                raise ValueError('approval must bind reviewed policy and evidence')
            self.transition(action_id, 'approved', approval_sha256=approval_sha256, approved_at=self.clock())
            return self.get(action_id)

    def refresh(self, action_id: str, evidence_id: str) -> dict[str, Any]:
        """Operator-only evidence refresh revokes approval; never resets attempts."""
        with self.transaction():
            task = self.get(action_id)
            if task['state'] not in {'pending', 'approved'}:
                raise ValueError('only an unstarted action may refresh evidence')
            updated = {**task, 'evidence_id': evidence_id}
            self.preconditions(updated)
            self.db.execute('UPDATE action_requests SET evidence_id=? WHERE id=?', (evidence_id, action_id))
            self.transition(action_id, 'pending', approval_sha256=None, approved_at=None)
            return self.get(action_id)

    def cancel(self, action_id: str) -> dict[str, Any]:
        with self.transaction():
            if self.get(action_id)['state'] not in {'pending', 'approved'}: raise ValueError('cannot cancel an effect already started')
            self.transition(action_id, 'cancelled')
            return self.get(action_id)

    def execute(self, action_id: str) -> dict[str, Any]:
        with self.transaction():
            task = self.get(action_id)
            p = self.preconditions(task)
            now = self.clock()
            local = datetime.fromtimestamp(now, ZoneInfo(p['maintenance_window']['timezone']))
            w = p['maintenance_window']
            if (task['state'] != 'approved' or not 0 <= now - task['approved_at'] < p['approval_ttl_seconds']
                    or not hmac.compare_digest(task['approval_sha256'], self.review(action_id)['approval_sha256'])
                    or local.weekday() not in w['weekdays']
                    or not minute(w['start']) <= local.hour * 60 + local.minute < minute(w['end'], end=True)):
                raise ValueError('approval expired or outside maintenance window')
            if self.db.execute("SELECT 1 FROM action_requests WHERE target=? AND (state IN ('running','postchecking','rolling_back','reconcile_required') OR started_at>?)",
                               (task['target'], now - p['cooldown_seconds'])).fetchone():
                raise ValueError('target blocked, occupied or cooling down')
            attempts = self.db.execute('SELECT count(*) FROM action_requests WHERE action_id=? AND target=? AND incident_key=? AND started_at IS NOT NULL',
                                       (task['action_id'], task['target'], task['incident_key'])).fetchone()[0]
            if attempts >= p['max_attempts']: raise ValueError('attempt budget exhausted')
            executor = SyntheticExecutor(self, p)
            snapshot = executor.prepare()
            self.transition(action_id, 'running', started_at=now, snapshot=json.dumps(snapshot))
        try:
            executor.apply(snapshot)
            with self.transaction(): self.transition(action_id, 'postchecking')
            if not executor.postcheck(snapshot): raise ValueError('postcheck failed')
        except Exception:
            with self.transaction(): self.transition(action_id, 'rolling_back', error='effect or postcheck failed; bounded rollback started')
            try:
                executor.rollback(snapshot)
                state = 'rolled_back' if executor.rollback_postcheck(snapshot) else 'reconcile_required'
            except Exception:
                state = 'reconcile_required'
            with self.transaction(): self.transition(action_id, state, error='effect failed; rollback verified' if state == 'rolled_back' else 'rollback uncertain; operator reconciliation required')
        else:
            with self.transaction(): self.transition(action_id, 'completed')
        return self.get(action_id)

    def seed_synthetic(self, target: str, *, healthy: bool, version: int) -> None:
        if target not in SYNTHETIC or type(healthy) is not bool or type(version) is not int or not 0 <= version <= 100000:
            raise ValueError('invalid synthetic fixture')
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO action_synthetic VALUES (?,?,?)', (target, int(healthy), version))

    def synthetic_state(self, target: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute('SELECT healthy,version FROM action_synthetic WHERE target=?', (target,)).fetchone()
            if row is None: raise ValueError('synthetic target not initialized')
            return {'healthy': bool(row['healthy']), 'version': row['version']}
