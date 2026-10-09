"""Offline controlled-action boundaries and durable synthetic lifecycle."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import sqlite3
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/codex-worker'))
from actions import ActionLedger, SyntheticExecutor, validate_policies
import actions

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()


class ActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.sqlite3'
        self.now = NOW
        self.policies = json.loads((ROOT / 'config/system-actions.json').read_text())
        self.monitoring = json.loads((ROOT / 'config/system-monitoring.json').read_text())
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.ledger.seed_synthetic('synthetic-core', healthy=False, version=1)

    def tearDown(self):
        self.ledger.db.close()
        self.temp.cleanup()

    def evidence(self, *, age=0, status='degraded', target='synthetic-core', incident='episode-1', gates=None):
        stamp = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')
        observation = {'system_id': target, 'check_id': 'synthetic.health', 'stable_key': target + ':synthetic.health',
                       'category': 'health', 'status': status, 'severity': 'warning' if status == 'degraded' else 'info', 'evidence': {'state': 'unhealthy'},
                       'observed_at': stamp(self.now - age), 'expires_at': stamp(self.now + 300 - age),
                       'action': 'review_only', 'approval_required': True, 'physical_device_actions': False}
        return self.ledger.ingest_evidence({'observation': observation, 'incident_key': incident, 'gates': gates or {}})['id']

    def propose(self, **kwargs):
        eid = kwargs.pop('evidence_id', None) or self.evidence()
        return self.ledger.propose({'action_id': 'synthetic.recover', 'target': 'synthetic-core',
                                    'incident_key': 'episode-1', 'evidence_id': eid, **kwargs})

    def approve(self, action):
        review = self.ledger.review(action['id'])
        return self.ledger.approve(action['id'], review['approval_sha256'])

    def test_complete_loop_and_durable_deduplication(self):
        action = self.propose(); self.approve(action)
        result = self.ledger.execute(action['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(self.ledger.synthetic_state('synthetic-core')['healthy'])
        self.assertEqual(self.ledger.audit(action['id'])[-1]['state'], 'completed')
        self.ledger.db.close()
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.assertEqual(self.propose()['id'], action['id'])
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])

    def test_approval_is_required_and_digest_bound(self):
        action = self.propose()
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])
        with self.assertRaises(ValueError): self.ledger.approve(action['id'], '0' * 64)
        self.assertEqual(self.ledger.get(action['id'])['state'], 'pending')

    def test_exact_ids_targets_and_request_shape(self):
        for changes in ({'target': 'home-core'}, {'action_id': 'sudo.reboot'}, {'argv': ['reboot']},
                        {'url': 'https://example.com'}, {'evidence': {'state': 'unhealthy'}}):
            with self.assertRaises(ValueError): self.propose(**changes)

    def test_disabled_production_stays_disabled(self):
        for policy in self.policies['actions']:
            if policy['environment'] == 'production':
                with self.assertRaises(ValueError):
                    self.ledger.propose({'action_id': policy['id'], 'target': policy['target'],
                                         'incident_key': 'episode-1', 'evidence_id': self.evidence()})
                hostile = copy.deepcopy(self.policies)
                next(x for x in hostile['actions'] if x['id'] == policy['id'])['enabled'] = True
                with self.assertRaises(ValueError): validate_policies(hostile, self.monitoring)

    def test_stale_future_and_unknown_evidence_fail_closed(self):
        for kwargs in ({'age': 301}, {'age': -10}, {'status': 'unknown'}, {'status': 'healthy'}):
            with self.assertRaises(ValueError): self.propose(evidence_id=self.evidence(**kwargs))

    def test_expiration_after_approval_prevents_effect(self):
        action = self.propose(); self.approve(action); self.now += 301
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])
        self.assertFalse(self.ledger.synthetic_state('synthetic-core')['healthy'])

    def test_policy_change_invalidates_approval(self):
        action = self.propose(); self.approve(action)
        self.ledger.policies['synthetic.recover']['cooldown_seconds'] += 1
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])
        self.assertFalse(self.ledger.synthetic_state('synthetic-core')['healthy'])

    def test_window_is_timezone_aware_and_checked_at_execution(self):
        self.ledger.policies['synthetic.recover']['maintenance_window'] = {
            'timezone': 'Europe/Copenhagen', 'weekdays': [4], 'start': '14:00', 'end': '14:30'}
        action = self.propose(); self.approve(action); self.now += 1800
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])

    def test_cooldown_target_lock_and_max_attempts(self):
        action = self.propose(); self.approve(action); self.ledger.execute(action['id'])
        other = self.propose(evidence_id=self.evidence(incident='episode-2'), incident_key='episode-2')
        self.approve(other)
        with self.assertRaises(ValueError): self.ledger.execute(other['id'])
        self.now += 61
        self.ledger.seed_synthetic('synthetic-core', healthy=False, version=1)
        retry = self.propose(evidence_id=self.evidence())
        self.assertEqual(retry['id'], action['id'])
        with self.assertRaises(ValueError): self.approve(retry)
        with self.assertRaises(ValueError): self.ledger.execute(retry['id'])

    def test_latest_observation_revokes_old_approval_even_at_same_timestamp(self):
        action = self.propose(); self.approve(action)
        self.evidence(status='healthy')
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])
        self.assertFalse(self.ledger.synthetic_state('synthetic-core')['healthy'])

    def test_operator_refresh_requires_a_new_digest_and_never_retries(self):
        action = self.propose(); self.approve(action)
        old = self.ledger.review(action['id'])['approval_sha256']
        self.now += 1
        fresh = self.evidence()
        self.assertEqual(self.ledger.refresh(action['id'], fresh)['state'], 'pending')
        with self.assertRaises(ValueError): self.ledger.approve(action['id'], old)
        self.approve(action); self.ledger.execute(action['id'])
        with self.assertRaises(ValueError): self.ledger.refresh(action['id'], fresh)

    def test_refresh_preserves_approval_bindings_and_original_policy_in_private_audit(self):
        action = self.propose()
        original = self.ledger.review(action['id'])
        self.approve(action)
        approved_at = self.now
        self.now += 1
        fresh = self.evidence()
        self.ledger.refresh(action['id'], fresh)
        self.ledger.policies['synthetic.recover']['cooldown_seconds'] += 1
        review = self.ledger.review(action['id'])
        self.assertEqual(review['policy'], original['policy'])
        self.assertNotEqual(review['policy'], self.ledger.policies['synthetic.recover'])
        approved = next(event['metadata'] for event in review['audit'] if event['state'] == 'approved')
        self.assertEqual(approved['approval_sha256'], original['approval_sha256'])
        self.assertEqual(approved['evidence_id'], action['evidence_id'])
        self.assertEqual(approved['evidence_sha256'], actions.digest(original['evidence']))
        self.assertEqual(approved['policy_sha256'], action['policy_sha256'])
        self.assertEqual(approved['approved_at'], approved_at)
        self.assertIsNone(approved['started_at'])
        refreshed = review['audit'][-1]['metadata']
        self.assertEqual(refreshed['evidence_id'], fresh)
        self.assertIsNone(refreshed['approval_sha256'])
        self.assertIsNone(refreshed['approved_at'])
        for public in (self.ledger.get(action['id']), self.ledger.list_actions()[0]):
            self.assertFalse({'snapshot', 'policy_json', 'policy', 'audit', 'evidence'} & set(public))
        self.ledger.db.close()
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.assertEqual(self.ledger.review(action['id'])['audit'], review['audit'])

    def test_old_audit_schema_fails_closed_without_resetting_existing_rows(self):
        old_path = Path(self.temp.name) / 'old.sqlite3'
        db = sqlite3.connect(old_path)
        db.execute('CREATE TABLE action_events(seq INTEGER PRIMARY KEY, action_id TEXT, state TEXT, at REAL)')
        db.execute("INSERT INTO action_events VALUES (1,'preserve','approved',1)")
        db.commit(); db.close()
        with self.assertRaisesRegex(ValueError, 'operator migration'):
            ActionLedger(old_path, self.policies, self.monitoring)
        db = sqlite3.connect(old_path)
        self.assertEqual(db.execute('SELECT state FROM action_events').fetchone()[0], 'approved')
        db.close()

    def test_update_availability_does_not_supply_qualification_or_approval(self):
        self.ledger.seed_synthetic('synthetic-spark', healthy=True, version=5)
        stamp = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        expires = datetime.fromtimestamp(self.now + 300, timezone.utc).isoformat()
        body = {'observation': {'system_id': 'synthetic-spark', 'check_id': 'synthetic.updates',
                'stable_key': 'synthetic-spark:synthetic.updates', 'category': 'updates', 'status': 'degraded',
                'severity': 'warning', 'evidence': {'candidate_count': 1}, 'observed_at': stamp, 'expires_at': expires,
                'action': 'review_only', 'approval_required': True, 'physical_device_actions': False},
                'incident_key': 'update-episode', 'gates': {}}
        request = {'action_id': 'synthetic.update', 'target': 'synthetic-spark', 'incident_key': 'update-episode',
                   'evidence_id': self.ledger.ingest_evidence(body)['id']}
        with self.assertRaises(ValueError): self.ledger.propose(request)
        body['gates'] = {gate: True for gate in self.ledger.policies['synthetic.update']['gates']}
        request['evidence_id'] = self.ledger.ingest_evidence(body)['id']
        action = self.ledger.propose(request)
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])
        self.approve(action)
        self.assertEqual(self.ledger.execute(action['id'])['state'], 'completed')
        self.assertEqual(self.ledger.synthetic_state('synthetic-spark'), {'healthy': True, 'version': 6})

    def test_concurrent_thread_claim_is_serialized_and_target_bounded(self):
        other = self.ledger
        started, release = threading.Event(), threading.Event()
        original = SyntheticExecutor.apply
        def slow(executor, snapshot):
            started.set()
            if not release.wait(5): raise AssertionError('fixture wait timed out')
            original(executor, snapshot)
        action = self.propose(); self.approve(action)
        result = []
        try:
            with patch.object(SyntheticExecutor, 'apply', slow):
                thread = threading.Thread(target=lambda: result.append(self.ledger.execute(action['id'])))
                thread.start()
                self.assertTrue(started.wait(5))
                eid = self.evidence(incident='other-episode')
                request = other.propose({'action_id': 'synthetic.recover', 'target': 'synthetic-core',
                                        'incident_key': 'other-episode', 'evidence_id': eid})
                other.approve(request['id'], other.review(request['id'])['approval_sha256'])
                with self.assertRaises(ValueError): other.execute(request['id'])
                release.set(); thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]['state'], 'completed')
        finally:
            release.set()

    def test_second_constructor_cannot_reconcile_an_active_owner(self):
        started, release = threading.Event(), threading.Event()
        original = SyntheticExecutor.apply
        def slow(executor, snapshot):
            started.set()
            if not release.wait(5): raise AssertionError('fixture wait timed out')
            original(executor, snapshot)
        action = self.propose(); self.approve(action)
        result = []
        try:
            with patch.object(SyntheticExecutor, 'apply', slow):
                thread = threading.Thread(target=lambda: result.append(self.ledger.execute(action['id'])))
                thread.start()
                self.assertTrue(started.wait(5))
                with self.assertRaisesRegex(ValueError, 'another broker owns'):
                    ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
                self.assertEqual(self.ledger.get(action['id'])['state'], 'running')
                release.set(); thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]['state'], 'completed')
                self.assertNotIn('reconcile_required', [row['state'] for row in self.ledger.audit(action['id'])])
        finally:
            release.set()
        self.ledger.db.close()
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.assertEqual(self.ledger.get(action['id'])['state'], 'completed')

    def test_ownership_lock_rejects_symlink_and_insecure_mode(self):
        self.ledger.db.close()
        lock = self.path.with_name(self.path.name + '.actions.lock')
        lock.unlink()
        lock.symlink_to(self.path)
        with self.assertRaises(OSError): ActionLedger(self.path, self.policies, self.monitoring)
        lock.unlink(); lock.touch(mode=0o644)
        with self.assertRaises(ValueError): ActionLedger(self.path, self.policies, self.monitoring)
        lock.chmod(0o600)
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)

    def test_constructor_failure_releases_ownership_lock(self):
        self.ledger.db.close()
        with patch.object(ActionLedger, '_initialize', side_effect=RuntimeError('synthetic initialization failure')):
            with self.assertRaises(RuntimeError): ActionLedger(self.path, self.policies, self.monitoring)
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.assertTrue(self.ledger.synthetic_state('synthetic-core'))

    def test_separate_process_cannot_acquire_live_ledger(self):
        code = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from actions import ActionLedger,load_actions
try:
 ledger=ActionLedger(Path(sys.argv[2]),load_actions(Path(sys.argv[3])),json.loads(Path(sys.argv[4]).read_text()))
except ValueError:
 sys.exit(0)
ledger.db.close()
sys.exit(3)
'''
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'deploy/codex-worker'), str(self.path),
                                 str(ROOT / 'config/system-actions.json'), str(ROOT / 'config/system-monitoring.json')],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_future_evidence_cannot_poison_later_valid_observations(self):
        with self.assertRaises(ValueError): self.evidence(age=-86400, incident='clock-error')
        fresh = self.evidence(incident='correct-clock')
        action = self.propose(evidence_id=fresh, incident_key='correct-clock')
        self.assertEqual(action['state'], 'pending')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM action_evidence').fetchone()[0], 1)

    def test_severity_matches_shared_status_contract(self):
        eid = self.evidence(status='healthy')
        body = self.ledger.evidence(eid)
        body['observation']['severity'] = 'warning'
        with self.assertRaises(ValueError): self.ledger.ingest_evidence(body)
        body['observation'].update(status='degraded', severity='info')
        with self.assertRaises(ValueError): self.ledger.ingest_evidence(body)

    def test_evidence_capacity_preserves_deduplication_and_existing_audit(self):
        with patch.object(actions, 'MAX_EVIDENCE_RECORDS', 2):
            first = self.evidence()
            self.evidence(incident='second-episode')
            self.assertEqual(self.evidence(), first)
            with self.assertRaises(ValueError): self.evidence(incident='third-episode')
            self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM action_evidence').fetchone()[0], 2)

    def test_failed_postcheck_uses_only_approved_bounded_rollback(self):
        action = self.propose(); self.approve(action)
        with patch.object(SyntheticExecutor, 'postcheck', return_value=False):
            result = self.ledger.execute(action['id'])
        self.assertEqual(result['state'], 'rolled_back')
        self.assertFalse(self.ledger.synthetic_state('synthetic-core')['healthy'])
        self.assertEqual([e['state'] for e in self.ledger.audit(action['id'])].count('rolling_back'), 1)

    def test_failed_rollback_requires_reconciliation(self):
        action = self.propose(); self.approve(action)
        with patch.object(SyntheticExecutor, 'postcheck', return_value=False), \
                patch.object(SyntheticExecutor, 'rollback', side_effect=RuntimeError('PRIVATE SECRET')):
            result = self.ledger.execute(action['id'])
        self.assertEqual(result['state'], 'reconcile_required')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_restart_never_replays_ambiguous_effect(self):
        action = self.propose(); self.approve(action)
        with patch.object(SyntheticExecutor, 'apply', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.ledger.execute(action['id'])
        self.ledger.db.close()
        self.ledger = ActionLedger(self.path, self.policies, self.monitoring, clock=lambda: self.now)
        self.assertEqual(self.ledger.get(action['id'])['state'], 'reconcile_required')
        with self.assertRaises(ValueError): self.ledger.execute(action['id'])

    def test_unprojected_evidence_and_registry_mismatch_rejected(self):
        eid = self.evidence()
        row = json.loads(self.ledger.db.execute('SELECT body FROM action_evidence WHERE id=?', (eid,)).fetchone()[0])
        row['observation']['evidence']['token'] = 'SECRET'
        with self.assertRaises(ValueError): self.ledger.ingest_evidence(row)
        hostile = copy.deepcopy(self.policies)
        hostile['actions'][-1]['target'] = 'unknown-host'
        with self.assertRaises(ValueError): validate_policies(hostile, self.monitoring)

    def test_incident_binding_and_cancel_are_durable(self):
        with self.assertRaises(ValueError): self.propose(incident_key='forged-episode')
        action = self.propose(); self.ledger.cancel(action['id'])
        with self.assertRaises(ValueError): self.approve(action)
        self.assertEqual(self.ledger.get(action['id'])['state'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
