#!/usr/bin/env python3
"""Check native LTX graphs remain runnable and use only the prepared checkpoints."""
import json
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / 'deploy/comfyui-control/workflows'


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = json.loads((WORKFLOWS / 'ltx25-text-api.json').read_text())
        cls.ui = json.loads((WORKFLOWS / 'ltx25-text-nodes.json').read_text())
        cls.nodes = {str(n['id']): n for n in cls.ui['nodes']}

    def of_type(self, kind):
        return [(key, node) for key, node in self.api.items() if node['class_type'] == kind]

    def test_all_dependencies_are_valid_acyclic_and_contribute_to_the_saved_video(self):
        self.assertEqual(set(self.nodes), set(self.api))
        visited, visiting = set(), set()

        def visit(key):
            self.assertNotIn(key, visiting, 'Sampling graph contains a cycle')
            if key in visited:
                return
            visiting.add(key)
            for value in self.api[key]['inputs'].values():
                if isinstance(value, list):
                    dependency, slot = value
                    self.assertIn(dependency, self.api)
                    self.assertIsInstance(slot, int)
                    self.assertTrue(0 <= slot < len(self.nodes[dependency]['outputs']))
                    visit(dependency)
            visiting.remove(key)
            visited.add(key)

        outputs = self.of_type('SaveVideo')
        self.assertEqual(len(outputs), 1)
        visit(outputs[0][0])
        self.assertEqual(visited, set(self.api), 'A disconnected node could request unnecessary weights')

    def test_ui_links_and_api_dependencies_describe_the_same_graph(self):
        actual = set()
        for key, node in self.api.items():
            for name, value in node['inputs'].items():
                if isinstance(value, list):
                    actual.add((key, name, value[0], value[1]))
        expected = set()
        for link_id, origin, origin_slot, target, target_slot, _kind in self.ui['links']:
            source = self.nodes[str(origin)]
            destination = self.nodes[str(target)]
            target_input = destination['inputs'][target_slot]
            self.assertEqual(target_input['link'], link_id)
            self.assertIn(link_id, source['outputs'][origin_slot]['links'])
            expected.add((str(target), target_input['name'], str(origin), origin_slot))
        self.assertEqual(actual, expected)

    def test_only_the_five_prepared_models_are_referenced_and_all_nodes_are_native(self):
        models = {value for node in self.api.values() for key, value in node['inputs'].items()
                  if key in {'unet_name', 'clip_name', 'vae_name', 'model_name'}}
        self.assertEqual(models, {
            'ltx-2.5-22b-distilled-transformer-nvfp4.safetensors',
            'gemma4-12b-with-proj-ltx-2.5-bf16.safetensors',
            'ltx-2.5-video-vae-bf16.safetensors', 'ltx-2.5-audio-vae-bf16.safetensors',
            'ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors',
        })
        self.assertTrue(all(n['properties']['cnr_id'] == 'comfy-core' for n in self.ui['nodes']))
        self.assertFalse(self.of_type('TextGenerateLTX2Prompt'))
        self.assertFalse(self.of_type('LTXVAudioVAELoader'))  # Split audio VAE uses VAELoader.
        self.assertTrue(all(set(n) == {'class_type', 'inputs'} for n in self.api.values()))
        self.assertEqual(self.ui['extra']['provenance']['revision'],
                         '3c2199b9362457b12b9c60c6cc303501526ebd62')

    def test_two_stage_sampling_preserves_official_schedules_and_audio_video_alignment(self):
        schedules = [list(map(float, n['inputs']['sigmas'].split(','))) for _, n in self.of_type('ManualSigmas')]
        self.assertCountEqual(schedules, [
            [1, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0],
            [0.85, 0.725, 0.4219, 0],
        ])
        self.assertEqual(len(self.of_type('SamplerCustomAdvanced')), 2)
        self.assertTrue(all(n['inputs']['sampler_name'] == 'euler_ancestral'
                            for _, n in self.of_type('KSamplerSelect')))
        self.assertTrue(all(n['inputs']['noise_seed'] == 42 for _, n in self.of_type('RandomNoise')))
        video = self.of_type('EmptyLTXVLatentVideo')[0][1]['inputs']
        audio = self.of_type('LTXVEmptyLatentAudio')[0][1]['inputs']
        self.assertEqual((video['width'] * 2, video['height'] * 2), (768, 512))
        self.assertEqual(video['length'], audio['frames_number'])
        self.assertEqual(video['length'], 49)
        self.assertEqual(audio['frame_rate'], 24)
        self.assertEqual(self.of_type('CreateVideo')[0][1]['inputs']['fps'], audio['frame_rate'])
        # Spatial refinement must keep the first pass's generated audio.
        split = self.of_type('LTXVSeparateAVLatent')[0][0]
        refined = self.of_type('LTXVConcatAVLatent')[1][1]['inputs']
        self.assertEqual(refined['audio_latent'], [split, 1])


