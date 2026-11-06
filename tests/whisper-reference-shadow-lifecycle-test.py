#!/usr/bin/env python3
"""Synthetic owner/resource lifecycle checks; no Docker or model operations."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('reference_lifecycle', SCRIPTS / 'whisper-reference-shadow.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)
import whisper_reference_guard as guard

MODEL = {'schema_version': 1, 'model_bytes': 3095033483, 'model_sha256': 'a' * 64, 'model_snapshot': 'b' * 40,
         'runtime': 'openai-whisper-reference', 'runtime_commit': 'c' * 40,
         'loader_version': 'ggml-v1', 'loader_sha256': 'd' * 64,
         'base_manifest': 'nvcr.io/nvidia/pytorch@sha256:' + 'e' * 64,
         'base_arm64_image': 'sha256:' + 'f' * 64, 'decoding_defaults': {'beam_size': 5}}
REVISION = '1' * 40
IMAGE = 'sha256:' + '2' * 64
CONTAINER = '3' * 64
OWNER = '4' * 24
MEMORY = {'total_bytes': 100 * guard.GIB, 'available_bytes': 30 * guard.GIB, 'trial_limit_bytes': guard.LIMIT}


def record():
    return {'owner': OWNER, 'name': 'hc-whisper-reference-' + OWNER, 'container_id': CONTAINER,
            'image_id': IMAGE, 'source_revision': REVISION, 'model_pin': copy.deepcopy(MODEL),
            'helper_sha256': '5' * 64, 'phase': 'ready', 'boot_id': 'boot',
            'trial_deadline_monotonic': 10000, 'loading_deadline_monotonic': 1000}


def actual(value=None):
    value = value or record()
    return {'Name': '/' + value['name'], 'Id': CONTAINER, 'Image': IMAGE,
            'State': {'Running': True, 'OOMKilled': False, 'ExitCode': 0, 'Pid': 123}, 'RestartCount': 0,
            'Config': {'User': '10001:10001', 'Entrypoint': ['python3'], 'Cmd': ['-I', '-B', '-c', lifecycle.BOOTSTRAP],
                       'Env': [key + '=' + item for key, item in lifecycle.Runtime().environment(value).items()], 'Labels': {lifecycle.OWNER_LABEL: OWNER,
                       **lifecycle.expected_labels(REVISION, MODEL)}},
            'HostConfig': {'NetworkMode': 'none', 'Init': True, 'ShmSize': 67108864,
                           'Tmpfs': {'/tmp': lifecycle.TMPFS.split(':', 1)[1]}, 'Memory': guard.LIMIT, 'MemorySwap': guard.LIMIT,
                           'NanoCpus': 4000000000, 'ReadonlyRootfs': True, 'Privileged': False,
                           'PidsLimit': 128, 'LogConfig': {'Type': 'none'}, 'PortBindings': {},
                           'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                           'RestartPolicy': {'Name': 'no'}, 'DeviceRequests': [{'DeviceIDs': ['0'], 'Capabilities': [['gpu']]}]},
            'Mounts': [{'Type': 'bind', 'Source': lifecycle.MODEL,
                        'Destination': '/models/ggml-large-v3.bin', 'RW': False}]}


def health():
    keys = ('model_sha256', 'model_snapshot', 'runtime', 'runtime_commit', 'loader_version', 'loader_sha256', 'decoding_defaults')
    return {**{key: MODEL[key] for key in keys}, 'status': 'ok', 'inference_busy': False,
            'image_id': IMAGE, 'homecompute_revision': REVISION, 'device': 'cuda', 'gpu_model_buffer_bytes': 3094359040,
            'max_audio_seconds': 30, 'max_upload_bytes': 1000000}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        os.chmod(self.state, 0o700)
        self.runtime = lifecycle.Runtime(self.state)
        self.guard = self.runtime.guard
        self.record = record()

    def save(self):
        guard.write_json(self.state / 'active.json', self.record)

    def test_private_journal_rejects_symlink_and_mode(self):
        self.save()
        os.chmod(self.state / 'active.json', 0o644)
        with self.assertRaises(guard.Refused): guard.read_json(self.state / 'active.json')
        (self.state / 'alias').symlink_to(self.state / 'active.json')
        with self.assertRaises(OSError): guard.read_json(self.state / 'alias')

    def test_active_trial_refuses_build_before_slow_source_or_docker(self):
        self.save()
        with patch.object(lifecycle, 'published_source') as source, patch.object(lifecycle, 'command') as command, self.assertRaises(guard.Refused):
            self.runtime.build(REVISION)
            source.assert_not_called(); command.assert_not_called()

    def test_published_clean_exact_branch_head_required(self):
        with patch.object(lifecycle, 'command', side_effect=[REVISION, '', REVISION + '\t' + lifecycle.BRANCH]):
            lifecycle.published_source(REVISION)
        for outputs in ([REVISION, ' M changed'], [REVISION, '', '0' * 40 + '\t' + lifecycle.BRANCH]):
            with patch.object(lifecycle, 'command', side_effect=outputs), self.assertRaises(guard.Refused):
                lifecycle.published_source(REVISION)

    def test_cached_base_build_caps_and_no_pull(self):
        image = {'Id': IMAGE, 'Architecture': 'arm64', 'Config': actual()['Config']}
        values = [json.dumps([{'Id': MODEL['base_arm64_image'], 'Architecture': 'arm64'}]), '', json.dumps([image])]
        with patch.object(lifecycle, 'manifest', return_value=MODEL), patch.object(lifecycle, 'memory', return_value=MEMORY), patch.object(lifecycle, 'published_source'), patch.object(self.runtime, 'verify_image') as verify, patch.object(lifecycle, 'command', side_effect=values) as command:
            result = self.runtime.build(REVISION)
        args = command.call_args_list[1].args[0]
        self.assertIn('--pull=false', args)
        self.assertIn('--force-rm', args)
        self.assertEqual(args[args.index('--memory') + 1], str(guard.LIMIT))
        self.assertEqual(args[args.index('--memory-swap') + 1], str(guard.LIMIT))
        self.assertEqual(args[args.index('--cpu-quota') + 1], '400000')
        self.assertEqual(command.call_args_list[1].kwargs['env']['DOCKER_BUILDKIT'], '0')
        self.assertEqual(result['image_id'], IMAGE)
        verify.assert_called_once_with(IMAGE, REVISION, MODEL)

    def test_run_bootstrap_waits_before_adapter_without_exported_secret(self):
        args = self.runtime.run_arguments(self.record)
        for flag in ('--network', '--read-only', '--memory-swap', '--gpus', '--cap-drop', '--tmpfs'):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertEqual(args[-1], lifecycle.BOOTSTRAP)
        self.assertLess(lifecycle.BOOTSTRAP.index('/tmp/load-enabled'), lifecycle.BOOTSTRAP.index('os.execvp'))
        self.assertIn('secrets.token_hex(32)', lifecycle.BOOTSTRAP)
        self.assertNotIn('--publish', args)
        self.assertNotIn('SHADOW_DEVICE=cpu', args)
        self.assertIn('type=bind,src=' + lifecycle.MODEL + ',dst=/models/ggml-large-v3.bin,readonly', args)

    def test_owned_identity_and_limits_checked_before_mutation(self):
        with patch.object(lifecycle, 'command', return_value=json.dumps([actual()])):
            self.assertEqual(self.runtime.owned_container(self.record)['Id'], CONTAINER)
        variations = [('NanoCpus', 2000000000), ('MemorySwap', -1), ('NetworkMode', 'host'), ('DeviceRequests', [])]
        for field, value in variations:
            candidate = actual(); candidate['HostConfig'][field] = value
            with patch.object(lifecycle, 'command', return_value=json.dumps([candidate])), self.assertRaises(guard.Refused):
                self.runtime.owned_container(self.record)
        bad = actual(); bad['Mounts'][0]['RW'] = True
        with patch.object(lifecycle, 'command', return_value=json.dumps([bad])), self.assertRaises(guard.Refused):
            self.runtime.owned_container(self.record)

    def test_actual_health_and_idle_503_identity(self):
        with patch.object(self.runtime, 'owned_container'), patch.object(lifecycle, 'command', return_value=json.dumps(health())):
            self.assertEqual(self.runtime.health(self.record)['device'], 'cuda')
        unavailable = health(); unavailable['status'] = 'unavailable'
        with patch.object(self.runtime, 'owned_container'), patch.object(lifecycle, 'command', return_value=json.dumps(unavailable)):
            self.runtime.health(self.record, require_ready=False)
            with self.assertRaises(guard.Refused): self.runtime.health(self.record)
        for field, value in [('inference_busy', None), ('device', 'cpu'), ('gpu_model_buffer_bytes', True), ('runtime_commit', 'wrong')]:
            invalid = health(); invalid[field] = value
            with patch.object(self.runtime, 'owned_container'), patch.object(lifecycle, 'command', return_value=json.dumps(invalid)), self.assertRaises(guard.Refused):
                self.runtime.health(self.record, require_ready=False)

    def test_normal_stop_never_signals_busy_candidate(self):
        with patch.object(self.runtime, 'owned_container', return_value=actual()), patch.object(self.runtime, 'health', return_value={'inference_busy': True}), patch.object(guard, 'command') as command, self.assertRaises(guard.Refused):
            self.runtime.stop(self.record)
            command.assert_not_called()

    def test_idle_unavailable_cleanup_is_allowed_and_owns_unlink(self):
        self.save()
        stopped = actual(); stopped['State']['Running'] = False
        with patch.object(self.runtime, 'owned_container', side_effect=[actual(), stopped]), patch.object(self.runtime, 'health', return_value={'inference_busy': False, 'status': 'unavailable'}) as check, patch.object(guard, 'command') as command:
            result = self.runtime.stop(self.record)
        check.assert_called_once_with(self.record, require_ready=False)
        self.assertFalse(result['force_kill'])
        self.assertFalse((self.state / 'active.json').exists())
        self.assertEqual(command.call_args_list[-1].args[0], ['docker', 'rm', CONTAINER])

    def test_changed_journal_not_removed(self):
        self.save()
        changed = {**self.record, 'owner': 'a' * 24}
        with self.assertRaises(guard.Refused): self.guard.remove_active(changed)
        self.assertTrue((self.state / 'active.json').exists())

    def test_emergency_requires_current_reason_and_owned_candidate(self):
        with patch.object(self.guard, 'emergency_reason', return_value=None), patch.object(guard, 'command') as command, self.assertRaises(guard.Refused):
            self.guard.emergency_abort(self.record)
            command.assert_not_called()
        self.save()
        stopped = actual(); stopped['State']['Running'] = False
        with patch.object(self.guard, 'emergency_reason', return_value='request_deadline'), patch.object(self.runtime, 'owned_container', side_effect=[actual()] * 11 + [stopped]), patch.object(guard.time, 'sleep'), patch.object(guard, 'command') as command:
            result = self.guard.emergency_abort(self.record)
        self.assertTrue(result['force_kill']); self.assertFalse(result['partial_output_valid'])
        self.assertIn(['docker', 'kill', '--signal', 'KILL', CONTAINER], [call.args[0] for call in command.call_args_list])
        self.assertFalse((self.state / 'active.json').exists())

    def test_monitor_failure_and_transient_floor_latched(self):
        low = {**MEMORY, 'available_bytes': 1}
        with patch.object(guard, 'memory', side_effect=[MEMORY, guard.MemoryFloor(low), MEMORY]), patch.object(guard.Path, 'read_text', return_value='boot'), patch.object(guard.time, 'monotonic', return_value=100):
            first = guard.monitor_sample({})
            self.assertFalse(first['qualification_failed'])
            self.assertEqual(self.guard.emergency_reason(self.record), 'host_memory_floor')
            self.assertIsNone(self.guard.emergency_reason(self.record))
        latched = self.guard.include_latched_failure(self.record, first)
        self.assertTrue(latched['qualification_failed']); self.assertEqual(latched['minimum_host_available_bytes'], 1)

    def test_missing_memory_counters_fail_closed(self):
        with patch.object(guard.Path, 'read_text', return_value='MemTotal: 50 kB\n'), self.assertRaises(guard.MonitorUnavailable):
            guard.memory()
        with patch.object(guard, 'memory', side_effect=guard.MonitorUnavailable('missing')):
            self.assertEqual(self.guard.emergency_reason(self.record), 'monitor_unavailable')
        self.assertTrue(self.guard.include_latched_failure(self.record, {})['qualification_failed'])

    def test_preload_full_reserve_in_addition_to_ten_percent(self):
        text = 'MemTotal: 104857600 kB\nMemAvailable: 15728640 kB\n'
        with patch.object(guard.Path, 'read_text', return_value=text):
            guard.memory()
            with self.assertRaises(guard.MemoryFloor): guard.memory(loading=True)

    def test_mounted_preflight_reads_no_payload_and_rejects_bad_metadata(self):
        good = SimpleNamespace(st_mode=stat.S_IFREG | 0o444, st_dev=1, st_ino=2, st_size=MODEL['model_bytes'])
        def execute(before, after):
            with patch.object(os, 'lstat', return_value=before), patch.object(os, 'open', return_value=17), patch.object(os, 'fstat', return_value=after), patch.object(os, 'close') as close, patch.object(os, 'read', side_effect=AssertionError('No payload read')), patch.object(os, 'geteuid', return_value=10001), patch.object(os, 'getegid', return_value=10001), patch.object(sys, 'argv', ['probe', str(MODEL['model_bytes'])]), contextlib.redirect_stdout(io.StringIO()) as output:
                exec(lifecycle.MODEL_PREFLIGHT, {})  # noqa: S102 - Execute fixed source against mocked metadata only.
                close.assert_called_once_with(17)
                return json.loads(output.getvalue())
        self.assertEqual(execute(good, good)['bytes'], MODEL['model_bytes'])
        for bad in (SimpleNamespace(**{**vars(good), 'st_mode': stat.S_IFDIR}),
                    SimpleNamespace(**{**vars(good), 'st_mode': stat.S_IFLNK}),
                    SimpleNamespace(**{**vars(good), 'st_size': 1})):
            with self.assertRaises(AssertionError): execute(bad, bad)

    def test_mounted_probe_requires_exact_owned_uid_file_and_size(self):
        proof = {'uid': 10001, 'gid': 10001, 'readable_regular_file': True, 'bytes': MODEL['model_bytes'], 'payload_bytes_read': 0}
        with patch.object(self.runtime, 'owned_container') as owner, patch.object(lifecycle, 'command', return_value=json.dumps(proof)) as command:
            self.assertEqual(self.runtime.verify_mounted_model(self.record), proof)
        owner.assert_called_once_with(self.record)
        args = command.call_args.args[0]
        self.assertEqual(args[:5], ['docker', 'exec', '--user', '10001:10001', CONTAINER])
        self.assertEqual(args[-2:], [lifecycle.MODEL_PREFLIGHT, str(MODEL['model_bytes'])])
        self.assertEqual(command.call_args.kwargs['timeout'], 8)
        with patch.object(self.runtime, 'owned_container'), patch.object(lifecycle, 'command', return_value='{}'), self.assertRaises(guard.Refused):
            self.runtime.verify_mounted_model(self.record)

    def test_start_uses_owned_mounted_preflight_without_host_path_stat(self):
        proof = {'uid': 10001, 'gid': 10001, 'readable_regular_file': True, 'bytes': MODEL['model_bytes'], 'payload_bytes_read': 0}
        with patch.object(lifecycle, 'published_source'), patch.object(lifecycle, 'manifest', return_value=MODEL), patch.object(self.runtime, 'verify_image'), patch.object(lifecycle, 'memory', return_value=MEMORY), patch.object(self.runtime, 'source_digest', return_value='5' * 64), patch.object(Path, 'read_text', return_value='boot'), patch.object(Path, 'is_file', side_effect=PermissionError('protected host')), patch.object(Path, 'is_symlink', side_effect=PermissionError('protected host')), patch.object(lifecycle, 'command', return_value=CONTAINER), patch.object(self.runtime, 'verify_mounted_model', return_value=proof) as check, patch.object(self.runtime, 'no_active'):
            result = self.runtime.start(REVISION, IMAGE)
        self.assertEqual(result['phase'], 'awaiting_guard')
        self.assertEqual(result['mounted_model_preflight'], proof)
        check.assert_called_once()

    def test_bad_mounted_preflight_cleans_only_new_waiting_candidate(self):
        with patch.object(lifecycle, 'published_source'), patch.object(lifecycle, 'manifest', return_value=MODEL), patch.object(self.runtime, 'verify_image'), patch.object(lifecycle, 'memory', return_value=MEMORY), patch.object(self.runtime, 'source_digest', return_value='5' * 64), patch.object(Path, 'read_text', return_value='boot'), patch.object(lifecycle, 'command', return_value=CONTAINER), patch.object(self.runtime, 'verify_mounted_model', side_effect=guard.Refused('bad metadata')), patch.object(self.guard, 'terminate_owned', return_value={'status': 'stopped', 'force_kill': False}) as stop, self.assertRaises(guard.Refused):
            self.runtime.start(REVISION, IMAGE)
        self.assertFalse((self.state / 'active.json').exists())
        self.assertTrue(stop.call_args.kwargs['loading'])
        self.assertEqual(stop.call_args.args[0]['container_id'], CONTAINER)
        self.assertEqual(len(list(self.state.glob('failed-start-*'))), 1)

    def test_load_cannot_release_without_current_guard(self):
        self.record['phase'] = 'awaiting_guard'
        with patch.object(self.guard, 'alive', side_effect=guard.Refused('absent')), patch.object(lifecycle, 'command') as command, self.assertRaises(guard.Refused):
            self.runtime.release_load(self.record)
            command.assert_not_called()
        self.save()
        with patch.object(self.guard, 'alive', return_value={'qualification_failed': False}), patch.object(self.guard, 'sample', return_value=MEMORY) as sample, patch.object(self.runtime, 'verify_mounted_model', return_value={'bytes': MODEL['model_bytes']}), patch.object(lifecycle, 'command'):
            self.runtime.release_load(self.record)
        sample.assert_called_once_with(self.record, loading=True)
        self.assertEqual(guard.read_json(self.state / 'active.json')['phase'], 'loading')

    def test_request_marker_exact_id_and_nonextendable_630_seconds(self):
        self.save()
        with patch.object(self.guard, 'alive', return_value={'qualification_failed': False}), patch.object(self.guard, 'emergency_reason', return_value=None), patch.object(self.runtime, 'health', return_value={'inference_busy': False}), patch.object(guard.time, 'monotonic', return_value=100):
            result = self.guard.request_mark(self.record)
            self.assertEqual(result['request_deadline_monotonic'], 730)
            with self.assertRaises(guard.Refused): self.guard.request_mark(self.record)
            with self.assertRaises(guard.Refused): self.guard.request_mark(self.record, finish_id='wrong')
            self.guard.request_mark(self.record, finish_id=result['request_id'])
        self.assertNotIn('request_id', guard.read_json(self.state / 'active.json'))

    def test_observed_failure_cannot_start_request(self):
        with patch.object(self.guard, 'alive', return_value={'qualification_failed': True}), patch.object(self.runtime, 'health') as health_check, self.assertRaises(guard.Refused):
            self.guard.request_mark(self.record)
            health_check.assert_not_called()

    def test_guard_alive_rejects_changed_source(self):
        with patch.object(self.runtime, 'source_digest', return_value='different'), self.assertRaises(guard.Refused):
            self.guard.alive(self.record)

    def test_guard_registration_serializes_before_new_heartbeat(self):
        self.save()
        with patch.object(self.runtime, 'source_digest', return_value=self.record['helper_sha256']), patch.object(self.runtime, 'owned_container'), patch.object(self.guard, 'alive', return_value={'qualification_failed': False}), patch.object(guard, 'write_json') as write, patch.object(guard.signal, 'signal'), self.assertRaises(guard.Refused):
            self.guard.run()
            write.assert_not_called()

    def test_read_only_status_does_not_block_emergency_lock(self):
        self.save()
        runtime = self.runtime
        with patch.object(runtime.guard, 'lock', side_effect=AssertionError('status must not lock')), patch.object(runtime, 'owned_container'), patch.object(runtime.guard, 'alive', return_value={'qualification_failed': False}), patch.object(runtime, 'resource', return_value={'running': True}), patch.object(runtime, 'health', return_value=health()):
            self.assertEqual(runtime.status()['health']['status'], 'ok')
            self.assertFalse(runtime.status(guard_only=True)['qualification_failed'])

    def test_resources_include_page_cache_anon_and_events(self):
        from io import StringIO
        texts = ['1', '2', str(guard.LIMIT), 'oom 0\noom_kill 0\n', '0', '0', 'anon 100\nfile 200\nkernel 10\n']
        with patch.object(guard.Path, 'read_text', return_value='0::/system.slice/docker-' + CONTAINER + '.scope\n'), patch.object(guard.Path, 'open', side_effect=[StringIO(text) for text in texts]):
            result = self.runtime.cgroup_snapshot(actual())
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['counters']['memory.stat'], {'anon': '100', 'file': '200', 'kernel': '10'})
        self.assertEqual(result['counters']['memory.events'], 'oom 0\noom_kill 0\n')
        with patch.object(guard.Path, 'read_text', return_value='0::/unrelated-container\n'):
            self.assertEqual(self.runtime.cgroup_snapshot(actual())['status'], 'unverified')
        with patch.object(self.runtime, 'owned_container', return_value=actual()), patch.object(self.runtime, 'cgroup_snapshot', return_value=result), patch.object(self.guard, 'sample', return_value=MEMORY):
            evidence = self.runtime.resource(self.record)
        self.assertFalse(evidence['oom_killed']); self.assertEqual(evidence['restart_count'], 0)


class DockerDeclarationTests(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPTS.parent / 'deploy/whisper-reference-shadow'
        self.docker = (self.source / 'Dockerfile').read_text()
        self.pin = json.loads((self.source / 'model-pin.json').read_text())

    def test_exact_base_source_loader_pins_and_offline_identity(self):
        self.assertIn('ARG PYTORCH_BASE=' + self.pin['base_manifest'], self.docker)
        digest = hashlib.sha256((self.source / 'ggml_loader.py').read_bytes()).hexdigest()
        self.assertEqual(digest, self.pin['loader_sha256'])
        self.assertIn('ARG LOADER_SHA256=' + digest, self.docker)
        self.assertIn('runtime-commit="' + self.pin['runtime_commit'] + '"', self.docker)
        self.assertNotIn('PENDING', self.docker)
        for declaration in ('CUBLAS_WORKSPACE_CONFIG=:4096:8', 'NUMBA_CACHE_DIR=/tmp/numba',
                            'USER 10001:10001', 'groupadd --gid 10001 hc-whisper-ref',
                            'ENTRYPOINT ["python3", "-I", "-B", "/opt/homecompute/adapter.py"]'):
            self.assertIn(declaration, self.docker)

    def test_overlay_only_three_explicit_hashed_artifacts_without_resolution(self):
        lines = [line for line in (self.source / 'requirements.lock').read_text().splitlines() if line and not line.startswith('#')]
        names = []
        for line in lines:
            fields = shlex.split(line)
            self.assertEqual(len(fields), 4)
            self.assertEqual(fields[1], '@')
            self.assertTrue(fields[2].startswith('https://files.pythonhosted.org/packages/'))
            self.assertRegex(fields[3], r'^--hash=sha256:[0-9a-f]{64}$')
            names.append(fields[0])
        self.assertEqual(names, ['openai-whisper', 'tiktoken', 'more-itertools'])
        self.assertIn('cp312-cp312-manylinux_2_28_aarch64.whl', lines[1])
        for flag in ('--isolated', '--break-system-packages', '--no-index', '--no-deps', '--no-build-isolation', '--require-hashes'):
            self.assertIn(flag, self.docker)
        self.assertIn('assert all(m.version(k)==v for k,v in expected.items())', self.docker)

    def test_build_smoke_parses_and_checks_resolved_profile_without_model_load(self):
        snippets = []
        for line in self.docker.splitlines():
            if 'python3 -I -B -c ' in line:
                snippet = shlex.split(line.split('python3 -I -B -c ', 1)[1].rstrip(' \\'))[0]
                ast.parse(snippet)
                snippets.append(snippet)
        smoke = next(code for code in snippets if 'verify_reference_assets' in code)
        self.assertIn('resolve_suppression_tokens(get_tokenizer(True,num_languages=100),LANGUAGES)', smoke)
        self.assertIn('pin["decoding_defaults"]==json.loads(json.dumps(', smoke)
        self.assertIn('torch.cuda.is_initialized()', smoke)
        self.assertFalse(re.search(r'\b(load_model|transcribe|transcribe_pcm)\(', smoke))


if __name__ == '__main__':
    unittest.main()
