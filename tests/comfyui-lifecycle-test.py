#!/usr/bin/env python3
"""Lifecycle, admission and idle invariants without touching a real GPU."""
import importlib.util
import json
import re
import socket
import sys
import time
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
TEXT = 'test-replaceable-text-model'
IMAGE_PROMPT = json.dumps({'prompt': {'1': {'class_type': 'SaveImage', 'inputs': {}}}}).encode()

SPEC = importlib.util.spec_from_file_location('comfyui_lifecycle', Path(__file__).resolve().parents[1] / 'scripts/comfyui_lifecycle.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FakeHost:
    def __init__(self):
        self.config = m.TextConfig(TEXT)
        self.clock = time.time
        self.activity_value = None
        self.containers = {TEXT: True, m.COMFY: False}
        self.events = []
        self.queue_value = (0, 0)
        self.history_value = 'old-history'
        self.fail_comfy = False
        self.fail_flash = False
        self.fail_queue = False
        self.fail_history = False
        self.flash_busy = False
        self.prompt_entered = threading.Event()
        self.prompt_release = None

    def running(self, name):
        return self.containers[name]

    def start(self, name):
        other = TEXT if name == m.COMFY else m.COMFY
        assert not self.containers[other], 'Concurrent GPU process start'
        self.events.append(('start', name))
        self.containers[name] = True

    def stop(self, name):
        self.events.append(('stop', name))
        self.containers[name] = False

    def ready(self, name):
        return self.containers[name] and not (name == m.COMFY and self.fail_comfy) and not (name == TEXT and self.fail_flash)

    def queue(self):
        if self.fail_queue:
            raise OSError('queue unavailable')
        return self.queue_value

    def history(self):
        if self.fail_history:
            raise OSError('history unavailable')
        return self.history_value

    def workflow_activity(self):
        return self.activity_value or dict(active_workflows=0, active_turns=0, observed_at=self.clock())

    def text_requests(self):
        return {'running': int(self.flash_busy), 'waiting': 0}

    def prompt(self, path, body):
        self.prompt_entered.set()
        if self.prompt_release:
            self.prompt_release.wait(3)
        self.queue_value = (0, 1)
        return 200, 'application/json', b'{"prompt_id":"new-job"}'


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 10000
        self.host = FakeHost()
        self.host.clock = lambda: self.now
        self.state = Path(self.directory.name) / 'controller.json'
        self.controller = m.Controller(self.host, self.state, lambda: self.now, self.advance)
        self.controller.recover()

    def advance(self, seconds):
        self.now += seconds

    def action(self, action):
        self.controller.submit(action)
        self.controller.worker.join(3)
        self.assertFalse(self.controller.worker.is_alive())

    def image_mode(self):
        self.action('start')
        self.assertEqual(self.controller.status()['mode'], 'image')

    def test_readiness_states_remain_independent_of_image_ui_modes(self):
        self.assertEqual(self.controller.llm_status()['state'], 'ready')
        self.image_mode()
        self.assertEqual(self.controller.llm_status()['state'], 'offline')
        self.assertFalse(self.controller.llm_status()['available'])

    def test_active_workflow_turns_keep_proxy_open_while_new_workflows_wait(self):
        calls = []
        def activity():
            calls.append((self.controller.llm_status()['available'], self.controller.gate.status()['available']))
            return dict(active_workflows=1 if len(calls) == 1 else 0,
                        active_turns=0, observed_at=self.now)
        self.host.workflow_activity = activity
        self.action('start')
        self.assertEqual(calls, [(False, True), (False, True), (False, True)])
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertFalse(self.controller.gate.status()['available'])

    def test_missing_or_stale_activity_preserves_healthy_text(self):
        for activity in ({'active_workflows': 0, 'active_turns': 0, 'observed_at': self.now - 16},
                         {'active_workflows': 0, 'observed_at': self.now}):
            self.host.activity_value = activity
            self.action('start')
            self.assertEqual(self.host.events, [])
            self.assertTrue(self.controller.llm_status()['available'])
            self.assertTrue(self.controller.gate.status()['available'])
            self.assertIn('Cannot verify', self.controller.status()['error'])

    def test_explicit_test_timeout_does_not_stop_active_text_request(self):
        self.assertTrue(self.controller.gate.enter(True))
        with self.assertRaisesRegex(RuntimeError, 'did not become idle'):
            self.controller.drain_text(timeout=10)
        self.assertEqual(self.host.events, [])
        self.assertTrue(self.controller.llm_status()['available'])
        self.controller.gate.leave()

    def test_workflow_drain_beyond_one_hour_keeps_new_workflow_admission_closed(self):
        started, observations = self.now, []
        def activity():
            status = self.controller.status()
            observations.append(self.now - started)
            self.assertEqual(status['mode'], 'starting')
            self.assertFalse(status['llm']['available'])
            self.assertTrue(self.controller.gate.status()['available'])
            self.assertEqual(self.host.events, [])
            return dict(active_workflows=int(self.now - started < 3700),
                        active_turns=0, observed_at=self.now)
        self.host.workflow_activity = activity
        self.action('start')
        self.assertGreaterEqual(max(observations), 3705)
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertFalse(self.controller.llm_status()['available'])

    def test_http_leases_and_metrics_drain_beyond_one_hour_without_reopening(self):
        started, released = self.now, False
        self.assertTrue(self.controller.gate.enter(True))
        self.host.flash_busy = True
        def wait(seconds):
            nonlocal released
            self.advance(seconds)
            self.assertEqual(self.controller.status()['mode'], 'starting')
            self.assertFalse(self.controller.llm_status()['available'])
            self.assertEqual(self.host.events, [])
            if not self.controller.gate.status()['available']:
                self.assertFalse(self.controller.gate.enter(True))
                if self.now - started >= 3700 and not released:
                    self.controller.gate.leave()
                    released = True
                if self.now - started >= 4000:
                    self.host.flash_busy = False
        self.controller.sleep = wait
        self.action('start')
        self.assertTrue(released)
        self.assertGreaterEqual(self.now - started, 4000)
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertFalse(self.controller.llm_status()['available'])

    def test_text_and_image_have_independent_cold_start_deadlines(self):
        self.host.fail_flash = True
        started = self.now
        with self.assertRaisesRegex(RuntimeError, 'Text model did not become ready'):
            self.controller.wait_ready(TEXT)
        self.assertEqual(self.now - started, 1800)
        self.assertTrue(self.controller.status()['text_running'])
        self.assertFalse(self.controller.status()['text_ready'])
        started = self.now
        with self.assertRaisesRegex(RuntimeError, 'ComfyUI did not become ready'):
            self.controller.wait_ready(m.COMFY)
        self.assertEqual(self.now - started, 600)

    def test_exclusive_switch_and_restore(self):
        self.image_mode()
        self.assertEqual(self.host.events, [('stop', TEXT), ('start', m.COMFY)])
        self.action('stop')
        self.assertEqual(self.host.events[-2:], [('stop', m.COMFY), ('start', TEXT)])
        self.assertEqual(self.controller.status()['mode'], 'text')

    def test_busy_flash_is_never_stopped(self):
        self.host.flash_busy = True
        with self.assertRaisesRegex(RuntimeError, 'did not become idle'):
            self.controller.drain_text(timeout=10)
        self.assertEqual(self.host.events, [])
        self.assertTrue(self.controller.status()['text_running'])

    def test_comfy_startup_failure_restores_healthy_flash(self):
        self.host.fail_comfy = True
        self.action('start')
        self.assertEqual(self.host.events, [('stop', TEXT), ('start', m.COMFY),
                                          ('stop', m.COMFY), ('start', TEXT)])
        status = self.controller.status()
        self.assertEqual(status['mode'], 'text')
        self.assertIn('restored', status['error'])
        self.assertFalse(status['comfy_running'])

    def test_inspect_failure_before_switch_leaves_flash_untouched(self):
        original = self.host.running
        failed = False
        def inspect(name):
            nonlocal failed
            if name == m.COMFY and not failed:
                failed = True
                raise OSError('inspect failed')
            return original(name)
        self.host.running = inspect
        self.action('start')
        self.assertEqual(self.host.events, [])
        self.assertTrue(self.host.containers[TEXT])

    def test_post_stop_inspect_failure_rolls_back_flash(self):
        original = self.host.running
        failed = False
        def inspect(name):
            nonlocal failed
            if name == m.COMFY and self.host.events and not failed:
                failed = True
                raise OSError('transient inspect failure')
            return original(name)
        self.host.running = inspect
        self.action('start')
        self.assertTrue(self.host.containers[TEXT])
        self.assertFalse(self.host.containers[m.COMFY])
        self.assertIn('restored', self.controller.status()['error'])

    def test_rollback_does_not_claim_readiness_when_flash_is_unhealthy(self):
        self.host.fail_comfy = self.host.fail_flash = True
        self.action('start')
        error = self.controller.status()['error']
        self.assertNotIn('restored', error)
        self.assertIn('Text model did not become ready', error)

    def test_exactly_thirty_minutes_idle_returns_to_text(self):
        self.image_mode()
        self.assertEqual(self.controller.status()['idle_timeout_seconds'], 1800)
        self.advance(1799)
        self.controller.tick()
        self.assertTrue(self.host.running(m.COMFY))
        self.advance(1)
        self.controller.tick()
        self.controller.worker.join(3)
        self.assertTrue(self.host.running(TEXT))
        self.assertFalse(self.host.running(m.COMFY))
        self.assertEqual(self.host.events[-2:], [('stop', m.COMFY), ('start', TEXT)])
        self.assertTrue(self.controller.llm_status()['available'])

    def test_browser_status_polling_does_not_extend_lease(self):
        self.image_mode()
        last = self.controller.last_activity
        self.advance(3000)
        for _ in range(50):
            self.controller.status()
        self.assertEqual(self.controller.last_activity, last)
        self.assertEqual(self.controller.status()['idle_seconds'], 3000)

    def test_busy_or_queued_work_blocks_stop_and_resets_idle(self):
        self.image_mode()
        self.advance(4000)
        self.host.queue_value = (1, 2)
        self.controller.tick()
        with self.assertRaises(ValueError):
            self.controller.submit('stop')
        self.assertTrue(self.host.running(m.COMFY))
        self.assertEqual(self.controller.status()['idle_seconds'], 0)

    def test_short_job_between_polls_resets_idle_via_history(self):
        self.image_mode()
        self.advance(1799)
        self.host.history_value = 'completed-between-polls'
        self.controller.tick()
        self.advance(2)
        self.controller.tick()
        self.assertTrue(self.host.running(m.COMFY))
        self.assertEqual(self.controller.status()['idle_seconds'], 2)

    def test_unknown_queue_or_history_fails_closed(self):
        self.image_mode()
        self.advance(4000)
        self.host.fail_queue = True
        self.controller.tick()
        self.assertTrue(self.host.running(m.COMFY))
        with self.assertRaises(ValueError):
            self.controller.submit('stop')
        self.host.fail_queue = False
        self.host.fail_history = True
        self.controller.tick()
        self.assertTrue(self.host.running(m.COMFY))

    def test_corrupt_lease_does_not_prevent_recovery(self):
        for value in ('[]', '{"last_activity":NaN}', '{"last_activity":true}'):
            self.state.write_text(value)
            replacement = m.Controller(self.host, self.state, lambda: self.now, self.advance)
            replacement.recover()
            self.assertEqual(replacement.status()['mode'], 'text')
            self.assertEqual(replacement.last_activity, self.now)

    def test_idle_lease_survives_restart(self):
        self.image_mode()
        self.advance(1700)
        replacement = m.Controller(self.host, self.state, lambda: self.now, self.advance)
        replacement.recover()
        self.assertEqual(replacement.status()['idle_seconds'], 1700)
        self.advance(100)
        replacement.tick()
        replacement.worker.join(3)
        self.assertTrue(self.host.running(TEXT))

    def test_crashed_comfy_restores_flash(self):
        self.image_mode()
        self.host.containers[m.COMFY] = False
        self.controller.tick()
        self.controller.worker.join(3)
        self.assertTrue(self.host.running(TEXT))

    def test_stop_waits_for_inflight_prompt_and_does_not_interrupt_it(self):
        self.image_mode()
        self.host.prompt_release = threading.Event()
        prompt_thread = threading.Thread(target=self.controller.prompt, args=('/prompt', IMAGE_PROMPT))
        prompt_thread.start()
        self.assertTrue(self.host.prompt_entered.wait(1))
        self.controller.submit('stop')
        self.assertEqual(self.controller.status()['mode'], 'stopping')
        self.host.prompt_release.set()
        prompt_thread.join(3)
        self.controller.worker.join(3)
        self.assertTrue(self.host.running(m.COMFY))
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertIn('work', self.controller.status()['error'])

    def test_prompt_admission_rejects_text_mode(self):
        status, _, body = self.controller.prompt('/prompt', b'{}')
        self.assertEqual(status, 503)
        self.assertIn(b'not accepting', body)
        self.assertFalse(self.host.prompt_entered.is_set())


class InstallerTest(unittest.TestCase):
    def test_preparation_accepts_an_existing_stopped_flash_container(self):
        installer = (Path(__file__).resolve().parents[1] / 'scripts/prepare-qwen-image-comfyui-spark.sh').read_text()
        preflight = installer.split('free_gib=', 1)[0]
        self.assertIn('docker inspect "$TEXT_CONTAINER" >/dev/null 2>&1', preflight)
        self.assertNotIn("--format '{{.State.Running}}' \"$TEXT_CONTAINER\"", preflight)

    def test_component_downloads_use_the_repository_that_publishes_them(self):
        installer = (Path(__file__).resolve().parents[1] / 'scripts/prepare-qwen-image-comfyui-spark.sh').read_text()
        records = re.findall(r'\("([^"\n]+)", [^\n]+\n\s+"([^"\n]+)",\n\s+"([^"\n]+)"\)', installer)
        by_destination = {destination: (repository, source) for repository, source, destination in records}
        self.assertEqual(by_destination['diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors'][0],
                         'Comfy-Org/Qwen-Image-Edit_ComfyUI')
        for destination in ('text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors',
                            'vae/qwen_image_vae.safetensors'):
            self.assertEqual(by_destination[destination], ('Comfy-Org/Qwen-Image_ComfyUI', 'split_files/' + destination))
        self.assertRegex(installer, r'readonly COMPONENT_REVISION=[0-9a-f]{40}\n')
        self.assertIn('-v "$USER_ROOT:/opt/ComfyUI/user"', installer)
        self.assertNotIn('/opt/ComfyUI/user/default/workflows:ro', installer)


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state = Path(self.directory.name)
        self.host = FakeHost()
        self.controller = m.Controller(self.host, state / 'state.json')
        self.controller.recover()
        page = state / 'index.html'
        page.write_text('<title>ComfyUI</title>')
        self.server = m.ControlHTTPServer(('127.0.0.1', 0), m.handler(self.controller, page))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def post(self, path, body=b'{}', origin='https://comfyui.home.arpa'):
        request = Request(self.url + path, body, {'Content-Type': 'application/json', 'Origin': origin})
        try:
            return urlopen(request, timeout=3)
        except HTTPError as error:
            return error

    def test_origin_and_fixed_action_validation(self):
        with self.post('/control/api/start', origin='https://evil.example') as response:
            self.assertEqual(response.status, 403)
        with self.post('/control/api/start', b'{"command":"arbitrary"}') as response:
            self.assertEqual(response.status, 409)
        with self.post('/control/api/anything') as response:
            self.assertEqual(response.status, 404)
        self.assertEqual(self.host.events, [])

    def test_status_and_embedded_page_are_read_only(self):
        with urlopen(self.url + '/control/?compact=1') as response:
            self.assertIn(b'ComfyUI', response.read())
        with urlopen(self.url + '/control/api/status') as response:
            self.assertEqual(json.load(response)['mode'], 'text')
        self.assertEqual(self.host.events, [])

    def test_slow_partial_body_expires_and_releases_worker(self):
        self.server.connection_timeout = 0.1
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(b'POST /control/api/start HTTP/1.1\r\nHost: localhost\r\n'
                           b'Origin: https://comfyui.home.arpa\r\nContent-Type: application/json\r\n'
                           b'Content-Length: 2\r\n\r\n{')
            self.assertIn(b'502', client.recv(4096))
        with urlopen(self.url + '/control/api/status') as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.host.events, [])

    def test_thread_saturation_refuses_connection_without_extra_worker(self):
        acquired = [self.server.slots.acquire(blocking=False) for _ in range(12)]
        self.assertTrue(all(acquired))
        try:
            with socket.create_connection(self.server.server_address, timeout=2) as client:
                client.sendall(b'GET /control/api/status HTTP/1.0\r\n\r\n')
                try:
                    self.assertEqual(client.recv(1024), b'')
                except ConnectionResetError:
                    pass
        finally:
            for _ in range(12):
                self.server.slots.release()

    def test_proxy_blocks_prompt_while_text_active(self):
        with self.post('/api/prompt') as response:
            self.assertEqual(response.status, 503)
        self.assertFalse(self.host.prompt_entered.is_set())


if __name__ == '__main__':
    unittest.main()