class ImageWorkflowTests(WorkflowTests):
    @classmethod
    def setUpClass(cls):
        cls.api = json.loads((WORKFLOWS / 'ltx25-image-api.json').read_text())
        cls.ui = json.loads((WORKFLOWS / 'ltx25-image-nodes.json').read_text())
        cls.nodes = {str(n['id']): n for n in cls.ui['nodes']}

    def test_starting_image_conditions_both_sampling_stages_without_changing_audio(self):
        self.assertEqual(self.api['900'], {'class_type': 'LoadImage', 'inputs': {'image': 'homecompute-start.png'}})
        self.assertEqual(self.api['901'], {'class_type': 'LTXVPreprocess', 'inputs': {'image': ['900', 0], 'img_compression': 18}})
        for key, latent, strength in [('902', '356', 0.7), ('903', '348', 1.0)]:
            self.assertEqual(self.api[key], {'class_type': 'LTXVImgToVideoInplace', 'inputs': {
                'vae': ['385', 0], 'image': ['901', 0], 'latent': [latent, 0], 'strength': strength, 'bypass': False}})
        self.assertEqual(self.api['377']['inputs']['video_latent'], ['902', 0])
        self.assertEqual(self.api['340']['inputs']['video_latent'], ['903', 0])
        text = json.loads((WORKFLOWS / 'ltx25-text-api.json').read_text())
        for key in text:
            if key not in {'377', '340', '75'}:
                self.assertEqual(self.api[key], text[key], 'Image guidance must preserve the qualified text/audio sampling path')
        self.assertEqual(self.api['75']['inputs']['filename_prefix'], 'video/LTX25-image')
        self.assertEqual(self.ui['extra']['provenance']['sha256'],
                         '03b136ba0ad752c1e91e496a23fa4f4826ef881777d591dc9129c459922e7dac')

    def test_chat_image_graph_and_comfyui_workflow_have_identical_inputs(self):
        path = ROOT / 'deploy/open-webui/functions/homecompute_videos.py'
        spec = importlib.util.spec_from_file_location('workflow_video_pipe', path)
        pipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipe)
        text = json.loads((WORKFLOWS / 'ltx25-text-api.json').read_text())
        self.assertEqual(self.api, pipe.image_graph(text, 'homecompute-start.png'))
        self.assertNotIn('900', text, 'Chat adaptation must not mutate the shared text graph')

    def test_image_widgets_match_runtime_inputs_and_only_known_asset_is_placeholder(self):
        self.assertEqual(self.nodes['900']['widgets_values'], ['homecompute-start.png', 'image'])
        self.assertEqual(self.nodes['901']['widgets_values'], [18])
        self.assertEqual(self.nodes['902']['widgets_values'], [0.7, False])
        self.assertEqual(self.nodes['903']['widgets_values'], [1, False])
        self.assertEqual(self.ui['last_node_id'], 903)
        ids = [link[0] for link in self.ui['links']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.ui['last_link_id'], max(ids))
        for target, node in self.api.items():
            for value in node['inputs'].values():
                if isinstance(value, list):
                    self.assertLess(self.nodes[value[0]]['order'], self.nodes[target]['order'])


if __name__ == '__main__':
    unittest.main()
