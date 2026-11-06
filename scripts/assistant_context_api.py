"""Authenticated local context API; actor, source grants and policy stay below tools."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from typing import Any

from assistant_attention import AttentionLedger, evaluate
from assistant_context_delivery import DeliveryOutbox, DeliveryError
from assistant_context_briefings import (BriefingBuilder, OccurrenceLedger, call_briefing_tool,
                                        load_config as load_briefings, run_routine)
from assistant_context_retrieval import AssistantContextRetrieval
from assistant_context_store import ContextStore, Scope
from assistant_context_tools import PREFIX, SOURCES, private_bytes, validate

MAX_BODY = 98304
MAX_RESPONSE = 131072
DEFAULT_CONTEXT_PORT = 18826
EVENT_KEYS = {'schema_version', 'source', 'object_id', 'revision', 'event_type',
              'operation', 'occurred_at', 'source_ref', 'payload'}


def instant(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('bounded explicit timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp timezone required')
    return parsed.astimezone(timezone.utc)


def json_data(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('finite JSON required')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def bounded_rows(rows: list[dict], maximum: int = 48000) -> tuple[list[dict], bool]:
    result, size = [], 2
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=True, allow_nan=False).encode()
        if size + len(encoded) + 1 > maximum:
            break
        result.append(row)
        size += len(encoded) + 1
    return result, len(result) != len(rows)


@dataclass(frozen=True)
class Client:
    name: str
    token: str
    role: str
    scope: Scope
    sources: frozenset[str]
    origin_id: str | None = None
    routines: frozenset[str] = frozenset()


def clients(path: Path) -> list[Client]:
    value = json_data(private_bytes(path))
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'clients'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['clients'], list) or not 1 <= len(value['clients']) <= 16):
        raise ValueError('bounded context client declarations required')
    result = []
    for row in value['clients']:
        if (not isinstance(row, dict) or not {'name', 'token', 'role', 'principal_scope',
                'data_domain', 'visibility', 'sources'} <= set(row)
                or set(row) - {'name', 'token', 'role', 'principal_scope', 'data_domain', 'visibility', 'sources', 'origin_id', 'routines'}
                or not isinstance(row['role'], str) or row['role'] not in {'assistant', 'collector', 'operator', 'routine'}
                or not isinstance(row['name'], str) or re.fullmatch(r'[a-z0-9-]{1,64}', row['name']) is None
                or not isinstance(row['token'], str) or re.fullmatch(r'[A-Za-z0-9_-]{40,256}', row['token']) is None
                or row['token'].startswith('replace') or not isinstance(row['sources'], list)
                or not row['sources'] or any(not isinstance(source, str) or source not in SOURCES for source in row['sources'])
                or len(row['sources']) != len(set(row['sources']))):
            raise ValueError('finite context client grant required')
        if 'origin_id' in row and (row['role'] != 'assistant' or not isinstance(row['origin_id'], str)
                or re.fullmatch(r'[A-Za-z0-9._:-]{1,160}', row['origin_id']) is None):
            raise ValueError('trusted assistant origin binding required')
        routine_ids = row.get('routines', [])
        if (not isinstance(routine_ids, list) or any(not isinstance(item, str) or item not in {'morning-overview', 'tomorrow-preparation'} for item in routine_ids)
                or len(set(routine_ids)) != len(routine_ids)
                or ('routines' in row) != (row['role'] == 'routine') or row['role'] == 'routine' and not routine_ids):
            raise ValueError('finite workflow-owned routine grant required')
        scope = Scope(row['principal_scope'], row['data_domain'], row['visibility'])
        if (row['role'] == 'routine' or 'origin_id' in row) and (
                scope != Scope('owner', 'personal', 'private') or frozenset(row['sources']) != frozenset(SOURCES)):
            raise ValueError('full owner-private context grant required')
        result.append(Client(row['name'], row['token'], row['role'],
            scope, frozenset(row['sources']), row.get('origin_id'), frozenset(routine_ids)))
    if len({row.token for row in result}) != len(result) or len({row.name for row in result}) != len(result):
        raise ValueError('independent named context credentials required')
    return result


class ContextService:
    """Only the service account opens state; callers use role-bound HTTP operations."""
    def __init__(self, state: Path, policy: dict, *, clock=None, embedding_client=None,
                 briefings: dict | None = None, reasoner=None, delegation=None,
                 delivery_auth=None, delivery_enabled: bool = False, improvement_status: bool = False,
                 delivery_transport=None):
        from assistant_context_recovery import assert_restore_ready
        assert_restore_ready(Path(state))
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy = dict(policy)
        if (self.policy.get('schema_version') != 1 or self.policy.get('mode') not in {'shadow', 'active'}):
            raise ValueError('reviewed attention policy required')
        state = Path(state)
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state.is_symlink() or state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
            raise ValueError('private service state required')
        self.store = ContextStore(state / 'context.sqlite3')
        self.attention = AttentionLedger(state / 'attention.sqlite3', dry_run=self.policy['mode'] != 'active')
        self.delivery = DeliveryOutbox(state / 'delivery.sqlite3', clock=self.clock)
        for known_scope in (Scope('owner', 'personal', 'private'), Scope('family', 'household', 'shared')):
            self.delivery.recover_inflight(scope=known_scope, now=self.clock())
        self.retrieval = AssistantContextRetrieval(self.store, state / 'retrieval.sqlite3', embedding_client=embedding_client)
        self.routines = OccurrenceLedger(state / 'routines.sqlite3', clock=self.clock)
        for known_scope in (Scope('owner', 'personal', 'private'), Scope('family', 'household', 'shared')):
            self.routines.recover_inflight(known_scope)
        self.briefings = briefings if briefings is not None else load_briefings(Path(__file__).resolve().parents[1] / 'config/assistant-briefings.json')
        self.reasoner, self.delegation = reasoner, delegation
        self.delegation_cleanup = delegation
        if delegation is None and (state / 'delegation.sqlite3').exists():
            from assistant_context_delegation import DelegationController, DelegationError, load_config
            class OfflineWorker:
                def call(self, *_args, **_kwargs):
                    raise DelegationError('native worker disabled; local erasure only')
            self.delegation_cleanup = DelegationController(state / 'delegation.sqlite3',
                load_config(Path(__file__).resolve().parents[1] / 'config/assistant-delegation.json'),
                transport=OfflineWorker(), maintenance_mode=True)
        self.delivery_auth, self.delivery_enabled = delivery_auth, delivery_enabled
        self.delivery_transport = delivery_transport
        self.improvement_status = improvement_status
        if delivery_enabled and delivery_auth is None:
            raise ValueError('qualified standing delivery grant required')
        self.coverage_path = state / 'coverage.sqlite3'
        fd = os.open(self.coverage_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ValueError('private service coverage state required')
        finally:
            os.close(fd)
        self.lock = threading.RLock()
        with closing(self.db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS coverage (principal TEXT, domain TEXT, visibility TEXT, '
                       'source TEXT, observed_at TEXT, complete INTEGER, PRIMARY KEY(principal,domain,visibility,source))')
            db.execute('CREATE TABLE IF NOT EXISTS coverage_failures (principal TEXT, domain TEXT, visibility TEXT, '
                       'source TEXT, observed_at TEXT, PRIMARY KEY(principal,domain,visibility,source))')
            db.commit()

    def db(self):
        db = sqlite3.connect(self.coverage_path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def identity(scope: Scope) -> tuple[str, str, str]:
        return scope.principal_scope, scope.data_domain, scope.visibility

    def coverage(self, scope: Scope) -> dict:
        now = self.clock()
        with closing(self.db()) as db:
            rows = db.execute('SELECT source,observed_at,complete FROM coverage WHERE principal=? AND domain=? AND visibility=?',
                              self.identity(scope)).fetchall()
            failures = db.execute('SELECT source,observed_at FROM coverage_failures WHERE principal=? AND domain=? AND visibility=?',
                                  self.identity(scope)).fetchall()
        result = {source: {'state': 'unavailable', 'last_success_at': None} for source in SOURCES}
        maximum = timedelta(minutes=int(self.policy.get('freshness_max_age_minutes', 1440)))
        for row in rows:
            age = now - instant(row['observed_at'])
            state = 'stale' if age < timedelta(0) or age > maximum else 'fresh' if row['complete'] else 'partial'
            result[row['source']] = {'state': state, 'last_success_at': row['observed_at']}
        for row in failures:
            success = result[row['source']]['last_success_at']
            if success is None or instant(row['observed_at']) >= instant(success):
                result[row['source']]['state'] = 'unavailable'
                result[row['source']]['last_failure_at'] = row['observed_at']
        return result

    def fail_coverage(self, client: Client, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) != {'source', 'observed_at'} or body['source'] not in client.sources:
            raise ValueError('source-bound failure receipt required')
        stamp = instant(body['observed_at'])
        if stamp > self.clock() + timedelta(minutes=5):
            raise ValueError('future source failure refused')
        key = (*self.identity(client.scope), body['source'])
        with self.lock, closing(self.db()) as db:
            previous = db.execute('SELECT observed_at FROM coverage_failures WHERE principal=? AND domain=? AND visibility=? AND source=?', key).fetchone()
            if previous is None or instant(previous[0]) <= stamp:
                db.execute('INSERT INTO coverage_failures VALUES(?,?,?,?,?) ON CONFLICT(principal,domain,visibility,source) '
                    'DO UPDATE SET observed_at=excluded.observed_at', (*key, stamp.isoformat()))
            db.commit()
        return {'accepted': True}

    def update_coverage(self, client: Client, body: dict) -> dict:
        if (not isinstance(body, dict) or set(body) != {'source', 'observed_at', 'complete'}
                or body['source'] not in client.sources or type(body['complete']) is not bool):
            raise ValueError('source-bound coverage report required')
        stamp = instant(body['observed_at'])
        if stamp > self.clock() + timedelta(minutes=5):
            raise ValueError('future coverage report refused')
        key = (*self.identity(client.scope), body['source'])
        with self.lock, closing(self.db()) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT observed_at FROM coverage WHERE principal=? AND domain=? AND visibility=? AND source=?', key).fetchone()
            if previous is None or instant(previous[0]) <= stamp:
                db.execute('INSERT INTO coverage VALUES(?,?,?,?,?,?) ON CONFLICT(principal,domain,visibility,source) '
                           'DO UPDATE SET observed_at=excluded.observed_at,complete=excluded.complete',
                           (*key, stamp.isoformat(), int(body['complete'])))
                failed = db.execute('SELECT observed_at FROM coverage_failures WHERE principal=? AND domain=? AND visibility=? AND source=?', key).fetchone()
                if failed is not None and instant(failed[0]) <= stamp:
                    db.execute('DELETE FROM coverage_failures WHERE principal=? AND domain=? AND visibility=? AND source=?', key)
            db.commit()
            reviewed = self.review(client.scope)
        return {'accepted': True, 'reviewed': reviewed}

    def ingest(self, client: Client, event: dict) -> dict:
        if not isinstance(event, dict) or event.get('source') not in client.sources:
            raise ValueError('source outside collector grant')
        with self.lock:
            receipt = self.store.ingest(client.scope, event)
            self.cleanup(client.scope)
        return {'accepted': True, **receipt}

    def cleanup(self, scope: Scope) -> int:
        """Recover admitted invalidations before exposing any derived data."""
        count = 0
        # Process a bounded batch, refusing reads if recovery needs another pass.
        for row in self.store.invalidations(scope):
            source, object_id = row['source'], row['object_id']
            self.delivery.purge_source(scope=scope, source=source, object_id=object_id, now=self.clock())
            self.attention.purge_source(scope=scope, source=source, object_id=object_id,
                                        preserve_controls=row['operation'] == 'upsert')
            self.retrieval.invalidate(scope, source, object_id)
            self.routines.purge_source(scope, source, object_id)
            worker = self.delegation or self.delegation_cleanup
            if worker is not None and scope == Scope('owner', 'personal', 'private'):
                kwargs = {'revision': row['previous_revision']} if row['operation'] == 'upsert' else {}
                worker.purge_source(scope=scope, source=source, object_id=object_id, **kwargs)
            self.store.ack_invalidation(scope, row['event_id'])
            count += 1
        if self.store.invalidations(scope, limit=1):
            raise OSError('derived cleanup is still pending; retry recovery')
        return count

    def review(self, scope: Scope) -> int:
        """Recover accepted work after a crash; ack only after durable decision admission."""
        count = 0
        coverage = self.coverage(scope)
        with self.lock:
            self.cleanup(scope)
            for projection in self.store.pending(scope, limit=100):
                if coverage[projection['source']]['state'] == 'unavailable':
                    continue
                event = {'schema_version': 1, **{key: projection[key] for key in EVENT_KEYS - {'schema_version'}}}
                result = evaluate(event, scope=scope, now=self.clock(), source_status=coverage, policy=self.policy)
                self.attention.record_decision(event, scope=scope, decision=result, now=self.clock())
                self.store.ack(scope, projection['event_id'])
                count += 1
            self.dispatch_attention(scope)
        return count

    def dispatch_attention(self, scope: Scope) -> dict:
        """One-shot source-linked notifications under the qualified standing policy."""
        counts = {'attempted': 0, 'delivered': 0, 'held': 0}
        if not self.delivery_enabled or self.policy['mode'] != 'active' or scope != Scope('owner', 'personal', 'private'):
            return counts
        for decision in self.attention.list_decisions(scope=scope, limit=100):
            if decision['outcome'] != 'notify' or decision['state'] != 'pending':
                continue
            existing = self.delivery.get_by_decision(scope=scope, decision_id=decision['decision_id'])
            if existing is not None and existing['status'] != 'pending':
                continue
            source = self.store.get(scope, decision['source'], decision['object_id'])
            if source is None or source['revision'] != decision['source_revision']:
                continue
            # Copy only bounded source facts; source text never selects urgency,
            # destination, tools or a model instruction for the delivery route.
            payload = source['payload']
            parts = [f"{source['source']} update to review.", 'Source content (untrusted evidence):']
            parts.extend(str(payload[key])[:700] for key in ('title', 'summary', 'text')
                         if isinstance(payload.get(key), str) and payload[key])
            parts.append('Source: ' + source['source_ref'][:500])
            message = '\n'.join(parts)
            message = message.encode('utf-8')[:3500].decode('utf-8', errors='ignore')
            try:
                admitted = self.delivery.admit(scope=scope, decision_id=decision['decision_id'], message=message,
                    ledger=self.attention, context=self.store, source_status=self.briefing_coverage(scope),
                    policy=self.policy, now=self.clock(), dry_run=False)
                if admitted['status'] != 'pending':
                    counts['held'] += 1
                    continue
                counts['attempted'] += 1
                result = self.delivery.send_once(scope=scope, decision_id=decision['decision_id'],
                    ledger=self.attention, context=self.store, source_status=lambda: self.briefing_coverage(scope),
                    policy=self.policy, auth=self.delivery_auth, now=self.clock(), transport=self.delivery_transport)
                counts['delivered'] += result['status'] == 'delivered'
            except DeliveryError:
                counts['held'] += 1
        return counts

    def call(self, client: Client, name: str, arguments: Any) -> dict:
        with self.lock:
            self.cleanup(client.scope)
            return self._call(client, name, arguments)

    def _call(self, client: Client, name: str, arguments: Any) -> dict:
        values = validate(name, arguments)
        operation = name.removeprefix(PREFIX)
        scope, now = client.scope, self.clock()
        if operation == 'search':
            retrieved = self.retrieval.search(scope, values['query'], limit=values.get('limit', 10), source_grants=client.sources)
            rows, truncated = bounded_rows(retrieved.pop('results'))
            return {**retrieved, 'results': rows, 'truncated': truncated,
                    'coverage': self.visible_coverage(client), 'evidence_only': True}
        if operation in {'briefing', 'routine', 'delegate', 'improvements'}:
            self.require_owner(client)
            if operation in {'briefing', 'routine'}:
                return call_briefing_tool(self.builder(scope), self.routines, scope, name, values)
            if operation == 'improvements':
                rows, truncated = bounded_rows(self.attention.list_candidates(scope=scope, limit=values.get('limit', 10)), 16000)
                pipeline = {'available': False, 'reason': 'trusted controller status is not configured'}
                if self.improvement_status:
                    from assistant_context_improvements import ImprovementController
                    try:
                        status = ImprovementController.read_status_projection()
                        projected, clipped = bounded_rows(status['candidates'][-values.get('limit', 10):], 16000)
                        pipeline = {'available': True, 'updated_at': status['updated_at'],
                                    'candidates': projected, 'truncated': clipped}
                    except (OSError, ValueError):
                        pipeline = {'available': False, 'reason': 'trusted controller status is unavailable'}
                return {'candidates': rows, 'truncated': truncated, 'authority': 'status-only', 'pipeline': pipeline}
            if self.delegation is None or client.origin_id is None:
                return {'enabled': False, 'reason': 'native worker and trusted origin are not configured'}
            if values['operation'] == 'submit':
                for ref in values.get('source_refs', []):
                    projection = self.store.get(scope, ref['source'], ref['object_id'])
                    if projection is None or projection['revision'] != ref['revision']:
                        raise ValueError('current authorized delegation citation required')
                return self.delegation.submit(scope=scope, origin_thread=client.origin_id,
                    request_id=values['request_id'], task_type=values['task_type'], task=values['task'],
                    source_refs=values.get('source_refs', []))
            method = self.delegation.status if values['operation'] == 'status' else self.delegation.cancel
            return method(scope=scope, request_id=values['request_id'], origin_thread=client.origin_id)
        if operation == 'today':
            rows = self.store.list_current(scope, limit=values.get('limit', 10), sources=client.sources)
            decisions = self.attention.list_decisions(scope=scope, limit=values.get('limit', 10), sources=client.sources)
            rows, truncated = bounded_rows(rows)
            decisions, decision_truncated = bounded_rows(decisions, 24000)
            candidates = (self.attention.list_candidates(scope=scope, limit=5)
                          if client.sources == frozenset(SOURCES) else [])
            candidates, candidate_truncated = bounded_rows(candidates, 16000)
            return {'context': rows, 'decisions': decisions, 'improvement_candidates': candidates,
                    'truncated': truncated or decision_truncated or candidate_truncated,
                    'coverage': self.visible_coverage(client), 'mode': self.policy['mode']}
        if operation == 'source':
            if values['source'] not in client.sources:
                raise ValueError('source outside assistant grant')
            return {'source': self.store.get(scope, values['source'], values['object_id'])}
        if operation in {'decision', 'feedback'}:
            row = self.attention.get_decision(scope=scope, decision_id=values['decision_id'])
            if row is None or row['source'] not in client.sources:
                raise ValueError('decision unavailable in authenticated scope')
            if operation == 'feedback':
                receipt = self.attention.record_feedback(scope=scope, decision_id=values['decision_id'],
                    kind=values['kind'], source_refs=[row['source_ref']], request_id=values['request_id'],
                    now=now, detail=values.get('detail'))
                candidate = self.attention.review_feedback(scope=scope, decision_id=values['decision_id'],
                                                         now=now, policy=self.policy)
                return {'feedback': receipt, 'improvement_candidate': candidate}
            if values['operation'] == 'show':
                delivery = self.delivery.get_by_decision(scope=scope, decision_id=values['decision_id'])
                if delivery is not None:
                    delivery = {key: delivery[key] for key in (
                        'delivery_id', 'status', 'revision', 'reason', 'route_name', 'target', 'updated_at')}
                return {'decision': row, 'delivery': delivery}
            return {'decision': self.attention.apply_decision(scope=scope, decision_id=values['decision_id'],
                operation=values['operation'], expected_revision=values['expected_revision'],
                request_id=values['request_id'], now=now, until=values.get('until'))}
        if operation == 'memory':
            if client.sources != frozenset(SOURCES):
                raise ValueError('personal memory requires the full owner context grant')
            action = values['operation']
            if action == 'list':
                rows, truncated = bounded_rows(self.store.list_memories(scope))
                return {'memories': rows, 'truncated': truncated}
            if action == 'export':
                result = self.store.export_memories_page(scope, limit=values.get('limit', 20), cursor=values.get('cursor'))
                records, truncated = bounded_rows(result['records'])
                if truncated:
                    result['next_cursor'] = records[-1]['memory_id'] if records else values.get('cursor')
                result['records'] = records
                return {'memories': result}
            if action == 'propose':
                return {'memory': self.store.propose_memory(scope, values['statement'], values['kind'],
                    values['source_ref'], values['request_id'])}
            return {'memory': self.store.update_memory(scope, values['memory_id'], action,
                values['expected_revision'], values['request_id'])}
        raise ValueError('unsupported finite context operation')

    @staticmethod
    def require_owner(client: Client) -> None:
        if client.scope != Scope('owner', 'personal', 'private') or client.sources != frozenset(SOURCES):
            raise ValueError('full owner-private context grant required')

    def builder(self, scope: Scope) -> BriefingBuilder:
        return BriefingBuilder(self.store, scope=scope, coverage=self.briefing_coverage(scope), clock=self.clock,
            reasoner=self.reasoner, formatting=self.briefings['formatting'], adaptation=self.briefings['trusted_adaptation'])

    def briefing_coverage(self, scope: Scope) -> dict:
        result = self.coverage(scope)
        for source, row in result.items():
            if row['last_success_at'] is not None and self.clock() - instant(row['last_success_at']) > timedelta(
                    minutes=self.briefings['max_age_minutes'][source]):
                row['state'] = 'stale'
        return result

    def run(self, client: Client, body: dict) -> dict:
        with self.lock:
            self.cleanup(client.scope)
            return self._run(client, body)

    def _run(self, client: Client, body: dict) -> dict:
        self.require_owner(client)
        if (not isinstance(body, dict) or set(body) != {'routine_id', 'scheduled_for'}
                or body['routine_id'] not in client.routines):
            raise ValueError('workflow-bound routine occurrence required')
        stamp = instant(body['scheduled_for'])
        if not self.clock() - timedelta(minutes=20) <= stamp <= self.clock() + timedelta(minutes=2):
            raise ValueError('current workflow occurrence required')
        if self.delivery_auth is None:
            return {'enabled': False, 'completed': False, 'reason': 'standing delivery grant is not configured',
                    'briefing': self.builder(client.scope).build('today' if body['routine_id'] == 'morning-overview' else 'tomorrow')}
        with self.lock:
            return run_routine(self.builder(client.scope), self.routines, client.scope,
                body['routine_id'], body['scheduled_for'], self.delivery_auth, self.policy['policy_version'],
                store=self.store, coverage=lambda: self.briefing_coverage(client.scope), enabled=self.delivery_enabled,
                now=self.clock())

    def visible_coverage(self, client: Client) -> dict:
        return {source: status for source, status in self.coverage(client.scope).items() if source in client.sources}


def serve(service: ContextService, configured: list[Client], address=('127.0.0.1', DEFAULT_CONTEXT_PORT)):
    if address[0] != '127.0.0.1':
        raise ValueError('context service must bind loopback')
    budget = threading.BoundedSemaphore(8)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_one_request(self):
            self.connection.settimeout(10)
            super().handle_one_request()

        def respond(self, status, body):
            raw = json.dumps(body, allow_nan=False).encode()
            if len(raw) > MAX_RESPONSE:
                status, raw = 503, b'{"error":"context response exceeds budget"}'
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def authenticate(self):
            headers = self.headers.get_all('Authorization', [])
            if len(headers) != 1 or self.headers.get('Origin') is not None:
                return None
            return next((client for client in configured if hmac.compare_digest(headers[0], 'Bearer ' + client.token)), None)

        def do_GET(self):
            if self.authenticate() is None:
                self.respond(401, {'error': 'unauthorized'}); return
            if self.path == '/v1/health':
                self.respond(200, {'ready': True, 'mode': service.policy['mode'], 'delivery_enabled': service.delivery_enabled,
                                   'delegation_enabled': service.delegation is not None})
            else:
                self.respond(404, {'error': 'not found'})

        def do_POST(self):
            client = self.authenticate()
            if client is None:
                self.respond(401, {'error': 'unauthorized'}); return
            roles = {'/v1/events': {'collector'}, '/v1/coverage': {'collector'}, '/v1/coverage/failure': {'collector'},
                     '/v1/tools': {'assistant'}, '/v1/review': {'operator'}, '/v1/routines/run': {'routine'}}
            if self.path not in roles:
                self.respond(404, {'error': 'not found'}); return
            if client.role not in roles[self.path]:
                self.respond(403, {'error': 'role not permitted'}); return
            if not budget.acquire(blocking=False):
                self.respond(429, {'error': 'context busy'}); return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get_content_type() != 'application/json'):
                    raise ValueError('one bounded JSON body required')
                size = int(lengths[0])
                if not 0 < size <= MAX_BODY:
                    self.respond(413, {'error': 'body exceeds budget'}); return
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('incomplete body')
                body = json_data(raw)
                if self.path == '/v1/events':
                    result = service.ingest(client, body)
                elif self.path == '/v1/coverage':
                    result = service.update_coverage(client, body)
                elif self.path == '/v1/coverage/failure':
                    result = service.fail_coverage(client, body)
                elif self.path == '/v1/review':
                    if body != {}:
                        raise ValueError('empty review request required')
                    result = {'reviewed': service.review(client.scope)}
                elif self.path == '/v1/routines/run':
                    result = service.run(client, body)
                else:
                    if not isinstance(body, dict) or set(body) != {'name', 'arguments'}:
                        raise ValueError('exact tool envelope required')
                    result = service.call(client, body['name'], body['arguments'])
                self.respond(200, result)
            except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                self.respond(400, {'error': 'invalid or conflicting context request'})
            except (OSError, sqlite3.Error):
                self.respond(503, {'error': 'context unavailable; inspect private service state'})
            finally:
                budget.release()

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    return server
