"""Offline contract/failure tests: no Torch, model imports, downloads or Docker."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


C = module('observer_common', 'deploy/whisper-language-observer/common.py')
L = module('observer_cli', 'scripts/language_observer.py')
O, A = L.C, L.A
G = module('observer_graph', 'deploy/whisper-language-observer/graph.py')
E = module('observer_export', 'deploy/whisper-language-observer/export.py')
B = module('observer_build', 'scripts/language_observer_build.py')


def wav(frames=48000):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b'\x01\x00' * frames)
    return stream.getvalue()


def owned_row(record):
    return {'Id': record['container_id'], 'Image': record['image_id'], 'Name': '/' + record['name'],
            'Config': {'Labels': {O.LABEL: record['owner']}, 'User': '10001:10001',
                       'Env': ['CUDA_VISIBLE_DEVICES=', 'NVIDIA_VISIBLE_DEVICES=void']},
            'Mounts': [{'Type': 'bind', 'Source': a, 'Destination': b, 'RW': False} for a, b in record['mounts']],
            'HostConfig': {'Memory': O.LIMIT, 'MemorySwap': O.LIMIT, 'NanoCpus': 4_000_000_000,
                'Tmpfs': {'/tmp': 'rw,noexec,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700'},
                'ShmSize': 32 * 1024 * 1024, 'NetworkMode': 'none', 'ReadonlyRootfs': True, 'RestartPolicy': {'Name': 'no'},
                'LogConfig': {'Type': 'none'}, 'Privileged': False, 'CapDrop': ['ALL'],
                'SecurityOpt': ['no-new-privileges'], 'Init': True, 'PidsLimit': 128},
            'State': {'Running': True, 'OOMKilled': False}, 'RestartCount': 0}


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.state = self.directory / 'state'
        self.auth = {'source_revision': 'a' * 40, 'image_id': 'sha256:' + 'b' * 64,
                     'lifecycle_directory': str(self.state)}
        self.control = O.Controller(self.state, self.auth)
        self.record = {**self.auth, 'owner': 'owner', 'name': 'test-owned', 'container_id': 'cid',
                       'mounts': [], 'failed': False, 'job': None, 'mode': 'classify',
                       'phase': 'idle', 'used_ids': [], 'boot_id': 'boot', 'deadline': 999999}
        self.control.save(self.record)

    def tearDown(self):
        self.temp.cleanup()

    def test_source_check_imports_no_model_and_calls_no_external_action(self):
        with patch.object(O, 'run', side_effect=AssertionError('External call')), \
             patch.object(A.urllib.request, 'urlopen', side_effect=AssertionError('Download')):
            value = L.check_only()
        self.assertEqual(value['download_bytes'], 255015678)
        self.assertEqual(value['artifact_count'], 35)
        self.assertFalse(any(x in sys.modules for x in ('torch', 'speechbrain', 'torchaudio')))

    def test_locked_wheels_and_requirements_are_exact(self):
        pin, lock = A.pins()
        rows = lock['wheels']
        self.assertEqual(len(rows), 32)
        self.assertEqual(sum(row['size'] for row in rows), lock['wheel_bytes'])
        self.assertEqual(sum(row['size'] for row in pin['files']), pin['model_bytes'])
        lines = set((C.ROOT / 'requirements.lock').read_text().splitlines()[1:])
        self.assertEqual(lines, {f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in rows})
        self.assertTrue(all(len(row['sha256']) == 64 for row in A.catalog()))

    def test_pcm_retains_every_sample_and_rejects_conversion_or_partial_frames(self):
        for frames in (48000, 48001, 479999, 480000):
            raw = wav(frames)
            self.assertEqual(C.pcm(raw), raw[44:])
        for raw in (wav(47999), wav(480001), wav() + b'\0', b'no wav', wav()[:-2]):
            with self.assertRaises(ValueError):
                C.pcm(raw)
        raw = bytearray(wav()); raw[24:28] = (8000).to_bytes(4, 'little')
        with self.assertRaises(ValueError):
            C.pcm(raw)

    def test_public_probe_bank_is_nonstationary_and_partial_frame_distinct(self):
        for frames, kind in C.SYNTHETIC:
            self.assertEqual(len(C.synthetic(frames, kind)), 2 * frames)
        raw = C.synthetic(480000)
        self.assertNotEqual(raw[:32000], raw[-32000:])
        self.assertEqual(C.synthetic(48000, 'zero'), bytes(96000))
        self.assertNotEqual(C.sha(C.synthetic(48000)), C.sha(C.synthetic(48001)))

    def test_full_posterior_parity_rejects_one_hidden_class_difference(self):
        row = [-math.log(107)] * 107
        C.close(row, row.copy())
        for index in (0, 17, 20, 106):
            altered = row.copy(); altered[index] += .001
            with self.assertRaises(ValueError):
                C.close(row, altered)
        for bad in ([float('nan')] * 107, [0.] * 107, row[:-1]):
            with self.assertRaises(ValueError):
                C.check_numbers(bad)

    def test_checksum_reader_rejects_symlinks_duplicate_keys_and_overwrite(self):
        path = self.directory / 'artifact'
        path.write_bytes(b'123')
        link = self.directory / 'link'; link.symlink_to(path)
        with self.assertRaises(OSError):
            C.read(link, 3)
        with self.assertRaises(ValueError):
            C.read(path, 2)
        with self.assertRaises(ValueError):
            C.read(path, 3, expected='0' * 64)
        path.write_text('{"x": 1, "x": 2}')
        with self.assertRaises(ValueError):
            C.load(path)
        with self.assertRaises(FileExistsError):
            C.save(path, {})

    def test_redirects_refuse_non_public_and_non_https_before_follow(self):
        for url in ('http://huggingface.co/a', 'https://evil.invalid/a', 'https://huggingface.co.evil/a',
                    'https://user:password@huggingface.co/a', 'https://huggingface.co:444/a'):
            self.assertFalse(A.allowed_url(url))
            with self.assertRaises(ValueError):
                A.Redirect().redirect_request(None, None, 302, '', {}, url)
        self.assertTrue(A.allowed_url('https://cas-bridge.xethub.hf.co/public'))

    def test_artifact_reader_rejects_changed_size_sha_and_symlink(self):
        path = self.directory / 'weight'; path.write_bytes(b'123')
        A.verify_file(path, {'size': 3, 'sha256': A.sha(b'123')})
        for item in ({'size': 4, 'sha256': A.sha(b'123')}, {'size': 3, 'sha256': 'a' * 64}):
            with self.assertRaises(ValueError):
                A.verify_file(path, item)
        link = self.directory / 'weight-link'; link.symlink_to(path)
        with self.assertRaises(OSError):
            A.verify_file(link, {'size': 3, 'sha256': A.sha(b'123')})

    def test_host_admission_uses_sum_of_floor_and_full_worker_reserve(self):
        total_kib = 100_000_000
        floor_kib = total_kib // 10
        reserve_kib = O.LIMIT // 1024
        for available, passed in ((floor_kib, False), (reserve_kib, False),
                                  (floor_kib + reserve_kib - 1, False), (floor_kib + reserve_kib, True)):
            text = f'MemTotal: {total_kib} kB\nMemAvailable: {available} kB\n'
            with patch.object(Path, 'read_text', return_value=text):
                if passed:
                    O.memory(loading=True)
                else:
                    with self.assertRaises(ValueError):
                        O.memory(loading=True)

    def test_isolation_and_owner_changes_fail_closed(self):
        row = owned_row(self.record)
        def inspect(value):
            with patch.object(O, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps([value]).encode(), b'')):
                return self.control.inspect(self.record)
        inspect(row)
        for group, key, value in [('HostConfig', 'MemorySwap', -1), ('HostConfig', 'NetworkMode', 'bridge'),
                                  ('HostConfig', 'DeviceRequests', [{'Driver': 'nvidia'}]),
                                  ('HostConfig', 'Privileged', True), ('HostConfig', 'ReadonlyRootfs', False),
                                  ('Config', 'User', '0'), ('Config', 'Env', []), ('HostConfig', 'CapDrop', [])]:
            altered = copy.deepcopy(row); altered[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                inspect(altered)
        altered = copy.deepcopy(row); altered['Config']['Labels'][O.LABEL] = 'foreign'
        with self.assertRaises(ValueError):
            inspect(altered)

    def test_uncertain_docker_failure_is_not_worker_absence(self):
        for error, absent in ((b'No such container: cid', True), (b'Cannot connect to Docker', False)):
            with patch.object(O, 'run', return_value=subprocess.CompletedProcess([], 1, b'', error)):
                if absent:
                    self.assertIsNone(self.control.inspect(self.record, absent=True))
                else:
                    with self.assertRaises(ValueError):
                        self.control.inspect(self.record, absent=True)

    def test_model_job_needs_fresh_independent_guard_before_exec(self):
        with patch.object(self.control, 'guard_alive', return_value=False), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('Must not exec')):
            with self.assertRaises(ValueError):
                self.control._job('classify', 'new', wav())
        self.assertEqual(self.control.read()['used_ids'], [])

    def test_model_failure_latches_stops_only_owned_and_never_retries(self):
        with patch.object(self.control, '_job', side_effect=OSError('launch failed')), \
             patch.object(self.control, 'inspect', return_value=owned_row(self.record)), \
             patch.object(O, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            with self.assertRaises(OSError):
                self.control.job('classify', 'new', wav())
        self.assertTrue(self.control.read()['failed'])
        self.assertEqual(run.call_args.args[0], ['docker', 'stop', '--time', '5', 'cid'])

    def test_export_cannot_normally_stop_before_public_extraction(self):
        row = self.control.read(); row['mode'] = 'export'; self.control.save(row)
        with patch.object(O, 'run', side_effect=AssertionError('No stop before extraction')):
            with self.assertRaises(ValueError):
                self.control.stop()

    def test_state_is_bound_to_root_reviewed_path(self):
        with self.assertRaises(ValueError):
            O.Controller(self.directory / 'fresh-retry', self.auth)

    def test_explicit_cpu_pipeline_and_strict_checkpoint_no_unsafe_fallback(self):
        torch = Mock(); torch.Tensor = type('Tensor', (), {})
        torch.load.return_value = {'wrong-key': object()}
        native = Mock(); native.state_dict.return_value = {'expected': object()}
        with self.assertRaises(ValueError):
            E.checkpoint(torch, native, Path('public.ckpt'))
        torch.load.assert_called_once_with(Path('public.ckpt'), map_location='cpu', weights_only=True)
        native.load_state_dict.assert_not_called()
        source = (C.ROOT / 'export.py').read_text()
        self.assertIn('speechbrain.lobes.models.Xvector import Classifier', source)
        self.assertNotIn('from_hparams', source)
        self.assertNotIn('yaml', source.lower().split('from __future__', 1)[1])

    def test_graph_policy_rejects_python_custom_ops_and_cuda_constants(self):
        torch = Mock(); torch.Tensor = type('Tensor', (), {}); torch.device = type('Device', (), {})
        model = Mock(); model.parameters.return_value = []; model.buffers.return_value = []
        model.inlined_graph.nodes.return_value = []
        for op in ('prim::PythonOp', 'custom::execute', 'aten::unreviewed'):
            torch.jit.export_opnames.return_value = [op]
            with self.assertRaises(ValueError) as error:
                G.validate(model, torch)
            self.assertIn(op, str(error.exception))
        names = ['aten::unreviewed_' + str(index).zfill(3) + 'x' * 200 for index in range(40)]
        torch.jit.export_opnames.return_value = names[::-1]
        with self.assertRaises(ValueError) as error:
            G.validate(model, torch)
        detail = json.loads(str(error.exception).split(': ', 1)[1])
        self.assertEqual(detail['unsupported_operator_count'], 40)
        self.assertEqual(detail['unsupported_operators'], [name[:160] for name in names[:32]])
        torch.jit.export_opnames.return_value = ['aten::add.Tensor']
        self.assertEqual(G.validate(model, torch), ['aten::add.Tensor'])
        torch.jit.export_opnames.return_value = ['aten::sub_.Scalar', 'aten::amax']
        self.assertEqual(G.validate(model, torch), ['aten::amax', 'aten::sub_.Scalar'])
        self.assertFalse({'ge', 'bitwise_not'} & G.ATEN)
        node = Mock(); node.kind.return_value = 'prim::Constant'; node.blocks.return_value = []
        output = Mock(); output.toIValue.return_value = 'cuda:0'; node.outputs.return_value = [output]
        model.inlined_graph.nodes.return_value = [node]
        with self.assertRaises(ValueError):
            G.validate(model, torch)
        device = torch.device(); device.type = 'cuda'
        output.toIValue.return_value = device
        with self.assertRaises(ValueError):
            G.validate(model, torch)
        self.assertIn('repeat', G.ATEN)  # Pinned official ECAPA temporal-context expansion.

    def test_guard_deadline_failure_stops_without_launching_model(self):
        with patch.object(O, 'process', return_value='ticks'), \
             patch.object(Path, 'read_text', return_value='boot'), \
             patch.object(O, 'memory', return_value={'available': 100}), \
             patch.object(self.control, 'inspect', return_value=owned_row(self.record)), \
             patch.object(O.time, 'monotonic', return_value=1_000_000), \
             patch.object(O, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({
                 'memory.events': 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n',
                 'memory.swap.current': '0', 'memory.swap.max': '0'}).encode(), b'')), \
             patch.object(self.control, 'fail') as fail, \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('No model')):
            with self.assertRaises(ValueError):
                self.control.guard()
        fail.assert_called_once()

    def test_attempted_mode_cannot_restart_after_close(self):
        self.control.active.unlink()
        A.create(self.state / 'attempt-export.json', {'retry_allowed': False})
        image = {'Id': self.auth['image_id'], 'Os': 'linux', 'Architecture': 'arm64', 'Config': {'Labels': {
            'io.homecompute.source-revision': self.auth['source_revision'],
            'io.homecompute.language-observer.scope': 'experimental-cpu-classification-only',
            'io.homecompute.language-observer.base': O.PIN['base_image_id']}}}
        with patch.object(O, 'memory'), patch.object(O, 'run', return_value=
                subprocess.CompletedProcess([], 0, json.dumps([image]).encode(), b'')) as run:
            with self.assertRaises(FileExistsError):
                self.control.start('export')
        self.assertEqual(run.call_count, 1)  # Image metadata only; no new worker.

    def test_stale_guard_or_repeat_job_never_imports_or_executes(self):
        for stale, repeated in ((True, False), (False, True)):
            row = self.control.read(); row['last_guard_sample'] = 0 if stale else 100
            row['used_ids'] = ['same'] if repeated else []; self.control.save(row)
            with patch.object(self.control, 'guard_alive', return_value=True), \
                 patch.object(O.time, 'monotonic', return_value=100), \
                 patch.object(subprocess, 'Popen', side_effect=AssertionError('No model')):
                with self.assertRaises(ValueError):
                    self.control._job('classify', 'same', wav())

    def test_collect_copies_and_hashes_before_normal_stop(self):
        row = self.control.read(); row['mode'] = 'export'; row['used_ids'] = ['public-export']
        self.control.save(row)
        graph = b'public synthetic graph stub'
        manifest = {'graph_sha256': C.sha(graph), 'code_sha256': C.code_hashes(), 'model': C.PIN,
                    'source_revision': self.auth['source_revision']}
        raw = json.dumps(manifest).encode()
        A.create(self.state / 'result-owner-public-export.json', {
            'graph_sha256': C.sha(graph), 'manifest_sha256': C.sha(raw)})
        destination = self.directory / 'collected'
        def copy_public(args, **kwargs):
            self.assertEqual(args[:2], ['docker', 'cp'])
            Path(args[-1]).write_bytes(graph if args[-2].endswith('observer.ts') else raw)
            return subprocess.CompletedProcess(args, 0, b'', b'')
        with patch.object(self.control, 'guard_alive', return_value=True), \
             patch.object(self.control, 'inspect', return_value=owned_row(row)), \
             patch.object(O, 'run', side_effect=copy_public):
            receipt = self.control.collect(destination)
        self.assertTrue(receipt['copied_before_stop'])
        self.assertEqual(receipt['files_sha256']['observer.ts'], C.sha(graph))
        self.assertEqual(destination.stat().st_mode & 0o777, 0o555)
        self.assertIn('collection', self.control.read())
        destination.chmod(0o700)

    def test_hard_download_cap_stops_stream_and_preserves_failed_attempt(self):
        cap = 10
        pin = {**A.pins()[0], 'download_cap_bytes': cap, 'disk_reserve_bytes': 0}
        item = {'group': 'model', 'name': 'weight', 'size': 11, 'sha256': A.sha(b'x' * 11),
                'url': 'https://huggingface.co/pinned-public'}
        response = io.BytesIO(b'x' * 11); response.geturl = lambda: item['url']
        opener = Mock(); opener.open.return_value = response
        destination = self.directory / 'download'
        with patch.object(A, 'pins', return_value=(pin, {})), patch.object(A, 'catalog', return_value=[item]), \
             patch.object(A.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                A.fetch(destination)
            with self.assertRaises(ValueError):
                A.fetch(destination)
        state = A.read_json(destination / 'download.json')
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['downloaded_bytes'], cap)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual((destination / 'model/weight.part').stat().st_size, cap)


    def test_build_uses_real_legacy_caps_without_model_assets(self):
        args = B.arguments(self.auth, self.directory, 'owner', 'local-image')
        for flag in ('--memory=2147483648', '--memory-swap=2147483648', '--cpu-period=100000',
                     '--cpu-quota=400000', '--network=none', '--pull=false', '--force-rm'):
            self.assertIn(flag, args)
        self.assertNotIn('--build-context', args)
        source = (ROOT / 'scripts/language_observer_build.py').read_text()
        self.assertIn("'DOCKER_BUILDKIT': '0'", source)
        dockerfile = (C.ROOT / 'Dockerfile').read_text()
        self.assertIn('COPY wheels/', dockerfile)
        self.assertNotIn('--from=wheels', dockerfile)
        self.assertLess(dockerfile.index('build-owner='), dockerfile.index('RUN '))
        self.assertNotIn('model', [name for name in B.FILES if name.endswith('.ckpt')])

    def test_build_rejects_actual_resource_mismatch_and_still_cleans_only_owned(self):
        row = {'Id': 'build-cid', 'Config': {'Cmd': ['/bin/sh', '-c', 'python3 -I -B installer_check.py snapshot /tmp/nvidia-packages.json'], 'Labels': {B.BUILD_LABEL: 'token',
               'io.homecompute.source-revision': self.auth['source_revision']}},
               'HostConfig': {'Memory': 1}, 'State': {'Running': True}}
        def run(args, **kwargs):
            raw = json.dumps([row]).encode() if args[1:3] == ['container', 'inspect'] else b'build-cid\n'
            return subprocess.CompletedProcess(args, 0, raw, b'')
        with patch.object(B.C, 'run', side_effect=run) as action:
            with self.assertRaises(ValueError):
                B.containers('token', self.auth['source_revision'])
            self.assertEqual(B.containers('token', self.auth['source_revision'], cleanup=True), ['build-cid'])
        self.assertIn(['docker', 'stop', '--time', '5', 'build-cid'], [c.args[0] for c in action.call_args_list])
        row['Config']['Labels'][B.BUILD_LABEL] = 'foreign'
        with patch.object(B.C, 'run', side_effect=run) as action:
            with self.assertRaises(ValueError):
                B.containers('token', self.auth['source_revision'], cleanup=True)
        self.assertFalse(any(c.args[0][1] in ('stop', 'rm') for c in action.call_args_list))

    def test_completed_removed_build_step_is_not_claimed_as_observed(self):
        responses = [subprocess.CompletedProcess([], 0, b'gone-cid\n', b''),
                     subprocess.CompletedProcess([], 1, b'', b'No such container: gone-cid')]
        with patch.object(B.C, 'run', side_effect=responses):
            self.assertEqual(B.containers('token', self.auth['source_revision']), [])

    def test_metadata_checker_never_imports_model_packages(self):
        import ast
        tree = ast.parse((C.ROOT / 'installer_check.py').read_text())
        names = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [x.name for n in ast.walk(tree) if isinstance(n, ast.Import) for x in n.names]
        self.assertFalse(any(n and n.split('.')[0] in ('torch', 'torchaudio', 'speechbrain') for n in names))


    def test_build_metadata_steps_are_owned_but_not_run_resource_observations(self):
        row = {'Id': 'meta-cid', 'Config': {'Labels': {B.BUILD_LABEL: 'token',
               'io.homecompute.source-revision': self.auth['source_revision']},
               'Cmd': ['/bin/sh', '-c', '#(nop) LABEL metadata']},
               'HostConfig': {}, 'State': {'Running': True}}
        def run(args, **kwargs):
            raw = json.dumps([row]).encode() if args[1:3] == ['container', 'inspect'] else b'meta-cid\n'
            return subprocess.CompletedProcess(args, 0, raw, b'')
        with patch.object(B.C, 'run', side_effect=run):
            self.assertEqual(B.containers('token', self.auth['source_revision']), [])
            self.assertEqual(B.containers('token', self.auth['source_revision'], cleanup=True), ['meta-cid'])
            row['Config']['Cmd'] = ['/bin/sh', '-c', 'python3 installer_check.py snapshot /tmp/nvidia-packages.json']
            with self.assertRaises(KeyError):  # Actual RUN must provide the enforced caps.
                B.containers('token', self.auth['source_revision'])
            row['HostConfig'] = {'Memory': B.C.LIMIT, 'MemorySwap': B.C.LIMIT, 'CpuPeriod': 100000,
                                 'CpuQuota': 400000, 'NetworkMode': 'none'}
            row['Config']['Env'] = ['CUDA_VISIBLE_DEVICES=', 'NVIDIA_VISIBLE_DEVICES=void']
            self.assertEqual(B.containers('token', self.auth['source_revision']), ['meta-cid'])
            row['State']['Running'] = False
            self.assertEqual(B.containers('token', self.auth['source_revision']), [])


    def test_normal_close_cannot_hide_forced_or_missing_worker(self):
        for exit_code, normal in ((143, True), (137, False)):
            self.control.save(self.record)
            running = owned_row(self.record); stopped = copy.deepcopy(running)
            stopped['State'].update(Running=False, ExitCode=exit_code)
            with patch.object(self.control, 'inspect', side_effect=[running, stopped]), \
                 patch.object(O, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                result = self.control.stop()
            self.assertEqual(result['normal_idle_close'], normal)
            (self.state / 'closed-owner.json').unlink()
        self.control.save(self.record)
        with patch.object(self.control, 'inspect', return_value=None):
            self.assertFalse(self.control.stop()['normal_idle_close'])


    def test_writable_tmpfs_does_not_masquerade_as_writable_model_bind(self):
        row = owned_row(self.record)
        row['Mounts'].append({'Type': 'tmpfs', 'Destination': '/tmp', 'Source': '', 'RW': True})
        self.assertTrue(O.mounts_match(self.record, row))
        row['Mounts'].append({'Type': 'bind', 'Source': '/unexpected', 'Destination': '/model', 'RW': True})
        self.assertFalse(O.mounts_match(self.record, row))
        row['Mounts'].pop(); row['HostConfig']['Tmpfs']['/tmp'] = 'rw,size=512m'
        self.assertFalse(O.mounts_match(self.record, row))


    def test_authorization_accepts_only_canonical_ssh_or_https_same_repository(self):
        path = self.directory / 'authorization.json'
        value = {'root_reviewed': True, 'authorized': True, 'scope': 'auxiliary-language-observer-only',
                 'source_revision': self.auth['source_revision'], 'operations': ['fetch']}
        A.create(path, value); digest = A.sha(path.read_bytes())
        for remote, allowed in (('git@github.com:madslundt/HomeCompute.git', True),
                                ('https://github.com/madslundt/HomeCompute.git', True),
                                ('git@github.com:other/HomeCompute.git', False)):
            def run(args, **kwargs):
                if 'rev-parse' in args:
                    raw = self.auth['source_revision']
                elif 'status' in args:
                    raw = ''
                elif 'remote' in args:
                    raw = remote
                else:
                    raw = self.auth['source_revision'] + '\trefs/heads/codex/whisper-language-observer'
                return subprocess.CompletedProcess(args, 0, raw.encode(), b'')
            with patch.object(O, 'run', side_effect=run):
                if allowed:
                    self.assertEqual(O.authorization(path, digest, self.auth['source_revision'], 'fetch'), value)
                else:
                    with self.assertRaises(ValueError):
                        O.authorization(path, digest, self.auth['source_revision'], 'fetch')


    def test_exact_official_cpu_internal_version_differs_from_distribution_version(self):
        torch = types.ModuleType('torch'); torch.version = types.SimpleNamespace(cuda=None)
        modules = {'torch': torch}
        for name, attribute in [('speechbrain.lobes.features', 'Fbank'),
                                ('speechbrain.processing.features', 'InputNormalization'),
                                ('speechbrain.lobes.models.ECAPA_TDNN', 'ECAPA_TDNN'),
                                ('speechbrain.lobes.models.Xvector', 'Classifier')]:
            modules[name] = types.ModuleType(name); setattr(modules[name], attribute, object())
        versions = {row['name']: row['version'] for row in E.C.LOCK['wheels']}
        self.assertEqual(versions['torch'], '2.7.1')
        with patch.dict(sys.modules, modules), patch.object(sys, 'prefix', '/isolated'), \
             patch.object(sys, 'base_prefix', '/base'), patch.object(Path, 'read_text', return_value='include-system-site-packages = false'), \
             patch.object(E.importlib.metadata, 'version', side_effect=versions.__getitem__), \
             patch.dict(os.environ, {'LD_LIBRARY_PATH': '', 'LD_PRELOAD': '', 'PYTHONPATH': '', 'PYTHONHOME': ''}):
            torch.__version__ = '2.7.1+cpu'
            self.assertIs(E.dependencies()[0], torch)
            for version in ('2.7.1', '2.7.1+cu126', '2.11.0a0'):
                torch.__version__ = version
                with self.assertRaises(ValueError):
                    E.dependencies()
            torch.__version__ = '2.7.1+cpu'; torch.version.cuda = '12.6'
            with self.assertRaises(ValueError):
                E.dependencies()



if __name__ == '__main__':
    unittest.main()
