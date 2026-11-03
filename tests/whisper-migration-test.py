#!/usr/bin/env python3
"""STT replacement gates, transaction recovery and consumer migration behavior."""
import copy
import contextlib
import importlib.util
import io
import json
import re
import stat
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


LIFECYCLE = module('whisper_lifecycle', 'scripts/setup-whisper-stt.py')
BOOTSTRAP = module('whisper_bootstrap', 'scripts/bootstrap-whisper-stt.py')
DAYTHREAD = module('daythread_whisper', 'scripts/migrate-daythread-whisper.py')


class MigrationTests(unittest.TestCase):
    def test_daythread_removes_only_reviewed_language_replacement(self):
        configuration = {'values': {'stt_model': 'whisper-turbo', 'stt_base_url': 'https://ai.home.arpa:8443/v1',
                                    'stt_routes': {'da': {'model': 'stt-danish', 'base_url': None}},
                                    'summary_language': 'da'}, 'secrets': {}}
        original = copy.deepcopy(configuration)
        self.assertEqual(DAYTHREAD.changes(configuration), {'stt_model': 'whisper', 'stt_routes': {}})
        self.assertEqual(configuration, original)
        for change in ({'model': 'foreign'}, {'base_url': 'https://foreign.invalid'}, {'token': True}):
            candidate = copy.deepcopy(configuration)
            if 'token' in change: candidate['secrets']['stt_routes.da.token'] = True
            else: candidate['values']['stt_routes']['da'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): DAYTHREAD.changes(candidate)

    def test_capacity_override_blocks_prepare_without_touching_containers(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(LIFECYCLE, 'compose') as compose:
            with self.assertRaisesRegex(ValueError, 'capacity'): LIFECYCLE.prepare()
        compose.assert_not_called()

    def test_prepare_keeps_runtime_candidate_opt_in(self):
        with patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE.Path, 'is_symlink', return_value=False), \
                patch.object(LIFECYCLE.Path, 'is_dir', return_value=True), patch.object(LIFECYCLE, 'compose') as compose:
            LIFECYCLE.prepare()
        calls = [call.args for call in compose.call_args_list]
        self.assertNotIn(('--profile', 'candidate', 'build', 'whisper-candidate'), calls)
        self.assertFalse(any('up' in call for call in calls))

    def test_runtime_candidate_preparation_builds_but_never_starts_it(self):
        with patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'protected'), \
                patch.object(LIFECYCLE.Path, 'read_text', return_value=json.dumps({
                    'repo_id': LIFECYCLE.MODEL, 'revision': LIFECYCLE.REVISION})), \
                patch.object(LIFECYCLE, 'compose') as compose, \
                patch.object(LIFECYCLE, 'candidate_image_id', return_value='sha256:' + 'a' * 64):
            LIFECYCLE.prepare_runtime_candidate()
        calls = [call.args for call in compose.call_args_list]
        self.assertEqual(calls, [('--profile', 'candidate', 'build', 'whisper-candidate')])

    def test_candidate_image_identity_requires_patch_and_base_labels(self):
        expected = 'sha256:' + 'a' * 64
        good = {'Id': expected, 'Config': {'Labels': {
            'io.homecompute.whisper-runtime-patch': '0.19.0-hc1',
            'io.homecompute.whisper-runtime-patcher-sha256':
                'fed6ae01930b2689fbfdcb039ffdb024778f84dc67ba61d0d84fb7fc54935c74',
            'io.homecompute.whisper-source-prompt-sha256':
                '44d196b1d3ec3182de51f4b5f87764a33a1b58c83e6afb5e452f52f4bb653dcf',
            'io.homecompute.whisper-source-serving-sha256':
                '7b9b47511ba3643ccae14ae8a4095153959a35a671eb3cc07707dfd1f10ebfa3',
            'org.opencontainers.image.base.name':
                'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'}}}
        with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([good]))):
            self.assertEqual(LIFECYCLE.candidate_image_id(), expected)
        good['Config']['Labels']['io.homecompute.whisper-runtime-patch'] = 'unqualified'
        with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([good]))), \
                self.assertRaisesRegex(ValueError, 'identity or base pin'):
            LIFECYCLE.candidate_image_id()

    def test_cuda_candidate_image_identity_requires_arm64_all_pins_and_backend_label(self):
        image_id = 'sha256:' + 'a' * 64
        labels = {
            'org.opencontainers.image.revision': LIFECYCLE.CPP_RUNTIME_COMMIT,
            'io.homecompute.source-revision': 'd' * 40,
            'io.homecompute.whisper-cpp.runtime-commit': LIFECYCLE.CPP_RUNTIME_COMMIT,
            'io.homecompute.whisper-cpp.model-sha256': LIFECYCLE.CPP_MODEL_SHA256,
            'io.homecompute.whisper-cpp.backend': 'cuda-121a',
            'io.homecompute.whisper-cpp.cuda-base': LIFECYCLE.CUDA_BASE_IMAGE,
        }
        good = {'Id': image_id, 'Os': 'linux', 'Architecture': 'arm64',
                'Config': {'Labels': labels}}
        with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([good]))):
            self.assertEqual(LIFECYCLE.cuda_candidate_image_id('d' * 40), image_id)
        for mutation in ({'Architecture': 'amd64'}, {'label': 'io.homecompute.whisper-cpp.backend'}):
            candidate = copy.deepcopy(good)
            if 'Architecture' in mutation:
                candidate['Architecture'] = mutation['Architecture']
            else:
                candidate['Config']['Labels'][mutation['label']] = 'cpu'
            with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([candidate]))), \
                    self.assertRaisesRegex(ValueError, 'identity or pinned build labels'):
                LIFECYCLE.cuda_candidate_image_id('d' * 40)

    def test_cuda_candidate_prepare_builds_only_native_pinned_image(self):
        with patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'check_cuda_host'), \
                patch.object(LIFECYCLE, 'compose_cuda') as compose, \
                patch.object(LIFECYCLE, 'cuda_candidate_image_id', return_value='sha256:' + 'a' * 64):
            LIFECYCLE.prepare_cuda_candidate('d' * 40)
        compose.assert_called_once_with('--profile', 'cuda-candidate', 'build', '--pull=false',
                                        'whisper-cpp-cuda', revision='d' * 40)
        self.assertNotIn('up', compose.call_args.args)

    def test_cuda_candidate_refuses_to_co_load_active_vllm_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({'state': 'applied'}))
            primary = {'Id': 'primary', 'Image': 'sha256:' + 'b' * 64,
                       'Name': '/gb10-whisper-primary',
                       'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                       'Config': {'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                                  'Image': 'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'}}
            active_vllm_candidate = {'Id': 'candidate'}
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'check_cuda_host'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=[primary, active_vllm_candidate]) as inspect, \
                    patch.object(LIFECYCLE, 'gate') as gate, patch.object(LIFECYCLE, 'compose_cuda') as compose:
                with self.assertRaisesRegex(ValueError, 'Another Whisper candidate container exists'):
                    LIFECYCLE.cuda_candidate_start('d' * 40)
            self.assertEqual(inspect.call_count, 2)
            gate.assert_not_called()
            compose.assert_not_called()

    def test_cuda_candidate_rollback_removes_only_its_service_and_restores_exact_primary(self):
        candidate = {'Id': 'cpp-cuda-id', 'Image': 'sha256:' + 'c' * 64,
                     'State': {'Running': True},
                     'Config': {'Labels': {'com.docker.compose.project': LIFECYCLE.CUDA_CANDIDATE_PROJECT,
                                           'com.docker.compose.service': 'whisper-cpp-cuda'}}}
        primary = {'Id': 'primary-id', 'Image': 'sha256:' + 'b' * 64,
                   'Name': '/gb10-whisper-primary',
                   'State': {'Running': False, 'Health': {'Status': 'unhealthy'}}}
        healthy = {**primary, 'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        tx = {'revision': 'd' * 40, 'state': 'primary-stopped',
              'candidate_image_id': candidate['Image'], 'primary_id': primary['Id'],
              'primary_image': primary['Image'], 'primary_restart': 'unless-stopped'}
        run_results = [SimpleNamespace(stdout='')]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(LIFECYCLE, 'STATE', Path(directory)), \
                patch.object(LIFECYCLE, 'inspect', side_effect=[candidate, None, primary, healthy]), \
                patch.object(LIFECYCLE, 'compose_cuda') as compose, \
                patch.object(LIFECYCLE, 'run', side_effect=lambda *_args, **_kwargs: run_results.pop(0)
                             if run_results else SimpleNamespace(stdout='')) as run:
            LIFECYCLE.cuda_candidate_rollback(tx)
            journal = json.loads((Path(directory) / 'cuda-candidate.json').read_text())
        compose.assert_not_called()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command for command in commands if tuple(command[:2]) in {
            ('docker', 'update'), ('docker', 'stop'), ('docker', 'rm'), ('docker', 'start')}], [
            ['docker', 'update', '--restart', 'no', 'cpp-cuda-id'],
            ['docker', 'stop', '--time', '180', 'cpp-cuda-id'],
            ['docker', 'rm', 'cpp-cuda-id'],
            ['docker', 'update', '--restart', 'unless-stopped', 'primary-id'],
            ['docker', 'start', 'primary-id'],
        ])
        self.assertEqual(tx['state'], 'rolled-back')
        self.assertEqual(journal['rollback_status'], 'completed')

    def test_cuda_candidate_bootstrap_actions_are_explicit(self):
        for action in ('prepare-cuda-candidate', 'cuda-candidate',
                       'cuda-candidate-rollback', 'cuda-candidate-status'):
            self.assertIn(action, BOOTSTRAP.ACTIONS)

    def test_bootstrap_forwards_bounded_longform_options_only_to_cuda_candidate(self):
        args = SimpleNamespace(action='cuda-candidate', revision='d' * 40,
                               max_audio_seconds=1200, max_upload_bytes=40_000_000,
                               native_timeout_seconds=14_400)
        self.assertEqual(BOOTSTRAP.setup_argv(Path('/release'), args), [
            BOOTSTRAP.sys.executable, '-I', '-B', '/release/scripts/setup-whisper-stt.py',
            'cuda-candidate', '--revision', 'd' * 40, '--max-audio-seconds', '1200',
            '--max-upload-bytes', '40000000', '--native-timeout-seconds', '14400'])
        args.action = 'status'
        with self.assertRaisesRegex(ValueError, 'only for cuda-candidate'):
            BOOTSTRAP.setup_argv(Path('/release'), args)

    def test_cuda_candidate_longform_limits_are_explicit_and_consistent(self):
        LIFECYCLE.validate_cuda_request_limits(1200, 40_000_000, 14_400)
        LIFECYCLE.validate_cuda_request_limits(7200, 256 * 1024 * 1024, 14_400)
        for limits in ((1200, 6_000_000, 900), (7201, 256 * 1024 * 1024, 14_400),
                       (1200, 40_000_000, 14_401)):
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, 'limits are invalid'):
                LIFECYCLE.validate_cuda_request_limits(*limits)

    def test_cuda_candidate_start_failure_rolls_back_releases_gate_and_keeps_private_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({'state': 'applied'}))
            primary_running = {
                'Id': 'primary-id', 'Image': 'sha256:' + 'b' * 64,
                'Name': '/gb10-whisper-primary',
                'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'Config': {'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                           'Image': 'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'},
                'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}},
            }
            primary_stopped = {**primary_running,
                               'State': {'Running': False, 'Health': {'Status': 'unhealthy'}}}
            primary_healthy = {**primary_running,
                               'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
            inspections = [primary_running, None, None, None, None, primary_stopped, primary_healthy]
            original_read_text = Path.read_text

            def read_text(path, *args, **kwargs):
                if str(path) == '/proc/meminfo':
                    return 'MemAvailable: 20000000 kB\nMemTotal: 128000000 kB\n'
                return original_read_text(path, *args, **kwargs)

            error = LIFECYCLE.ManagedCommandFailure(
                'docker', 17, diagnostic_text='PRIVATE_FAKE_CUDA_SECRET')
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'check_cuda_host'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=inspections), \
                    patch.object(LIFECYCLE, 'cuda_candidate_image_id', return_value='sha256:' + 'c' * 64), \
                    patch.object(LIFECYCLE, 'validate_cuda_artifacts'), patch.object(LIFECYCLE, 'quiet'), \
                    patch.object(LIFECYCLE, 'gate') as gate, patch.object(LIFECYCLE, 'run',
                        return_value=SimpleNamespace(stdout='')) as run, \
                    patch.object(LIFECYCLE, 'compose_cuda', side_effect=error), \
                    patch.object(LIFECYCLE.Path, 'read_text', read_text):
                with self.assertRaises(LIFECYCLE.CudaCandidateFailure) as failure:
                    LIFECYCLE.cuda_candidate_start('d' * 40)
            gate_calls = [call.args for call in gate.call_args_list]
            self.assertEqual(gate_calls[0][1], True)
            self.assertEqual(gate_calls[-1][1], False)
            self.assertEqual(gate_calls[0][0], gate_calls[-1][0])
            journal = json.loads((state / 'cuda-candidate.json').read_text())
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['failure_stage'], 'candidate-start')
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertTrue(journal['failure_diagnostic_available'])
            public = LIFECYCLE.format_cuda_candidate_failure(failure.exception.metadata)
            self.assertNotIn('PRIVATE_FAKE_CUDA_SECRET', public)
            self.assertNotIn('PRIVATE_FAKE_CUDA_SECRET', json.dumps(journal))
            diagnostic = state / ('candidate-diagnostic-' + journal['failure_id'] + '.json')
            self.assertEqual(stat.S_IMODE(diagnostic.stat().st_mode), 0o600)
            self.assertIn('PRIVATE_FAKE_CUDA_SECRET', diagnostic.read_text())
            self.assertTrue(any(call.args[0][:2] == ['docker', 'start'] for call in run.call_args_list))

    def test_cuda_candidate_status_allowlists_only_safe_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'cuda-candidate.json').write_text(json.dumps({
                'revision': 'd' * 40, 'state': 'rollback-failed',
                'candidate_stage': 'PRIVATE_FAKE_CUDA_SECRET',
                'failure_id': 'PRIVATE_FAKE_CUDA_SECRET',
                'failure_stage': 'candidate-start',
                'failure_class': 'PRIVATE_FAKE_CUDA_SECRET',
                'failure_classification': 'unknown',
                'rollback_status': 'failed',
                'rollback_failure_stage': 'compose-down',
            }))
            stdout = io.StringIO()
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'protected'), \
                    contextlib.redirect_stdout(stdout):
                LIFECYCLE.cuda_candidate_status('d' * 40)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['state'], 'rollback-failed')
            self.assertEqual(result['failure_stage'], 'candidate-start')
            self.assertIsNone(result['failure_id'])
            self.assertIsNone(result['candidate_stage'])
            self.assertIsNone(result['failure_class'])
            self.assertNotIn('PRIVATE_FAKE_CUDA_SECRET', stdout.getvalue())

    def test_candidate_allocation_is_bounded_without_changing_production(self):
        compose = (ROOT / 'deploy/whisper-stt/compose.yaml').read_text()
        def service_block(name):
            match = re.search(rf'(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)', compose)
            self.assertIsNotNone(match, f'missing Compose service {name}')
            return match.group(1)
        primary = service_block('whisper-primary')
        candidate = service_block('whisper-candidate')
        self.assertIn('--gpu-memory-utilization 0.08', primary)
        self.assertIn('--gpu-memory-utilization 0.06', candidate)
        for args in ('--max-num-seqs 1', '--max-num-batched-tokens 2048', '--enforce-eager'):
            with self.subTest(args=args):
                self.assertIn(args, primary)
                self.assertIn(args, candidate)

    def test_candidate_start_accepts_successfully_legacy_retired_full_whisper(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({
                'state': 'legacy-retired', 'revision': 'a' * 40,
                'rollback_containers_retained': False,
            }))
            primary = {
                'Id': 'full-whisper-id', 'Image': 'sha256:' + 'b' * 64,
                'Name': '/' + LIFECYCLE.NEW[0],
                'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'Config': {
                    'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                    'Image': 'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d',
                },
                'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}},
            }
            calls = []
            def stop_before_mutation(tx):
                calls.append(tx)
                raise RuntimeError('test stops before candidate switch')
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=[primary, None]), \
                    patch.object(LIFECYCLE, 'candidate_image_id', return_value='sha256:' + 'c' * 64), \
                    patch.object(LIFECYCLE, 'save_candidate', side_effect=stop_before_mutation), \
                    patch.object(LIFECYCLE, 'gate') as gate, patch.object(LIFECYCLE, 'compose') as compose:
                with self.assertRaisesRegex(RuntimeError, 'stops before candidate switch'):
                    LIFECYCLE.candidate_start('d' * 40)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['state'], 'prepared')
            self.assertEqual(calls[0]['primary_id'], 'full-whisper-id')
            gate.assert_not_called()
            compose.assert_not_called()

    def test_candidate_start_refuses_mid_retirement_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({'state': 'retiring-legacy'}))
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'inspect') as inspect, \
                    patch.object(LIFECYCLE, 'candidate_image_id') as image:
                with self.assertRaisesRegex(ValueError, 'applied or legacy-retired'):
                    LIFECYCLE.candidate_start('d' * 40)
            inspect.assert_not_called()
            image.assert_not_called()

    def test_candidate_rollback_restores_only_journaled_full_whisper_primary(self):
        primary = {
            'Id': 'full-whisper-id', 'Image': 'sha256:' + 'b' * 64,
            'Name': '/gb10-whisper-primary',
            'State': {'Running': False, 'Health': {'Status': 'unhealthy'}},
        }
        healthy_primary = {
            **primary,
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
        }
        tx = {
            'primary_id': 'full-whisper-id', 'primary_image': 'sha256:' + 'b' * 64,
            'primary_restart': 'unless-stopped', 'candidate_image_id': 'sha256:' + 'c' * 64,
        }
        with patch.object(LIFECYCLE, 'inspect', side_effect=[None, primary, healthy_primary]), \
                patch.object(LIFECYCLE, 'run') as run, patch.object(LIFECYCLE, 'save_candidate') as save:
            LIFECYCLE.candidate_rollback(tx)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['docker', 'update', '--restart', 'unless-stopped', 'full-whisper-id'],
            ['docker', 'start', 'full-whisper-id'],
        ])
        self.assertEqual(tx['state'], 'rolled-back')
        self.assertGreaterEqual(save.call_count, 1)
        self.assertEqual(save.call_args_list[-1].args[0], tx)

    def run_candidate_failure(self, directory, *, compose_error=None, smoke_error=None,
                              postload_low=False, rollback_error=False):
        state = Path(directory)
        (state / 'transaction.json').write_text(json.dumps({
            'state': 'legacy-retired', 'revision': 'a' * 40,
            'rollback_containers_retained': False,
        }))
        primary_id = 'primary-id'
        primary = {
            'Id': primary_id, 'Image': 'sha256:' + 'b' * 64, 'Name': '/gb10-whisper-primary',
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {
                'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                'Image': 'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d',
            },
            'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}},
        }
        candidate = {
            'Id': 'candidate-id', 'Image': 'sha256:' + 'c' * 64, 'Name': '/gb10-whisper-candidate',
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {
                'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                'Labels': {'com.docker.compose.project': 'whisper-large-v3-stt',
                           'com.docker.compose.service': 'whisper-candidate'},
            },
        }
        candidate_inspects = 0
        primary_id_inspects = 0
        meminfo_reads = 0
        original_read_text = Path.read_text

        def inspect(name):
            nonlocal candidate_inspects, primary_id_inspects
            if name == LIFECYCLE.NEW[0]:
                return primary
            if name == LIFECYCLE.CANDIDATE:
                candidate_inspects += 1
                return None if candidate_inspects == 1 else candidate
            if name == primary_id:
                primary_id_inspects += 1
                if primary_id_inspects == 1:
                    return {**primary, 'State': {'Running': False, 'Health': {'Status': 'unhealthy'}}}
                return primary
            raise AssertionError('unexpected inspect target')

        def read_text(path, *args, **kwargs):
            nonlocal meminfo_reads
            if str(path) == '/proc/meminfo':
                meminfo_reads += 1
                available = 11_000_000 if postload_low and meminfo_reads == 2 else 20_000_000
                return f'MemAvailable: {available} kB\nMemTotal: 120000000 kB\n'
            return original_read_text(path, *args, **kwargs)

        run_calls = []
        def managed_run(args, **kwargs):
            run_calls.append(args)
            if args[:3] == ['docker', 'logs', '--tail']:
                return SimpleNamespace(returncode=0, stdout=(
                    'Worker initialized\n'
                    'Authorization: Bearer PRIVATE_FAKE_SECRET_42\n'
                    'transcript response: PRIVATE_FAKE_SECRET_42\n'
                    'Engine ready\n'), stderr='')
            if rollback_error and args[:4] == ['docker', 'stop', '--time', '180'] and args[-1] == 'candidate-id':
                raise LIFECYCLE.ManagedCommandFailure('docker', 9)
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        gate_calls = []
        candidate_stdout = io.StringIO()
        with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'inspect', side_effect=inspect), \
                patch.object(LIFECYCLE, 'candidate_image_id', return_value='sha256:' + 'c' * 64), \
                patch.object(LIFECYCLE, 'gate', side_effect=lambda chain, enabled: gate_calls.append(enabled)), \
                patch.object(LIFECYCLE, 'quiet'), patch.object(LIFECYCLE, 'run', side_effect=managed_run), \
                patch.object(LIFECYCLE, 'compose', side_effect=compose_error), \
                patch.object(LIFECYCLE, 'smoke', side_effect=smoke_error), \
                patch.object(LIFECYCLE.Path, 'read_text', new=read_text), \
                contextlib.redirect_stdout(candidate_stdout):
            with self.assertRaises(LIFECYCLE.CandidateQualificationFailure) as raised:
                LIFECYCLE.candidate_start('d' * 40)
        return state, raised.exception, gate_calls, run_calls, candidate_stdout.getvalue()

    def test_candidate_compose_failure_diagnostic_precedes_rollback_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, run_calls, stdout = self.run_candidate_failure(
                directory, compose_error=LIFECYCLE.ManagedCommandFailure(
                    'docker', 17, diagnostic_text='CUDA out of memory; API_KEY=PRIVATE_FAKE_SECRET_42'))
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            report_path = state / ('candidate-diagnostic-' + journal['failure_id'] + '.json')
            report = json.loads(report_path.read_text())
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertEqual(journal['failure_stage'], 'candidate-start')
            self.assertEqual(journal['failure_class'], 'ManagedCommandFailure')
            self.assertEqual(journal['failure_executable'], 'docker')
            self.assertEqual(journal['failure_exit_code'], 17)
            self.assertEqual(journal['failure_classification'], 'cuda-out-of-memory')
            self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
            self.assertIn('Worker initialized', report['candidate_log_excerpt'])
            self.assertIn('PRIVATE_FAKE_SECRET_42', report['candidate_log_excerpt'])
            self.assertIn('PRIVATE_FAKE_SECRET_42', report['command_diagnostic_excerpt'])
            self.assertLessEqual(report_path.stat().st_size, LIFECYCLE.MAX_PRIVATE_DIAGNOSTIC_BYTES * 3 + 4096)
            self.assertEqual(gate_calls, [True, False])
            log_index = run_calls.index(['docker', 'logs', '--tail', '120', LIFECYCLE.CANDIDATE])
            remove_index = run_calls.index(['docker', 'rm', 'candidate-id'])
            self.assertLess(log_index, remove_index)
            public = LIFECYCLE.format_candidate_failure(failure.metadata)
            self.assertIn('stage=candidate-start', public)
            self.assertIn('exit_code=17', public)
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', public)
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(LIFECYCLE.safe_candidate_metadata(journal)))
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(journal))
            self.assertEqual(stdout, '')

    def test_candidate_compose_runtime_error_is_reported_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, _, _ = self.run_candidate_failure(
                directory, compose_error=RuntimeError('model response PRIVATE_FAKE_SECRET_42'))
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            report_path = state / ('candidate-diagnostic-' + journal['failure_id'] + '.json')
            report = json.loads(report_path.read_text())
            public = LIFECYCLE.format_candidate_failure(failure.metadata)
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['failure_stage'], 'candidate-start')
            self.assertEqual(journal['failure_class'], 'RuntimeError')
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertEqual(gate_calls, [True, False])
            self.assertEqual(report['command_diagnostic_excerpt'], '')
            self.assertIn('PRIVATE_FAKE_SECRET_42', report['failure_diagnostic_excerpt'])
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', public)
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(LIFECYCLE.safe_candidate_metadata(journal)))
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(journal))

    def test_managed_command_failure_uses_only_safe_command_metadata(self):
        with patch.object(LIFECYCLE.subprocess, 'run', return_value=SimpleNamespace(
                returncode=23, stdout='PRIVATE_FAKE_SECRET_42',
                stderr='CUDA out of memory; API_KEY=PRIVATE_FAKE_SECRET_42')):
            with self.assertRaises(LIFECYCLE.ManagedCommandFailure) as raised:
                LIFECYCLE.run(['docker', 'compose', '--secret', 'PRIVATE_FAKE_SECRET_42'])
        failure = raised.exception
        self.assertEqual(failure.executable, 'docker')
        self.assertEqual(failure.exit_code, 23)
        self.assertEqual(failure.classification, 'cuda-out-of-memory')
        self.assertNotIn('PRIVATE_FAKE_SECRET_42', str(failure))
        self.assertIn('PRIVATE_FAKE_SECRET_42', failure.private_diagnostic)
        self.assertLessEqual(len(failure.private_diagnostic.encode('utf-8')),
                             LIFECYCLE.MAX_PRIVATE_DIAGNOSTIC_BYTES)
        self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps({
            'executable': failure.executable, 'exit_code': failure.exit_code,
            'classification': failure.classification,
        }))

    def test_failure_classifier_uses_only_allowlisted_categories(self):
        cases = {
            'CUDA out of memory': 'cuda-out-of-memory',
            'Free memory on GPU is less than requested GPU memory utilization': 'vllm-memory-budget-refusal',
            'SHA256 checksum mismatch while loading weights': 'model-cache-integrity',
            'container is unhealthy': 'container-health',
            'permission denied': 'permission-denied',
            'config gpu_memory_utilization=0.06; worker failed to initialize': 'unknown',
            'Verifying SHA256 checksum; weights loaded successfully': 'unknown',
            'host out of memory while allocating cache': 'unknown',
            'opaque PRIVATE_FAKE_SECRET_42 failure': 'unknown',
        }
        for text, expected in cases.items():
            with self.subTest(expected=expected):
                result = LIFECYCLE.classify_diagnostic(text)
                self.assertEqual(result, expected)
                self.assertIn(result, LIFECYCLE.FAILURE_CLASSIFICATIONS)
        bounded = LIFECYCLE.bounded_private_excerpt('safe diagnostic\n' * 10_000)
        self.assertLessEqual(len(bounded.encode('utf-8')), LIFECYCLE.MAX_CANDIDATE_LOG_BYTES)

    def test_candidate_smoke_failure_keeps_initial_stage_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, _, _ = self.run_candidate_failure(
                directory, smoke_error=RuntimeError('response=PRIVATE_FAKE_SECRET_42'))
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            public = LIFECYCLE.format_candidate_failure(failure.metadata)
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['failure_stage'], 'synthetic-relay-smoke')
            self.assertEqual(journal['failure_class'], 'RuntimeError')
            self.assertEqual(journal['failure_classification'], 'relay-smoke')
            self.assertIsNone(journal['failure_executable'])
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertEqual(gate_calls, [True, False])
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', public)
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(LIFECYCLE.safe_candidate_metadata(journal)))
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(journal))

    def test_candidate_postload_capacity_failure_keeps_gate_and_failure_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, _, _ = self.run_candidate_failure(directory, postload_low=True)
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['failure_stage'], 'post-load-memory-check')
            self.assertEqual(journal['failure_class'], 'ValueError')
            self.assertEqual(journal['failure_classification'], 'host-memory-headroom')
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertEqual(gate_calls, [True, False])
            self.assertIn('stage=post-load-memory-check', LIFECYCLE.format_candidate_failure(failure.metadata))

    def test_candidate_rollback_failure_is_separate_from_compose_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, _, _ = self.run_candidate_failure(
                directory, compose_error=LIFECYCLE.ManagedCommandFailure('docker', 17), rollback_error=True)
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            self.assertEqual(journal['state'], 'rollback-failed')
            self.assertEqual(journal['failure_stage'], 'candidate-start')
            self.assertEqual(journal['failure_class'], 'ManagedCommandFailure')
            self.assertEqual(journal['failure_exit_code'], 17)
            self.assertEqual(journal['rollback_failure_stage'], 'stop-candidate')
            self.assertEqual(journal['rollback_failure_class'], 'ManagedCommandFailure')
            self.assertEqual(journal['rollback_failure_exit_code'], 9)
            self.assertTrue(journal['failure_diagnostic_available'])
            self.assertTrue((state / ('candidate-diagnostic-' + journal['failure_id'] + '.json')).is_file())
            self.assertEqual(gate_calls, [True, False])
            public = LIFECYCLE.format_candidate_failure(failure.metadata)
            self.assertIn('exit_code=17', public)
            self.assertIn('rollback_stage=stop-candidate', public)
            self.assertIn('rollback_class=ManagedCommandFailure', public)

    def test_keyboard_interrupt_still_rolls_back_and_releases_intake_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            state, failure, gate_calls, _, _ = self.run_candidate_failure(
                directory, compose_error=KeyboardInterrupt('PRIVATE_FAKE_SECRET_42'), rollback_error=True)
            journal = json.loads((state / 'runtime-candidate.json').read_text())
            public = LIFECYCLE.format_candidate_failure(failure.metadata)
            self.assertEqual(journal['state'], 'rollback-failed')
            self.assertEqual(journal['failure_class'], 'KeyboardInterrupt')
            self.assertEqual(journal['failure_stage'], 'candidate-start')
            self.assertEqual(journal['rollback_failure_stage'], 'stop-candidate')
            self.assertEqual(gate_calls, [True, False])
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', public)
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', json.dumps(journal))

    def test_candidate_status_exposes_only_safe_failure_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _, _, _, _ = self.run_candidate_failure(
                directory, smoke_error=RuntimeError('PRIVATE_FAKE_SECRET_42'))
            stdout = io.StringIO()
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'protected'), \
                    contextlib.redirect_stdout(stdout):
                LIFECYCLE.candidate_status('d' * 40)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['failure_stage'], 'synthetic-relay-smoke')
            self.assertEqual(result['failure_class'], 'RuntimeError')
            self.assertNotIn('PRIVATE_FAKE_SECRET_42', stdout.getvalue())
            self.assertNotIn('candidate_log_excerpt', stdout.getvalue())

    def test_public_bootstrap_allows_only_declared_candidate_lifecycle_actions(self):
        self.assertIn('prepare-runtime-candidate', BOOTSTRAP.ACTIONS)
        self.assertIn('runtime-candidate', BOOTSTRAP.ACTIONS)
        self.assertIn('runtime-candidate-rollback', BOOTSTRAP.ACTIONS)
        self.assertIn('runtime-candidate-status', BOOTSTRAP.ACTIONS)

    def old_containers(self):
        return [{'Id': name + '-id', 'Name': '/' + name, 'Image': 'sha256:old',
                 'State': {'Running': True}, 'Config': {'Cmd': ['41f01f3fe87f28c78e2fbf8b568835947dd65ed9']},
                 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}}}
                for name in LIFECYCLE.OLD]

    def test_failed_start_restores_previous_containers_and_releases_intake_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            old = self.old_containers()
            with patch.object(LIFECYCLE, 'STATE', Path(directory)), \
                    patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'inspect', side_effect=old), \
                    patch.object(LIFECYCLE, 'gate') as gate, patch.object(LIFECYCLE, 'quiet'), \
                    patch.object(LIFECYCLE, 'run'), patch.object(LIFECYCLE, 'rollback') as rollback, \
                    patch.object(LIFECYCLE, 'compose', side_effect=RuntimeError('start failed')), \
                    patch.object(LIFECYCLE.Path, 'read_text', return_value='MemAvailable: 20000000 kB\n'):
                with self.assertRaises(RuntimeError): LIFECYCLE.apply('a' * 40)
                rollback.assert_called_once()
                self.assertEqual(gate.call_args_list[-1].args[1], False)
                self.assertEqual(len(rollback.call_args.args[0]['old']), 4)

    def test_rollback_refuses_to_remove_foreign_replacement(self):
        tx = {'old': [], 'state': 'replacing'}
        with patch.object(LIFECYCLE, 'inspect', return_value={
                'Id': 'foreign', 'Config': {'Labels': {'com.docker.compose.project': 'another-project'}}}), \
                patch.object(LIFECYCLE, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'ownership'): LIFECYCLE.rollback(tx)
        run.assert_not_called()

    def test_success_retires_only_stt_and_commits_after_gate_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            old = self.old_containers()
            calls = []
            with patch.object(LIFECYCLE, 'STATE', Path(directory)), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=[*old, *[{'State': {'Running': False}}] * 4]), \
                    patch.object(LIFECYCLE, 'gate', side_effect=lambda *args: calls.append(('gate', args[1]))), \
                    patch.object(LIFECYCLE, 'save', side_effect=lambda tx: calls.append(('save', tx['state']))), \
                    patch.object(LIFECYCLE, 'quiet'), patch.object(LIFECYCLE, 'smoke'), \
                    patch.object(LIFECYCLE, 'run') as run, patch.object(LIFECYCLE, 'compose'), \
                    patch.object(LIFECYCLE.Path, 'read_text', return_value='MemAvailable: 20000000 kB\nMemTotal: 120000000 kB\n'):
                LIFECYCLE.apply('a' * 40)
            self.assertEqual(calls[-2:], [('gate', False), ('save', 'applied')])
            self.assertTrue(all('plapre' not in str(call) and 'qwen' not in str(call)
                                for call in run.call_args_list))

    def test_aliases_share_full_whisper_without_hviske_fallback(self):
        catalog = json.loads((ROOT / 'config/model-catalog.json').read_text())
        routes = json.loads((ROOT / 'config/capability-routes.json').read_text())
        self.assertEqual(set(catalog['transcription_deployments']), {'whisper-large-v3-spark-primary'})
        for route in routes['transcription_routes'].values():
            self.assertEqual(route['deployments'], ['whisper-large-v3-spark-primary'])

    def test_retirement_requires_a_healthy_full_model_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({'state': 'applied', 'revision': 'a' * 40, 'old': []}))
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'inspect', return_value=None), \
                    patch.object(LIFECYCLE, 'run') as run, patch.object(LIFECYCLE.shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(ValueError, 'Healthy pinned full Whisper'):
                    LIFECYCLE.retire('b' * 40)
                self.assertTrue(all(call.args[0][0] == 'git' for call in run.call_args_list))
                remove.assert_not_called()

    def test_retirement_paths_cannot_include_full_model_or_recordings(self):
        paths = [str(path) for path in LIFECYCLE.retirement_paths()]
        self.assertEqual(len(paths), 5)
        self.assertTrue(all(path.startswith('/srv/gb10-ai/cache/') for path in paths))
        self.assertNotIn('/srv/gb10-ai/cache/huggingface/hub/models--openai--whisper-large-v3', paths)

    def test_missing_container_detection_accepts_current_docker_error_case(self):
        with patch.object(LIFECYCLE, 'run', side_effect=[
                SimpleNamespace(returncode=1, stderr='error: no such object: old'), SimpleNamespace(stdout='29.7')]):
            self.assertIsNone(LIFECYCLE.inspect('old'))


if __name__ == '__main__': unittest.main()
