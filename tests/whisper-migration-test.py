#!/usr/bin/env python3
"""STT replacement gates, transaction recovery and consumer migration behavior."""
import copy
import importlib.util
import json
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

    def test_public_bootstrap_allows_only_declared_candidate_lifecycle_actions(self):
        self.assertIn('prepare-runtime-candidate', BOOTSTRAP.ACTIONS)
        self.assertIn('runtime-candidate', BOOTSTRAP.ACTIONS)
        self.assertIn('runtime-candidate-rollback', BOOTSTRAP.ACTIONS)

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
