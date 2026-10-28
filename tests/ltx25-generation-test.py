#!/usr/bin/env python3
"""Real local HTTP exercises job identity, queue draining and prior-mode restoration."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ltx25_generate', ROOT / 'scripts/generate-ltx25-comfyui.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.mode, self.failed, self.missing_id = 'text', False, False
        self.history_polls, self.queue_polls, self.stops = 0, 0, 0
        self.terminal, self.started, self.submission = False, False, None
        self.refuse_switch, self.unified = False, True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, value, status=200):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith('/history/') or self.path == '/queue':
                    if self.headers.get('Origin') is not None:
                        self.send_response(403)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                if self.path == '/control/api/status':
                    state = dict(mode=owner.mode, text_running=owner.mode == 'text',
                                 text_ready=owner.mode == 'text', comfy_running=owner.mode in {'image', 'video'},
                                 comfy_ready=owner.mode in {'image', 'video'})
                    if owner.unified:
                        state['target_mode'] = owner.mode
                    self.reply(state)
                elif self.path == '/history/our-prompt':
                    owner.history_polls += 1
                    owner.terminal = owner.history_polls > 1
                    history = {'status': {'completed': True, 'status_str': 'error' if owner.failed else 'success',
                                          'messages': [['execution_error', {'node_id': '384'}]] if owner.failed else []},
                               'outputs': {'75': {'images': [{'filename': 'fox.mp4', 'subfolder': 'video', 'type': 'output'}]}}}
                    self.reply({'our-prompt': history} if owner.terminal else {})
                elif self.path == '/queue':
                    owner.queue_polls += 1
                    # Accepted unknown-ID work and another client's queued work both finish first.
                    busy = owner.queue_polls <= 2
                    self.reply({'queue_running': [[0, 'other-or-unknown-prompt']] if busy else [], 'queue_pending': []})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.assertEqual(self.headers.get('Origin'), 'https://models.home.arpa')
                if self.path == '/control/api/start/video':
                    if owner.refuse_switch:
                        self.reply({'error': 'Image queue is busy'}, status=409)
                        return
                    owner.started, owner.mode = True, 'video'
                    self.reply({'target_mode': 'video'})
                elif self.path == '/prompt':
                    owner.assertEqual(owner.mode, 'video')
                    owner.submission = body
                    self.reply({'number': 1} if owner.missing_id else {'prompt_id': 'our-prompt'})
                elif self.path == '/control/api/stop':
                    owner.assertGreaterEqual(owner.queue_polls, 4)
                    owner.assertTrue(owner.terminal or owner.missing_id)
                    owner.stops += 1
                    owner.mode = 'text'
                    self.reply({})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = m.Client(self.server.server_port, self.server.server_port)

    def run_clip(self, **kwargs):
        report = m.generate(self.root, self.client, timeout=1, interval=0.001, **kwargs)
        self.assertEqual(Path(report['report_path']).stat().st_mode & 0o777, 0o600)
        return report

    def test_success_waits_for_other_queued_work_then_restores_and_records_exact_job(self):
        report = self.run_clip(prompt='A quiet fox', frames=57, width=1024, height=576)
        self.assertEqual(report['status'], 'completed')
        self.assertTrue(report['restored_text'])
        self.assertEqual(self.stops, 1)
        self.assertEqual(report['prompt_id'], 'our-prompt')
        self.assertEqual(report['mp4'][0]['filename'], 'fox.mp4')
        graph = self.submission['prompt']
        workflow = self.submission['extra_data']['extra_pnginfo']['workflow']
        self.assertEqual(graph['356']['inputs']['length'], 57)
        ui = next(n for n in workflow['nodes'] if n['id'] == 356)
        self.assertEqual(ui['widgets_values'], [512, 288, 57, 1])

    def test_node_execution_failure_still_waits_for_queue_idle_before_restoring(self):
        self.failed = True
        report = self.run_clip()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(report['restored_text'])
        self.assertEqual(report['history']['status']['status_str'], 'error')
        self.assertEqual(report['mp4'], [])
        self.assertEqual(self.stops, 1)

    def test_missing_submission_id_never_claims_success_and_drains_unknown_job(self):
        self.missing_id = True
        report = self.run_clip()
        self.assertEqual(report['status'], 'failed')
        self.assertIsNone(report['prompt_id'])
        self.assertEqual(report['mp4'], [])
        self.assertTrue(report['restored_text'])
        self.assertEqual(self.history_polls, 0)

    def test_preexisting_video_mode_is_preserved(self):
        self.mode = 'video'
        report = self.run_clip()
        self.assertEqual(report['status'], 'completed')
        self.assertFalse(report['owns_video_mode'])
        self.assertFalse(report['mode_switch_accepted'])
        self.assertFalse(self.started)
        self.assertEqual(self.stops, 0)
        self.assertEqual(self.mode, 'video')

    def test_initial_image_switches_to_video_and_restores_text_after_accepted_switch(self):
        self.mode = 'image'
        report = self.run_clip()
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['initial_mode'], 'image')
        self.assertTrue(report['mode_switch_accepted'])
        self.assertTrue(report['owns_video_mode'])
        self.assertTrue(report['restored_text'])
        self.assertEqual(self.mode, 'text')
        self.assertEqual(self.stops, 1)

    def test_busy_image_refusal_does_not_enqueue_or_later_stop_the_prior_image_service(self):
        self.mode, self.refuse_switch = 'image', True
        report = self.run_clip()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['mode_switch_accepted'])
        self.assertFalse(report['owns_video_mode'])
        self.assertEqual(self.mode, 'image')
        self.assertIsNone(self.submission)
        self.assertEqual(self.stops, 0)
        self.assertEqual(self.queue_polls, 0)

    def test_legacy_controller_is_rejected_before_any_mode_change_or_submission(self):
        self.unified = False
        with self.assertRaisesRegex(RuntimeError, 'Unified controls not installed'):
            self.run_clip()
        self.assertFalse(self.started)
        self.assertIsNone(self.submission)
        self.assertEqual(self.stops, 0)
        self.assertFalse((self.root / 'outputs').exists())


if __name__ == '__main__':
    unittest.main()
