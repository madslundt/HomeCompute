#!/usr/bin/env python3
"""Synthetic restore fencing and deletion replay contracts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from assistant_context_store import ContextStore, Scope
from assistant_context_delegation import DelegationController
from assistant_context_delivery import DeliveryOutbox
from assistant_context_recovery import (RestoreFenceError, SCOPES, assert_restore_ready,
    complete_restore, export_deletion_ledger, stage_restore)
from assistant_context_retrieval import AssistantContextRetrieval, AuthenticatedHarrierClient
from embedding_client import DIMENSIONS

OWNER = Scope('owner', 'personal', 'private')
FAMILY = Scope('family', 'household', 'shared')


def event(revision: int, operation: str = 'upsert', payload: dict | None = None) -> dict:
    return {'schema_version': 1, 'source': 'daythread', 'object_id': 'entry-7', 'revision': revision,
        'event_type': 'daythread.commitment', 'operation': operation,
        'occurred_at': f'2026-11-06T10:00:{revision:02d}Z', 'source_ref': 'daythread:decision/entry-7',
        'payload': payload or ({'text': 'synthetic old deleted commitment'} if operation == 'upsert' else {})}


class FixedEmbeddings:
    def embed(self, texts):
        result = []
        for _ in texts:
            vector = [0.0] * DIMENSIONS
            vector[0] = 1.0
            result.append(vector)
        return result


class FakeRecovery:
    """Verifier stand-in that materializes a private synthetic archived tree."""
    def __init__(self, source_root: Path):
        self.source_root = source_root
        self.SCRATCH = source_root.parent

    def verify(self, archive: Path, target: Path):
        target.mkdir(mode=0o700, parents=True)
        (target / 'state').mkdir(mode=0o700)
        (target / 'state/assistant-context').mkdir(mode=0o700)
        state = target / 'state/assistant-context/state'
        state.mkdir(mode=0o700)
        for name in ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3',
                     'retrieval.sqlite3', 'delegation.sqlite3', 'routines.sqlite3'):
            src = self.source_root / name
            if src.exists():
                shutil.copy2(src, state / name)
        manifest = {'schema_version': 1, 'assistant_context': {'directories': ['assistant-context/state'], 'files': []}}
        (target / 'manifest.json').write_text(json.dumps(manifest))
        (target / 'manifest.json').chmod(0o600)
        return {'sqlite_integrity': 'ok', 'sqlite_databases_checked': 5}


class AssistantContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_state = self.root / 'source-state'
        self.source_state.mkdir(mode=0o700)
        self.store = ContextStore(self.source_state / 'context.sqlite3')
        self.store.ingest(OWNER, event(1))
        self.old_state = self.root / 'old-state'
        self.old_state.mkdir(mode=0o700)
        shutil.copy2(self.source_state / 'context.sqlite3', self.old_state / 'context.sqlite3')
        for name in ('attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3'):
            with closing(sqlite3.connect(self.source_state / name)) as db:
                db.execute('CREATE TABLE fixture(value TEXT)')
                db.commit()
            shutil.copy2(self.source_state / name, self.old_state / name)
        DeliveryOutbox(self.old_state / 'delivery.sqlite3')
        with closing(sqlite3.connect(self.old_state / 'delivery.sqlite3')) as db:
            db.execute('''INSERT INTO delivery_outbox(delivery_id,principal,domain,visibility,decision_id,
                occurrence_key,source,object_id,source_revision,route_name,target,message,message_hash,
                request_hash,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('synthetic-delivery', 'owner', 'personal', 'private', 'synthetic-decision',
                 'synthetic-occurrence', 'daythread', 'entry-7', 1, 'route', 'mattermost',
                 'deleted source text', 'message-hash', 'request-hash', 'claimed', 1,
                 '2026-11-06T11:00:00Z', '2026-11-06T11:00:00Z'))
            db.commit()
        old_store = ContextStore(self.old_state / 'context.sqlite3')
        self.retrieval = AssistantContextRetrieval(old_store, self.old_state / 'retrieval.sqlite3',
            embedding_client=FixedEmbeddings())
        self.retrieval.search(OWNER, 'synthetic old', source_grants=['daythread'])
        self.assertGreater(self._vector_count(self.old_state / 'retrieval.sqlite3'), 0)
        class Offline:
            def call(self, *_args, **_kwargs):
                raise AssertionError('synthetic restore must never reach the native worker')
        delegation = DelegationController(self.old_state / 'delegation.sqlite3',
            json.loads((ROOT / 'config/assistant-delegation.json').read_text()),
            transport=Offline(), maintenance_mode=True)
        with delegation._connect() as db:
            db.execute('''INSERT INTO delegation_requests(request_id,scope_key,origin_thread,task_type,task_text,
                request_digest,budget_seconds,expires_at,state,native_session_key,created_at,updated_at,
                source_refs_json,purge_keys_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('synthetic-request', 'owner/personal/private', 'synthetic-thread', 'analysis',
                 'derived from deleted source', 'digest', 60, 2000000000, 'completed',
                 'agent:assistant-context:hc-synthetic-request', 1, 1,
                 json.dumps([{'source': 'daythread', 'object_id': 'entry-7', 'revision': 1}]), '[]'))
        # A separately current source-controller snapshot records deletion revision 2.
        self.store.ingest(OWNER, event(2, 'delete'))
        self.ledger = export_deletion_ledger(self.store, [OWNER])
        self.ledger_bytes = json.dumps(self.ledger, sort_keys=True, separators=(',', ':')).encode()
        self.ledger_path = self.root / 'trusted-ledger.json'
        self.ledger_path.write_bytes(self.ledger_bytes)
        self.ledger_path.chmod(0o600)
        self.ledger_sha = hashlib.sha256(self.ledger_bytes).hexdigest()
        self.admission_path = self.root / 'source-controller-admission.json'
        admission = {'schema_version': 1, 'issuer': 'source-controller', 'status': 'current',
            'ledger_sha256': self.ledger_sha, 'scopes': ['owner/personal/private'],
            'reconciled_at': '2026-11-06T12:00:00Z'}
        self.admission_bytes = json.dumps(admission, sort_keys=True, separators=(',', ':')).encode()
        self.admission_path.write_bytes(self.admission_bytes)
        self.admission_path.chmod(0o600)
        self.admission_sha = hashlib.sha256(self.admission_bytes).hexdigest()
        self.archive = self.root / 'synthetic.tar'
        self.archive.write_bytes(b'synthetic archive marker')
        self.target = self.root / 'restore-target'

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _vector_count(path: Path) -> int:
        with closing(sqlite3.connect(path)) as db:
            return db.execute('SELECT count(*) FROM vectors').fetchone()[0]

    def _stage(self):
        with patch('assistant_context_recovery._load_recovery', return_value=FakeRecovery(self.old_state)):
            return stage_restore(self.archive, self.target)

    def _complete(self, *, expected_sha256=None, admission_path=None, expected_admission_sha256=None):
        with patch('assistant_context_recovery._load_recovery', return_value=FakeRecovery(self.old_state)):
            return complete_restore(self.target, self.ledger_path,
                expected_sha256=expected_sha256 or self.ledger_sha,
                admission_path=admission_path or self.admission_path,
                expected_admission_sha256=expected_admission_sha256 or self.admission_sha)

    def test_startup_allows_legacy_state_but_refuses_pending_or_malformed_fence(self):
        legacy = self.root / 'legacy'; legacy.mkdir(mode=0o700)
        assert_restore_ready(legacy)
        (legacy / 'restore-fence.json').write_text('{')
        (legacy / 'restore-fence.json').chmod(0o600)
        with self.assertRaises(RestoreFenceError):
            assert_restore_ready(legacy)

    def test_stage_is_pending_then_trusted_replay_scrubs_old_content_and_cache(self):
        self.assertEqual(self._stage()['phase'], 'pending')
        state = self.target / 'state/assistant-context/state'
        with self.assertRaises(RestoreFenceError):
            assert_restore_ready(state)
        result = self._complete()
        self.assertEqual(result['phase'], 'ready')
        assert_restore_ready(state)
        restored = ContextStore(state / 'context.sqlite3')
        self.assertIsNone(restored.get(OWNER, 'daythread', 'entry-7'))
        self.assertEqual(restored.search(OWNER, 'synthetic old deleted commitment'), [])
        self.assertEqual(self._vector_count(state / 'retrieval.sqlite3'), 0)
        with closing(sqlite3.connect(state / 'delegation.sqlite3')) as db:
            row = db.execute('SELECT task_text,result_text,evidence_json,source_refs_json,state FROM delegation_requests '
                             'WHERE request_id=?', ('synthetic-request',)).fetchone()
        self.assertEqual(row[:4], ('', None, None, '[]'))
        self.assertEqual(row[4], 'purge_pending')
        with closing(sqlite3.connect(state / 'delivery.sqlite3')) as db:
            delivery = db.execute('SELECT message,status,reason FROM delivery_outbox WHERE delivery_id=?',
                                  ('synthetic-delivery',)).fetchone()
        self.assertEqual(delivery, (None, 'uncertain', 'source_deleted'))
        # Equal and older source revisions cannot resurrect the archived content.
        self.assertIn(restored.ingest(OWNER, event(1))['status'], {'duplicate', 'stale'})
        self.assertIsNone(restored.get(OWNER, 'daythread', 'entry-7'))

    def test_hash_admission_scope_and_external_location_are_required_and_failure_stays_fenced(self):
        self._stage()
        state = self.target / 'state/assistant-context/state'
        with self.assertRaisesRegex(RestoreFenceError, 'SHA-256'):
            self._complete(expected_sha256='0' * 64)
        bad = self.root / 'stale-admission.json'
        bad.write_text(json.dumps({'schema_version': 1, 'issuer': 'archive', 'status': 'current',
            'ledger_sha256': self.ledger_sha, 'scopes': ['owner/personal/private'],
            'reconciled_at': '2026-11-06T12:00:00Z'}))
        bad.chmod(0o600)
        with self.assertRaisesRegex(RestoreFenceError, 'admission'):
            with patch('assistant_context_recovery._load_recovery', return_value=FakeRecovery(self.old_state)):
                complete_restore(self.target, self.ledger_path, expected_sha256=self.ledger_sha,
                    admission_path=bad, expected_admission_sha256=hashlib.sha256(bad.read_bytes()).hexdigest())
        with self.assertRaises(RestoreFenceError):
            assert_restore_ready(state)

    def test_completion_refuses_a_ledger_that_omits_a_scope_stored_in_the_archive(self):
        archived = ContextStore(self.old_state / 'context.sqlite3')
        archived.ingest(FAMILY, event(1, payload={'text': 'synthetic archived family record'}))
        self._stage()
        with self.assertRaisesRegex(RestoreFenceError, 'omits a scope'):
            self._complete()
        with self.assertRaises(RestoreFenceError):
            assert_restore_ready(self.target / 'state/assistant-context/state')

    def test_ledger_export_has_no_payload_and_marks_freshness_unverified(self):
        value = export_deletion_ledger(self.store, [OWNER, FAMILY])
        encoded = json.dumps(value)
        self.assertEqual(value['freshness'], 'unverified')
        self.assertNotIn('synthetic old deleted commitment', encoded)
        self.assertEqual(set(value['scopes']), {'owner/personal/private', 'family/household/shared'})
        with self.assertRaises(ValueError):
            export_deletion_ledger(self.store, [])

    def test_restore_never_uses_network_transport(self):
        self._stage()
        with patch.object(AuthenticatedHarrierClient, 'embed', side_effect=AssertionError('network forbidden')):
            self._complete()


if __name__ == '__main__':
    unittest.main()
