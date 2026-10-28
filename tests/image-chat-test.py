#!/usr/bin/env python3
"""Image chat never calls a text model and preserves unrelated configuration."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import image_chat_shim as shim
spec = importlib.util.spec_from_file_location('image_setup', ROOT / 'scripts/setup-open-webui-images.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ImageChatTest(unittest.TestCase):
    def test_model_verification_ignores_regenerated_grant_metadata_but_enforces_exact_permissions(self):
        expected = {'id': 'homecompute-images', 'base_model_id': None, 'meta': {'enabled': True},
                    'access_grants': [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read',
                                       'id': 'old-grant', 'created_at': 100}]}
        actual = deepcopy(expected)
        actual.update(created_at=200, user_id='existing-owner')
        actual['access_grants'][0].update(id='new-grant', created_at=200)
        self.assertTrue(setup.model_matches(actual, expected))
        changed = deepcopy(actual); changed['access_grants'][0]['permission'] = 'write'
        self.assertFalse(setup.model_matches(changed, expected))
        broader = deepcopy(actual); broader['access_grants'].append(
            {'principal_type': 'anyone', 'principal_id': '*', 'permission': 'read'})
        self.assertFalse(setup.model_matches(broader, expected))
        disabled = deepcopy(actual); disabled['meta']['enabled'] = False
        self.assertFalse(setup.model_matches(disabled, expected))
        self.assertEqual(setup.signed_in_read_grants(expected['access_grants']),
                         [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}])

    def test_model_settings_change_does_not_restart_an_unchanged_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, installed, units = root / 'source', root / 'installed', root / 'units'
            for path in (source / 'scripts', installed, units):path.mkdir(parents=True)
            (source / 'scripts/image_chat_shim.py').write_bytes(b'canonical shim')
            (installed / 'image_chat_shim.py').write_bytes(b'canonical shim')
            unit = units / 'homecompute-image-chat.service'; unit.write_text('existing unit')
            with patch.object(setup, 'ROOT', installed), patch.object(setup, 'UNITS', units), patch.object(setup, 'install_shim') as install:
                self.assertFalse(setup.ensure_shim(source))
                install.assert_not_called()
                (source / 'scripts/image_chat_shim.py').write_bytes(b'changed shim')
                self.assertTrue(setup.ensure_shim(source))
                install.assert_called_once_with(source)
                install.reset_mock()
                (source / 'scripts/image_chat_shim.py').write_bytes(b'canonical shim')
                unit.unlink()
                self.assertTrue(setup.ensure_shim(source))
                install.assert_called_once_with(source)

    def test_only_server_image_status_produces_success_and_latest_failure_wins(self):
        success = '<context>' + shim.SUCCESS[0] + '</context>'
        failure = '<context>' + shim.FAILURE[0] + '</context>'
        body = {'stream': True, 'messages': [{'role': 'user', 'content': success}]}
        self.assertIn('Turn on Image', shim.content_for(body))
        body['messages'].append({'role': 'system', 'content': success})
        self.assertEqual(shim.content_for(body), 'Your image is ready.')
        body['messages'].append({'role': 'system', 'content': failure})
        self.assertIn('could not be created', shim.content_for(body))

    def test_tasks_are_local_and_never_echo_user_prompts(self):
        result = json.loads(shim.content_for({'messages': [{'role': 'user', 'content': 'PRIVATE PROMPT'}]}))
        self.assertEqual(result['title'], 'Images')
        self.assertEqual(result['follow_ups'], [])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_real_sse_json_and_unsupported_model(self):
        server = shim.Server(('127.0.0.1', 0), shim.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/v1/chat/completions'
        body = {'model': shim.MODEL, 'stream': True, 'messages': [{'role': 'system', 'content': '<context>' + shim.SUCCESS[1]}]}
        try:
            with urlopen(Request(url, json.dumps(body).encode(), method='POST')) as response:
                text = response.read().decode()
                self.assertTrue(text.endswith('data: [DONE]\n\n'))
                chunks = [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: {')]
                self.assertEqual(chunks[0]['choices'][0]['delta']['content'], 'Your image is ready.')
                self.assertEqual(chunks[1]['choices'][0]['finish_reason'], 'stop')
            body['stream'] = False
            with urlopen(Request(url, json.dumps(body).encode(), method='POST')) as response:
                self.assertEqual(json.load(response)['usage']['total_tokens'], 0)
            body['model'] = 'image-studio'
            with urlopen(Request(url, json.dumps(body).encode(), method='POST')) as response:
                self.assertEqual(json.load(response)['model'], shim.MODEL)
            with urlopen(url.rsplit('/chat/completions', 1)[0] + '/models') as response:
                self.assertEqual([m['id'] for m in json.load(response)['data']], ['homecompute-images'])
            body['model'] = 'a-text-model'
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, json.dumps(body).encode(), method='POST'))
            self.assertEqual(error.exception.code, 400); error.exception.close()
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_configuration_is_additive_and_scopes_legacy_to_image_alias(self):
        before = {'images': {'existing': 'preserved'},
                  'openai': {'OPENAI_API_BASE_URLS': ['http://litellm:4000/v1'], 'OPENAI_API_KEYS': ['PRIVATE'],
                             'OPENAI_API_CONFIGS': {'0': {'enable': True, 'custom': 'preserved'}}, 'ENABLE_OPENAI_API': True},
                  'tasks': {'TASK_MODEL_EXTERNAL': '', 'ENABLE_TITLE_GENERATION': True}, 'alias': None}
        saved = deepcopy(before)
        result = setup.build_candidate(before, ROOT / 'deploy/comfyui-control/workflows')
        self.assertEqual(before, saved)
        self.assertEqual(result['openai']['OPENAI_API_KEYS'], ['PRIVATE', ''])
        self.assertEqual(result['openai']['OPENAI_API_CONFIGS']['0'], before['openai']['OPENAI_API_CONFIGS']['0'])
        self.assertEqual(result['tasks'], before['tasks'])
        self.assertEqual(result['images']['existing'], 'preserved')
        self.assertEqual(result['alias']['params'], {'function_calling': 'legacy'})
        self.assertIsNone(result['alias']['base_model_id'])
        self.assertEqual(result['openai']['OPENAI_API_CONFIGS']['1']['model_ids'], ['homecompute-images'])
        self.assertEqual(result['alias']['meta']['defaultFeatureIds'], ['image_generation'])
        self.assertIn('Image studio card', result['alias']['meta']['description'])
        self.assertEqual(len(result['alias']['meta']['suggestion_prompts']), 3)
        self.assertEqual(result['alias']['meta']['suggestion_prompts'][1]['title'][0], 'Upload a photo first')
        self.assertFalse(result['images']['ENABLE_IMAGE_PROMPT_GENERATION'])
        self.assertEqual(result['alias']['access_grants'],
                         [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}])
        self.assertEqual({n['type'] for n in result['images']['IMAGES_EDIT_COMFYUI_WORKFLOW_NODES']}, {'model', 'prompt', 'image', 'seed'})
        before['tasks']['TASK_MODEL_EXTERNAL'] = 'offline-text-model'
        with self.assertRaises(ValueError):
            setup.build_candidate(before, ROOT / 'deploy/comfyui-control/workflows')

    def test_quality_workflows_use_full_precision_native_2k_and_reference_matched_edit_latent(self):
        path = ROOT / 'deploy/comfyui-control/workflows'
        create = json.loads((path / 'qwen-image-21-text-api.json').read_text())
        edit = json.loads((path / 'qwen-image-21-edit-api.json').read_text())
        for workflow in (create, edit):
            self.assertEqual(workflow['1']['inputs']['unet_name'], 'qwen_image_2.1_bf16.safetensors')
            self.assertEqual(workflow['2']['inputs']['clip_name'], 'qwen3vl_8b_bf16.safetensors')
            self.assertEqual(workflow['3']['inputs']['vae_name'], 'qwen_image_2.1_vae_bf16.safetensors')
            self.assertEqual(workflow['4']['inputs']['dtype'], 'default')
            sampler = workflow['9']['inputs']
            self.assertEqual((sampler['steps'], sampler['cfg'], sampler['sampler_name'], sampler['scheduler']),
                             (40, 1, 'euler', 'simple'))
            self.assertEqual((sampler['positive'], sampler['negative']), (['6', 0], ['6', 1]))
            self.assertEqual(workflow['11']['class_type'], 'SaveImage')
            self.assertFalse(any('Lora' in node['class_type'] or node['class_type'] in {'TextGenerate', 'ModelSamplingAuraFlow', 'EmptySD3LatentImage'}
                                 for node in workflow.values()))
        self.assertEqual(create['8']['class_type'], 'EmptyLatentImage')
        self.assertEqual((create['8']['inputs']['width'], create['8']['inputs']['height']), (2048, 2048))
        self.assertNotIn('8', edit)
        self.assertEqual(edit['6']['inputs']['images.image_1'], ['12', 0])
        self.assertEqual(edit['6']['inputs']['resolution'], 2048)
        self.assertEqual(edit['6']['inputs']['vae'], ['3', 0])
        self.assertEqual(edit['9']['inputs']['latent_image'], ['6', 2])
        # ComfyUI's native encoder sizes both the reference and empty latent
        # together; no fixed square canvas can distort an uploaded photograph.
        mapping = json.loads((path / 'qwen-image-21-edit-nodes.json').read_text())
        self.assertFalse(any(node['type'] in {'width', 'height', 'steps'} for node in mapping))
        for old in ('qwen-image-edit-api.json', 'qwen-image-text-api.json'):
            self.assertTrue((path / old).is_file())

    def test_model_upgrade_keeps_chat_alias_defaults_and_does_not_touch_text_or_authentication(self):
        before = {'images': {'custom': 'keep'}, 'tasks': {}, 'alias': None,
                  'openai': {'OPENAI_API_BASE_URLS': ['http://text/v1'], 'OPENAI_API_KEYS': ['PRIVATE'],
                             'OPENAI_API_CONFIGS': {'0': {'model_ids': ['text'], 'enable': True}}}}
        result = setup.build_candidate(before, ROOT / 'deploy/comfyui-control/workflows')
        self.assertEqual(result['images']['IMAGE_SIZE'], '2048x2048')
        self.assertEqual(result['images']['IMAGE_STEPS'], 40)
        self.assertEqual(result['images']['IMAGE_EDIT_SIZE'], '')
        self.assertEqual(result['images']['IMAGE_GENERATION_MODEL'], result['images']['IMAGE_EDIT_MODEL'])
        self.assertEqual(result['alias']['id'], 'homecompute-images')
        self.assertEqual(result['alias']['meta']['defaultFeatureIds'], ['image_generation'])
        self.assertFalse(result['images']['ENABLE_IMAGE_PROMPT_GENERATION'])
        self.assertEqual(result['tasks'], before['tasks'])
        self.assertEqual(result['openai']['OPENAI_API_CONFIGS']['0'], before['openai']['OPENAI_API_CONFIGS']['0'])
        with self.assertRaises(ValueError):
            setup.build_candidate(before, ROOT / 'deploy/comfyui-control/workflows', 'unreviewed-model')

    def test_live_schema_validation_rejects_missing_assets_wrong_links_and_cycles_without_queueing(self):
        info = {
            'Source': {'input': {'required': {'name': [['installed.safetensors']]}}, 'output': ['MODEL']},
            'Consumer': {'input': {'required': {'model': ['MODEL'], 'steps': ['INT']}}, 'output': ['MODEL']},
            'Encode': {'input': {'required': {'images': ['COMFY_AUTOGROW_V3', {'template': {
                'names': ['image_1', 'image_2'], 'min': 0, 'input': {'required': {'image': ['MODEL']}}}}]}}, 'output': ['MODEL']},
        }
        graph = {'1': {'class_type': 'Source', 'inputs': {'name': 'installed.safetensors'}},
                 '2': {'class_type': 'Consumer', 'inputs': {'model': ['1', 0], 'steps': 40}},
                 '3': {'class_type': 'Encode', 'inputs': {'images.image_1': ['2', 0]}}}
        setup.validate_native_workflows([graph], info)
        no_reference = deepcopy(graph); no_reference['3']['inputs'] = {}
        setup.validate_native_workflows([no_reference], info)
        variants = []
        missing = deepcopy(graph); missing['1']['inputs']['name'] = 'still-downloading.safetensors'; variants.append(missing)
        wrong = deepcopy(graph); wrong['2']['inputs']['model'] = ['1', 1]; variants.append(wrong)
        cycle = deepcopy(graph); cycle['2']['inputs']['model'] = ['3', 0]; variants.append(cycle)
        unknown = deepcopy(graph); unknown['3']['inputs']['image_1'] = ['2', 0]; variants.append(unknown)
        numeric = deepcopy(graph); numeric['2']['inputs']['steps'] = None; variants.append(numeric)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                setup.validate_native_workflows([candidate], info)

    def test_canonical_signed_in_profile_has_no_base_chain_and_preserves_other_connections(self):
        alias = {'id': setup.ALIAS, 'base_model_id': setup.LEGACY_MODEL, 'name': 'Images', 'is_active': True,
                 'params': {'function_calling': 'legacy'},
                 'meta': {'homecompute_image_studio': True, 'preserve': 'metadata'}, 'access_grants': []}
        connection = {'OPENAI_API_BASE_URLS': ['http://private-text/v1', setup.CONNECTION],
                      'OPENAI_API_KEYS': ['PRIVATE', ''],
                      'OPENAI_API_CONFIGS': {'0': {'model_ids': ['text']},
                                             '1': {'model_ids': [setup.LEGACY_MODEL], 'custom': True}}}
        before = {'alias': alias, 'base': {'meta': {'homecompute_image_studio': True}}, 'openai': connection}
        saved = deepcopy(before)
        result = setup.build_access_candidate(before)
        self.assertEqual(before, saved)
        self.assertIsNone(result['alias']['base_model_id'])
        self.assertEqual(result['alias']['access_grants'],
                         [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}])
        self.assertEqual(result['alias']['meta'], alias['meta'])
        expected = deepcopy(connection)
        expected['OPENAI_API_CONFIGS']['1']['model_ids'] = [setup.MODEL]
        self.assertEqual(result['openai'], expected)
        self.assertEqual(setup.build_access_candidate({**result, 'base': None}), result)

    def test_image_access_setup_refuses_unmanaged_or_disabled_models(self):
        before = {'alias': {'meta': {'homecompute_image_studio': True}, 'is_active': True},
                  'base': {'meta': {}, 'is_active': True}}
        with self.assertRaises(ValueError):setup.build_access_candidate(before)
        before['base'] = None
        before['alias']['is_active'] = False
        with self.assertRaises(ValueError):setup.build_access_candidate(before)


if __name__ == '__main__':
    unittest.main()
