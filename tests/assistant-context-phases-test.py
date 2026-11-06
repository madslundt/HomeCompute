"""Combined source-only acceptance for the later assistant slices."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_api import Client, ContextService, clients, serve
from assistant_context_store import Scope
from assistant_context_tools import PREFIX, SOURCES, validate
from assistant_context_delegation import DelegationController, ROLE
from assistant_context_delivery import load_delivery_auth
from embedding_client import DIMENSIONS

NOW = datetime(2026, 11, 6, 9, tzinfo=timezone.utc)
OWNER = Scope('owner', 'personal', 'private')


class Embeddings:
    def __init__(self):
        self.inputs = []
        self.fail = False

    def embed(self, inputs):
        self.inputs.extend(inputs)
        if self.fail:
            raise TimeoutError('fixture outage')
        return [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in inputs]


class Worker:
    def __init__(self):
        self.requests, self.purges = [], []

    def submit(self, **request):
        self.requests.append(request)
        return {'state': 'accepted', 'request_id': request['request_id'], 'completed': False}

    def purge_source(self, **request):
        self.purges.append(request)


class Phases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.embedding, self.worker = Embeddings(), Worker()
        self.policy = json.loads((ROOT / 'config/assistant-attention.json').read_text())
        self.service = ContextService(self.root / 'state', self.policy, clock=lambda: NOW,
            embedding_client=self.embedding, delegation=self.worker)
        self.owner = Client('owner', 'o' * 48, 'assistant', OWNER, frozenset(SOURCES), 'trusted-origin')
        self.limited = Client('aula', 'a' * 48, 'assistant', OWNER, frozenset({'aula'}))
        self.collector = Client('collector', 'c' * 48, 'collector', OWNER, frozenset(SOURCES))
        self.routine = Client('routine', 'r' * 48, 'routine', OWNER, frozenset(SOURCES),
                              routines=frozenset({'morning-overview'}))

    def event(self, source='aula', object_id='school', revision=1, operation='upsert', payload=None):
        return {'schema_version': 1, 'source': source, 'object_id': object_id, 'revision': revision,
                'operation': operation, 'event_type': 'fixture.change', 'occurred_at': NOW.isoformat(),
                'source_ref': f'fixture://{source}/{object_id}',
                'payload': {} if operation == 'delete' else payload or {'title': 'school pickup'} }

    def ingest(self, **changes):
        return self.service.ingest(self.collector, self.event(**changes))

    def call(self, operation, arguments, client=None):
        return self.service.call(client or self.owner, PREFIX + operation, arguments)

    def cover(self, source, age=timedelta()):
        self.service.update_coverage(self.collector, {'source': source,
            'observed_at': (NOW - age).isoformat(), 'complete': True})

    def test_hybrid_search_filters_sources_before_embedding_and_degrades_explicitly(self):
        self.ingest()
        self.ingest(source='daythread', object_id='private', payload={'text': 'secret excluded source'})
        result = self.call('search', {'query': 'school'}, self.limited)
        self.assertFalse(result['degraded'])
        self.assertEqual({row['source'] for row in result['results']}, {'aula'})
        self.assertNotIn('secret excluded source', json.dumps(self.embedding.inputs))
        self.embedding.fail = True
        degraded = self.call('search', {'query': 'school'}, self.limited)
        self.assertTrue(degraded['degraded'])
        self.assertEqual(degraded['results'][0]['object_id'], 'school')

    def test_combined_briefing_exposes_conflict_followup_and_coverage(self):
        self.ingest(source='daythread', payload={'text': 'Remember school pickup after the meeting'})
        for index, start, end in ((1, '10:00', '11:00'), (2, '10:30', '11:30')):
            self.ingest(source='calendar', object_id=f'event-{index}', payload={
                'title': 'School meeting', 'start': f'2026-11-06T{start}:00+01:00',
                'end': f'2026-11-06T{end}:00+01:00'})
        self.ingest()
        for source in ('aula', 'calendar', 'daythread'):
            self.cover(source)
        result = self.call('briefing', {'window': 'today'})
        self.assertEqual(len(result['calendar_collisions']), 1)
        self.assertTrue(result['daythread_followups'])
        self.assertEqual(result['coverage']['home-assistant'], 'unavailable')
        self.assertEqual(result['action_authority'], 'none')
        with self.assertRaises(ValueError):
            self.call('briefing', {}, self.limited)

    def test_briefing_uses_shorter_source_freshness_budget(self):
        self.ingest(source='calendar')
        self.cover('calendar', timedelta(hours=7))
        self.assertEqual(self.service.coverage(OWNER)['calendar']['state'], 'fresh')
        self.assertEqual(self.call('briefing', {})['coverage']['calendar'], 'stale')

    def test_failed_source_keeps_last_success_and_recovery_never_clears_newer_failure(self):
        self.cover('aula', timedelta(minutes=15))
        success = self.service.coverage(OWNER)['aula']['last_success_at']
        self.service.fail_coverage(self.collector, {'source': 'aula', 'observed_at': NOW.isoformat()})
        failed = self.service.coverage(OWNER)['aula']
        self.assertEqual(failed['last_success_at'], success)
        self.assertEqual(failed['state'], 'unavailable')
        self.cover('aula', timedelta(minutes=10))
        self.assertEqual(self.service.coverage(OWNER)['aula']['state'], 'unavailable')
        self.cover('aula')
        self.assertEqual(self.service.coverage(OWNER)['aula']['state'], 'fresh')
        self.assertNotIn('last_failure_at', self.service.coverage(OWNER)['aula'])

    def test_delegation_uses_bound_origin_and_current_citations(self):
        self.ingest()
        request = {'operation': 'submit', 'request_id': str(uuid.uuid4()), 'task_type': 'summary',
                   'task': 'Summarize the supplied school information',
                   'source_refs': [{'source': 'aula', 'object_id': 'school', 'revision': 1}]}
        self.assertEqual(self.call('delegate', request)['state'], 'accepted')
        self.assertIs(self.worker.requests[0]['scope'], self.owner.scope)
        self.assertEqual(self.worker.requests[0]['origin_thread'], 'trusted-origin')
        for changes in ({'origin_thread': 'attacker'}, {'scope': 'family'}, {'endpoint': 'https://example.com'}):
            with self.assertRaises(ValueError):
                self.call('delegate', {**request, **changes})
        self.ingest(revision=2)
        with self.assertRaises(ValueError):
            self.call('delegate', request)
        self.assertEqual(self.worker.purges[-1]['revision'], 1)
        self.assertEqual(len(self.worker.requests), 1)

    def test_source_delete_clears_vectors_and_delegation_but_old_delete_does_not(self):
        self.ingest()
        self.call('search', {'query': 'school'})
        with closing(sqlite3.connect(self.root / 'state/retrieval.sqlite3')) as db:
            self.assertGreater(db.execute('SELECT count(*) FROM vectors').fetchone()[0], 0)
        self.ingest(revision=2, operation='delete')
        with closing(sqlite3.connect(self.root / 'state/retrieval.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM vectors').fetchone()[0], 0)
        self.assertEqual(self.call('search', {'query': 'school'})['results'], [])
        self.ingest(revision=3)
        count = len(self.worker.purges)
        self.ingest(revision=2, operation='delete')
        self.assertEqual(len(self.worker.purges), count)
        self.assertEqual(self.call('search', {'query': 'school'})['results'][0]['revision'], 3)

    def test_actual_delegation_controller_accepts_canonical_integer_source_revisions(self):
        class Native:
            def call(self, operation, request, **_kwargs):
                return {'schema_version': 1, 'ok': True, 'native': {
                    'runId': request['request_id'], 'sessionKey': request['session_key'],
                    'agentId': ROLE, 'status': 'accepted', 'acceptedAt': int(NOW.timestamp())}}
        config = json.loads((ROOT / 'config/assistant-delegation.json').read_text())
        config['enabled'] = True
        self.service.delegation = DelegationController(self.root / 'state/delegation.sqlite3', config,
            transport=Native(), clock=lambda: NOW.timestamp())
        self.ingest(object_id='calendar/private:id')
        request = {'operation': 'submit', 'request_id': str(uuid.uuid4()), 'task_type': 'summary',
                   'task': 'Summarize supplied context', 'source_refs': [
                       {'source': 'aula', 'object_id': 'calendar/private:id', 'revision': 1}]}
        result = self.call('delegate', request)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['source_refs'][0]['revision'], 1)
        # Simulate process loss after source commit and before derived cleanup.
        correction = self.event(object_id='calendar/private:id', revision=2)
        self.service.store.ingest(OWNER, correction)
        self.assertTrue(self.service.store.invalidations(OWNER))
        recovered = ContextService(self.root / 'state', self.policy, clock=lambda: NOW)
        self.assertEqual(recovered.ingest(self.collector, correction)['status'], 'duplicate')
        self.assertEqual(recovered.store.invalidations(OWNER), [])
        self.assertEqual(recovered.delegation_cleanup._current(OWNER, request['request_id'])['source_refs'], [])
        # A feature switch never permits old private result text to survive deletion.
        restored = ContextService(self.root / 'state', self.policy, clock=lambda: NOW)
        restored.ingest(self.collector, self.event(object_id='calendar/private:id', revision=3, operation='delete'))
        row = restored.delegation_cleanup._current(OWNER, request['request_id'])
        self.assertEqual(row['source_refs'], [])
        self.assertIsNone(row['result'])

    def test_routine_control_is_revision_bound_and_cannot_admit_work(self):
        paused = self.call('routine', {'routine_id': 'morning-overview', 'operation': 'pause',
            'request_id': str(uuid.uuid4()), 'expected_revision': 0})
        self.assertEqual(paused['operation'], 'pause')
        with self.assertRaises(ValueError):
            self.call('routine', {'routine_id': 'morning-overview', 'operation': 'run'})
        with self.assertRaises(ValueError):
            self.call('improvements', {'operation': 'publish'})

    def test_routine_http_role_and_canonical_occurrence_envelope(self):
        server = serve(self.service, [self.owner, self.routine, self.collector], ('127.0.0.1', 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(path, body, client):
                with closing(http.client.HTTPConnection(*server.server_address, timeout=3)) as connection:
                    connection.request('POST', path, json.dumps(body), {'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + client.token})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
            body = {'routine_id': 'morning-overview', 'scheduled_for': NOW.isoformat()}
            failure = {'source': 'aula', 'observed_at': NOW.isoformat()}
            self.assertEqual(post('/v1/coverage/failure', failure, self.owner)[0], 403)
            self.assertEqual(post('/v1/coverage/failure', failure, self.collector)[0], 200)
            self.assertEqual(post('/v1/coverage/failure', {**failure, 'source': 'work'}, self.collector)[0], 400)
            self.assertEqual(post('/v1/routines/run', body, self.owner)[0], 403)
            self.assertEqual(post('/v1/tools', {'name': PREFIX + 'today', 'arguments': {}}, self.routine)[0], 403)
            status, result = post('/v1/routines/run', body, self.routine)
            self.assertEqual(status, 200)
            self.assertFalse(result['completed'])
            self.assertEqual(post('/v1/routines/run', {**body, 'occurrence_key': 'bypass'}, self.routine)[0], 400)
            self.assertEqual(post('/v1/routines/run', {**body, 'routine_id': 'tomorrow-preparation'}, self.routine)[0], 400)
            self.assertEqual(post('/v1/routines/run', {**body, 'scheduled_for': (NOW - timedelta(days=1)).isoformat()}, self.routine)[0], 400)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_pending_restore_fence_blocks_service_before_data_open(self):
        state = self.root / 'restored'
        state.mkdir(mode=0o700)
        fence = state / 'restore-fence.json'
        fence.write_text(json.dumps({'schema_version': 1, 'status': 'pending'}))
        fence.chmod(0o600)
        with self.assertRaises(ValueError):
            ContextService(state, self.policy)
        self.assertFalse((state / 'context.sqlite3').exists())

    def notification_service(self, transport, *, enabled=True):
        path = self.root / 'delivery-auth.json'
        grant = {'schema_version': 1, 'route_name': 'homecompute-context-owner', 'route_registered': True,
            'target': 'mattermost', 'route_config_sha256': 'a' * 64, 'standing_grant_id': 'b' * 32,
            'standing_grant_status': 'active', 'owner_user_id': 'owner', 'bot_user_id': 'bot', 'channel_type': 'D',
            'owner_destination_id': 'owner-private-dm', 'allowed_sources': ['aula'],
            'allowed_policy_versions': ['1.0.0'], 'grant_issued_at': '2026-11-06T00:00:00Z',
            'grant_expires_at': '2026-12-01T00:00:00Z', 'qualification_evidence_ref': 'fixture-only',
            'route_secret': 's' * 48}
        path.write_text(json.dumps(grant)); path.chmod(0o400)
        policy = {**self.policy, 'mode': 'active', 'rules': [
            {'source': 'aula', 'event_type': 'fixture.change', 'outcome': 'notify', 'class': 'fixture'}]}
        return ContextService(self.root / 'notifications', policy, clock=lambda: NOW,
            delivery_auth=load_delivery_auth(path, now=NOW), delivery_enabled=enabled, delivery_transport=transport)

    def test_qualified_automatic_change_notifications_obey_budget_and_never_repeat(self):
        calls = []
        def delivered(body, headers, **_kwargs):
            calls.append(json.loads(body))
            return 200, json.dumps({'status': 'delivered', 'route': 'homecompute-context-owner',
                'target': 'mattermost', 'delivery_id': headers['X-Request-ID']}).encode()
        service = self.notification_service(delivered)
        for index in range(3):
            service.ingest(self.collector, self.event(object_id=f'change-{index}'))
        self.assertEqual(calls, [])  # unknown source coverage prevents dispatch
        service.update_coverage(self.collector, {'source': 'aula', 'observed_at': NOW.isoformat(), 'complete': True})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('Source content (untrusted evidence)' in row['message'] for row in calls))
        self.assertTrue(all('fixture://aula/' in row['message'] for row in calls))
        service.review(OWNER)
        self.assertEqual(len(calls), 2)

    def test_automatic_notification_lost_reply_is_not_retried_and_shadow_sends_nothing(self):
        calls = []
        def lost(*_args, **_kwargs):
            calls.append(1)
            raise TimeoutError('synthetic lost reply')
        service = self.notification_service(lost, enabled=False)
        service.ingest(self.collector, self.event())
        service.update_coverage(self.collector, {'source': 'aula', 'observed_at': NOW.isoformat(), 'complete': True})
        self.assertEqual(calls, [])
        service.delivery_enabled = True
        service.review(OWNER)
        service.review(OWNER)
        self.assertEqual(len(calls), 1)
        decision = service.attention.list_decisions(scope=OWNER)[0]
        self.assertEqual(service.delivery.get_by_decision(scope=OWNER, decision_id=decision['decision_id'])['status'], 'uncertain')

    def test_restart_recovers_interrupted_routine_after_its_schedule_window(self):
        auth = self.notification_service(None, enabled=False).delivery_auth
        self.ingest()
        self.cover('aula')
        key = 'morning-overview@2026-11-06'
        self.service.routines.admit(OWNER, 'morning-overview', key, NOW.isoformat())
        def crash(*_args, **_kwargs):
            raise SystemExit('simulate process exit after durable claim')
        with self.assertRaises(SystemExit):
            self.service.routines.send_once(OWNER, 'morning-overview', key, NOW.isoformat(),
                self.service.builder(OWNER).build(), auth, '1.0.0', store=self.service.store,
                coverage=self.service.coverage(OWNER), enabled=True, now=NOW, transport=crash)
        restarted = ContextService(self.root / 'state', self.policy, clock=lambda: NOW + timedelta(days=2))
        self.assertEqual(restarted.routines.status(OWNER, 'morning-overview')['occurrences'][0]['status'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
