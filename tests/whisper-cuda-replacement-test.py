#!/usr/bin/env python3
"""Synthetic guarded replacement, predecessor recovery and refusal tests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


REPLACE = module('replacement', 'whisper-cuda-replacement.py')
LIFECYCLE = module('replacement_lifecycle', 'setup-whisper-stt.py')
NEW_REVISION = 'a' * 40
OLD_REVISION = 'b' * 40
NEW_IMAGE = 'sha256:' + 'c' * 64
NEW_SOURCE = 'd' * 40
RELAY_IMAGE = 'sha256:' + 'e' * 64


class Fleet:
    CUDA_CANDIDATE = 'gb10-whisper-cpp-primary'
    CUDA_CANDIDATE_PROJECT = 'whisper-cpp-gb10-cuda'
    CPP_QUALIFIED_IMAGE_ID = NEW_IMAGE
    CPP_QUALIFIED_SOURCE_REVISION = NEW_SOURCE
    CPP_MODEL_PATH = Path('/synthetic/model')

    def __init__(self, state):
        self.STATE = state
        self.gates = set()
        self.events = []
        self.failure = None
        self.recovery_failure = False
        self.gate_unknown = False
        self.prior = {'revision': OLD_REVISION, 'state': 'promoted', 'promotion': True,
                      'candidate_image_id': REPLACE.PREVIOUS_IMAGE,
                      'image_source_revision': REPLACE.PREVIOUS_SOURCE,
                      'relay_candidate_id': 'relay', 'relay_candidate_image_id': RELAY_IMAGE,
                      'primary_id': 'vllm', 'primary_image': 'vllm-image',
                      'relay_original': {'id': 'old-relay', 'image': 'old-relay-image'},
                      'chain': 'HC-STT-111111111111', 'maintenance_bundle_sha256': 'f' * 64}
        self.current = copy.deepcopy(self.prior)
        self.persist()
        self.old = SimpleNamespace(qualified_cuda_candidate_image_id=lambda: REPLACE.PREVIOUS_IMAGE,
                                   cuda_candidate_stop=self.stop_old,
                                   ensure_spark_maintenance_ownership=self.maintenance,
                                   cuda_candidate_start=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                       AssertionError('automatic-subnet owner must never restore')))
        self.recovery = SimpleNamespace(qualified_cuda_candidate_image_id=lambda: REPLACE.PREVIOUS_IMAGE,
                                        cuda_candidate_stop=self.stop_recovery,
                                        ensure_spark_maintenance_ownership=self.maintenance,
                                        cuda_candidate_start=self.restore_old)

    def persist(self):
        (self.STATE / 'cuda-candidate.json').write_text(json.dumps(self.current))

    def promoted(self, image, source, revision):
        self.current = copy.deepcopy(self.prior)
        self.current.update(candidate_image_id=image, image_source_revision=source, revision=revision,
                            chain='HC-STT-222222222222' if image == NEW_IMAGE else self.prior['chain'])
        self.persist()

    def inspect(self, name):
        if name == 'vllm':
            return {'Id': 'vllm', 'Image': 'vllm-image', 'Name': '/gb10-whisper-primary',
                    'State': {'Running': self.current['state'] == 'rolled-back'},
                    'Config': {'Cmd': ['model', '--gpu-memory-utilization', '0.08']},
                    'HostConfig': {'Memory': 12 * 1024**3}}
        if name == 'old-relay':
            return {'Id': 'old-relay', 'Image': 'old-relay-image'}
        if self.current['state'] != 'promoted':
            return None
        if name == self.CUDA_CANDIDATE:
            image, project, service, identifier = self.current['candidate_image_id'], self.CUDA_CANDIDATE_PROJECT, 'whisper-cpp-cuda', 'cpp'
            networks = {self.CUDA_CANDIDATE_PROJECT + '_candidate': {},
                        'whisper-large-v3-stt_inference': {'Aliases': ['whisper-primary', 'whisper-cpp-recording']}}
        elif name == 'gb10-whisper-cpp-http':
            image, project, service, identifier = RELAY_IMAGE, 'whisper-large-v3-stt', 'whisper-cpp-http', 'relay'
            networks = {'whisper-large-v3-stt_edge': {}, 'whisper-large-v3-stt_inference': {}}
        else:
            return None
        return {'Id': identifier, 'Image': image, 'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'NetworkSettings': {'Networks': networks},
                'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': service}}}

    def protected(self, path, directory=False):
        if path.is_symlink():
            raise ValueError('unsafe')

    def guards(self):
        self.events.append('guards')

    def qualified_cuda_candidate_image_id(self):
        return NEW_IMAGE

    def verify_cpp_model(self, path):
        pass

    def run(self, arguments):
        if arguments[:3] == ['docker', 'image', 'inspect']:
            return SimpleNamespace(stdout=json.dumps([{'Id': arguments[3]}]))
        raise AssertionError(arguments)

    def restart_policy(self, item):
        return 'unless-stopped'

    def gate(self, chain, install):
        self.events.append(('gate', chain, install))
        self.gates.add(chain) if install else self.gates.discard(chain)

    def gate_is_active(self, chain):
        if self.gate_unknown:
            raise ValueError('unknown gate')
        return chain in self.gates

    def stop_old(self, revision):
        assert revision == OLD_REVISION and self.gates
        self.events.append('stop-old')
        if self.failure == 'old-stop':
            self.failure = None
            self.current['state'] = 'rollback-failed'
            self.persist()
            raise RuntimeError('synthetic interruption')
        self.current['state'] = 'rolled-back'
        self.gates.discard(self.current['chain'])
        self.persist()

    def stop_recovery(self, revision):
        assert revision == REPLACE.RECOVERY_RELEASE and self.gates
        self.events.append('stop-recovery')
        self.current['state'] = 'rolled-back'
        self.gates.discard(self.current['chain'])
        self.persist()

    def cuda_candidate_stop(self, revision):
        assert revision == NEW_REVISION and self.gates
        self.events.append('stop-new')
        self.current['state'] = 'rolled-back'
        self.gates.discard(self.current['chain'])
        self.persist()

    def promote_qualified_cuda_candidate(self, revision):
        assert revision == NEW_REVISION and self.gates
        self.events.append('promote-new')
        if self.failure == 'before-new-start':
            raise RuntimeError('synthetic build failure')
        self.promoted(NEW_IMAGE, NEW_SOURCE, revision)
        if self.failure == 'new-start':
            self.current['state'] = 'rollback-failed'
            self.persist()
            raise RuntimeError('synthetic startup failure')

    def maintenance(self):
        self.events.append('old-maintenance')
        return 'f' * 64

    def restore_old(self, revision, **kwargs):
        assert revision == REPLACE.RECOVERY_RELEASE and self.gates
        assert kwargs['relay_image'] == kwargs['relay_image_id'] == RELAY_IMAGE
        assert kwargs['promotion'] is True and kwargs['max_audio_seconds'] == 1200
        self.events.append('restore-old')
        if self.recovery_failure:
            raise RuntimeError('synthetic vLLM readiness failure')
        self.promoted(REPLACE.PREVIOUS_IMAGE, REPLACE.PREVIOUS_SOURCE, revision)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.api = Fleet(Path(self.directory.name))
        previous = patch.object(REPLACE, 'previous_module', return_value=self.api.old)
        previous.start(); self.addCleanup(previous.stop)
        recovery = patch.object(REPLACE, 'recovery_module', return_value=self.api.recovery)
        recovery.start(); self.addCleanup(recovery.stop)
        network = patch.object(REPLACE, 'verify_network')
        self.network = network.start(); self.addCleanup(network.stop)
        bundle = patch.object(REPLACE, 'maintenance_bundle', return_value='f' * 64)
        self.bundle = bundle.start(); self.addCleanup(bundle.stop)
        transition = patch.object(REPLACE, 'verify_maintenance_transition')
        self.transition = transition.start(); self.addCleanup(transition.stop)
        memory = patch.object(REPLACE, 'available_memory', return_value=19 * 1024**3)
        memory.start(); self.addCleanup(memory.stop)

    def test_success_keeps_outer_gate_across_both_guarded_lifecycles(self):
        result = REPLACE.replace(self.api, NEW_REVISION)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.api.current['candidate_image_id'], NEW_IMAGE)
        self.assertFalse(self.api.gates)
        self.assertLess(self.api.events.index('stop-old'), self.api.events.index('promote-new'))
        before = list(self.api.events)
        self.assertEqual(REPLACE.replace(self.api, NEW_REVISION)['state'], 'completed')
        self.assertEqual(self.api.events.count('promote-new'), before.count('promote-new'))

    def test_failures_restore_exact_previous_artifacts_before_releasing_gate(self):
        for failure in ('old-stop', 'before-new-start', 'new-start'):
            with self.subTest(failure=failure):
                self.setUp()
                self.api.failure = failure
                with self.assertRaisesRegex(RuntimeError, 'previous qualified runtime restored'):
                    REPLACE.replace(self.api, NEW_REVISION)
                self.assertEqual(self.api.current['candidate_image_id'], REPLACE.PREVIOUS_IMAGE)
                self.assertEqual(REPLACE.read(self.api, REPLACE.JOURNAL)['phase'], 'restored')
                self.assertFalse(self.api.gates)
                self.assertEqual(self.api.current['revision'], REPLACE.RECOVERY_RELEASE)
                self.assertIn('restore-old', self.api.events)

    def test_partial_outer_gate_install_failure_restores_without_stopping_predecessor(self):
        gate = self.api.gate
        first = True
        def interrupted(chain, install):
            nonlocal first
            gate(chain, install)
            if first and install:
                first = False
                raise InterruptedError('synthetic gate interruption')
        self.api.gate = interrupted
        with self.assertRaisesRegex(RuntimeError, 'previous qualified runtime restored'):
            REPLACE.replace(self.api, NEW_REVISION)
        self.assertNotIn('stop-old', self.api.events)
        self.assertNotIn('promote-new', self.api.events)
        self.assertEqual(self.api.current['candidate_image_id'], REPLACE.PREVIOUS_IMAGE)
        self.assertEqual(REPLACE.read(self.api, REPLACE.JOURNAL)['phase'], 'restored')
        self.assertFalse(self.api.gates)

    def test_failed_inner_gate_cleanup_never_starts_recovery_promotion(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.api.current['state'] = 'rolled-back'
        self.api.persist()
        self.api.gates.add(self.api.current['chain'])
        def incomplete(_revision):
            self.api.current['state'] = 'rolled-back'
            self.api.persist()
        self.api.cuda_candidate_stop = incomplete
        with self.assertRaisesRegex(ValueError, 'intake gate is not complete'):
            REPLACE.recover(self.api, NEW_REVISION)
        self.assertNotIn('restore-old', self.api.events)
        self.assertTrue(self.api.gates)

    def test_failed_recovery_stays_gated_and_retry_only_finishes_recovery(self):
        self.api.failure = 'new-start'
        self.api.recovery_failure = True
        with self.assertRaisesRegex(RuntimeError, 'recovery held'):
            REPLACE.replace(self.api, NEW_REVISION)
        self.assertTrue(self.api.gates)
        self.assertEqual(REPLACE.read(self.api, REPLACE.JOURNAL)['phase'], 'recovery-required')
        self.api.recovery_failure = False
        result = REPLACE.replace(self.api, NEW_REVISION)
        self.assertEqual(result['state'], 'restored')
        self.assertEqual(self.api.events.count('promote-new'), 1)
        self.assertFalse(self.api.gates)

    def test_explicit_recovery_restores_predecessor_after_success(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.assertEqual(REPLACE.recover(self.api, NEW_REVISION)['state'], 'restored')
        self.assertEqual(self.api.current['candidate_image_id'], REPLACE.PREVIOUS_IMAGE)

    def test_unrelated_or_unknown_state_never_releases_gate(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.api.current['revision'] = '9' * 40
        self.api.current['state'] = 'rolled-back'
        self.api.persist()
        with self.assertRaisesRegex(ValueError, 'Unexpected active transaction'):
            REPLACE.recover(self.api, NEW_REVISION)
        self.assertTrue(self.api.gates)

    def test_unknown_inner_gate_cannot_mark_completed(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.api.gates.add(self.api.current['chain'])
        with self.assertRaisesRegex(ValueError, 'active intake gate'):
            REPLACE.replace(self.api, NEW_REVISION)
        self.assertTrue(self.api.gates)

    def test_rolled_back_with_held_inner_gate_runs_owning_cleanup_before_restore(self):
        for revision, image, event in ((NEW_REVISION, NEW_IMAGE, 'stop-new'),
                                       (OLD_REVISION, REPLACE.PREVIOUS_IMAGE, 'stop-old'),
                                       (REPLACE.RECOVERY_RELEASE, REPLACE.PREVIOUS_IMAGE, 'stop-recovery')):
            with self.subTest(revision=revision):
                self.setUp()
                REPLACE.replace(self.api, NEW_REVISION)
                self.api.current.update(revision=revision, candidate_image_id=image, state='rolled-back')
                self.api.persist()
                self.api.gates.add(self.api.current['chain'])
                prior = self.api.events.count(event)
                REPLACE.recover(self.api, NEW_REVISION)
                self.assertEqual(self.api.events.count(event), prior + 1)
                self.assertEqual(self.api.current['revision'], REPLACE.RECOVERY_RELEASE)
                self.assertFalse(self.api.gates)

    def test_unknown_rolled_back_gate_holds_outer_and_does_not_restore(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.api.current['state'] = 'rolled-back'
        self.api.persist()
        self.api.gate_unknown = True
        with self.assertRaisesRegex(ValueError, 'unknown gate'):
            REPLACE.recover(self.api, NEW_REVISION)
        self.assertTrue(self.api.gates)
        self.assertNotIn('restore-old', self.api.events)

    def test_wrong_image_for_recovery_owner_is_refused(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.api.current.update(revision=REPLACE.RECOVERY_RELEASE, candidate_image_id=NEW_IMAGE, state='rolled-back')
        self.api.persist()
        with self.assertRaisesRegex(ValueError, 'Unexpected active transaction'):
            REPLACE.recover(self.api, NEW_REVISION)
        self.assertTrue(self.api.gates)
        self.assertNotIn('restore-old', self.api.events)

    def test_new_and_recovery_networks_verified_before_first_gate(self):
        def verify(_api, owner, revision):
            self.assertFalse(self.api.gates)
            self.assertFalse((self.api.STATE / REPLACE.JOURNAL).exists())
        self.network.side_effect = verify
        REPLACE.replace(self.api, NEW_REVISION)
        self.assertEqual([call.args[2] for call in self.network.call_args_list],
                         [NEW_REVISION, REPLACE.RECOVERY_RELEASE])

    def test_installed_or_journal_maintenance_drift_refuses_before_gating(self):
        self.bundle.side_effect = ValueError('installed bundle differs')
        with self.assertRaisesRegex(ValueError, 'installed bundle differs'):
            REPLACE.replace(self.api, NEW_REVISION)
        self.bundle.side_effect = None
        self.api.current['maintenance_bundle_sha256'] = '0' * 64
        self.api.persist()
        with self.assertRaisesRegex(ValueError, 'journal and installed maintenance'):
            REPLACE.replace(self.api, NEW_REVISION)
        self.assertFalse(self.api.gates)
        self.assertNotIn('stop-old', self.api.events)
        self.assertFalse((self.api.STATE / REPLACE.JOURNAL).exists())

    def test_capacity_or_current_drift_refuses_before_mutation(self):
        with patch.object(REPLACE, 'available_memory', return_value=11 * 1024**3):
            with self.assertRaisesRegex(ValueError, '12 GiB'):
                REPLACE.replace(self.api, NEW_REVISION)
        self.assertFalse(self.api.gates)
        self.assertNotIn('stop-old', self.api.events)
        self.assertFalse((self.api.STATE / REPLACE.JOURNAL).exists())

    def test_later_unrelated_maintenance_update_refuses_recovery_before_stopping(self):
        REPLACE.replace(self.api, NEW_REVISION)
        self.transition.side_effect = ValueError('unrelated changes')
        before = list(self.api.events)
        with self.assertRaisesRegex(ValueError, 'unrelated changes'):
            REPLACE.recover(self.api, NEW_REVISION)
        self.assertEqual(self.api.events, before)
        self.assertEqual(self.api.current['candidate_image_id'], NEW_IMAGE)

    def test_pending_replacement_blocks_other_lifecycle_actions(self):
        self.api.failure = 'new-start'
        self.api.recovery_failure = True
        with self.assertRaises(RuntimeError):
            REPLACE.replace(self.api, NEW_REVISION)
        with self.assertRaisesRegex(ValueError, 'pending CPP replacement'):
            REPLACE.require_no_pending(self.api)

    def test_promoted_smokes_use_container_namespaces_not_gated_host_ports(self):
        source = (ROOT / 'scripts/setup-whisper-stt.py').read_text()
        smoke = LIFECYCLE.promoted_relay_smoke_program('a' * 40)
        self.assertIn('whisper-cpp-recording:8000', smoke)
        self.assertIn('127.0.0.1:8007', smoke)
        self.assertNotIn('127.0.0.1:8003', smoke)
        self.assertIn("['docker', 'exec', 'gb10-whisper-cpp-http'", source)
        self.assertIn("for port in ('8003', '8007')", source)
        compose = (ROOT / 'deploy/whisper-stt/compose.yaml').read_text()
        relay = compose.split('  whisper-cpp-http:', 1)[1].split('\nnetworks:', 1)[0]
        self.assertIn('networks: [edge, inference]', relay)
        self.assertNotIn('network_mode:', relay)


class RecoveryPreflightTests(unittest.TestCase):
    def test_recovery_materializes_only_the_fixed_published_release_before_import(self):
        events = []
        bootstrap = SimpleNamespace(materialize=lambda revision: events.append(('materialize', revision)))
        loader = SimpleNamespace(exec_module=lambda _module: events.append(('load-bootstrap',)))
        spec = SimpleNamespace(loader=loader)
        api = SimpleNamespace(ROOT=Path('/synthetic/current-release'))
        with patch.object(REPLACE.importlib.util, 'spec_from_file_location', return_value=spec), \
                patch.object(REPLACE.importlib.util, 'module_from_spec', return_value=bootstrap), \
                patch.object(REPLACE, 'previous_module', side_effect=lambda _api, revision: events.append(('verify', revision))):
            REPLACE.recovery_module(api, materialize=True)
        self.assertEqual(events, [('load-bootstrap',), ('materialize', REPLACE.RECOVERY_RELEASE),
                                  ('verify', REPLACE.RECOVERY_RELEASE)])

    def test_maintenance_is_verified_without_installing(self):
        calls = []
        digest = 'f' * 64
        def run(arguments, **kwargs):
            calls.append(arguments)
            if '--verify-bundle-sha256' in arguments:
                return SimpleNamespace(stdout=json.dumps({'host': 'home-spark', 'verified': True, 'bundle_sha256': digest}))
            return SimpleNamespace(stdout=json.dumps({'installed': False, 'bundle_sha256': digest}))
        owner = SimpleNamespace(ROOT=Path('/synthetic/release'), run=run)
        self.assertEqual(REPLACE.maintenance_bundle(owner, installed=True), digest)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('--bundle-sha256' not in call for call in calls))

    def test_incorrect_installed_bundle_receipt_refuses(self):
        responses = iter(({'installed': False, 'bundle_sha256': 'f' * 64},
                          {'host': 'home-spark', 'verified': True, 'bundle_sha256': '0' * 64}))
        owner = SimpleNamespace(ROOT=Path('/synthetic/release'),
                                run=lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(next(responses))))
        with self.assertRaisesRegex(ValueError, 'Installed maintenance differs'):
            REPLACE.maintenance_bundle(owner, installed=True)

    def test_partial_owned_install_is_allowed_but_unrelated_file_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            files = [root / 'installed.py', root / 'dispatcher']
            old = {files[0]: (b'old source', 0o644), files[1]: (b'old wrapper', 0o755)}
            target = {files[0]: (b'new source', 0o644), files[1]: (b'new wrapper', 0o755)}
            verifier = SimpleNamespace(trusted_path=lambda *_args, **_kwargs: None)
            record = {'previous_maintenance_bundle_sha256': 'f' * 64,
                      'target_maintenance_bundle_sha256': 'a' * 64}
            def artifacts(owner):
                return (verifier, 'f' * 64, old) if owner == 'old' else (verifier, 'a' * 64, target)
            installed = {files[0]: b'new source', files[1]: b'old wrapper'}
            with patch.object(REPLACE, 'maintenance_artifacts', side_effect=artifacts), \
                    patch.object(REPLACE, 'read_installed', side_effect=lambda path, _mode: installed[path]):
                REPLACE.verify_maintenance_transition('new', 'old', record)
                installed[files[1]] = b'unrelated later update'
                with self.assertRaisesRegex(ValueError, 'unrelated changes'):
                    REPLACE.verify_maintenance_transition('new', 'old', record)
                installed[files[1]] = b'new wrapper'
                REPLACE.verify_maintenance_transition('new', 'old', record)
                record['target_maintenance_bundle_sha256'] = 'b' * 64
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    REPLACE.verify_maintenance_transition('new', 'old', record)

    def test_installed_maintenance_rejects_wrong_file_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'installed.py'
            source.write_bytes(b'content')
            source.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'ownership or mode'):
                REPLACE.read_installed(source, 0o644)
            link = source.with_name('link.py')
            link.symlink_to(source)
            with self.assertRaises(OSError):
                REPLACE.read_installed(link, 0o644)

    def test_automatic_subnet_and_overlapping_routes_refuse(self):
        network = {'internal': True}
        owner = SimpleNamespace(CPP_QUALIFIED_IMAGE_ID=REPLACE.PREVIOUS_IMAGE,
                                compose_cuda=lambda *_args, **_kwargs: SimpleNamespace(
                                    stdout=json.dumps({'networks': {'candidate': network}})))
        api = SimpleNamespace(CUDA_CANDIDATE_PROJECT='whisper-cpp-gb10-cuda')
        with self.assertRaisesRegex(ValueError, 'explicit private subnet'):
            REPLACE.verify_network(api, owner, REPLACE.RECOVERY_RELEASE)
        network['ipam'] = {'config': [{'subnet': '10.253.73.0/28', 'gateway': '10.253.73.1'}]}
        api.run = lambda args: SimpleNamespace(stdout='' if args[:3] == ['docker', 'network', 'ls'] else
                                               json.dumps([{'dst': '10.253.73.0/24', 'dev': 'eth0'}]))
        with self.assertRaisesRegex(ValueError, 'overlaps a host route'):
            REPLACE.verify_network(api, owner, REPLACE.RECOVERY_RELEASE)
        api.run = lambda args: SimpleNamespace(stdout='' if args[:3] == ['docker', 'network', 'ls'] else '[]')
        REPLACE.verify_network(api, owner, REPLACE.RECOVERY_RELEASE)


class PreviousReleaseTests(unittest.TestCase):
    def test_unverified_old_source_is_never_imported(self):
        for dirty, remote in ((' M modified.py', 'https://github.com/madslundt/HomeCompute.git'),
                              ('', 'https://invalid.example/untrusted.git')):
            replies = iter((OLD_REVISION, dirty, remote))
            api = SimpleNamespace(protected=lambda *_: None,
                                  run=lambda _: SimpleNamespace(stdout=next(replies)))
            with patch.object(REPLACE.importlib.util, 'spec_from_file_location') as loader:
                with self.assertRaisesRegex(ValueError, 'Previous release'):
                    REPLACE.previous_module(api, OLD_REVISION)
                loader.assert_not_called()

    def test_bad_revision_is_refused_before_any_release_access(self):
        api = SimpleNamespace(protected=lambda *_: self.fail('must reject before reading'))
        with self.assertRaisesRegex(ValueError, 'exact published release'):
            REPLACE.previous_module(api, '../arbitrary')


if __name__ == '__main__':
    unittest.main(verbosity=2)
