#!/usr/bin/env python3
"""Synthetic restore fencing and deletion replay contracts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from contextlib import closing
from pathlib import Path
import platform
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
    admit_empty_synthetic_ledger, complete_restore, export_deletion_ledger,
    stage_restore, stage_verified_extracted, write_deletion_ledger)
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

    def _empty_admission_fixture(self):
        case_root = self.root / ('admission-case-' + str(len(list(self.root.glob('admission-case-*')))))
        case_root.mkdir(mode=0o700)
        live = case_root / 'live-context'; live.mkdir(mode=0o700)
        live_store = ContextStore(live / 'context.sqlite3')
        target = case_root / 'admission-target'; target.mkdir(mode=0o700)
        archived_state = target / 'state/assistant-context/state'
        archived_state.mkdir(mode=0o700, parents=True)
        shutil.copy2(live / 'context.sqlite3', archived_state / 'context.sqlite3')
        for name in ('attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3', 'retrieval.sqlite3', 'routines.sqlite3'):
            with closing(sqlite3.connect(archived_state / name)) as db:
                db.commit()
            (archived_state / name).chmod(0o600)
        fence = archived_state / 'restore-fence.json'
        fence.write_text(json.dumps({'schema_version': 1, 'phase': 'pending',
            'created_at': '2026-11-06T12:00:00Z', 'reason': 'trusted deletion replay required'},
            sort_keys=True, separators=(',', ':')))
        fence.chmod(0o600)
        ledger_path = case_root / 'current-ledger.json'
        ledger_sha = write_deletion_ledger(live_store, [OWNER, FAMILY], ledger_path)
        output_root = case_root / 'run'; output_root.mkdir(mode=0o700)
        output = output_root / 'assistant-context-admission-synthetic.json'
        return live, live_store, target, ledger_path, ledger_sha, output_root, case_root, output

    def _admit_synthetic(self, fixture):
        live, _store, target, ledger, digest, output_root, scratch_root, output = fixture
        return admit_empty_synthetic_ledger(target, ledger, expected_sha256=digest,
            output=output, live_state=live, scratch_root=scratch_root, output_root=output_root)

    def _verified_extraction_fixture(self):
        index = len(list(self.root.glob('verified-case-*')))
        case_root = self.root / ('verified-case-' + str(index))
        case_root.mkdir(mode=0o700)
        target = self.root / ('nas-archive-application-' + str(index)); target.mkdir(mode=0o700)
        state_root = target / 'state/assistant-context/state'; state_root.mkdir(mode=0o700, parents=True)
        ContextStore(state_root / 'context.sqlite3')
        for name in ('attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3'):
            with closing(sqlite3.connect(state_root / name)) as db:
                db.commit()
            (state_root / name).chmod(0o600)
        credential = target / 'state/assistant-context/clients.json'; credential.write_text('{}'); credential.chmod(0o600)
        manifest = {'schema_version': 1, 'assistant_context': {
            'directories': ['assistant-context/state'], 'files': ['assistant-context/clients.json'],
            'sqlite_databases': [f'assistant-context/state/{name}' for name in
                ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3')]}}
        manifest_path = target / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, sort_keys=True)); manifest_path.chmod(0o600)
        receipt_root = case_root / 'receipts'; receipt_root.mkdir(mode=0o700)
        snapshot = 'a' * 64; archive = 'homecompute-applications-20261106T120000.000000Z.tar'; revision = 'b' * 40
        (receipt_root / 'last-backup.json').write_text(json.dumps({'snapshot': snapshot,
            'scope': '/srv/backups/agent-recovery', 'time': '2026-11-06T12:00:00Z'}))
        (receipt_root / 'last-application-verification.json').write_text(json.dumps({
            'snapshot': snapshot, 'archive': archive,
            'verification': {'sqlite_integrity': 'ok', 'sqlite_databases_checked': 39,
                'gzip_dumps_checked': 4, 'qcow2_disks_checked': 3, 'agents_seed_verified': True},
            'restore_directory': str(target.resolve()), 'restored_archive_reused': True,
            'source_revision': revision, 'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}))
        for name in ('last-backup.json', 'last-application-verification.json'):
            (receipt_root / name).chmod(0o600)
        return case_root, target, receipt_root, snapshot, archive, revision

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

    def test_synthetic_admission_requires_exact_empty_live_and_archive_snapshots(self):
        fixture = self._empty_admission_fixture()
        result = self._admit_synthetic(fixture)
        self.assertEqual(result['phase'], 'admitted-synthetic-empty')
        self.assertEqual(result['source_events'], 0)
        self.assertEqual(result['memories'], 0)
        admission_path = fixture[-1]
        admission = json.loads(admission_path.read_bytes())
        self.assertEqual(admission['issuer'], 'source-controller')
        self.assertEqual(admission['status'], 'current')
        self.assertEqual(admission['scopes'], sorted(SCOPES))
        self.assertEqual(admission['ledger_sha256'], fixture[4])
        self.assertEqual(admission_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RestoreFenceError, 'new admission'):
            self._admit_synthetic(fixture)

    def test_synthetic_admission_refuses_live_or_archived_events(self):
        live_fixture = self._empty_admission_fixture()
        live_fixture[1].ingest(OWNER, event(1))
        with self.assertRaisesRegex(RestoreFenceError, 'live ContextStore'):
            self._admit_synthetic(live_fixture)
        self.assertFalse(live_fixture[-1].exists())

        archive_fixture = self._empty_admission_fixture()
        archive_store = ContextStore(archive_fixture[2] / 'state/assistant-context/state/context.sqlite3')
        archive_store.ingest(FAMILY, event(1))
        with self.assertRaisesRegex(RestoreFenceError, 'archive ContextStore'):
            self._admit_synthetic(archive_fixture)
        self.assertFalse(archive_fixture[-1].exists())

    @unittest.skipUnless(platform.system() == 'Linux' and os.geteuid() == 0,
        'requires Linux root to verify a different-UID live SQLite snapshot')
    def test_live_wal_snapshot_preserves_different_uid_sources_and_reads_committed_wal(self):
        fixture = self._empty_admission_fixture()
        live, _store, _target, _ledger, _digest, _output_root, _scratch, output = fixture
        database = live / 'context.sqlite3'
        db = sqlite3.connect(database)
        self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        db.execute('''INSERT INTO context_events(
            principal_scope,data_domain,visibility,source,object_id,revision,event_type,
            operation,occurred_at,ingested_at,source_ref,payload_json,fingerprint,status
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('owner', 'personal', 'private', 'daythread', 'wal-only-event', 1,
             'daythread.commitment', 'upsert', '2026-11-06T10:00:00Z',
             '2026-11-06T10:00:00Z', 'daythread:decision/wal-only-event', '{}',
             'a' * 64, 'accepted'))
        db.commit()
        sidecars = [Path(str(database) + suffix) for suffix in ('-wal', '-shm')]
        self.assertTrue(all(path.is_file() for path in sidecars))

        for path in [live, database, *sidecars]:
            os.chown(path, 991, 991)
            os.chmod(path, 0o700 if path == live else 0o600)
        before = {path: (path.read_bytes() if path.is_file() else None,
            path.lstat().st_dev, path.lstat().st_ino, path.lstat().st_uid,
            path.lstat().st_gid, path.lstat().st_mode, path.lstat().st_size,
            path.lstat().st_mtime_ns, path.lstat().st_ctime_ns)
            for path in [live, database, *sidecars]}
        try:
            with self.assertRaisesRegex(RestoreFenceError, 'live ContextStore is not synthetic-empty'):
                self._admit_synthetic(fixture)
            self.assertFalse(output.exists())
            for path, expected in before.items():
                current = path.lstat()
                actual = (path.read_bytes() if path.is_file() else None,
                    current.st_dev, current.st_ino, current.st_uid, current.st_gid,
                    current.st_mode, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                self.assertEqual(actual, expected, str(path))
            self.assertEqual(database.stat().st_uid, 991)
            self.assertEqual(Path(str(database) + '-wal').stat().st_uid, 991)
        finally:
            db.close()

    def test_synthetic_admission_refuses_pruned_source_tombstone_floors(self):
        fixture = self._empty_admission_fixture()
        live, store, target, ledger_path, _old_sha, output_root, scratch_root, output = fixture
        with closing(sqlite3.connect(live / 'context.sqlite3')) as db:
            db.execute('INSERT INTO context_source_tombstones VALUES(?,?,?,?,?,?)',
                ('owner', 'personal', 'private', 'daythread', 'synthetic-pruned-item', 1))
            db.commit()
        ledger_value = export_deletion_ledger(store, [OWNER, FAMILY])
        ledger_bytes = json.dumps(ledger_value, sort_keys=True, separators=(',', ':')).encode()
        ledger_path.write_bytes(ledger_bytes); ledger_path.chmod(0o600)
        with self.assertRaisesRegex(RestoreFenceError, 'live ContextStore is not synthetic-empty'):
            admit_empty_synthetic_ledger(target, ledger_path,
                expected_sha256=hashlib.sha256(ledger_bytes).hexdigest(), output=output,
                live_state=live, scratch_root=scratch_root, output_root=output_root)
        self.assertFalse(output.exists())

    def test_synthetic_admission_refuses_incomplete_or_nonmatching_ledger(self):
        fixture = self._empty_admission_fixture()
        live, _store, _target, ledger_path, _sha, output_root, scratch_root, output = fixture
        partial = export_deletion_ledger(_store, [OWNER])
        partial_bytes = json.dumps(partial, sort_keys=True, separators=(',', ':')).encode()
        ledger_path.write_bytes(partial_bytes); ledger_path.chmod(0o600)
        with self.assertRaisesRegex(RestoreFenceError, 'every enabled context scope'):
            admit_empty_synthetic_ledger(fixture[2], ledger_path,
                expected_sha256=hashlib.sha256(partial_bytes).hexdigest(), output=output,
                live_state=live, scratch_root=scratch_root, output_root=output_root)

        full = export_deletion_ledger(_store, [OWNER, FAMILY])
        full['scopes']['owner/personal/private']['source_tombstones'].append(
            {'source': 'daythread', 'object_id': 'synthetic-tombstone', 'min_revision': 1})
        changed = json.dumps(full, sort_keys=True, separators=(',', ':')).encode()
        ledger_path.write_bytes(changed)
        with self.assertRaisesRegex(RestoreFenceError, 'differs from the current live'):
            admit_empty_synthetic_ledger(fixture[2], ledger_path,
                expected_sha256=hashlib.sha256(changed).hexdigest(), output=output,
                live_state=live, scratch_root=scratch_root, output_root=output_root)
        self.assertFalse(output.exists())

    def test_verified_extraction_stage_uses_matching_nas_receipt_without_reextracting(self):
        _case, target, receipts, snapshot, archive, revision = self._verified_extraction_fixture()
        result = stage_verified_extracted(target, snapshot=snapshot, archive=archive,
            source_revision=revision, receipt_root=receipts, scratch_root=self.root)
        self.assertEqual(result['phase'], 'pending')
        self.assertEqual(result['snapshot'], snapshot)
        fence = target / 'state/assistant-context/state/restore-fence.json'
        value = json.loads(fence.read_bytes())
        self.assertEqual(value['phase'], 'pending')
        self.assertEqual(value['source_revision'], revision)
        self.assertEqual(fence.stat().st_mode & 0o777, 0o600)

    def test_verified_extraction_stage_refuses_stale_or_changed_receipt(self):
        _case, target, receipts, snapshot, archive, revision = self._verified_extraction_fixture()
        proof_path = receipts / 'last-application-verification.json'
        proof = json.loads(proof_path.read_bytes())
        proof['manifest_sha256'] = '0' * 64
        proof_path.write_text(json.dumps(proof)); proof_path.chmod(0o600)
        with self.assertRaisesRegex(RestoreFenceError, 'manifest changed'):
            stage_verified_extracted(target, snapshot=snapshot, archive=archive,
                source_revision=revision, receipt_root=receipts, scratch_root=self.root)
        self.assertFalse((target / 'state/assistant-context/state/restore-fence.json').exists())

    def test_restore_never_uses_network_transport(self):
        self._stage()
        with patch.object(AuthenticatedHarrierClient, 'embed', side_effect=AssertionError('network forbidden')):
            self._complete()


if __name__ == '__main__':
    unittest.main()
