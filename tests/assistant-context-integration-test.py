"""End-to-end synthetic context, attention, deletion and MCP trust tests."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
import urllib.request
import uuid
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_api import Client, ContextService, serve as serve_context
from assistant_context_delivery import DeliveryOutbox
from assistant_context_store import Scope
from assistant_context_tools import ContextClient, PREFIX, SOURCES
from openclaw_notifications import Outbox

spec = importlib.util.spec_from_file_location('communication_integration', ROOT / 'scripts/openclaw-communication.py')
communication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(communication)

NOW = datetime(2026, 11, 6, 11, tzinfo=timezone.utc)
OWNER = Scope('owner', 'personal', 'private')
FAMILY = Scope('family', 'household', 'shared')
TOKENS = {name: (name + '-context-' + ('x' * 48)) for name in SOURCES}
OWNER_TOKEN = 'assistant-context-owner-' + 'y' * 48


def policy(*, mode='shadow'):
    value = json.loads((ROOT / 'config/assistant-attention.json').read_text())
    value['mode'] = mode
    value['daily_discretionary_notification_limit'] = 20
    value['rules'] = [{'source': source, 'event_type': 'synthetic.change',
                       'outcome': 'notify', 'class': 'synthetic-integration'} for source in SOURCES]
    return value


class ContextIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        # Synthetic active policy exercises the durable decision/outbox interop;
        # this suite never configures transport credentials or sends anything.
        self.policy = policy(mode='active')
        self.clients = [Client('collector-' + source, TOKENS[source], 'collector', OWNER, frozenset({source}))
                        for source in sorted(SOURCES)]
        self.clients += [Client('assistant-owner', OWNER_TOKEN, 'assistant', OWNER, frozenset(SOURCES)),
                         Client('assistant-aula', 'assistant-aula-' + 'z' * 48, 'assistant', OWNER, frozenset({'aula'})),
                         Client('assistant-family', 'assistant-family-' + 'z' * 48, 'assistant', FAMILY, frozenset({'aula'})),
                         Client('operator', 'operator-context-' + 'q' * 48, 'operator', OWNER, frozenset(SOURCES))]
        self.by_source = {next(iter(client.sources)): client for client in self.clients if client.role == 'collector'}
        self.service = ContextService(self.state, self.policy, clock=lambda: NOW)
        self.server = serve_context(self.service, self.clients, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def post(self, path, body, client):
        data = json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}{path}', data=data,
            headers={'Authorization': 'Bearer ' + client.token, 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def event(self, source, object_id, *, revision=1, operation='upsert', source_ref=None, payload=None, occurred_at=None):
        return {'schema_version': 1, 'source': source, 'object_id': object_id, 'revision': revision,
                'event_type': 'synthetic.change', 'operation': operation,
                'occurred_at': (occurred_at or NOW).isoformat(),
                'source_ref': source_ref or f'fixture://{source}/{object_id}/revision-{revision}',
                'payload': payload if payload is not None else {'title': 'needle synthetic context',
                                                                 'text': f'Private fixture for {source} {object_id}'}}

    def ingest(self, event):
        return self.post('/v1/events', event, self.by_source[event['source']])

    def cover(self, source, *, at=NOW):
        return self.post('/v1/coverage', {'source': source, 'observed_at': at.isoformat(), 'complete': True},
                         self.by_source[source])

    def tool(self, name, arguments, client=None):
        return self.post('/v1/tools', {'name': PREFIX + name, 'arguments': arguments},
                         client or next(item for item in self.clients if item.name == 'assistant-owner'))

    def seed_all_sources(self):
        for source in sorted(SOURCES):
            status, receipt = self.ingest(self.event(source, source + '-fixture'))
            self.assertEqual(status, 200, receipt)
            self.assertEqual(receipt['status'], 'accepted')
        for source in sorted(SOURCES):
            status, receipt = self.cover(source)
            self.assertEqual(status, 200, receipt)
        return self.tool('today', {'limit': 20})[1]

    def test_all_four_source_grants_project_and_restart_durably(self):
        result = self.seed_all_sources()
        self.assertEqual({row['source'] for row in result['context']}, set(SOURCES))
        self.assertEqual(set(result['coverage']), set(SOURCES))
        self.assertTrue(all(row['state'] == 'fresh' for row in result['coverage'].values()))
        restored = ContextService(self.state, self.policy, clock=lambda: NOW)
        for source in SOURCES:
            self.assertIsNotNone(restored.store.get(OWNER, source, source + '-fixture'))
        self.assertEqual(len(restored.attention.list_decisions(scope=OWNER, limit=20)), 4)
        self.assertEqual(restored.review(OWNER), 0)

    def test_source_grant_filters_before_ranking_and_result_budget_is_bounded(self):
        self.seed_all_sources()
        aula_client = next(item for item in self.clients if item.name == 'assistant-aula')
        status, result = self.tool('search', {'query': 'needle', 'limit': 1}, aula_client)
        self.assertEqual(status, 200)
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['source'], 'aula')
        self.assertEqual(set(result['coverage']), {'aula'})

        # Each event is individually valid, while 20 matching projections exceed the
        # search tool's 48 KiB row budget and must be returned with truncation metadata.
        large = 'needle ' + ('synthetic fixture text ' * 70)
        for index in range(20):
            payload = {'title': large, 'field_a': large, 'field_b': large, 'field_c': large}
            status, _ = self.ingest(self.event('aula', f'large-{index:02d}', payload=payload,
                                               occurred_at=NOW - timedelta(minutes=index)))
            self.assertEqual(status, 200)
        status, result = self.tool('search', {'query': 'needle', 'limit': 20}, aula_client)
        self.assertEqual(status, 200)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result['results']).encode()), 48000)

    def test_role_and_scope_denials_are_enforced_across_http_tools(self):
        event = self.event('aula', 'scoped-fixture')
        owner_assistant = next(item for item in self.clients if item.name == 'assistant-owner')
        self.assertEqual(self.post('/v1/events', event, owner_assistant)[0], 403)
        self.assertEqual(self.post('/v1/coverage', {'source': 'aula', 'observed_at': NOW.isoformat(), 'complete': True}, owner_assistant)[0], 403)
        self.assertEqual(self.tool('today', {}, self.by_source['aula'])[0], 403)
        self.ingest(event)
        self.cover('aula')
        family = next(item for item in self.clients if item.name == 'assistant-family')
        status, result = self.tool('search', {'query': 'needle'}, family)
        self.assertEqual(status, 200)
        self.assertEqual(result['results'], [])
        self.assertEqual(self.tool('source', {'source': 'aula', 'object_id': 'scoped-fixture'}, family)[1]['source'], None)
        aula_assistant = next(item for item in self.clients if item.name == 'assistant-aula')
        memory_args = {'operation': 'propose', 'statement': 'Synthetic preference', 'kind': 'preference',
                       'source_ref': 'fixture://aula/scoped-fixture/revision-1', 'request_id': str(uuid.uuid4())}
        self.assertEqual(self.tool('memory', memory_args, aula_assistant)[0], 400)

    def test_three_unique_feedback_records_create_one_candidate_then_delete_cascades(self):
        objects = ['feedback-one', 'feedback-two', 'feedback-three']
        for object_id in objects:
            status, _ = self.ingest(self.event('aula', object_id, source_ref=f'fixture://aula/{object_id}/revision-1'))
            self.assertEqual(status, 200)
        self.cover('aula')
        decisions = {row['object_id']: row for row in self.service.attention.list_decisions(scope=OWNER, limit=20)
                     if row['object_id'] in objects}
        self.assertEqual(set(decisions), set(objects))

        memory = self.service.store.propose_memory(OWNER, 'Prefer a concise morning summary', 'preference',
            'fixture://aula/feedback-one/revision-1', str(uuid.uuid4()))
        for object_id in objects:
            row = decisions[object_id]
            status, result = self.tool('feedback', {'decision_id': row['decision_id'], 'kind': 'correction',
                'detail': 'synthetic explicit correction', 'request_id': str(uuid.uuid4())})
            self.assertEqual(status, 200)
        candidates = self.service.attention.list_candidates(scope=OWNER, limit=20)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['allowed_class'], 'attention-threshold')
        self.assertEqual(self.tool('today', {'limit': 20})[1]['improvement_candidates'], candidates)

        # Admit and hold a private dry-run body. Source deletion must retain the
        # outbox receipt while scrubbing its queued content.
        decision = decisions['feedback-one']
        held = self.service.delivery.admit(scope=OWNER, decision_id=decision['decision_id'],
            message='Private queued fixture body', ledger=self.service.attention, context=self.service.store,
            source_status={'aula': {'state': 'fresh', 'last_success_at': NOW.isoformat()}},
            policy=self.policy, now=NOW, dry_run=False)
        held = self.service.delivery.hold(scope=OWNER, decision_id=decision['decision_id'], now=NOW, reason='synthetic_hold')
        self.assertEqual(held['status'], 'held')
        # The correlated read includes only a bounded receipt, never its message.
        shown = self.tool('decision', {'decision_id': decision['decision_id'], 'operation': 'show'})
        self.assertEqual(shown[0], 200)
        self.assertEqual(shown[1]['delivery']['status'], 'held')
        self.assertNotIn('message', shown[1]['delivery'])

        self.ingest(self.event('aula', 'feedback-one', revision=2,
                               source_ref='fixture://aula/feedback-one/revision-2'))
        self.cover('aula', at=NOW + timedelta(seconds=1))
        status, receipt = self.ingest(self.event('aula', 'feedback-one', revision=3, operation='delete',
            source_ref='fixture://aula/feedback-one/revision-3', payload={}))
        self.assertEqual(status, 200, receipt)
        memory_after = self.service.store.inspect_memory(OWNER, memory['memory_id'])
        self.assertEqual(memory_after['status'], 'deleted')
        self.assertIsNone(memory_after['statement'])
        self.assertEqual(memory_after['source_refs'], [])
        self.assertEqual(self.service.attention.list_candidates(scope=OWNER, limit=20), [])
        self.assertNotIn('synthetic explicit correction', json.dumps(self.service.attention.list_decisions(scope=OWNER, limit=20)))
        persisted = self.service.delivery.get_by_decision(scope=OWNER, decision_id=decision['decision_id'])
        self.assertIsNotNone(persisted, 'held outbox receipt must remain for reconciliation')
        self.assertEqual((persisted['status'], persisted['reason']), ('held', 'source_deleted'))
        self.assertNotIn('message', persisted)
        with closing(self.service.delivery._db()) as db:
            self.assertIsNone(db.execute('SELECT message FROM delivery_outbox WHERE decision_id=?',
                                         (decision['decision_id'],)).fetchone()[0])

    def test_snooze_and_stop_suppress_later_source_revisions(self):
        for object_id in ('snoozed-object', 'stopped-object'):
            self.ingest(self.event('aula', object_id))
        self.cover('aula')
        decisions = {row['object_id']: row for row in self.service.attention.list_decisions(scope=OWNER, limit=20)}
        snoozed = decisions['snoozed-object']
        stopped = decisions['stopped-object']
        status, snooze_receipt = self.tool('decision', {'decision_id': snoozed['decision_id'], 'operation': 'snooze',
            'expected_revision': snoozed['revision'], 'request_id': str(uuid.uuid4()),
            'until': (NOW + timedelta(days=1)).isoformat()})
        self.assertEqual((status, snooze_receipt['decision']['state']), (200, 'snoozed'))
        status, stop_receipt = self.tool('decision', {'decision_id': stopped['decision_id'], 'operation': 'stop',
            'expected_revision': stopped['revision'], 'request_id': str(uuid.uuid4())})
        self.assertEqual((status, stop_receipt['decision']['state']), (200, 'stopped'))
        for object_id in ('snoozed-object', 'stopped-object'):
            self.ingest(self.event('aula', object_id, revision=2,
                                   source_ref=f'fixture://aula/{object_id}/revision-2'))
        self.cover('aula', at=NOW + timedelta(seconds=1))
        rows = self.service.attention.list_decisions(scope=OWNER, limit=20)
        self.assertEqual([row['source_revision'] for row in rows[:2]], [2, 2])
        latest = {row['object_id']: row for row in sorted(rows, key=lambda item: item['source_revision'])}
        self.assertEqual((latest['snoozed-object']['source_revision'], latest['snoozed-object']['state'],
                          latest['snoozed-object']['reason']), (2, 'snoozed', 'occurrence_snoozed'))
        self.assertEqual((latest['stopped-object']['source_revision'], latest['stopped-object']['state'],
                          latest['stopped-object']['reason']), (2, 'stopped', 'occurrence_stopped'))

    def test_actual_mcp_bridge_exposes_context_only_to_hermes_owner(self):
        self.seed_all_sources()
        client_token = self.root / 'context-token'
        client_token.write_text(OWNER_TOKEN)
        client_token.chmod(0o600)
        config = self.root / 'context-client.json'
        config.write_text(json.dumps({'schema_version': 1, 'token_file': str(client_token)}))
        config.chmod(0o600)
        bridge_client = ContextClient(config)
        settings = {'schema_version': 1, 'destination': 'operator-private', 'conversations': ['operator'],
                    'projects': [], 'actions': {}, 'timezone': 'Europe/Copenhagen',
                    'quiet_start_hour': 22, 'quiet_end_hour': 7, 'cooldown_seconds': 1800}
        transport_tokens = {name: name + '-' + 'r' * 48 for name in ('collector', 'conversation', 'delivery')}
        mcp_tokens = {'openclaw': 'native-openclaw-' + 's' * 48, 'hermes-owner': 'native-hermes-owner-' + 't' * 48}
        tasks = SimpleNamespace(tokens={'assistant': 'broker-assistant-' + 'u' * 48,
                                        'snapshot': 'broker-snapshot-' + 'v' * 48}, projects=set())
        transport = Outbox(self.root / 'transport.sqlite3', settings)
        adapter = communication.serve(transport, transport_tokens, {}, lambda *_: self.fail('MCP must not invoke a model'),
            ('127.0.0.1', 0), tasks=tasks, mcp_tokens=mcp_tokens, assistant_context=bridge_client)
        adapter_thread = threading.Thread(target=adapter.serve_forever, daemon=True)
        adapter_thread.start()

        def rpc(actor, method, params=None):
            request = urllib.request.Request(f'http://127.0.0.1:{adapter.server_address[1]}/mcp',
                data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode(),
                headers={'Authorization': 'Bearer ' + mcp_tokens[actor], 'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream'})
            with urllib.request.urlopen(request, timeout=4) as response:
                return json.loads(response.read())

        try:
            with patch('assistant_context_tools.ORIGIN', f'http://127.0.0.1:{self.server.server_address[1]}'):
                owner_tools = {row['name'] for row in rpc('hermes-owner', 'tools/list')['result']['tools']}
                openclaw_tools = {row['name'] for row in rpc('openclaw', 'tools/list')['result']['tools']}
                context_tools = {PREFIX + row for row in ('search', 'today', 'source', 'decision', 'feedback', 'memory',
                                                         'briefing', 'routine', 'delegate', 'improvements')}
                baseline_tools = {'homecompute_task_submit', 'homecompute_task_status', 'homecompute_task_cancel'}
                self.assertTrue(context_tools <= owner_tools)
                self.assertFalse(context_tools & openclaw_tools)
                self.assertEqual(openclaw_tools, baseline_tools)
                self.assertEqual(owner_tools - context_tools, baseline_tools)
                call = rpc('hermes-owner', 'tools/call', {'name': PREFIX + 'today', 'arguments': {'limit': 10}})
                result = json.loads(call['result']['content'][0]['text'])
                self.assertEqual({row['source'] for row in result['context']}, set(SOURCES))
                denied = rpc('openclaw', 'tools/call', {'name': PREFIX + 'today', 'arguments': {'limit': 10}})
                self.assertEqual(denied['error']['code'], -32602)
        finally:
            adapter.shutdown()
            adapter.server_close()
            adapter_thread.join(timeout=3)
            transport.db.close()


if __name__ == '__main__':
    unittest.main()
