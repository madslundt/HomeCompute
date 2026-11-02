#!/usr/bin/env python3
"""Retirement must never remove a different Compose service or clone."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('omnivoice_setup', ROOT / 'scripts/setup-omnivoice.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.item = {'Id': 'fixed-id', 'Name': '/homecompute-plapre-plapre-primary-1',
            'Image': 'sha256:fixed-image', 'State': {'Running': True}, 'Config': {'Labels': {
                'com.docker.compose.project': 'homecompute-plapre',
                'com.docker.compose.service': 'plapre-primary'}}}

    def test_metadata_excludes_environment_and_content(self):
        self.item['Config']['Env'] = ['SECRET=private']
        row = module.retired_rows([self.item], 'home-spark')[0]
        self.assertEqual(set(row), {'id', 'name', 'image', 'service', 'running'})
        self.assertEqual(row['name'], 'homecompute-plapre-plapre-primary-1')

    def test_refuses_different_project_service_or_rollback_clone(self):
        for field, value in [('project', 'unrelated'), ('service', 'hviske-primary')]:
            item = copy.deepcopy(self.item)
            item['Config']['Labels']['com.docker.compose.' + field] = value
            with self.assertRaises(ValueError):
                module.retired_rows([item], 'home-spark')
        self.item['Name'] += '-rollback'
        with self.assertRaises(ValueError):
            module.retired_rows([self.item], 'home-spark')

    def test_build_is_offline_at_runtime_and_avoids_extra_asr(self):
        text = (ROOT / 'deploy/omnivoice/Dockerfile.cpu').read_text()
        self.assertIn('HF_HUB_OFFLINE=1', text)
        self.assertIn('USER 65532:65532', text)
        self.assertIn('--require-hashes', text)
        runtime = (ROOT / 'scripts/omnivoice-runtime.py').read_text()
        self.assertIn('local_files_only=True', runtime)
        self.assertNotIn('load_asr=True', runtime)

    def test_fallback_changes_only_tts_and_preserves_latest_stt(self):
        original = {'id': 'pipeline', 'tts_engine': 'tts.plapre_nano_v2',
                    'tts_language': 'da_DK', 'tts_voice': 'danish-default',
                    'stt_engine': 'stt.new_whisper', 'conversation_engine': 'conversation.current',
                    'prefer_local_intents': False}
        after = module.pipeline_candidate(original)
        self.assertEqual(after['stt_engine'], original['stt_engine'])
        self.assertEqual(after['conversation_engine'], original['conversation_engine'])
        self.assertEqual(after['tts_engine'], 'tts.piper')
        self.assertEqual(original['tts_engine'], 'tts.plapre_nano_v2')
        self.assertIsNone(module.pipeline_candidate({**original, 'tts_engine': 'tts.piper'}))

    def test_inference_rejects_arbitrary_reference_and_language_fields(self):
        spec = importlib.util.spec_from_file_location('omnivoice_runtime', ROOT / 'scripts/omnivoice-runtime.py')
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        self.assertEqual(runtime.request_fields({'text': 'Hej.', 'speaker': 'da'}), ('Hej.', 'da'))
        for value in ({'text': 'Hej.', 'speaker': 'fr'},
                      {'text': 'Hej.', 'speaker': 'da', 'ref_audio': '/private/file'},
                      {'text': 'x'*2001, 'speaker': 'da'},
                      {'text': '', 'speaker': 'en'}):
            with self.assertRaises(ValueError):
                runtime.request_fields(value)


if __name__ == '__main__':
    unittest.main()
