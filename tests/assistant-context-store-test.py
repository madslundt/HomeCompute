#!/usr/bin/env python3
"""Synthetic contract tests for the private assistant context journal."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('assistant_context_store', ROOT / 'scripts/assistant_context_store.py')
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
ContextStore, Scope = MODULE.ContextStore, MODULE.Scope
OWNER = Scope('owner', 'personal')
FAMILY = Scope('family', 'household', 'shared')


def event(revision: int, *, operation: str = 'upsert', payload: dict | None = None,
          source: str = 'daythread', object_id: str = 'entry-1') -> dict:
    return {
        'schema_version': 1,
        'source': source,
        'object_id': object_id,
        'revision': revision,
        'event_type': 'summary.updated' if operation == 'upsert' else 'summary.deleted',
        'operation': operation,
        'occurred_at': f'2026-11-06T10:00:{revision % 60:02d}Z',
        'source_ref': f'daythread://entry/{object_id}',
        'payload': {'text': f'synthetic note revision {revision}'} if payload is None and operation == 'upsert'
                   else payload or {},
    }


class ContextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'state'
        self.root.mkdir(mode=0o700)
        self.database = self.root / 'context.sqlite3'
        self.store = ContextStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_restart_projection_and_source_linked_search(self) -> None:
        receipt = self.store.ingest(OWNER, event(1, payload={'title': 'Parent meeting', 'text': 'Discuss school pickup'}))
        self.assertEqual(receipt['status'], 'accepted')
        self.assertNotIn('payload', receipt)
        restarted = ContextStore(self.database)
        result = restarted.get(OWNER, 'daythread', 'entry-1')
        self.assertEqual(result['payload']['text'], 'Discuss school pickup')
        self.assertTrue(result['ingested_at'].endswith('Z'))
        self.assertEqual(restarted.search(OWNER, 'school pickup')[0]['source_ref'], 'daythread://entry/entry-1')
        self.assertEqual(restarted.list_current(OWNER)[0]['object_id'], 'entry-1')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_idempotent_revision_conflict_and_stale_arrival(self) -> None:
        first = event(3)
        receipt = self.store.ingest(OWNER, first)
        duplicate = self.store.ingest(OWNER, first)
        self.assertEqual(duplicate['status'], 'duplicate')
        self.assertEqual(duplicate['event_id'], receipt['event_id'])
        with self.assertRaisesRegex(ValueError, 'Conflicting content'):
            self.store.ingest(OWNER, event(3, payload={'text': 'changed at same revision'}))
        stale = self.store.ingest(OWNER, event(2))
        self.assertEqual(stale['status'], 'stale')
        self.assertEqual(self.store.get(OWNER, 'daythread', 'entry-1')['revision'], 3)

    def test_delete_scrubs_online_content_and_stale_replay_cannot_resurrect(self) -> None:
        secret_fixture = 'synthetic-delete-me-7c193'
        self.store.ingest(OWNER, event(4, payload={'text': secret_fixture}))
        deleted = self.store.ingest(OWNER, event(5, operation='delete'))
        self.assertEqual(deleted['status'], 'accepted')
        self.assertIsNone(self.store.get(OWNER, 'daythread', 'entry-1'))
        self.assertEqual(self.store.search(OWNER, secret_fixture), [])
        stale = event(4, payload={'text': 'late replay payload'})
        # Existing revision has a content fingerprint, so exact replay is duplicate.
        self.assertEqual(self.store.ingest(OWNER, event(4, payload={'text': secret_fixture}))['status'], 'duplicate')
        # A never-seen older revision after the tombstone is recorded without content.
        self.assertEqual(self.store.ingest(OWNER, event(3, payload={'text': 'late replay payload'}))['status'], 'stale')
        with sqlite3.connect(self.database) as db:
            serialized = json.dumps(db.execute('SELECT payload_json FROM context_events').fetchall())
        self.assertNotIn(secret_fixture, serialized)
        self.assertNotIn('late replay payload', serialized)
        # A newer source revision explicitly recreates the object.
        self.assertEqual(self.store.ingest(OWNER, event(6, payload={'text': 'recreated'}))['status'], 'accepted')
        self.assertEqual(self.store.get(OWNER, 'daythread', 'entry-1')['payload']['text'], 'recreated')

    def test_scope_filtering_and_allowed_scope_pairs(self) -> None:
        self.store.ingest(OWNER, event(1, payload={'text': 'owner only'}))
        self.store.ingest(FAMILY, event(1, payload={'text': 'shared household'}))
        self.assertEqual(self.store.search(OWNER, 'owner')[0]['payload']['text'], 'owner only')
        self.assertEqual(self.store.search(FAMILY, 'household')[0]['payload']['text'], 'shared household')
        self.assertEqual(self.store.search(FAMILY, 'owner'), [])
        with self.assertRaises(ValueError):
            Scope('partner', 'personal')
        with self.assertRaises(ValueError):
            Scope('owner', 'household', 'shared')

    def test_invalid_envelopes_payloads_and_queries_are_rejected(self) -> None:
        malformed = [
            {**event(1), 'scope': {'principal_scope': 'family'}},
            {**event(1), 'occurred_at': '2026-11-06T10:00:00'},
            {**event(1), 'revision': True},
            {**event(1), 'source': []},
            {**event(1), 'operation': {}},
            {**event(1), 'payload': {'deep': {'a': {'b': {'c': {'d': {'e': {'f': {'g': {'h': 'too deep'}}}}}}}}}},
            {**event(1), 'payload': {'nan': float('nan')}},
            {**event(1), 'payload': {'text': 'x' * 8193}},
            {**event(1), 'operation': 'delete', 'payload': {'text': 'must not retain'}},
        ]
        for item in malformed:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    self.store.ingest(OWNER, item)
        with self.assertRaises(ValueError):
            self.store.search(OWNER, '   ')
        with self.assertRaises(ValueError):
            self.store.list_current(OWNER, True)

    def test_private_file_and_directory_modes_are_enforced(self) -> None:
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        public_dir = Path(self.temp.name) / 'public'
        public_dir.mkdir(mode=0o755)
        os.chmod(public_dir, 0o755)
        with self.assertRaisesRegex(ValueError, 'Private context store directory'):
            ContextStore(public_dir / 'bad.sqlite3')
        public_db = self.root / 'public.sqlite3'
        public_db.touch(mode=0o644)
        os.chmod(public_db, 0o644)
        with self.assertRaisesRegex(ValueError, 'Private context database file'):
            ContextStore(public_db)

    def test_concurrent_duplicate_ingestion_is_safe(self) -> None:
        sample = event(9)
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda _: ContextStore(self.database).ingest(OWNER, sample), range(16)))
        self.assertEqual({receipt['event_id'] for receipt in receipts}, {receipts[0]['event_id']})
        self.assertEqual(sum(receipt['status'] == 'accepted' for receipt in receipts), 1)
        self.assertEqual(sum(receipt['status'] == 'duplicate' for receipt in receipts), 15)

    def test_pending_admission_ack_and_supersession_are_transactional(self) -> None:
        first = self.store.ingest(OWNER, event(1))
        self.assertEqual(self.store.pending(OWNER)[0]['event_id'], first['event_id'])
        self.assertEqual(self.store.pending(FAMILY), [])
        self.assertTrue(self.store.ack(OWNER, first['event_id']))
        self.assertFalse(self.store.ack(OWNER, first['event_id']))
        # An idempotent retry does not reopen an already acknowledged event.
        self.assertEqual(self.store.ingest(OWNER, event(1))['status'], 'duplicate')
        self.assertEqual(self.store.pending(OWNER), [])
        second = self.store.ingest(OWNER, event(2))
        self.assertEqual([row['event_id'] for row in self.store.pending(OWNER)], [second['event_id']])
        self.store.ingest(OWNER, event(3, operation='delete'))
        self.assertEqual(self.store.pending(OWNER), [])

    def test_memory_candidate_review_expiry_deletion_export_and_tombstone(self) -> None:
        request_id = str(uuid.uuid4())
        candidate = self.store.propose_memory(OWNER, 'Prefers concise planning notes', 'preference',
                                              'daythread://entry/preferences-v1', request_id)
        self.assertEqual(candidate['status'], 'candidate')
        self.assertEqual(candidate['provenance'], 'inferred')
        self.assertEqual(candidate['sensitivity'], 'ordinary')
        self.assertIsNone(candidate['last_confirmed_at'])
        self.assertEqual(self.store.propose_memory(OWNER, 'Prefers concise planning notes', 'preference',
                                                   'daythread://entry/preferences-v1', request_id)['memory_id'],
                         candidate['memory_id'])
        self.assertEqual(self.store.inspect_memory(OWNER, candidate['memory_id']), candidate)
        active = self.store.confirm_memory(OWNER, candidate['memory_id'], 1, str(uuid.uuid4()))
        self.assertEqual(active['status'], 'active')
        self.assertIsNotNone(active['last_confirmed_at'])
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            self.store.update_memory(OWNER, candidate['memory_id'], 'expire', 1, str(uuid.uuid4()))
        expired = self.store.update_memory(OWNER, candidate['memory_id'], 'expire', 2, str(uuid.uuid4()))
        self.assertEqual(expired['status'], 'expired')
        exported = self.store.export_memories(OWNER)
        self.assertEqual(exported['records'][0]['memory_id'], candidate['memory_id'])

        deletable = self.store.propose_memory(OWNER, 'Likes calendar summaries', 'preference',
                                              'calendar://preference-source-v1', str(uuid.uuid4()))
        deleted = self.store.update_memory(OWNER, deletable['memory_id'], 'delete', 1, str(uuid.uuid4()))
        self.assertEqual(deleted['status'], 'deleted')
        self.assertIsNone(deleted['statement'])
        self.assertEqual(deleted['source_refs'], [])
        with self.assertRaisesRegex(ValueError, 'tombstoned'):
            self.store.propose_memory(OWNER, 'Likes calendar summaries', 'preference',
                                      'calendar://preference-source-v1', str(uuid.uuid4()))
        self.assertEqual(self.store.inspect_memory(OWNER, deleted['memory_id'])['status'], 'deleted')
        with self.assertRaises(ValueError):
            self.store.propose_memory(FAMILY, 'Shared?', 'preference', 'calendar://shared', str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, 'Sensitive or restricted'):
            self.store.propose_memory(OWNER, 'My medication reminder', 'constraint',
                                      'daythread://sensitive-v1', str(uuid.uuid4()))

    def test_memory_correction_supersedes_and_delete_scrubs_versions(self) -> None:
        original = self.store.propose_memory(OWNER, 'Prefers weekly plans', 'preference',
                                             'daythread://planning-v1', str(uuid.uuid4()))
        corrected = self.store.correct_memory(OWNER, original['memory_id'], 'Prefers monthly plans',
                                              1, str(uuid.uuid4()))
        self.assertEqual(corrected['status'], 'active')
        self.assertEqual(corrected['provenance'], 'explicit')
        self.assertEqual(corrected['supersedes'], original['memory_id'])
        self.assertEqual(self.store.inspect_memory(OWNER, original['memory_id'])['status'], 'corrected')
        deleted = self.store.update_memory(OWNER, corrected['memory_id'], 'delete', 1, str(uuid.uuid4()))
        self.assertEqual(deleted['status'], 'deleted')
        self.assertEqual(self.store.inspect_memory(OWNER, original['memory_id'])['statement'], None)
        self.assertEqual(self.store.inspect_memory(OWNER, original['memory_id'])['source_refs'], [])

    def test_source_delete_atomically_scrubs_derived_memories(self) -> None:
        source_ref = 'daythread://entry/source-delete-v1'
        self.store.ingest(OWNER, event(1, source='daythread', object_id='source-delete',
                                       payload={'text': 'synthetic sensitive-looking phrase without real data'}))
        original = self.store.propose_memory(OWNER, 'Prefers weekly notes', 'preference',
                                             source_ref, str(uuid.uuid4()))
        corrected = self.store.correct_memory(OWNER, original['memory_id'], 'Prefers monthly notes',
                                              1, str(uuid.uuid4()))
        deletion = event(2, operation='delete', source='daythread', object_id='source-delete')
        deletion['source_ref'] = source_ref
        self.assertEqual(self.store.ingest(OWNER, deletion)['status'], 'accepted')
        self.assertEqual(self.store.inspect_memory(OWNER, original['memory_id'])['status'], 'deleted')
        self.assertEqual(self.store.inspect_memory(OWNER, corrected['memory_id'])['status'], 'deleted')
        self.assertEqual(self.store.inspect_memory(OWNER, corrected['memory_id'])['statement'], None)
        with self.assertRaisesRegex(ValueError, 'tombstoned'):
            self.store.propose_memory(OWNER, 'Prefers weekly notes', 'preference', source_ref, str(uuid.uuid4()))


if __name__ == '__main__':
    unittest.main()
