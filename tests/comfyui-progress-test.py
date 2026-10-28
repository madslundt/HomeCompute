#!/usr/bin/env python3
"""Progress follows real lifecycle operations without changing their admission rules."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from comfyui_lifecycle import COMFY, Controller, Host
from comfyui_progress import text_requests

spec = importlib.util.spec_from_file_location('lifecycle_fixture', ROOT / 'tests/comfyui-lifecycle-test.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
TEXT = fixture.TEXT


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 10000
        self.host = fixture.FakeHost()
        self.host.clock = lambda: self.now
        self.controller = Controller(self.host, Path(self.directory.name) / 'state.json',
                                     lambda: self.now, self.advance)
        self.controller.recover()

    def advance(self, seconds):
        self.now += seconds

    def finish(self):
        self.controller.worker.join(3)
        self.assertFalse(self.controller.worker.is_alive())

    def transition(self):
        return self.controller.status()['transition']

    def test_initial_check_and_elapsed_are_visible_before_worker_can_proceed(self):
        self.assertIsNone(self.transition())
        with self.controller.admission:
            response = self.controller.submit('start')
            self.assertEqual(response['transition'], {
                'action': 'start', 'target_mode': 'image', 'phase': 'waiting_workflows', 'elapsed_seconds': 0,
                'phase_elapsed_seconds': 0, 'workflows': None, 'agent_turns': None,
                'text_requests': None, 'reason': None})
            self.advance(8)
            self.assertEqual(self.transition()['elapsed_seconds'], 8)
            self.assertEqual(self.transition()['phase_elapsed_seconds'], 8)
            self.assertFalse(self.controller.llm_status()['available'])
            self.assertEqual(self.host.events, [])
        self.finish()
        self.assertIsNone(self.transition())

    def test_workflow_counts_update_without_restarting_phase_clock(self):
        remaining = iter([(2, 3), (1, 1), (0, 0), (0, 0)])
        readings, initial = [], []
        def activity():
            initial.append(self.transition())
            workflows, turns = next(remaining)
            return dict(active_workflows=workflows, active_turns=turns, observed_at=self.now)
        def wait(seconds):
            readings.append(self.transition())
            self.assertFalse(self.controller.llm_status()['available'])
            self.assertTrue(self.controller.gate.status()['available'])
            self.advance(seconds)
        self.host.workflow_activity, self.controller.sleep = activity, wait
        self.controller.submit('start')
        self.finish()
        self.assertIsNone(initial[0]['workflows'])
        self.assertEqual([(r['workflows'], r['agent_turns']) for r in readings],
                         [(2, 3), (1, 1), (0, 0)])
        self.assertEqual([r['phase_elapsed_seconds'] for r in readings], [0, 5, 10])
        self.assertEqual([r['elapsed_seconds'] for r in readings], [0, 5, 10])
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertIsNone(self.transition())

    def test_vllm_counts_remain_visible_with_zero_http_leases_and_unknown_is_not_idle(self):
        remaining = iter([{'running': 2, 'waiting': 1}, {'running': 0, 'waiting': 1},
                          None, {'running': 0, 'waiting': 0}])
        readings, initial = [], []
        def metrics():
            initial.append(self.transition())
            self.assertEqual(self.host.events, [])
            return next(remaining)
        def wait(seconds):
            progress = self.transition()
            if progress['phase'] == 'draining_text':
                readings.append(progress)
                self.assertEqual(self.controller.llm_status()['active_requests'], 0)
                self.assertFalse(self.controller.gate.status()['available'])
            self.advance(seconds)
        self.host.text_requests, self.controller.sleep = metrics, wait
        self.controller.submit('start')
        self.finish()
        self.assertIsNone(initial[0]['text_requests'])
        self.assertEqual([r['text_requests'] for r in readings],
                         [{'running': 2, 'waiting': 1}, {'running': 0, 'waiting': 1}, None])
        self.assertEqual([r['phase_elapsed_seconds'] for r in readings], [0, 2, 4])
        self.assertEqual([r['elapsed_seconds'] for r in readings], [5, 7, 9])
        self.assertEqual(self.host.events, [('stop', TEXT), ('start', COMFY)])
        self.assertIsNone(self.transition())

    def test_start_phases_precede_blocking_docker_and_health_operations(self):
        stops, starts, ready = [(threading.Event(), threading.Event()) for _ in range(3)]
        self.addCleanup(lambda: [pair[1].set() for pair in (stops, starts, ready)])
        original_stop, original_start, original_ready = self.host.stop, self.host.start, self.host.ready
        trace, seen_ready = [], False
        def stop(name):
            if name == TEXT:
                trace.append(self.transition()['phase'])
                stops[0].set()
                stops[1].wait(3)
            original_stop(name)
        def start(name):
            if name == COMFY:
                trace.append(self.transition()['phase'])
                starts[0].set()
                starts[1].wait(3)
            original_start(name)
        def health(name):
            nonlocal seen_ready
            if name == COMFY and self.host.containers[name] and not seen_ready:
                seen_ready = True
                trace.append(self.transition()['phase'])
                ready[0].set()
                ready[1].wait(3)
            return original_ready(name)
        self.host.stop, self.host.start, self.host.ready = stop, start, health
        self.controller.submit('start')
        for pair, phase, duration in [(stops, 'stopping_text', 7),
                                      (starts, 'starting_comfyui', 3),
                                      (ready, 'waiting_comfyui', 11)]:
            self.assertTrue(pair[0].wait(1), phase)
            self.assertEqual(self.transition()['phase'], phase)
            self.advance(duration)
            self.assertEqual(self.transition()['phase_elapsed_seconds'], duration)
            self.assertFalse(self.controller.status()['comfy_ready'])
            pair[1].set()
        self.finish()
        self.assertEqual(trace, ['stopping_text', 'starting_comfyui', 'waiting_comfyui'])
        self.assertEqual(self.controller.status()['mode'], 'image')
        self.assertIsNone(self.transition())

    def test_stop_phases_precede_operations_and_clear_when_text_is_ready(self):
        self.controller.submit('start')
        self.finish()
        original_stop, original_start, original_ready = self.host.stop, self.host.start, self.host.ready
        trace = []
        def record(operation, name, *args):
            progress = self.transition()
            if progress is not None:
                trace.append((operation, name, progress['phase'], progress['action']))
            return {'stop': original_stop, 'start': original_start, 'ready': original_ready}[operation](name, *args)
        self.host.stop = lambda name: record('stop', name)
        self.host.start = lambda name: record('start', name)
        self.host.ready = lambda name: record('ready', name)
        self.controller.submit('stop')
        self.finish()
        self.assertEqual(trace[:3], [('stop', COMFY, 'stopping_comfyui', 'stop'),
                                    ('start', TEXT, 'starting_text', 'stop'),
                                    ('ready', TEXT, 'waiting_text', 'stop')])
        self.assertIsNone(self.transition())

    def test_media_profile_switch_exposes_target_before_unloading_the_previous_process(self):
        self.controller.submit('start')
        self.finish()
        phases, original_stop = [], self.host.stop

        def stop(name):
            phases.append((name, self.controller.status()['target_mode'], self.transition()['phase']))
            original_stop(name)

        self.host.stop = stop
        with self.controller.admission:
            response = self.controller.submit('start', 'video')
            self.assertEqual(response['target_mode'], 'video')
            self.assertEqual(response['transition']['target_mode'], 'video')
            self.assertEqual(response['transition']['phase'], 'stopping_comfyui')
        self.finish()
        self.assertEqual(phases, [(COMFY, 'video', 'stopping_comfyui')])
        self.assertEqual(self.controller.status()['mode'], 'video')
        self.assertIsNone(self.transition())

    def test_rollback_reason_is_visible_while_text_reloads_and_clears_at_completion(self):
        self.host.fail_comfy = True
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original_start, original_ready = self.host.start, self.host.ready
        reloads = []
        def start(name):
            if name == TEXT:
                reloads.append(self.transition())
            original_start(name)
        def health(name):
            if name == TEXT and self.transition() is not None:
                entered.set()
                release.wait(3)
            return original_ready(name)
        self.host.start, self.host.ready = start, health
        self.controller.submit('start')
        self.assertTrue(entered.wait(1))
        progress = self.transition()
        self.assertEqual((progress['action'], progress['phase'], progress['reason']),
                         ('start', 'waiting_text', 'image_start_failed'))
        self.assertEqual(progress['elapsed_seconds'], 605)
        self.assertFalse(self.controller.llm_status()['available'])
        self.assertEqual((reloads[0]['phase'], reloads[0]['reason']),
                         ('starting_text', 'image_start_failed'))
        self.advance(20)
        self.assertEqual(self.transition()['phase_elapsed_seconds'], 20)
        release.set()
        self.finish()
        self.assertEqual(self.controller.status()['mode'], 'text')
        self.assertIn('restored', self.controller.status()['error'])
        self.assertIsNone(self.transition())
        self.assertTrue(self.controller.llm_status()['available'])

    def test_activity_failure_and_recovery_have_no_stale_transition(self):
        self.host.activity_value = {'active_workflows': 0, 'active_turns': 0, 'observed_at': self.now - 60}
        self.controller.submit('start')
        self.finish()
        self.assertIsNone(self.transition())
        self.assertIn('Cannot verify', self.controller.status()['error'])
        self.assertEqual(self.host.events, [])
        replacement = Controller(self.host, Path(self.directory.name) / 'state.json')
        replacement.recover()
        self.assertIsNone(replacement.status()['transition'])


class MetricsTests(unittest.TestCase):
    def test_labelled_engine_counts_are_summed_without_request_payloads(self):
        self.assertEqual(text_requests('''# HELP vllm:num_requests_running test
vllm:num_requests_running{engine="0"} 2
vllm:num_requests_waiting{engine="0"} 1
vllm:num_requests_running{engine="1"} 3e0
vllm:num_requests_waiting{engine="1"} 4
'''), {'running': 5, 'waiting': 5})

    def test_missing_invalid_or_fractional_counts_never_claim_idle(self):
        for running in ('NaN', '+Inf', '-1', '0.5', 'bogus'):
            self.assertIsNone(text_requests(f'vllm:num_requests_running {running}\nvllm:num_requests_waiting 0'))
        for missing in ('', 'vllm:num_requests_running 0', 'vllm:num_requests_waiting 0'):
            self.assertIsNone(text_requests(missing))
        self.assertEqual(text_requests('vllm:num_requests_running 0\nvllm:num_requests_waiting 0'),
                         {'running': 0, 'waiting': 0})

    def test_host_uses_configured_metric_port_and_unknown_network_is_null(self):
        host = Host(fixture.FakeHost().config)
        with patch.object(host, 'get', return_value='vllm:num_requests_running 2\nvllm:num_requests_waiting 1') as get:
            self.assertEqual(host.text_requests(), {'running': 2, 'waiting': 1})
            get.assert_called_once_with(host.config.port, '/metrics', raw=True)
        with patch.object(host, 'get', side_effect=OSError('network')):
            self.assertIsNone(host.text_requests())


if __name__ == '__main__':
    unittest.main()
