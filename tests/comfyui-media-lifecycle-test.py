#!/usr/bin/env python3
"""Shared image/video process ownership, guarded admission, and restart recovery."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from comfyui_lifecycle import COMFY, Controller
from comfyui_control_http import prompt_mode

SPEC = importlib.util.spec_from_file_location('media_fixture', ROOT / 'tests/comfyui-lifecycle-test.py')
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
TEXT = fixture.TEXT
IMAGE = fixture.IMAGE_PROMPT
VIDEO = json.dumps({'prompt': {'1': {'class_type': 'CreateVideo', 'inputs': {}}}}).encode()


class MediaLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now, self.host = 10000, fixture.FakeHost()
        self.host.clock = lambda: self.now
        self.state = Path(self.directory.name) / 'state.json'
        self.controller = Controller(self.host, self.state, lambda: self.now, self.advance)
        self.controller.recover()

    def advance(self, seconds):
        self.now += seconds

    def finish(self):
        self.controller.worker.join(3)
        self.assertFalse(self.controller.worker.is_alive())

    def activate(self, target):
        self.controller.submit('start', target)
        self.finish()
        self.assertEqual(self.controller.status()['mode'], target)

    def test_switching_media_profiles_restarts_only_the_media_process(self):
        self.activate('image')
        for target in ('video', 'image'):
            start = len(self.host.events)
            self.activate(target)
            self.assertEqual(self.host.events[start:], [('stop', COMFY), ('start', COMFY)])
            self.assertFalse(self.host.running(TEXT))
            self.assertFalse(self.controller.llm_status()['available'])

    def test_busy_or_unverifiable_media_blocks_manual_switch_and_stop(self):
        for mode, target in (('image', 'video'), ('video', 'image')):
            self.activate(mode)
            for unknown in (False, True):
                self.host.queue_value, self.host.fail_queue = (1, 1), unknown
                self.controller.sample()
                before = list(self.host.events)
                for action, destination in (('start', target), ('stop', 'text')):
                    with self.assertRaises(ValueError):
                        self.controller.submit(action, destination)
                self.assertEqual(self.host.events, before)
            self.host.queue_value, self.host.fail_queue = (0, 0), False
            self.controller.sample()

    def test_queue_arriving_after_submit_is_checked_again_before_unloading(self):
        self.activate('image')
        before = list(self.host.events)
        with self.controller.admission:
            self.controller.submit('start', 'video')
            self.host.queue_value = (1, 0)
        self.finish()
        self.assertEqual(self.host.events, before)
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertIn('work', self.controller.status()['error'])

    def test_video_selection_and_idle_lease_survive_controller_restart(self):
        self.activate('video')
        self.assertEqual(json.loads(self.state.read_text())['media_mode'], 'video')
        self.advance(1700)
        replacement = Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        self.assertEqual(replacement.status()['mode'], 'video')
        self.assertEqual(replacement.status()['target_mode'], 'video')
        self.assertEqual(replacement.status()['idle_seconds'], 1700)

    def test_legacy_saved_state_defaults_to_image_when_media_is_running(self):
        self.host.containers = {TEXT: False, COMFY: True}
        self.state.write_text(json.dumps({'last_activity': self.now}))
        replacement = Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        self.assertEqual(replacement.status()['mode'], 'image')

    def test_video_idle_restores_text_only_after_active_work_and_thirty_idle_minutes(self):
        self.activate('video')
        self.advance(1800)
        self.host.queue_value = (1, 0)
        self.controller.tick()
        self.assertTrue(self.host.running(COMFY))
        self.host.queue_value = (0, 0)
        self.controller.tick()
        self.advance(1799)
        self.controller.tick()
        self.assertTrue(self.host.running(COMFY))
        self.advance(1)
        self.controller.tick()
        self.finish()
        self.assertTrue(self.host.running(TEXT))
        self.assertFalse(self.host.running(COMFY))

    def test_wrong_media_mode_is_rejected_before_any_host_prompt(self):
        for mode, wrong in (('image', VIDEO), ('video', IMAGE)):
            self.activate(mode)
            status, _, body = self.controller.prompt('/prompt', wrong)
            self.assertEqual(status, 409)
            self.assertIn(b'Start', body)
            self.assertFalse(self.host.prompt_entered.is_set())

    def test_failed_video_activation_unloads_media_and_restores_text(self):
        self.activate('image')
        start = len(self.host.events)
        self.host.fail_comfy = True
        original_queue = self.host.queue

        def queue():
            if len(self.host.events) > start and self.host.events[-1] == ('start', COMFY):
                raise OSError('Failed media startup has no reachable HTTP queue')
            return original_queue()

        self.host.queue = queue
        self.controller.submit('start', 'video')
        self.finish()
        self.assertEqual(self.host.events[start:], [('stop', COMFY), ('start', COMFY),
                                                   ('stop', COMFY), ('start', TEXT)])
        self.assertEqual(self.controller.status()['mode'], 'text')
        self.assertTrue(self.controller.llm_status()['available'])
        self.assertIn('restored', self.controller.status()['error'])

    def test_startup_measurement_excludes_active_workflow_and_request_drain(self):
        began = self.now
        original_stop, original_start = self.host.stop, self.host.start

        def stop(name):
            self.advance(11)
            original_stop(name)

        def start(name):
            self.advance(20)
            original_start(name)

        self.host.stop, self.host.start = stop, start
        self.host.workflow_activity = lambda: dict(active_workflows=int(self.now - began < 100),
                                                  active_turns=0, observed_at=self.now)
        self.host.text_requests = lambda: dict(running=int(self.now - began < 200), waiting=0)
        self.activate('image')
        self.assertGreater(self.now - began, 200)
        measured = self.controller.status()['startup_timings']
        self.assertEqual(measured['image'], {'seconds': 31, 'samples': 1, 'min_seconds': 31, 'max_seconds': 31})
        self.assertEqual(measured['video']['seconds'], None)
        self.assertEqual(measured['text']['samples'], 0)
        self.activate('image')  # Already ready: no new measurement.
        self.assertEqual(self.controller.status()['startup_timings'], measured)
        replacement = Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        self.assertEqual(replacement.status()['startup_timings'], measured)
        self.host.fail_comfy = True
        self.controller.submit('start', 'video')
        self.finish()
        self.assertEqual(self.controller.status()['startup_timings']['video']['samples'], 0)
        self.assertEqual(self.controller.status()['startup_timings']['text']['seconds'], 31)

    def test_successful_start_samples_are_bounded_and_survive_restart(self):
        original_start = self.host.start

        def start(name):
            self.advance(7)
            original_start(name)

        self.host.start = start
        for target in ['image', 'video'] * 7:
            self.activate(target)
        saved = json.loads(self.state.read_text())['startup_seconds']
        self.assertEqual(saved, {'text': [], 'image': [7] * 5, 'video': [7] * 5})
        replacement = Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        self.assertEqual(replacement.status()['startup_timings']['video'],
                         {'seconds': 7, 'samples': 5, 'min_seconds': 7, 'max_seconds': 7})

    def test_invalid_persisted_startup_samples_never_become_estimates(self):
        self.state.write_text(json.dumps({'startup_seconds': {
            'text': [True, -1, float('nan'), 'bad', 2, 4, 6, 8, 10, 12], 'image': 'bad'}}))
        replacement = Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        timings = replacement.status()['startup_timings']
        self.assertEqual(timings['text'], {'seconds': 8, 'samples': 5, 'min_seconds': 4, 'max_seconds': 12})
        self.assertEqual(timings['image']['samples'], 0)
        self.assertIsNone(timings['image']['seconds'])


class MediaHTTPTests(unittest.TestCase):
    def setUp(self):
        fixture.HTTPTest.setUp(self)
        self.now = 10000
        self.controller.clock = self.controller.progress.clock = self.host.clock = lambda: self.now
        self.controller.sleep = lambda seconds: setattr(self, 'now', self.now + seconds)

    def post(self, *args, **kwargs):
        return fixture.HTTPTest.post(self, *args, **kwargs)

    def test_explicit_profiles_and_legacy_routes_preserve_fixed_origin_contract(self):
        for path, target in (('start/video', 'video'), ('start/text', 'text'),
                             ('start/image', 'image'), ('stop', 'text'), ('start', 'image')):
            with self.post('/control/api/' + path) as response:
                self.assertEqual(response.status, 202)
                self.assertEqual(json.load(response)['target_mode'], target)
            self.controller.worker.join(3)
            self.assertEqual(self.controller.status()['mode'], target)
        before = list(self.host.events)
        with self.post('/control/api/start/video', origin='https://evil.example') as response:
            self.assertEqual(response.status, 403)
        with self.post('/control/api/start/video', b'{"container":"other"}') as response:
            self.assertEqual(response.status, 409)
        self.assertEqual(self.host.events, before)

    def test_malformed_or_unknown_workflow_is_a_client_validation_error(self):
        with self.post('/control/api/start/image'):
            pass
        self.controller.worker.join(3)
        for graph in ({}, {'1': None}, {'1': {'class_type': 42, 'inputs': {}}},
                      {'1': {'class_type': 'UnknownNode', 'inputs': {}}}):
            with self.post('/prompt', json.dumps({'prompt': graph}).encode()) as response:
                self.assertEqual(response.status, 400)
        with self.post('/api/prompt', VIDEO) as response:
            self.assertEqual(response.status, 409)
        self.assertFalse(self.host.prompt_entered.is_set())

    def test_model_controls_and_legacy_origins_can_start_the_same_guarded_profile(self):
        for origin in ('https://models.home.arpa', 'https://comfyui.home.arpa',
                       'https://home-core.tail479ad.ts.net:18188'):
            with self.post('/control/api/start/image', origin=origin) as response:
                self.assertEqual(response.status, 202)
            self.controller.worker.join(3)
            self.assertEqual(self.controller.status()['mode'], 'image')
        for origin in ('http://models.home.arpa', 'https://models.home.arpa.evil.example'):
            with self.post('/control/api/start/video', origin=origin) as response:
                self.assertEqual(response.status, 403)

    def test_existing_repository_graphs_identify_their_media_profile(self):
        workflows = ROOT / 'deploy/comfyui-control/workflows'
        for name, mode in (('qwen-image-21-text-api.json', 'image'), ('ltx25-text-api.json', 'video')):
            self.assertEqual(prompt_mode({'prompt': json.loads((workflows / name).read_text())}), mode)
        self.assertEqual(prompt_mode({'prompt': {'1': {'class_type': 'UNETLoader',
                         'inputs': {'unet_name': 'ltx-2.5-distilled.safetensors'}}}}), 'video')


if __name__ == '__main__':
    unittest.main()
