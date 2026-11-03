#!/usr/bin/env python3
"""STT replacement gates, transaction recovery and consumer migration behavior."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


LIFECYCLE = module('whisper_lifecycle', 'scripts/setup-whisper-stt.py')
BOOTSTRAP = module('whisper_bootstrap', 'scripts/bootstrap-whisper-stt.py')
DAYTHREAD = module('daythread_whisper', 'scripts/migrate-daythread-whisper.py')
QUALIFICATION = module('whisper_cuda_qualification', 'scripts/whisper-cuda-qualification.py')


def qualification_fixture(*, truncated=False):
    directory = tempfile.TemporaryDirectory(prefix='homecompute-whisper-stt.', dir='/tmp')
    staging = Path(directory.name)
    staging.chmod(0o700)
    audio_path = staging / 'sample.wav'
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'PCM'))
        wav.writeframes(b'\0\0' * 1600)
    audio = stream.getvalue()
    frames = 1600
    if truncated:
        audio = bytearray(audio)
        struct.pack_into('<I', audio, 40, frames + 100)
        audio = bytes(audio)
        frames += 100
    audio_path.write_bytes(audio)
    audio_path.chmod(0o600)
    manifest = {
        'schema_version': 1, 'max_audio_seconds': 1, 'max_upload_bytes': 40_000,
        'native_timeout_seconds': 30,
        'inputs': [{'id': 'sample', 'file': audio_path.name, 'bytes': len(audio),
                    'sha256': hashlib.sha256(audio).hexdigest(), 'frames': frames}],
    }
    manifest_path = staging / 'qualification-manifest.json'
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    return directory, manifest_path, audio


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
        with patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'provision_cpp_role_key', return_value='existing'), \
                patch.object(LIFECYCLE.Path, 'is_symlink', return_value=False), \
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
            'io.homecompute.whisper-cpp.cuda-patch': LIFECYCLE.CPP_CUDA_PATCH_VERSION,
            'io.homecompute.whisper-cpp.cuda-patcher-sha256': LIFECYCLE.CPP_CUDA_PATCHER_SHA256,
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

    def test_promoted_image_identity_requires_exact_qualified_image_and_source_revision(self):
        labels = {
            'org.opencontainers.image.revision': LIFECYCLE.CPP_RUNTIME_COMMIT,
            'io.homecompute.source-revision': LIFECYCLE.CPP_QUALIFIED_SOURCE_REVISION,
            'io.homecompute.whisper-cpp.runtime-commit': LIFECYCLE.CPP_RUNTIME_COMMIT,
            'io.homecompute.whisper-cpp.model-sha256': LIFECYCLE.CPP_MODEL_SHA256,
            'io.homecompute.whisper-cpp.backend': 'cuda-121a',
            'io.homecompute.whisper-cpp.cuda-patch': LIFECYCLE.CPP_CUDA_PATCH_VERSION,
            'io.homecompute.whisper-cpp.cuda-patcher-sha256': LIFECYCLE.CPP_CUDA_PATCHER_SHA256,
            'io.homecompute.whisper-cpp.cuda-base': LIFECYCLE.CUDA_BASE_IMAGE,
        }
        image = {'Id': LIFECYCLE.CPP_QUALIFIED_IMAGE_ID, 'Os': 'linux', 'Architecture': 'arm64',
                 'Config': {'Labels': labels}}
        with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([image]))):
            self.assertEqual(LIFECYCLE.qualified_cuda_candidate_image_id(), LIFECYCLE.CPP_QUALIFIED_IMAGE_ID)
        for mutation in ({'Id': 'sha256:' + 'c' * 64},
                         {'label': 'io.homecompute.source-revision', 'value': 'a' * 40}):
            candidate = copy.deepcopy(image)
            if 'Id' in mutation:
                candidate['Id'] = mutation['Id']
            else:
                candidate['Config']['Labels'][mutation['label']] = mutation['value']
            with patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout=json.dumps([candidate]))), \
                    self.assertRaisesRegex(ValueError, 'Qualified.*identity'):
                LIFECYCLE.qualified_cuda_candidate_image_id()

    def test_production_network_override_is_opt_in_to_promotion_compose_only(self):
        identity = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        with patch.object(LIFECYCLE.pwd, 'getpwnam', return_value=identity), \
                patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout='', returncode=0)) as run:
            LIFECYCLE.compose_cuda('up', 'whisper-cpp-cuda')
            qualification_call = run.call_args.args[0]
            self.assertFalse(any('compose-cuda-production.yaml' in arg for arg in qualification_call))
            LIFECYCLE.compose_cuda('up', 'whisper-cpp-cuda', production_network=True)
            promotion_call = run.call_args.args[0]
        self.assertTrue(any('compose-cuda-production.yaml' in arg for arg in promotion_call))
        base_index = next(index for index, arg in enumerate(promotion_call) if 'compose-cuda.yaml' in arg)
        override_index = next(index for index, arg in enumerate(promotion_call)
                              if 'compose-cuda-production.yaml' in arg)
        self.assertLess(base_index, override_index)

    def test_promotion_uses_only_the_qualified_image_and_full_recording_bounds(self):
        with patch.object(LIFECYCLE, 'guards'), \
                patch.object(LIFECYCLE, 'qualified_cuda_candidate_image_id', return_value=LIFECYCLE.CPP_QUALIFIED_IMAGE_ID) as image, \
                patch.object(LIFECYCLE, 'provision_cpp_role_key', return_value='existing') as key, \
                patch.object(LIFECYCLE, 'verify_cpp_model') as model, \
                patch.object(LIFECYCLE, 'compose') as compose, \
                patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout='[{"Id":"sha256:' + 'a' * 64 + '"}]')) as run, \
                patch.object(LIFECYCLE, 'cuda_candidate_start') as start:
            LIFECYCLE.promote_qualified_cuda_candidate('e' * 40)
        image.assert_called_once_with()
        key.assert_called_once_with()
        model.assert_called_once_with(LIFECYCLE.CPP_MODEL_PATH)
        compose.assert_called_once_with('--profile', 'cpp-production', 'build', 'whisper-cpp-http',
                                        relay_image='homecompute/stt-http:2.0.0-cpp-' + 'e' * 12)
        run.assert_called_once_with(['docker', 'image', 'inspect', 'homecompute/stt-http:2.0.0-cpp-' + 'e' * 12])
        start.assert_called_once_with('e' * 40, max_audio_seconds=1200, max_upload_bytes=40_000_000,
                                      native_timeout_seconds=1800, promotion=True,
                                      relay_image='homecompute/stt-http:2.0.0-cpp-' + 'e' * 12,
                                      relay_image_id='sha256:' + 'a' * 64)

    def test_cuda_candidate_prepare_builds_only_native_pinned_image(self):
        with patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'provision_cpp_model', return_value='existing') as model, \
                patch.object(LIFECYCLE, 'provision_cpp_key', return_value='existing') as key, \
                patch.object(LIFECYCLE, 'validate_cpp_artifact_files') as artifacts, \
                patch.object(LIFECYCLE, 'check_cuda_host'), \
                patch.object(LIFECYCLE, 'compose_cuda') as compose, \
                patch.object(LIFECYCLE, 'cuda_candidate_image_id', return_value='sha256:' + 'a' * 64):
            LIFECYCLE.prepare_cuda_candidate('d' * 40)
        model.assert_called_once_with()
        key.assert_called_once_with()
        artifacts.assert_called_once_with()
        compose.assert_called_once_with('--profile', 'cuda-candidate', 'build', '--pull=false',
                                        'whisper-cpp-cuda', revision='d' * 40)

    def test_production_cpp_credential_is_a_private_idempotent_copy_of_role_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source-key'
            target = root / 'whisper_cpp_role_key'
            source.write_bytes(b'private-production-role-key\n')
            source.chmod(0o400)
            source_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0,
                                          st_size=len(source.read_bytes()))
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE.os, 'fstat', return_value=source_info), \
                    patch.object(LIFECYCLE.os, 'fchown'), \
                    patch.object(LIFECYCLE, 'validate_provisioned_key') as validate:
                self.assertEqual(LIFECYCLE.provision_cpp_role_key(target, source), 'copied')
                self.assertEqual(target.read_bytes(), b'private-production-role-key')
                self.assertEqual(LIFECYCLE.provision_cpp_role_key(target, source), 'existing')
                self.assertEqual(validate.call_count, 2)
                source.chmod(0o600)
                source.write_bytes(b'a-different-production-role-key\n')
                with self.assertRaisesRegex(ValueError, 'differs'):
                    LIFECYCLE.provision_cpp_role_key(target, source)
    def test_cuda_prepare_failure_is_sanitized_and_keeps_bounded_private_diagnostic(self):
        error = LIFECYCLE.ManagedCommandFailure(
            'docker', 23, diagnostic_text='PRIVATE_FAKE_SECRET_72 CUDA build failure')
        metadata = {'diagnostic_id': 'a' * 12, 'stage': 'build-image',
                    'failure_class': 'ManagedCommandFailure', 'classification': 'unknown',
                    'executable': 'docker', 'exit_code': 23, 'diagnostic_available': True}
        with patch.object(LIFECYCLE, 'STATE', Path('/private/state')), \
                patch.object(LIFECYCLE, 'write_candidate_diagnostic') as write:
            with patch.object(LIFECYCLE.os, 'urandom', return_value=bytes.fromhex('a' * 12)):
                actual = LIFECYCLE.cuda_prepare_failure('build-image', error)
        self.assertEqual(actual, metadata)
        report = write.call_args.args[1]
        self.assertIn('PRIVATE_FAKE_SECRET_72', report['private_detail'])
        public = LIFECYCLE.format_cuda_prepare_failure(actual)
        self.assertIn('stage=build-image', public)
        self.assertIn('exit_code=23', public)
        self.assertNotIn('PRIVATE_FAKE_SECRET_72', public)
        self.assertNotIn('PRIVATE_FAKE_SECRET_72', json.dumps(actual))

    def test_cuda_prepare_reports_artifact_stage_and_does_not_leak_exception_text(self):
        with patch.object(LIFECYCLE, 'STATE', Path('/private/state')), \
                patch.object(LIFECYCLE, 'guards'), \
                patch.object(LIFECYCLE, 'check_cuda_host'), \
                patch.object(LIFECYCLE, 'provision_cpp_model', side_effect=RuntimeError('PRIVATE_FAKE_SECRET_93')), \
                patch.object(LIFECYCLE, 'write_candidate_diagnostic') as write, \
                patch.object(LIFECYCLE.os, 'urandom', return_value=bytes.fromhex('b' * 12)):
            with self.assertRaises(LIFECYCLE.CudaPrepareFailure) as failure:
                LIFECYCLE.prepare_cuda_candidate('d' * 40)
        metadata = failure.exception.metadata
        self.assertEqual(metadata['stage'], 'provision-model')
        self.assertEqual(metadata['failure_class'], 'RuntimeError')
        self.assertNotIn('PRIVATE_FAKE_SECRET_93', LIFECYCLE.format_cuda_prepare_failure(metadata))
        self.assertNotIn('PRIVATE_FAKE_SECRET_93', json.dumps(metadata))
        self.assertIn('PRIVATE_FAKE_SECRET_93', write.call_args.args[1]['private_detail'])

    def test_cuda_model_provisioning_reuses_valid_file_and_refuses_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.bin'
            header = struct.pack('<12i', 0x67676D6C, 51866, 1500, 1280, 20, 32,
                                 448, 1280, 20, 32, 128, 1)
            model.write_bytes(header)
            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_gid=10001, st_size=len(header))
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SIZE', len(header)), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SHA256', hashlib.sha256(header).hexdigest()), \
                    patch.object(Path, 'lstat', return_value=info):
                self.assertEqual(LIFECYCLE.provision_cpp_model(model), 'existing')
                model.write_bytes(b'wrong')
                info.st_size = 5
                with self.assertRaisesRegex(ValueError, 'size does not match'):
                    LIFECYCLE.provision_cpp_model(model)
                info.st_mode = stat.S_IFREG | 0o644
                with self.assertRaisesRegex(ValueError, 'root:10001 mode 0440'):
                    LIFECYCLE.provision_cpp_model(model)

    def test_cuda_key_provisioning_is_idempotent_and_never_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_gid=10001,
                                       st_size=len(b'NEW_PRIVATE_KEY_VALUE'))
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE.secrets, 'token_urlsafe', return_value='NEW_PRIVATE_KEY_VALUE'), \
                    patch.object(LIFECYCLE.os, 'fchown'), patch.object(Path, 'lstat', return_value=key_info):
                with patch.object(LIFECYCLE.os, 'geteuid', return_value=0):
                    self.assertEqual(LIFECYCLE.provision_cpp_key(key), 'created')
                    saved = key.read_bytes()
                    self.assertEqual(LIFECYCLE.provision_cpp_key(key), 'existing')
                self.assertEqual(key.read_bytes(), saved)
                self.assertEqual(saved, b'NEW_PRIVATE_KEY_VALUE')
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o440)
                key_info.st_mode = stat.S_IFREG | 0o600
                with self.assertRaisesRegex(ValueError, 'root:10001 mode 0440'):
                    LIFECYCLE.provision_cpp_key(key)
                key_info.st_mode = stat.S_IFREG | 0o440
                key.chmod(0o640)
                key.write_bytes(b'bad\n' + b'x' * 16)
                key_info.st_size = 20
                with self.assertRaisesRegex(ValueError, 'printable non-whitespace'):
                    LIFECYCLE.provision_cpp_key(key)

    def test_cuda_model_download_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.bin'
            class Response:
                headers = {'Content-Length': '6'}
                calls = 0
                def __enter__(self): return self
                def __exit__(self, *_args): return None
                def read(self, _size):
                    self.calls += 1
                    return b'bad' if self.calls == 1 else b''
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SIZE', 6), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_FREE_SPACE_RESERVE', 0), \
                    patch.object(LIFECYCLE.shutil, 'disk_usage', return_value=SimpleNamespace(free=100)), \
                    patch.object(LIFECYCLE.urllib.request, 'urlopen', return_value=Response()), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SHA256', '0' * 64):
                with self.assertRaisesRegex(ValueError, 'size or checksum'):
                    LIFECYCLE.provision_cpp_model(model)
            self.assertFalse(model.exists())
            self.assertEqual(list(Path(directory).glob('*.partial')), [])

    def test_cuda_model_download_refuses_low_space_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.bin'
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SIZE', 100), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_FREE_SPACE_RESERVE', 10), \
                    patch.object(LIFECYCLE.shutil, 'disk_usage', return_value=SimpleNamespace(free=109)), \
                    patch.object(LIFECYCLE.urllib.request, 'urlopen') as urlopen:
                with self.assertRaisesRegex(ValueError, 'Insufficient free space'):
                    LIFECYCLE.provision_cpp_model(model)
            urlopen.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cuda_artifact_directory_preserves_safe_root_owned_metadata_and_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'root-owned-parent'
            parent.mkdir(mode=0o750)
            target = parent / 'secrets'
            target.mkdir(mode=0o750)
            target_mode = stat.S_IMODE(target.stat().st_mode)
            real_lstat = Path.lstat

            def root_metadata(path):
                info = real_lstat(path)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=0,
                                       st_size=info.st_size)

            with patch.object(Path, 'lstat', autospec=True, side_effect=root_metadata), \
                    patch.object(LIFECYCLE.os, 'chown') as chown:
                LIFECYCLE.ensure_private_directory(target, gid=10001)
                chown.assert_not_called()
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), target_mode)
                target.chmod(0o777)
                with self.assertRaisesRegex(ValueError, 'ownership or permissions are unsafe'):
                    LIFECYCLE.ensure_private_directory(target, gid=10001)
                target.chmod(0o750)
                target.rmdir()
                target.symlink_to(parent)
                with self.assertRaisesRegex(ValueError, 'ownership or permissions are unsafe'):
                    LIFECYCLE.ensure_private_directory(target, gid=10001)

    def test_cuda_model_download_is_atomic_pinned_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.bin'
            header = struct.pack('<12i', 0x67676D6C, 51866, 1500, 1280, 20, 32,
                                 448, 1280, 20, 32, 128, 1)
            digest = hashlib.sha256(header).hexdigest()
            class Response:
                headers = {'Content-Length': str(len(header))}
                calls = 0
                def __enter__(self): return self
                def __exit__(self, *_args): return None
                def read(self, _size):
                    self.calls += 1
                    return header if self.calls == 1 else b''
            info = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_gid=10001,
                                   st_size=len(header))
            with patch.object(LIFECYCLE, 'ensure_private_directory'), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SIZE', len(header)), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_SHA256', digest), \
                    patch.object(LIFECYCLE, 'CPP_MODEL_FREE_SPACE_RESERVE', 0), \
                    patch.object(LIFECYCLE.shutil, 'disk_usage', return_value=SimpleNamespace(free=100)), \
                    patch.object(LIFECYCLE.urllib.request, 'urlopen', return_value=Response()) as urlopen, \
                    patch.object(LIFECYCLE.os, 'chown'), patch.object(Path, 'lstat', return_value=info):
                self.assertEqual(LIFECYCLE.provision_cpp_model(model, url='https://example.invalid/pinned'), 'downloaded')
                saved = model.read_bytes()
                self.assertEqual(LIFECYCLE.provision_cpp_model(model), 'existing')
            self.assertEqual(saved, header)
            self.assertEqual(urlopen.call_count, 1)
            self.assertEqual(list(Path(directory).glob('*.partial')), [])

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

    def test_cuda_exposed_candidate_timeout_remains_busy_guarded_on_retry(self):
        candidate = {'Id': 'cpp-cuda-id', 'Image': 'sha256:' + 'c' * 64,
                     'State': {'Running': True},
                     'Config': {'Labels': {'com.docker.compose.project': LIFECYCLE.CUDA_CANDIDATE_PROJECT,
                                           'com.docker.compose.service': 'whisper-cpp-cuda'}}}
        primary = {'Id': 'primary-id', 'Image': 'sha256:' + 'b' * 64,
                   'Name': '/gb10-whisper-primary',
                   'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        tx = {'revision': 'd' * 40, 'state': 'rollback-failed',
              'candidate_stage': 'synthetic-candidate-smoke',
              'native_timeout_seconds': 60,
              'candidate_image_id': candidate['Image'], 'primary_id': primary['Id'],
              'primary_image': primary['Image'], 'primary_restart': 'unless-stopped'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(LIFECYCLE, 'STATE', Path(directory)), \
                patch.object(LIFECYCLE, 'inspect', side_effect=[candidate]) as inspect, \
                patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout='{"busy":true}', returncode=0)) as run, \
                patch.object(LIFECYCLE.time, 'monotonic', side_effect=[0, 181]), \
                patch.object(LIFECYCLE.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'did not become idle') as error:
                LIFECYCLE.cuda_candidate_rollback(tx)
            metadata = LIFECYCLE.cuda_candidate_failure(tx, error.exception, rollback=True)
            journal = json.loads((Path(directory) / 'cuda-candidate.json').read_text())
            self.assertEqual(journal['state'], 'rollback-failed')
            self.assertTrue(journal['candidate_exposed'])
            self.assertEqual(metadata['rollback_failure_classification'], 'unknown')
            self.assertNotIn('docker stop', repr(run.call_args_list))
            inspect.assert_called_once_with(LIFECYCLE.CUDA_CANDIDATE)

            idle_candidate = candidate
            primary_healthy = primary
            inspection_results = [idle_candidate, None, primary_healthy, primary_healthy]
            def inspect_retry(name):
                return inspection_results.pop(0)
            idle_results = [SimpleNamespace(stdout='{"busy":false}', returncode=0) for _ in range(3)]
            def run_retry(command, **_kwargs):
                if command[:3] == ['docker', 'exec', LIFECYCLE.CUDA_CANDIDATE]:
                    return idle_results.pop(0)
                return SimpleNamespace(stdout='', returncode=0)
            with patch.object(LIFECYCLE, 'inspect', side_effect=inspect_retry), \
                    patch.object(LIFECYCLE, 'run', side_effect=run_retry) as retry_run, \
                    patch.object(LIFECYCLE.time, 'monotonic', return_value=0), \
                    patch.object(LIFECYCLE.time, 'sleep'):
                LIFECYCLE.cuda_candidate_rollback(tx)
            self.assertEqual(tx['state'], 'rolled-back')
            self.assertTrue(tx['candidate_exposed'])
            self.assertTrue(any(call.args[0][:3] == ['docker', 'exec', LIFECYCLE.CUDA_CANDIDATE]
                                for call in retry_run.call_args_list))
            self.assertEqual(len(idle_results), 0)

    def test_cuda_rollback_waits_for_predecoder_upload_after_native_goes_idle(self):
        candidate = {'Id': 'cpp-cuda-id', 'Image': 'sha256:' + 'c' * 64,
                     'State': {'Running': True},
                     'Config': {'Labels': {'com.docker.compose.project': LIFECYCLE.CUDA_CANDIDATE_PROJECT,
                                           'com.docker.compose.service': 'whisper-cpp-cuda'}}}
        tx = {'revision': 'd' * 40, 'state': 'rollback-failed',
              'candidate_stage': 'recording-relay-smoke',
              'native_timeout_seconds': 60,
              'candidate_image_id': candidate['Image'], 'primary_id': 'primary-id',
              'primary_image': 'sha256:' + 'b' * 64, 'primary_restart': 'unless-stopped'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(LIFECYCLE, 'STATE', Path(directory)), \
                patch.object(LIFECYCLE, 'inspect', side_effect=[candidate]) as inspect, \
                patch.object(LIFECYCLE, 'run', side_effect=[
                    SimpleNamespace(stdout='{"busy":false}'),
                    SimpleNamespace(stdout='ESTAB 0 0 127.0.0.1:8007 127.0.0.1:443'),
                ]) as run, \
                patch.object(LIFECYCLE.time, 'monotonic', side_effect=[0, 181]), \
                patch.object(LIFECYCLE.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'did not become idle') as error:
                LIFECYCLE.cuda_candidate_rollback(tx)
            self.assertTrue(LIFECYCLE.cuda_candidate_was_exposed(tx))
            journal = json.loads((Path(directory) / 'cuda-candidate.json').read_text())
            self.assertTrue(journal['candidate_exposed'])
            self.assertNotIn('docker stop', repr(run.call_args_list))
            self.assertEqual(inspect.call_count, 1)

    def test_cuda_stop_keeps_intake_gate_when_rollback_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'cuda-candidate.json').write_text(json.dumps({
                'revision': 'd' * 40, 'state': 'promoted', 'chain': 'HC-STT-' + 'a' * 12,
                'candidate_exposed': True,
            }))
            gate_calls = []
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'protected'), \
                    patch.object(LIFECYCLE, 'gate', side_effect=lambda chain, enabled: gate_calls.append(enabled)), \
                    patch.object(LIFECYCLE, 'cuda_candidate_rollback', side_effect=ValueError('active upload')):
                with self.assertRaises(LIFECYCLE.CudaCandidateFailure):
                    LIFECYCLE.cuda_candidate_stop('d' * 40)
            journal = json.loads((state / 'cuda-candidate.json').read_text())
            self.assertEqual(gate_calls, [True])
            self.assertEqual(journal['state'], 'rollback-failed')
            self.assertTrue(journal['gate_held'])
            self.assertEqual(journal['rollback_failure_class'], 'ValueError')

    def test_cuda_stop_retry_reuses_same_held_gate_and_releases_only_after_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            chain = 'HC-STT-' + 'b' * 12
            (state / 'cuda-candidate.json').write_text(json.dumps({
                'revision': 'd' * 40, 'state': 'rollback-failed', 'chain': chain,
                'candidate_exposed': True, 'gate_held': True,
            }))
            gate_calls = []

            def complete_rollback(tx, *, idle_timeout=None):
                tx.update(state='rolled-back', rollback_status='completed')

            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'protected'), \
                    patch.object(LIFECYCLE, 'gate', side_effect=lambda nonce, enabled: gate_calls.append((nonce, enabled))), \
                    patch.object(LIFECYCLE, 'cuda_candidate_rollback', side_effect=complete_rollback):
                LIFECYCLE.cuda_candidate_stop('d' * 40)
            journal = json.loads((state / 'cuda-candidate.json').read_text())
            self.assertEqual(gate_calls, [(chain, True), (chain, False)])
            self.assertEqual(journal['state'], 'rolled-back')
            self.assertEqual(journal['rollback_status'], 'completed')
            self.assertFalse(journal['gate_held'])

    def test_existing_exact_gate_is_reused_instead_of_recreated(self):
        chain = 'HC-STT-' + 'a' * 12
        rules = '\n'.join([
            '-N ' + chain,
            '-A ' + chain + ' -p tcp -m conntrack --ctstate NEW --ctorigdst 127.0.0.1 --ctorigdstport 8003 -j REJECT',
            '-A ' + chain + ' -p tcp -m conntrack --ctstate NEW --ctorigdst 127.0.0.1 --ctorigdstport 8007 -j REJECT',
        ])
        def run(command, **_kwargs):
            if command[2:4] == ['-S', chain]:
                return SimpleNamespace(returncode=0, stdout=rules)
            if command[2:4] == ['-C', 'OUTPUT']:
                return SimpleNamespace(returncode=0, stdout='')
            raise AssertionError('unexpected gate command')
        with patch.object(LIFECYCLE, 'run', side_effect=run):
            LIFECYCLE.gate(chain, True)

    def test_gate_status_distinguishes_absent_from_unavailable(self):
        chain = 'HC-STT-' + 'a' * 12
        absent = SimpleNamespace(returncode=1, stdout='', stderr='iptables: No chain/target/match by that name.')
        with patch.object(LIFECYCLE, 'run', return_value=absent):
            self.assertFalse(LIFECYCLE.gate_is_active(chain))
        unavailable = SimpleNamespace(returncode=1, stdout='', stderr='iptables: permission denied')
        with patch.object(LIFECYCLE, 'run', return_value=unavailable), \
                self.assertRaisesRegex(ValueError, 'state is unavailable'):
            LIFECYCLE.gate_is_active(chain)

    def test_cuda_candidate_smoke_executes_valid_multipart_pcm_child_program(self):
        code = LIFECYCLE.cuda_candidate_smoke_program()
        body_requests = []
        secret = 'private-test-token-with-enough-bytes'
        def fake_open(path, mode='r', *args, **kwargs):
            if path == '/fake/key':
                return io.BytesIO(secret.encode('ascii'))
            return open(path, mode, *args, **kwargs)
        def fake_urlopen(request, timeout):
            body_requests.append((request, timeout))
            return io.BytesIO(b'{"text":"synthetic"}')
        stdout = io.StringIO()
        with patch('builtins.open', side_effect=fake_open), \
                patch('urllib.request.urlopen', side_effect=fake_urlopen), \
                patch.dict(os.environ, {'WHISPER_CPP_API_KEY_FILE': '/fake/key'}), \
                contextlib.redirect_stdout(stdout):
            exec(compile(code, '<cuda-smoke-child>', 'exec'), {'__name__': 'smoke_test'})
        self.assertEqual(len(body_requests), 1)
        request, timeout = body_requests[0]
        self.assertEqual(timeout, 120)
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + secret)
        body = request.data
        self.assertIn(b'\r\nContent-Disposition:', body)
        self.assertIn(b'\r\n\r\n', body)
        self.assertNotIn(b'\\r\\n', body)
        wav_bytes = body.split(b'Content-Type: audio/wav\r\n\r\n', 1)[1].split(b'\r\n--smoke--', 1)[0]
        with __import__('wave').open(io.BytesIO(wav_bytes), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()),
                             (1, 2, 16000, 16000))
            self.assertEqual(wav.readframes(16000), bytes(32000))
        self.assertNotIn(secret, stdout.getvalue())

    def test_promoted_relay_smoke_covers_standard_and_longform_auth_provenance(self):
        code = LIFECYCLE.promoted_relay_smoke_program(LIFECYCLE.CPP_QUALIFIED_SOURCE_REVISION)
        self.assertIn("post('/v1/audio/transcriptions'", code)
        self.assertIn("post('/v1/recording-transcriptions'", code)
        self.assertIn("'carry_initial_prompt','false'", code)
        self.assertIn("'max_len','-1'", code)
        for header in ('X-HomeCompute-STT-Model-SHA256', 'X-HomeCompute-STT-Model-Snapshot',
                       'X-HomeCompute-STT-Runtime', 'X-HomeCompute-STT-Runtime-Commit',
                       'X-HomeCompute-STT-HomeCompute-Revision', 'X-HomeCompute-STT-Image-ID'):
            self.assertIn(header, code)
        self.assertNotIn('FAIR data', code)
        self.assertNotIn('2022', code)

        captured = []
        secret = 'synthetic-role-key-with-length'
        headers = {
            'X-HomeCompute-STT-Model-SHA256': LIFECYCLE.CPP_MODEL_SHA256,
            'X-HomeCompute-STT-Model-Snapshot': LIFECYCLE.CPP_MODEL_SNAPSHOT,
            'X-HomeCompute-STT-Runtime': 'whisper.cpp',
            'X-HomeCompute-STT-Runtime-Commit': LIFECYCLE.CPP_RUNTIME_COMMIT,
            'X-HomeCompute-STT-HomeCompute-Revision': LIFECYCLE.CPP_QUALIFIED_SOURCE_REVISION,
            'X-HomeCompute-STT-Image-ID': LIFECYCLE.CPP_QUALIFIED_IMAGE_ID,
        }
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(b'{"text":"synthetic","segments":[]}')
                self.headers = headers
            def __enter__(self): return self
            def __exit__(self, *_args): self.close()
        def fake_open(path, mode='r', *args, **kwargs):
            if path == '/run/secrets/whisper_cpp_api_key':
                return io.BytesIO(secret.encode())
            return open(path, mode, *args, **kwargs)
        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return Response()
        output = io.StringIO()
        with patch('builtins.open', side_effect=fake_open), \
                patch('urllib.request.urlopen', side_effect=fake_urlopen), \
                contextlib.redirect_stdout(output):
            exec(compile(code, '<promoted-relay-smoke>', 'exec'), {'__name__': 'smoke_test'})
        self.assertEqual([item[0].full_url for item in captured], [
            'http://127.0.0.1:8007/v1/audio/transcriptions',
            'http://127.0.0.1:8007/v1/recording-transcriptions'])
        self.assertTrue(all(item[1] == 180 for item in captured))
        self.assertTrue(all(item[0].get_header('Authorization') == 'Bearer ' + secret for item in captured))
        self.assertNotIn(secret, output.getvalue())
        long_body = captured[1][0].data
        self.assertIn(b"name=\"carry_initial_prompt\"", long_body)
        self.assertIn(b"name=\"no_timestamps\"", long_body)
        self.assertNotIn(b'\\r\\n', long_body)

    def test_promotion_retains_original_relay_and_starts_dedicated_image(self):
        old = {'Id': 'old-relay', 'Image': 'sha256:' + 'a' * 64, 'Name': '/gb10-whisper-http',
               'State': {'Running': True, 'Health': {'Status': 'healthy'}},
               'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
               'Config': {'Labels': {'com.docker.compose.project': 'whisper-large-v3-stt',
                                     'com.docker.compose.service': 'whisper-http'}}}
        new = {'Id': 'new-relay', 'Image': 'sha256:' + 'b' * 64,
               'State': {'Running': True, 'Health': {'Status': 'healthy'}},
               'Config': {'Labels': {'com.docker.compose.project': 'whisper-large-v3-stt',
                                     'com.docker.compose.service': 'whisper-cpp-http'}}}
        tx = {'revision': 'e' * 40, 'relay_image': 'homecompute/stt-http:cpp',
              'relay_candidate_image_id': new['Image']}
        with patch.object(LIFECYCLE, 'inspect', side_effect=[old, None, new]), \
                patch.object(LIFECYCLE, 'save_cuda_candidate') as save, \
                patch.object(LIFECYCLE, 'run') as run, \
                patch.object(LIFECYCLE, 'compose') as compose:
            LIFECYCLE.promote_relay(tx)
        self.assertEqual(tx['relay_original']['id'], 'old-relay')
        self.assertEqual(tx['relay_original']['image'], old['Image'])
        self.assertTrue(tx['relay_update_attempted'])
        self.assertEqual(tx['relay_candidate_id'], 'new-relay')
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['docker', 'update', '--restart', 'no', 'old-relay'],
            ['docker', 'stop', '--time', '30', 'old-relay'],
            ['docker', 'rename', 'old-relay', 'gb10-whisper-http-before-cpp-' + 'e' * 12],
            ['docker', 'update', '--restart', 'no', 'new-relay'],
        ])
        compose.assert_called_once()
        self.assertIn('whisper-cpp-http', compose.call_args.args)
        self.assertEqual(save.call_count, 2)
        self.assertTrue(all(call.args[0] is tx for call in save.call_args_list))

    def test_rollback_restores_exact_retained_relay_container_and_restart_policy(self):
        current = {'Id': 'new-relay', 'Image': 'sha256:' + 'b' * 64,
                   'State': {'Running': True},
                   'Config': {'Labels': {'com.docker.compose.project': 'whisper-large-v3-stt',
                                         'com.docker.compose.service': 'whisper-cpp-http'}}}
        prior = {'Id': 'old-relay', 'Image': 'sha256:' + 'a' * 64,
                 'Name': '/gb10-whisper-http-before-cpp-eeeeeeeeeeee',
                 'State': {'Running': False}}
        healthy = {**prior, 'Name': '/gb10-whisper-http',
                   'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
        tx = {'relay_candidate_id': 'new-relay', 'relay_candidate_image_id': current['Image'],
              'relay_original': {'id': prior['Id'], 'image': prior['Image'],
                  'name': '/gb10-whisper-http', 'backup_name': prior['Name'],
                  'restart': 'unless-stopped', 'was_running': True}}
        stages = []
        def inspect(name):
            if name == 'gb10-whisper-cpp-http': return current
            if name == 'old-relay': return prior if not stages or 'restore-original-relay' not in stages else healthy
            return None
        def run(command, **_kwargs):
            if command[:2] == ['docker', 'rename']:
                stages.append('restore-original-relay')
            return SimpleNamespace(stdout='')
        with patch.object(LIFECYCLE, 'inspect', side_effect=inspect), \
                patch.object(LIFECYCLE, 'run', side_effect=run) as docker:
            LIFECYCLE.restore_promoted_relay(tx, stages.append)
        self.assertIn(['docker', 'rm', 'new-relay'], [call.args[0] for call in docker.call_args_list])
        self.assertIn(['docker', 'rename', 'old-relay', 'gb10-whisper-http'],
                      [call.args[0] for call in docker.call_args_list])
        self.assertIn(['docker', 'update', '--restart', 'unless-stopped', 'old-relay'],
                      [call.args[0] for call in docker.call_args_list])

    def test_cuda_candidate_key_check_executes_and_rejects_newline(self):
        program = LIFECYCLE.cuda_candidate_key_check_program()
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            for value, expected in ((b'private-key-with-enough-bytes', 0),
                                    (b'private-key-with-enough-bytes\n', 1)):
                key.chmod(0o600) if key.exists() else None
                key.write_bytes(value)
                key.chmod(0o440)
                result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(key)],
                                        env={**os.environ, 'WHISPER_CPP_API_KEY_FILE': str(key)},
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode == 0, expected == 0)
                self.assertNotIn(value.decode('ascii'), result.stdout + result.stderr)

    def test_active_cuda_journal_blocks_other_primary_mutating_lifecycles_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'cuda-candidate.json').write_text(json.dumps({
                'revision': 'd' * 40, 'state': 'rollback-failed', 'candidate_exposed': True,
            }))
            operations = (
                ('apply', lambda: LIFECYCLE.apply('d' * 40)),
                ('classic rollback', lambda: LIFECYCLE.rollback({'old': []})),
                ('runtime candidate', lambda: LIFECYCLE.candidate_start('d' * 40)),
                ('runtime rollback', lambda: LIFECYCLE.candidate_stop('d' * 40)),
                ('retire', lambda: LIFECYCLE.retire('d' * 40)),
            )
            for label, operation in operations:
                with self.subTest(label=label), \
                        patch.object(LIFECYCLE, 'STATE', state), \
                        patch.object(LIFECYCLE, 'protected'), \
                        patch.object(LIFECYCLE, 'guards'), \
                        patch.object(LIFECYCLE, 'run') as run, \
                        patch.object(LIFECYCLE, 'compose') as compose, \
                        patch.object(LIFECYCLE, 'compose_cuda') as compose_cuda, \
                        patch.object(LIFECYCLE, 'inspect') as inspect:
                    with self.assertRaisesRegex(ValueError, 'cuda-candidate-rollback'):
                        operation()
                    run.assert_not_called()
                    compose.assert_not_called()
                    compose_cuda.assert_not_called()
                    inspect.assert_not_called()

    def test_cuda_candidate_bootstrap_actions_are_explicit(self):
        for action in ('prepare-cuda-candidate', 'cuda-candidate',
                       'cuda-candidate-rollback', 'cuda-candidate-status', 'cuda-candidate-qualify',
                       'cuda-candidate-promote'):
            self.assertIn(action, BOOTSTRAP.ACTIONS)

    def test_bootstrap_forwards_bounded_longform_options_only_to_cuda_candidate(self):
        args = SimpleNamespace(action='cuda-candidate', revision='d' * 40,
                               max_audio_seconds=1200, max_upload_bytes=40_000_000,
                               native_timeout_seconds=14_400, qualification_manifest=None)
        self.assertEqual(BOOTSTRAP.setup_argv(Path('/release'), args), [
            BOOTSTRAP.sys.executable, '-I', '-B', '/release/scripts/setup-whisper-stt.py',
            'cuda-candidate', '--revision', 'd' * 40, '--max-audio-seconds', '1200',
            '--max-upload-bytes', '40000000', '--native-timeout-seconds', '14400'])
        args.action = 'status'
        with self.assertRaisesRegex(ValueError, 'only for cuda-candidate'):
            BOOTSTRAP.setup_argv(Path('/release'), args)

    def test_bootstrap_forwards_exact_image_promotion_as_explicit_action(self):
        args = SimpleNamespace(action='cuda-candidate-promote', revision='e' * 40,
                               max_audio_seconds=None, max_upload_bytes=None,
                               native_timeout_seconds=None, qualification_manifest=None)
        self.assertEqual(BOOTSTRAP.setup_argv(Path('/release'), args), [
            BOOTSTRAP.sys.executable, '-I', '-B', '/release/scripts/setup-whisper-stt.py',
            'cuda-candidate-promote', '--revision', 'e' * 40])

    def test_bootstrap_qualification_manifest_stays_in_private_staging_directory(self):
        args = SimpleNamespace(action='cuda-candidate-qualify', revision='d' * 40,
                               max_audio_seconds=None, max_upload_bytes=None,
                               native_timeout_seconds=None,
                               qualification_manifest='/tmp/homecompute-whisper-stt.Ab12/qualification-manifest.json')
        with patch.object(BOOTSTRAP, '__file__', '/tmp/homecompute-whisper-stt.Ab12/bootstrap-whisper-stt.py'):
            self.assertEqual(BOOTSTRAP.setup_argv(Path('/release'), args), [
                BOOTSTRAP.sys.executable, '-I', '-B', '/release/scripts/setup-whisper-stt.py',
                'cuda-candidate-qualify', '--revision', 'd' * 40,
                '--qualification-manifest', args.qualification_manifest])
            args.qualification_manifest = '/tmp/elsewhere/qualification-manifest.json'
            with self.assertRaisesRegex(ValueError, 'bootstrap staging directory'):
                BOOTSTRAP.setup_argv(Path('/release'), args)

    def test_qualification_manifest_validation_rejects_bad_hash_truncation_mode_and_symlinks(self):
        for case in ('hash', 'truncated', 'mode', 'symlink'):
            with self.subTest(case=case):
                staging, manifest, audio = qualification_fixture(truncated=case == 'truncated')
                try:
                    path = manifest.parent / 'sample.wav'
                    if case == 'hash':
                        document = json.loads(manifest.read_text())
                        document['inputs'][0]['sha256'] = '0' * 64
                        manifest.write_text(json.dumps(document))
                    elif case == 'mode':
                        path.chmod(0o640)
                    elif case == 'symlink':
                        real = manifest.parent / 'real.wav'
                        path.rename(real)
                        path.symlink_to(real.name)
                    with self.assertRaises((ValueError, QUALIFICATION.QualificationError)):
                        QUALIFICATION._validate_manifest(manifest, os.getuid())
                finally:
                    staging.cleanup()
        staging, manifest, _audio = qualification_fixture()
        try:
            document = json.loads(manifest.read_text())
            document['schema_version'] = True
            manifest.write_text(json.dumps(document))
            with self.assertRaises((ValueError, QUALIFICATION.QualificationError)):
                QUALIFICATION._validate_manifest(manifest, os.getuid())
        finally:
            staging.cleanup()

    def test_qualification_manifest_copies_opened_audio_bytes_and_detects_toctou_change(self):
        staging, manifest, audio = qualification_fixture()
        try:
            real_open = os.open
            replacement = bytearray(audio)
            replacement[-1] = 1
            changed = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal changed
                if path == 'sample.wav' and kwargs.get('dir_fd') is not None and not changed:
                    changed = True
                    audio_path = manifest.parent / 'sample.wav'
                    audio_path.unlink()
                    audio_path.write_bytes(replacement)
                    audio_path.chmod(0o600)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(QUALIFICATION.os, 'open', side_effect=racing_open):
                with self.assertRaisesRegex(ValueError, 'changed during open'):
                    QUALIFICATION._validate_manifest(manifest, os.getuid())
            self.assertTrue(changed)
        finally:
            staging.cleanup()

    class FakeCudaQualificationLifecycle:
        def __init__(self, state):
            self.STATE = state
            self.CUDA_CANDIDATE = 'gb10-whisper-cpp-primary'
            self.CPP_MODEL_SHA256 = '1' * 64
            self.CPP_MODEL_SNAPSHOT = 'c' * 40
            self.CPP_RUNTIME_COMMIT = '2' * 40
            self.prepared = False
            self.started = False
            self.stopped = False
            self.stop_timeout = None
            self.run_failure = None
            self.http_error = False
            self.rollback_failure = False
            self.failure_recorded = False
            self.prepare_failure = None
            self.tx_path = self.STATE / 'cuda-candidate.json'
            self.image_id = 'sha256:' + '3' * 64

        def ensure_no_cuda_candidate_transaction(self, _action):
            return None

        def inspect(self, _name):
            return None

        def prepare_cuda_candidate(self, _revision):
            if self.prepare_failure:
                raise self.prepare_failure
            self.prepared = True

        def cuda_candidate_start(self, revision, **limits):
            self.started = True
            self.limits = limits
            self.tx_path.write_text(json.dumps({
                'revision': revision, 'state': 'candidate-running', 'candidate_exposed': True,
                'candidate_image_id': self.image_id,
            }))
            self.tx_path.chmod(0o600)

        def run_with_input(self, _command, audio, *, timeout):
            self.audio_seen = audio
            self.request_timeout = timeout
            code = _command[-1]
            self.program_fields = json.loads(re.search(r'FIELDS=(\{[^\n]+\})', code).group(1))
            if self.run_failure:
                if self.run_failure == 'hup':
                    signal.raise_signal(signal.SIGHUP)
                if self.run_failure == 'term':
                    signal.raise_signal(signal.SIGTERM)
                raise self.run_failure
            body = json.dumps({'text': 'Synthetic private transcript.', 'segments': [
                {'id': 0, 'start': 0.0, 'end': 0.1, 'text': 'Synthetic private transcript.'},
            ]}).encode()
            headers = {
                'x-homecompute-stt-model-sha256': self.CPP_MODEL_SHA256,
                'x-homecompute-stt-model-snapshot': self.CPP_MODEL_SNAPSHOT,
                'x-homecompute-stt-runtime': 'whisper.cpp',
                'x-homecompute-stt-runtime-commit': self.CPP_RUNTIME_COMMIT,
                'x-homecompute-stt-homecompute-revision': 'd' * 40,
                'x-homecompute-stt-image-id': self.image_id,
            }
            envelope = {'ok': not self.http_error, 'status': 400 if self.http_error else 200,
                        'headers': headers, 'body_truncated': False,
                        'body_base64': __import__('base64').b64encode(
                            b'private synthetic HTTP error' if self.http_error else body).decode()}
            return SimpleNamespace(stdout=json.dumps(envelope))

        def cuda_candidate_stop(self, _revision, *, idle_timeout):
            self.stop_timeout = idle_timeout
            if self.rollback_failure:
                self.tx_path.write_text(json.dumps({'revision': 'd' * 40, 'state': 'rollback-failed'}))
                self.tx_path.chmod(0o600)
                raise RuntimeError('private rollback diagnostic')
            tx = json.loads(self.tx_path.read_text())
            tx['state'] = 'rolled-back'
            self.tx_path.write_text(json.dumps(tx))
            self.tx_path.chmod(0o600)
            self.stopped = True

        def cuda_candidate_failure(self, tx, error, *, rollback):
            self.failure_recorded = rollback and isinstance(error, RuntimeError)
            tx['state'] = 'rollback-failed'
            self.tx_path.write_text(json.dumps(tx))
            self.tx_path.chmod(0o600)

    def _run_fake_cuda_qualification(self, *, run_failure=None, rollback_failure=False):
        staging, manifest, audio = qualification_fixture()
        state = Path(tempfile.mkdtemp(prefix='whisper-qualification-state-'))
        state.chmod(0o700)
        fake = self.FakeCudaQualificationLifecycle(state)
        fake.run_failure = run_failure
        fake.rollback_failure = rollback_failure
        try:
            outcome = QUALIFICATION.qualify(fake, manifest, 'd' * 40, operator_uid=os.getuid())
            return staging, state, manifest, audio, fake, outcome
        except BaseException:
            staging.cleanup()
            shutil.rmtree(state)
            raise

    def test_one_command_qualification_preserves_raw_response_and_rolls_back(self):
        staging, state, manifest, audio, fake, outcome = self._run_fake_cuda_qualification()
        try:
            self.assertTrue(fake.prepared and fake.started and fake.stopped)
            self.assertEqual(fake.audio_seen, audio)
            self.assertEqual(fake.limits, {'max_audio_seconds': 1, 'max_upload_bytes': 40_000,
                                           'native_timeout_seconds': 30})
            self.assertEqual(fake.stop_timeout, 210)
            self.assertEqual(fake.program_fields, QUALIFICATION.REQUEST_FIELDS)
            for field in ('carry_initial_prompt', 'translate', 'no_timestamps'):
                self.assertEqual(fake.program_fields[field], 'false')
            self.assertNotIn('prompt', fake.program_fields)
            self.assertEqual(outcome['state'], 'completed')
            self.assertEqual(outcome['rollback'], 'completed')
            result_path = Path(outcome['results'][0])
            self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
            result = json.loads(result_path.read_text())
            body = __import__('base64').b64decode(result['response_body_base64'])
            self.assertIn('Synthetic private transcript.', body.decode())
            self.assertEqual(set(result['headers']), set(QUALIFICATION.PROVENANCE_HEADERS))
            self.assertNotIn('Synthetic private transcript.', json.dumps(outcome))
            root_receipt = next((state / 'cuda-qualifications' / outcome['qualification_id']).glob('receipt-*.json'))
            receipt = json.loads(root_receipt.read_text())
            self.assertGreaterEqual(receipt['timings']['per_input_seconds']['sample'], 0)
            self.assertIn('prepare_seconds', receipt['timings'])
            self.assertIn('start_seconds', receipt['timings'])
            self.assertIn('rollback_seconds', receipt['timings'])
            self.assertIn('total_seconds', receipt['timings'])
            root_copy = state / 'cuda-qualifications' / outcome['qualification_id'] / receipt['results'][0]
            self.assertTrue(root_copy.exists())
            self.assertEqual(stat.S_IMODE(root_copy.stat().st_mode), 0o600)
        finally:
            staging.cleanup()
            shutil.rmtree(state)

    def test_generated_qualification_client_executes_real_multipart_and_http_error_capture(self):
        audio = b'RIFF' + b'\0' * 16 + b'PCM-SYNTHETIC'
        for http_error in (False, True):
            with self.subTest(http_error=http_error):
                observed = {}
                headers = {name: 'synthetic' for name in QUALIFICATION.PROVENANCE_HEADERS}

                # Provide a context-manager-capable response while executing the exact
                # generated child program, not a reimplementation of its multipart logic.
                class Response:
                    status = 200
                    def __init__(self, response_headers): self.headers = response_headers
                    def __enter__(self): return self
                    def __exit__(self, *_args): return None
                    def read(self, _limit): return b'{"text":"ok","segments":[]}'

                def open_key(path, mode='r', *args, **kwargs):
                    if path == '/run/secrets/whisper_cpp_api_key' and mode == 'rb':
                        return io.BytesIO(b'test-key\n')
                    return real_open(path, mode, *args, **kwargs)

                real_open = open
                def fake_urlopen(request, timeout):
                    observed['request'] = request
                    observed['timeout'] = timeout
                    if http_error:
                        raise urllib.error.HTTPError(request.full_url, 422, 'safe test', headers,
                                                     io.BytesIO(b'private error body'))
                    return Response(headers)

                namespace = {}
                output = io.StringIO()
                with patch('builtins.open', side_effect=open_key), patch.object(urllib.request, 'urlopen', fake_urlopen), \
                     patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(audio))), \
                     contextlib.redirect_stdout(output):
                    try:
                        exec(QUALIFICATION._client_program(40_000, 17), namespace)
                    except SystemExit as exit_error:
                        self.assertEqual(exit_error.code, 0)
                envelope = json.loads(output.getvalue())
                request = observed['request']
                body = request.data
                self.assertTrue(body.startswith(b'--hc-qual-'))
                self.assertIn(b'Content-Disposition: form-data; name="no_timestamps"\r\n\r\nfalse\r\n', body)
                self.assertIn(b'Content-Disposition: form-data; name="carry_initial_prompt"\r\n\r\nfalse\r\n', body)
                self.assertIn(b'\r\n' + audio + b'\r\n--hc-qual-', body)
                self.assertNotIn(b'\\r\\n', body)
                self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
                self.assertEqual(observed['timeout'], 17)
                if http_error:
                    self.assertEqual(envelope['status'], 422)
                    self.assertFalse(envelope['ok'])
                    self.assertEqual(__import__('base64').b64decode(envelope['body_base64']), b'private error body')
                    self.assertEqual(set(envelope['headers']), set(QUALIFICATION.PROVENANCE_HEADERS))
                else:
                    self.assertEqual(envelope['status'], 200)
                    self.assertTrue(envelope['ok'])

    def test_one_command_qualification_rolls_back_after_request_error_and_interrupt(self):
        for error in (RuntimeError('private synthetic inference error'), KeyboardInterrupt(), 'hup', 'term'):
            with self.subTest(error=type(error).__name__):
                staging, state, _manifest, _audio, fake, outcome = self._run_fake_cuda_qualification(run_failure=error)
                try:
                    self.assertTrue(fake.prepared and fake.started and fake.stopped)
                    self.assertEqual(outcome['state'], 'failed')
                    self.assertEqual(outcome['rollback'], 'completed')
                    expected = 'InterruptedError' if error in ('hup', 'term') else type(error).__name__
                    self.assertEqual(outcome['failure']['failure_class'], expected)
                finally:
                    staging.cleanup()
                    shutil.rmtree(state)

    def test_qualification_persists_http_error_body_and_provenance_before_failure(self):
        staging, manifest, audio = qualification_fixture()
        state = Path(tempfile.mkdtemp(prefix='whisper-qualification-state-'))
        state.chmod(0o700)
        fake = self.FakeCudaQualificationLifecycle(state)
        fake.http_error = True
        try:
            outcome = QUALIFICATION.qualify(fake, manifest, 'd' * 40, operator_uid=os.getuid())
            self.assertEqual(outcome['state'], 'failed')
            self.assertEqual(outcome['rollback'], 'completed')
            result_path = Path(outcome['results'][0])
            result = json.loads(result_path.read_text())
            self.assertEqual(result['http_status'], 400)
            self.assertEqual(result['headers']['x-homecompute-stt-model-sha256'], fake.CPP_MODEL_SHA256)
            body = __import__('base64').b64decode(result['response_body_base64'])
            self.assertEqual(body, b'private synthetic HTTP error')
            self.assertNotIn('private synthetic HTTP error', json.dumps(outcome))
        finally:
            staging.cleanup()
            shutil.rmtree(state)

    def test_one_command_qualification_reports_rollback_failure_without_hiding_it(self):
        staging, state, _manifest, _audio, fake, outcome = self._run_fake_cuda_qualification(rollback_failure=True)
        try:
            self.assertTrue(fake.started)
            self.assertTrue(fake.failure_recorded)
            self.assertEqual(outcome['state'], 'rollback-failed')
            self.assertEqual(outcome['rollback'], 'failed')
            self.assertEqual(outcome['rollback_failure']['failure_class'], 'RuntimeError')
            self.assertIsNone(outcome['failure'])
            self.assertNotEqual(outcome['failure'], outcome['rollback_failure'])
            root_receipt = next((state / 'cuda-qualifications' / outcome['qualification_id']).glob('receipt-*.json'))
            receipt = json.loads(root_receipt.read_text())
            self.assertEqual(receipt['rollback'], 'failed')
        finally:
            staging.cleanup()
            shutil.rmtree(state)

    def test_prepare_diagnostic_metadata_is_preserved_without_raw_details(self):
        staging, manifest, _audio = qualification_fixture()
        state = Path(tempfile.mkdtemp(prefix='whisper-qualification-state-'))
        state.chmod(0o700)
        fake = self.FakeCudaQualificationLifecycle(state)
        class PrepareError(RuntimeError):
            metadata = {'diagnostic_id': 'f00ba412', 'stage': 'model-provision',
                        'failure_class': 'PermissionError', 'classification': 'permission-denied',
                        'executable': 'python3', 'exit_code': 13,
                        'diagnostic_available': True, 'secret': 'do-not-leak'}
        fake.prepare_failure = PrepareError('secret-bearing detail')
        try:
            outcome = QUALIFICATION.qualify(fake, manifest, 'd' * 40, operator_uid=os.getuid())
            self.assertEqual(outcome['state'], 'failed')
            self.assertEqual(outcome['failure'], {
                'failure_id': 'f00ba412', 'failure_stage': 'model-provision',
                'failure_class': 'PermissionError', 'failure_executable': 'python3',
                'failure_exit_code': 13, 'failure_classification': 'permission-denied'})
            self.assertNotIn('secret', json.dumps(outcome))
            self.assertFalse(fake.started)
        finally:
            staging.cleanup()
            shutil.rmtree(state)

    def test_qualification_failure_format_uses_nested_safe_build_stage(self):
        outcome = {'qualification_id': 'bead1234', 'state': 'failed', 'rollback': 'completed',
                   'failure': {'failure_stage': 'build-image', 'failure_class': 'ManagedCommandFailure'},
                   'receipt': None}
        public = LIFECYCLE.format_cuda_qualification_failure(outcome)
        self.assertIn('stage=build-image', public)
        self.assertNotIn('PRIVATE', public)

    def test_qualification_progress_is_content_free_and_uses_fixed_stages(self):
        staging, manifest, _audio = qualification_fixture()
        state = Path(tempfile.mkdtemp(prefix='whisper-qualification-state-'))
        state.chmod(0o700)
        fake = self.FakeCudaQualificationLifecycle(state)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                outcome = QUALIFICATION.qualify(fake, manifest, 'd' * 40, operator_uid=os.getuid())
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event['stage'] for event in events],
                             ['prepare', 'start', 'inference', 'rollback'])
            self.assertEqual(events[2]['input_index'], 1)
            self.assertTrue(all(event['qualification_id'] == outcome['qualification_id'] for event in events))
            self.assertNotIn('sample', output.getvalue())
            self.assertNotIn('Synthetic private transcript', output.getvalue())
        finally:
            staging.cleanup()
            shutil.rmtree(state)

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

    def test_cuda_start_finally_keeps_gate_when_automatic_rollback_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'transaction.json').write_text(json.dumps({'state': 'applied'}))
            primary = {
                'Id': 'primary-id', 'Image': 'sha256:' + 'b' * 64,
                'Name': '/gb10-whisper-primary',
                'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'Config': {'Cmd': [LIFECYCLE.MODEL, LIFECYCLE.REVISION],
                           'Image': 'nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d'},
                'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
            }
            stopped = {**primary, 'State': {'Running': False, 'Health': {'Status': 'unhealthy'}}}
            healthy = {**primary, 'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
            inspections = [primary, None, None, None, None, stopped, healthy]
            original_read_text = Path.read_text
            def read_text(path, *args, **kwargs):
                if str(path) == '/proc/meminfo':
                    return 'MemAvailable: 20000000 kB\nMemTotal: 128000000 kB\n'
                return original_read_text(path, *args, **kwargs)
            gate_calls = []
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'guards'), \
                    patch.object(LIFECYCLE, 'protected'), patch.object(LIFECYCLE, 'check_cuda_host'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=inspections), \
                    patch.object(LIFECYCLE, 'cuda_candidate_image_id', return_value='sha256:' + 'c' * 64), \
                    patch.object(LIFECYCLE, 'validate_cuda_artifacts'), patch.object(LIFECYCLE, 'quiet'), \
                    patch.object(LIFECYCLE, 'gate', side_effect=lambda _chain, enabled: gate_calls.append(enabled)), \
                    patch.object(LIFECYCLE, 'run', return_value=SimpleNamespace(stdout='')), \
                    patch.object(LIFECYCLE, 'compose_cuda', side_effect=RuntimeError('startup failed')), \
                    patch.object(LIFECYCLE, 'cuda_candidate_rollback', side_effect=ValueError('not idle')), \
                    patch.object(LIFECYCLE.Path, 'read_text', read_text):
                with self.assertRaises(LIFECYCLE.CudaCandidateFailure):
                    LIFECYCLE.cuda_candidate_start('d' * 40)
            journal = json.loads((state / 'cuda-candidate.json').read_text())
            self.assertEqual(journal['state'], 'rollback-failed')
            self.assertEqual(gate_calls, [True, True])
            self.assertTrue(journal['gate_held'])

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
                    patch.object(LIFECYCLE, 'inspect', return_value=None), \
                    contextlib.redirect_stdout(stdout):
                LIFECYCLE.cuda_candidate_status('d' * 40)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['state'], 'rollback-failed')
            self.assertEqual(result['failure_stage'], 'candidate-start')
            self.assertIsNone(result['failure_id'])
            self.assertIsNone(result['candidate_stage'])
            self.assertIsNone(result['failure_class'])
            self.assertNotIn('PRIVATE_FAKE_CUDA_SECRET', stdout.getvalue())

    def test_cuda_status_identifies_persisted_cpp_owner_and_retained_original_relay(self):
        candidate = {'Id': 'cpp-id', 'Image': LIFECYCLE.CPP_QUALIFIED_IMAGE_ID,
                     'State': {'Running': True}, 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
                     'Config': {'Labels': {'com.docker.compose.project': LIFECYCLE.CUDA_CANDIDATE_PROJECT,
                                           'com.docker.compose.service': 'whisper-cpp-cuda'}}}
        relay = {'Id': 'relay-id', 'Image': 'sha256:' + 'a' * 64,
                 'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
                 'Config': {'Labels': {'com.docker.compose.project': 'whisper-large-v3-stt',
                                       'com.docker.compose.service': 'whisper-cpp-http'}}}
        old = {'Id': 'old-relay', 'Image': 'sha256:' + 'b' * 64,
               'Name': '/gb10-whisper-http-before-cpp-eeeeeeeeeeee',
               'State': {'Running': False}}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'cuda-candidate.json').write_text(json.dumps({
                'revision': 'd' * 40, 'state': 'promoted', 'promotion': True,
                'candidate_exposed': True, 'candidate_image_id': candidate['Image'],
                'relay_candidate_id': relay['Id'], 'relay_candidate_image_id': relay['Image'],
                'relay_original': {'id': old['Id'], 'image': old['Image'],
                                   'name': '/gb10-whisper-http', 'backup_name': old['Name']},
            }))
            def inspect(name):
                return {LIFECYCLE.CUDA_CANDIDATE: candidate,
                        'gb10-whisper-cpp-http': relay, 'old-relay': old}.get(name)
            stdout = io.StringIO()
            with patch.object(LIFECYCLE, 'STATE', state), patch.object(LIFECYCLE, 'protected'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=inspect), \
                    contextlib.redirect_stdout(stdout):
                LIFECYCLE.cuda_candidate_status('d' * 40)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result['desired_backend'], 'whisper-cpp-cuda')
        self.assertTrue(result['candidate_running'])
        self.assertEqual(result['candidate_restart_policy'], 'unless-stopped')
        self.assertTrue(result['promoted_relay_healthy'])
        self.assertEqual(result['promoted_relay_restart_policy'], 'unless-stopped')
        self.assertTrue(result['original_relay_retained'])

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
                    patch.object(LIFECYCLE, 'guards'), patch.object(LIFECYCLE, 'provision_cpp_role_key'), \
                    patch.object(LIFECYCLE, 'inspect', side_effect=old), \
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
                    patch.object(LIFECYCLE, 'provision_cpp_role_key'), \
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
        self.assertEqual(set(catalog['transcription_deployments']), {
            'whisper-large-v3-spark-primary', 'whisper-large-v3-spark-vllm-rollback'})
        rollback = catalog['transcription_deployments']['whisper-large-v3-spark-vllm-rollback']
        self.assertEqual(rollback['runtime_profile'], 'vllm-whisper')
        self.assertEqual(rollback['availability'], 'intentionally-stopped')
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
