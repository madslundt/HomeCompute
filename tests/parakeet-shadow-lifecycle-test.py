#!/usr/bin/env python3
"""Shadow lifecycle refusals, actual identity checks and owned-only recovery."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('shadow_lifecycle', ROOT / 'scripts/parakeet-shadow.py')
APP = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(APP)
REVISION = 'a' * 40
IMAGE = 'sha256:' + 'b' * 64
CID = 'c' * 64
OWNER = 'd' * 24


def record():
    return {'name': 'hc-parakeet-shadow-' + OWNER, 'owner': OWNER, 'container_id': CID,
            'source_revision': REVISION, 'image_id': IMAGE, 'device': 'cuda', 'phase': 'ready',
            'boot_id': 'boot', 'trial_deadline_monotonic': 1000, 'loading_deadline_monotonic': 300}


def container(*, running=True):
    return {'Id': CID, 'Name': '/' + record()['name'], 'Image': IMAGE,
            'Config': {'Labels': {APP.OWNER_LABEL: OWNER}, 'User': '10001:10001'},
            'HostConfig': {'NetworkMode': 'none', 'Memory': APP.LIMIT, 'MemorySwap': APP.LIMIT,
                           'ReadonlyRootfs': True, 'Privileged': False, 'PidsLimit': 128,
                           'LogConfig': {'Type': 'none'}, 'CapDrop': ['ALL'],
                           'SecurityOpt': ['no-new-privileges'], 'RestartPolicy': {'Name': 'no'}},
            'Mounts': [{'Type': 'bind', 'Source': str(APP.STATE / ('model-' + APP.manifest()['model_snapshot'])),
                        'Destination': '/models', 'RW': False}], 'State': {'Running': running}}


class LifecycleTests(unittest.TestCase):
    def test_publication_requires_exact_clean_advertised_source(self):
        with patch.object(APP, 'command', side_effect=[REVISION, '', REVISION + '\trefs/heads/codex/parakeet-shadow']):
            APP.published_source(REVISION)
        for outputs in ([REVISION, ' M private'], [REVISION, '', 'f' * 40 + '\trefs/heads/codex/parakeet-shadow'], ['f' * 40]):
            with self.subTest(outputs=outputs), patch.object(APP, 'command', side_effect=outputs):
                self.assertRaises(APP.Refused, APP.published_source, REVISION)
        with patch.object(APP, 'command') as run:
            self.assertRaises(APP.Refused, APP.published_source, 'main')
            run.assert_not_called()

    def test_preload_reserves_six_gib_above_ten_percent_floor(self):
        total = 100 * APP.GIB
        def info(available):
            return f'MemTotal: {total // 1024} kB\nMemAvailable: {available // 1024} kB\n'
        with patch.object(Path, 'read_text', return_value=info(15 * APP.GIB)):
            self.assertRaises(APP.Refused, APP.memory, loading=True)
            APP.memory()
        with patch.object(Path, 'read_text', return_value=info(16 * APP.GIB)):
            self.assertEqual(APP.memory(loading=True)['trial_limit_bytes'], 6 * APP.GIB)
        with patch.object(Path, 'read_text', return_value=info(9 * APP.GIB)):
            self.assertRaises(APP.Refused, APP.memory)

    def test_run_has_no_network_ports_secrets_or_writable_host_mount(self):
        args = APP.run_arguments(record(), Path('/private/model'))
        for flag, value in [('--network', 'none'), ('--user', '10001:10001'), ('--memory', str(6 * APP.GIB)),
                            ('--memory-swap', str(6 * APP.GIB)), ('--cap-drop', 'ALL'), ('--log-driver', 'none')]:
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn('--read-only', args)
        self.assertEqual(args.count('--mount'), 1)
        self.assertEqual(args[args.index('--mount') + 1], 'type=bind,src=/private/model,dst=/models,readonly')
        self.assertFalse(set(args) & {'--privileged', '--publish', '-p', '--volume', '-v', '--pid', '--ipc'})
        self.assertEqual(args[-1], IMAGE)
        self.assertIn('secrets.token_hex(32)', APP.KEY_PROGRAM)
        self.assertNotIn('print(', APP.KEY_PROGRAM)
        cpu = record(); cpu['device'] = 'cpu'
        self.assertNotIn('--gpus', APP.run_arguments(cpu, Path('/private/model')))

    def test_image_identity_is_inspected_not_a_caller_label(self):
        model = APP.manifest()
        value = {'Id': IMAGE, 'Architecture': 'arm64', 'Config': {'User': '10001:10001',
                 'Labels': APP.expected_labels(REVISION, model)}}
        with patch.object(APP, 'command', return_value=json.dumps([value])):
            self.assertEqual(APP.verify_image(IMAGE, REVISION, model), IMAGE)
        for field, replacement in [('Id', 'sha256:' + 'e' * 64), ('Architecture', 'amd64')]:
            altered = {**value, field: replacement}
            with patch.object(APP, 'command', return_value=json.dumps([altered])):
                self.assertRaises(APP.Refused, APP.verify_image, IMAGE, REVISION, model)
        value['Config']['Labels']['io.homecompute.parakeet.model-sha256'] = 'f' * 64
        with patch.object(APP, 'command', return_value=json.dumps([value])):
            self.assertRaises(APP.Refused, APP.verify_image, IMAGE, REVISION, model)

    def test_container_identity_rejects_production_or_replaced_candidate(self):
        with patch.object(APP, 'command', return_value=json.dumps([container()])):
            self.assertEqual(APP.owned_container(record())['Id'], CID)
        for key, wrong in [('Name', '/gb10-whisper-cpp-primary'), ('Image', 'sha256:' + 'f' * 64), ('Id', 'e' * 64)]:
            altered = {**container(), key: wrong}
            with patch.object(APP, 'command', return_value=json.dumps([altered])):
                self.assertRaises(APP.Refused, APP.owned_container, record())
        altered = container(); altered['Config']['Labels'][APP.OWNER_LABEL] = 'other'
        with patch.object(APP, 'command', return_value=json.dumps([altered])):
            self.assertRaises(APP.Refused, APP.owned_container, record())
        for field, wrong in [('Memory', 0), ('ReadonlyRootfs', False), ('NetworkMode', 'host')]:
            altered = container(); altered['HostConfig'][field] = wrong
            with patch.object(APP, 'command', return_value=json.dumps([altered])):
                self.assertRaises(APP.Refused, APP.owned_container, record())
        altered = container(); altered['Mounts'][0]['RW'] = True
        with patch.object(APP, 'command', return_value=json.dumps([altered])):
            self.assertRaises(APP.Refused, APP.owned_container, record())

    def test_health_checks_loaded_identity_packages_device_and_parameters(self):
        model = APP.manifest()
        identity = {key: model[key] for key in ('model_repository', 'model_snapshot', 'model_sha256', 'runtime', 'runtime_commit', 'dtype')}
        identity.update(device='cuda:0', parameter_bytes=2500000000, package_versions=model['packages'])
        value = {'status': 'ready', 'inference_busy': False, 'image_id': IMAGE, 'homecompute_revision': REVISION, 'identity': identity}
        with patch.object(APP, 'command', return_value=json.dumps(value)):
            APP.health(record())
        unavailable = {**value, 'status': 'unavailable'}
        with patch.object(APP, 'command', return_value=json.dumps(unavailable)):
            self.assertRaises(APP.Refused, APP.health, record())
            APP.health(record(), require_ready=False)
        for key, wrong in [('device', 'cpu'), ('parameter_bytes', None), ('runtime_commit', 'f' * 40), ('package_versions', {})]:
            altered = {**value, 'identity': {**identity, key: wrong}}
            with patch.object(APP, 'command', return_value=json.dumps(altered)):
                self.assertRaises(APP.Refused, APP.health, record())

    def test_normal_stop_refuses_busy_without_any_signal(self):
        with patch.object(APP, 'owned_container', return_value=container()), patch.object(APP, 'health', return_value={'inference_busy': True}), patch.object(APP, 'command') as run:
            self.assertRaises(APP.Refused, APP.terminate_owned, record())
            run.assert_not_called()

    def test_normal_stop_signals_and_removes_only_recorded_id(self):
        with patch.object(APP, 'owned_container', side_effect=[container(), container(running=False)]), patch.object(APP, 'health', return_value={'inference_busy': False}), patch.object(APP, 'command') as run:
            result = APP.terminate_owned(record())
            self.assertFalse(result['force_kill'])
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             [['docker', 'kill', '--signal', 'TERM', CID], ['docker', 'rm', CID]])

    def test_normal_stop_never_escalates_when_exit_is_not_confirmed(self):
        with patch.object(APP, 'owned_container', return_value=container()), patch.object(APP, 'health', return_value={'inference_busy': False}), patch.object(APP, 'time') as clock, patch.object(APP, 'command') as run:
            self.assertRaises(APP.Refused, APP.terminate_owned, record())
            self.assertEqual(run.call_count, 1)
            self.assertNotIn('KILL', run.call_args.args[0])
            self.assertEqual(clock.sleep.call_count, 60)

    def test_emergency_requires_observed_floor_or_bound_monotonic_deadline(self):
        with patch.object(APP, 'memory'), patch.object(Path, 'read_text', return_value='boot'), patch.object(APP.time, 'monotonic', return_value=999):
            self.assertIsNone(APP.emergency_reason(record()))
        with patch.object(APP, 'memory'), patch.object(Path, 'read_text', return_value='boot'), patch.object(APP.time, 'monotonic', return_value=1000):
            self.assertEqual(APP.emergency_reason(record()), 'trial_deadline')
        with patch.object(APP, 'memory', side_effect=APP.Refused('floor')):
            self.assertEqual(APP.emergency_reason(record()), 'host_memory_floor')
        with patch.object(APP, 'emergency_reason', return_value=None), patch.object(APP, 'owned_container') as inspect:
            self.assertRaises(APP.Refused, APP.emergency_abort, record())
            inspect.assert_not_called()

    def test_emergency_escalates_only_owned_candidate_and_invalidates_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'emergency_reason', return_value='host_memory_floor'), patch.object(APP, 'owned_container', side_effect=[container()] * 11 + [container(running=False)]), patch.object(APP.time, 'sleep'), patch.object(APP, 'command') as run:
            result = APP.emergency_abort(record())
            self.assertTrue(result['force_kill']); self.assertFalse(result['partial_output_valid'])
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             [['docker', 'kill', '--signal', 'TERM', CID], ['docker', 'kill', '--signal', 'KILL', CID], ['docker', 'rm', CID]])
            self.assertEqual(len(list(Path(directory).glob('emergency-*.json'))), 1)

    def test_cleanup_cannot_unlink_another_trial_journal(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)):
            other = {**record(), 'owner': 'f' * 24}
            APP.write_json(Path(directory) / 'active.json', other)
            self.assertRaises(APP.Refused, APP.remove_active, record())
            self.assertEqual(APP.read_json(Path(directory) / 'active.json'), other)

    def test_guard_revalidates_owner_under_lock_before_emergency(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'owned_container'), patch.object(APP, 'emergency_reason', return_value='host_memory_floor'), patch.object(APP, 'emergency_abort') as abort:
            active = Path(directory) / 'active.json'; APP.write_json(active, record())
            other = {**record(), 'owner': 'f' * 24}
            with patch.object(APP, 'read_json', side_effect=[record(), record(), other]):
                APP.guard()
            abort.assert_not_called()
            self.assertTrue(active.exists())

    def test_request_deadline_cannot_be_extended_or_cleared_while_busy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'emergency_reason', return_value=None), patch.object(APP, 'health', return_value={'inference_busy': False}), patch.object(APP.time, 'monotonic', return_value=100):
            value = record()
            marked = APP.request_mark(value)
            self.assertEqual(marked['request_deadline_monotonic'], 730)
            self.assertRaises(APP.Refused, APP.request_mark, value)
            with patch.object(APP, 'health', return_value={'inference_busy': True}):
                self.assertRaises(APP.Refused, APP.request_mark, value, finish_id=marked['request_id'])
            self.assertRaises(APP.Refused, APP.request_mark, value, finish_id='wrong')
            APP.request_mark(value, finish_id=marked['request_id'])
            self.assertNotIn('request_deadline_monotonic', value)

    def test_request_timeout_precedes_whole_trial_deadline(self):
        value = {**record(), 'request_deadline_monotonic': 630}
        with patch.object(APP, 'memory'), patch.object(Path, 'read_text', return_value='boot'), patch.object(APP.time, 'monotonic', return_value=630):
            self.assertEqual(APP.emergency_reason(value), 'request_deadline')

    def test_build_uses_cached_base_and_enforced_legacy_resource_limits(self):
        model = APP.manifest()
        def run(args, **kwargs):
            if args[:3] == ['docker', 'image', 'inspect']:
                if args[3] == model['base_manifest']:
                    return json.dumps([{'Id': model['base_arm64_image'], 'Architecture': 'arm64'}])
                return json.dumps([{'Id': IMAGE}])
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'memory'), patch.object(APP, 'verify_image'), patch.object(APP, 'command', side_effect=run) as calls:
            APP.build(REVISION)
            commands = [call.args[0] for call in calls.call_args_list]
            self.assertFalse(any(args[:2] == ['docker', 'pull'] for args in commands))
            build = next(call for call in calls.call_args_list if call.args[0][:2] == ['docker', 'build'])
            self.assertEqual(build.kwargs['env']['DOCKER_BUILDKIT'], '0')
            args = build.args[0]
            for flag, value in [('--memory', str(6 * APP.GIB)), ('--memory-swap', str(6 * APP.GIB)), ('--cpu-period', '100000'), ('--cpu-quota', '400000')]:
                self.assertEqual(args[args.index(flag) + 1], value)
        with patch.object(APP, 'memory'), patch.object(APP, 'command', side_effect=APP.Refused('missing')) as calls:
            self.assertRaises(APP.Refused, APP.build, REVISION)
            self.assertEqual(calls.call_count, 1)

    def test_long_artifact_actions_refuse_active_trial_before_external_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'command') as run:
            APP.write_json(Path(directory) / 'active.json', record())
            self.assertRaises(APP.Refused, APP.require_no_active)
            self.assertRaises(APP.Refused, APP.build, REVISION)
            run.assert_not_called()

    def test_unavailable_monitor_is_an_explicit_fail_closed_emergency(self):
        for text in ('', 'MemTotal: 1 kB\nMemAvailable: 2 kB\n'):
            with patch.object(Path, 'read_text', return_value=text):
                self.assertEqual(APP.emergency_reason(record()), 'monitor_unavailable')
        with patch.object(Path, 'read_text', side_effect=OSError('private external detail')):
            self.assertEqual(APP.emergency_reason(record()), 'monitor_unavailable')

    def test_private_state_and_journal_refuse_symlinks_or_open_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'target'; target.mkdir(mode=0o700)
            link = root / 'link'; link.symlink_to(target)
            self.assertRaises(APP.Refused, APP.private_directory, link)
            target.chmod(0o755)
            self.assertRaises(APP.Refused, APP.private_directory, target)
            receipt = root / 'receipt.json'; APP.write_json(receipt, {'safe': True})
            self.assertEqual(APP.read_json(receipt), {'safe': True})
            receipt.chmod(0o644)
            self.assertRaises(APP.Refused, APP.read_json, receipt)

    def test_missing_model_or_low_memory_prevents_any_docker_run(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(APP, 'STATE', Path(directory)), patch.object(APP, 'verify_image'), patch.object(APP, 'artifacts') as artifact, patch.object(APP, 'command') as run:
            artifact.return_value.verify.side_effect = ValueError('hash mismatch')
            self.assertRaises(ValueError, APP.start, REVISION, IMAGE, 'cuda')
            run.assert_not_called()
            self.assertFalse((Path(directory) / 'active.json').exists())


class ArtifactTests(unittest.TestCase):
    def test_provision_download_is_bounded_and_verifies_before_publish(self):
        artifact = APP.artifacts()
        content = b'synthetic model'
        item = {'path': 'model.bin', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        manifest = {'model_repository': 'nvidia/parakeet-tdt-0.6b-v3', 'model_snapshot': REVISION, 'files': [item]}
        class Response(io.BytesIO):
            url = 'https://huggingface.co/synthetic'
        with tempfile.TemporaryDirectory() as directory, patch.object(artifact.urllib.request, 'urlopen', return_value=Response(content)) as download:
            state = Path(directory)
            result = artifact.provision(state, APP.SOURCE, manifest)
            self.assertEqual(result['files'][0]['sha256'], item['sha256'])
            self.assertEqual((state / ('model-' + REVISION) / 'model.bin').read_bytes(), content)
            self.assertEqual(download.call_args.args[0].headers, {'User-agent': 'HomeCompute-shadow'})
            self.assertEqual((state / ('model-' + REVISION)).stat().st_mode & 0o777, 0o555)
            (state / ('model-' + REVISION)).chmod(0o700)
        for bad in (content[:-1], content + b'extra', b'x' * len(content)):
            with tempfile.TemporaryDirectory() as directory, patch.object(artifact.urllib.request, 'urlopen', return_value=Response(bad)):
                with self.assertRaises(Exception):
                    artifact.provision(Path(directory), APP.SOURCE, manifest)
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
