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
