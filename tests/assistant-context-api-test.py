"""Synthetic HTTP role, source, restart and derived-state deletion acceptance."""
from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_api import Client, ContextService, clients, serve
from assistant_context_store import Scope
from assistant_context_tools import PREFIX, TOOLS, ContextServices, validate

NOW = datetime(2026, 11, 6, 11, tzinfo=timezone.utc)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owner = Scope('owner', 'personal')
        self.family = Scope('family', 'household', 'shared')
        self.collector = Client('collector', 'a' * 48, 'collector', self.owner, frozenset({'aula'}))
        self.assistant = Client('assistant', 'b' * 48, 'assistant', self.owner, frozenset({'aula'}))
        self.other = Client('family', 'c' * 48, 'assistant', self.family, frozenset({'aula'}))
        self.operator = Client('operator', 'd' * 48, 'operator', self.owner, frozenset({'aula'}))
        self.full_assistant = Client('owner-context', 'e' * 48, 'assistant', self.owner, frozenset({'home-assistant', 'daythread', 'aula', 'calendar'}))
        self.policy = json.loads((ROOT / 'config/assistant-attention.json').read_text())
        self.service = ContextService(self.root / 'state', self.policy, clock=lambda: NOW)
        self.server = serve(self.service, [self.collector, self.assistant, self.other, self.operator, self.full_assistant], ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.tmp.cleanup()

    def event(self, **changes):
        return dict({'schema_version': 1, 'source': 'aula', 'object_id': 'post:fixture', 'revision': 1,
                     'event_type': 'aula.posts.snapshot', 'operation': 'upsert',
                     'occurred_at': NOW.isoformat(), 'source_ref': 'aula:post:fixture',
                     'payload': {'title': 'Synthetic early closure', 'text': 'Fixture pickup change'}}, **changes)

    def request(self, path, body, client=None, *, raw=None, headers=None):
        client = client or self.collector
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=3)) as connection:
            merged = {'Authorization': 'Bearer ' + client.token, 'Content-Type': 'application/json'}
            merged.update(headers or {})
            connection.request('POST', path, raw if raw is not None else json.dumps(body), merged)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def tool(self, name, arguments, client=None):
        return self.request('/v1/tools', {'name': PREFIX + name, 'arguments': arguments}, client or self.assistant)

    def ingest_and_review(self):
        status, accepted = self.request('/v1/events', self.event())
        self.assertEqual(status, 200)
        self.assertTrue(accepted['accepted'])
        status, result = self.request('/v1/coverage', {'source': 'aula', 'observed_at': NOW.isoformat(), 'complete': True})
        self.assertEqual(status, 200)
        self.assertEqual(result['reviewed'], 1)
        return self.tool('today', {})[1]['decisions'][0]

    def test_role_and_source_assignment_are_below_payload(self):
        self.assertEqual(self.request('/v1/events', self.event(), self.assistant)[0], 403)
        self.assertEqual(self.tool('today', {}, self.collector)[0], 403)
        self.assertEqual(self.request('/v1/events', self.event(source='calendar'))[0], 400)
        self.assertEqual(self.request('/v1/events', self.event(principal_scope='family'))[0], 400)
        self.assertEqual(self.request('/v1/events', self.event(), headers={'Authorization': 'Bearer bad'})[0], 401)
        self.assertEqual(self.request('/v1/events', self.event(), headers={'Origin': 'https://example.com'})[0], 401)
        self.assertEqual(self.request('/other', {})[0], 404)

    def test_restart_recovers_pending_after_coverage_and_deduplicates_decisions(self):
        self.request('/v1/events', self.event())
        self.assertEqual(self.service.review(self.owner), 0)  # unknown coverage remains pending
        restored = ContextService(self.root / 'state', self.policy, clock=lambda: NOW)
        result = restored.update_coverage(self.collector, {'source': 'aula', 'observed_at': NOW.isoformat(), 'complete': True})
        self.assertEqual(result['reviewed'], 1)
        self.assertEqual(restored.review(self.owner), 0)
        self.assertEqual(self.request('/v1/events', self.event())[1]['status'], 'duplicate')
        self.assertEqual(len(restored.attention.list_decisions(scope=self.owner)), 1)

    def test_cross_scope_read_feedback_and_decision_mutation_are_denied(self):
        row = self.ingest_and_review()
        self.assertEqual(self.tool('search', {'query': 'pickup'}, self.other)[1]['results'], [])
        self.assertIsNone(self.tool('source', {'source': 'aula', 'object_id': 'post:fixture'}, self.other)[1]['source'])
        self.assertEqual(self.tool('decision', {'decision_id': row['decision_id'], 'operation': 'show'}, self.other)[0], 400)
        self.assertEqual(self.tool('feedback', {'decision_id': row['decision_id'], 'kind': 'useful', 'request_id': str(uuid.uuid4())}, self.other)[0], 400)

    def test_delete_erases_source_and_derived_feedback_context(self):
        row = self.ingest_and_review()
        feedback = {'decision_id': row['decision_id'], 'kind': 'correction', 'request_id': str(uuid.uuid4()), 'detail': 'private fixture correction'}
        self.assertEqual(self.tool('feedback', feedback)[0], 200)
        self.assertEqual(self.tool('feedback', feedback)[0], 200)
        self.assertEqual(self.request('/v1/events', self.event(revision=2, operation='delete', payload={}))[0], 200)
        self.assertEqual(self.tool('search', {'query': 'pickup'})[1]['results'], [])
        self.assertNotIn('private fixture correction', json.dumps(self.tool('today', {})[1]))
        self.assertEqual(self.request('/v1/events', self.event())[1]['status'], 'duplicate')
        self.assertEqual(self.tool('search', {'query': 'pickup'})[1]['results'], [])

    def test_memory_proposals_remain_reviewable_and_have_no_approval_tool(self):
        arguments = {'operation': 'propose', 'statement': 'Prefer a concise morning briefing', 'kind': 'preference',
                     'source_ref': 'owner-message:synthetic', 'request_id': str(uuid.uuid4())}
        self.assertEqual(self.tool('memory', arguments)[0], 400)
        status, result = self.tool('memory', arguments, self.full_assistant)
        self.assertEqual(status, 200)
        self.assertEqual(result['memory']['status'], 'candidate')
        self.assertEqual(self.tool('memory', {'operation': 'approve', 'memory_id': 'x'})[0], 400)
        self.assertEqual(self.tool('memory', arguments, self.full_assistant)[1], result)

    def test_json_and_configuration_reject_ambiguous_or_unsafe_values(self):
        self.assertEqual(self.request('/v1/events', {}, raw='{"source":"aula","source":"calendar"}')[0], 400)
        self.assertEqual(self.request('/v1/events', {}, raw='{"payload":NaN}')[0], 400)
        path = self.root / 'clients.json'
        path.write_text(json.dumps({'schema_version': 1, 'clients': []})); path.chmod(0o600)
        with self.assertRaises(ValueError): clients(path)
        path.chmod(0o644)
        with self.assertRaises(ValueError): clients(path)
        with self.assertRaises(ValueError): serve(self.service, [], ('0.0.0.0', 0))

    def test_source_filter_precedes_ranking_and_result_limits(self):
        self.service.store.ingest(self.owner, self.event(source='calendar', object_id='private-other-source'))
        self.service.store.ingest(self.owner, self.event())
        rows = self.service.store.search(self.owner, 'pickup', limit=1, sources={'aula'})
        self.assertEqual([row['source'] for row in rows], ['aula'])
        self.assertEqual([row['source'] for row in self.service.store.list_current(self.owner, limit=1, sources={'aula'})], ['aula'])

    def test_source_delete_scrubs_memories_from_older_version_references(self):
        self.request('/v1/events', self.event(source_ref='aula:post:fixture:revision1'))
        proposal = self.service.store.propose_memory(self.owner, 'Prefer an early briefing', 'preference',
            'aula:post:fixture:revision1', str(uuid.uuid4()))
        self.request('/v1/events', self.event(revision=2, source_ref='aula:post:fixture:revision2'))
        self.request('/v1/events', self.event(revision=3, operation='delete', source_ref='aula:post:fixture:revision3', payload={}))
        row = self.service.store.inspect_memory(self.owner, proposal['memory_id'])
        self.assertEqual(row['status'], 'deleted')
        self.assertNotIn('Prefer an early briefing', json.dumps(row))

    def test_paginated_memory_export_keeps_every_authorized_record(self):
        expected = set()
        for index in range(5):
            row = self.service.store.propose_memory(self.owner, 'Prefer briefing item ' + str(index),
                'preference', 'owner-message:fixture:' + str(index), str(uuid.uuid4()))
            expected.add(row['memory_id'])
        collected, cursor = set(), None
        for _ in range(5):
            args = {'operation': 'export', 'limit': 2}
            if cursor:
                args['cursor'] = cursor
            status, result = self.tool('memory', args, self.full_assistant)
            self.assertEqual(status, 200)
            self.assertLessEqual(len(result['memories']['records']), 2)
            collected.update(row['memory_id'] for row in result['memories']['records'])
            cursor = result['memories']['next_cursor']
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(collected, expected)

    def test_large_context_is_bounded_without_losing_source_identity(self):
        for index in range(20):
            self.service.store.ingest(self.owner, self.event(object_id='post:' + str(index),
                payload={'text': 'afhentning ' * 600}))
        status, result = self.tool('today', {'limit': 20})
        self.assertEqual(status, 200)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['context'])
        self.assertLess(len(json.dumps(result).encode()), 131072)
        self.assertIn('source_ref', result['context'][0])

    def test_systemd_read_only_credential_projection_is_accepted(self):
        path = self.root / 'loaded-clients'
        path.write_text(json.dumps({'schema_version': 1, 'clients': [{
            'name': 'fixture-owner', 'token': self.assistant.token, 'role': 'assistant',
            'principal_scope': 'owner', 'data_domain': 'personal', 'visibility': 'private', 'sources': ['aula'],
        }]}))
        path.chmod(0o400)
        self.assertEqual(clients(path)[0].scope, self.owner)


if __name__ == '__main__':
    unittest.main()
