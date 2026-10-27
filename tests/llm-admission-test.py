#!/usr/bin/env python3
"""Real HTTP/SSE admission and lifecycle draining with a replaceable backend."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llm_admission import IntakeGate, TextConfig, WorkflowActivity, make_proxy
from comfyui_lifecycle import COMFY, Controller

TEXT = 'replacement-text-v2'


class Host:
    def __init__(self, config, clock):
        self.config, self.clock = config, clock
        self.containers = {TEXT: True, COMFY: False}
        self.events, self.fail_image = [], False

    def running(self, name):
        return self.containers[name]

    def ready(self, name):
        return self.running(name) and not (name == COMFY and self.fail_image)

    def queue(self):
        return 0, 0

    def history(self):
        return 'history'

    def workflow_activity(self):
        return dict(active_workflows=0, active_turns=0, observed_at=self.clock())

    def text_requests(self):
        return {'running': 0, 'waiting': 0}

    def start(self, name):
        self.events.append(('start', name))
        self.containers[name] = True

    def stop(self, name):
        self.events.append(('stop', name))
        self.containers[name] = False


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.received, self.backend_count = [], 0
        owner = self
        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def do_POST(self):
                owner.backend_count += 1
                owner.received.append((self.path, self.headers.get('Authorization'),
                                       self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(b'data: first\n\n')
                self.wfile.flush()
                owner.started.set()
                owner.release.wait(4)
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.backend.daemon_threads = True
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.config = TextConfig(TEXT, f'http://127.0.0.1:{self.backend.server_port}', 'Replacement model')
        self.clock = lambda: time.monotonic() * 1000
        self.host = Host(self.config, self.clock)
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.host, Path(self.directory.name) / 'state.json',
                                     self.clock, lambda seconds: time.sleep(min(seconds, .01)))
        self.controller.recover()
        self.proxy = make_proxy(self.config, self.controller.gate, port=0)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.proxy.server_port}'

    def tearDown(self):
        self.release.set()
        if self.controller.worker:
            self.controller.worker.join(3)
        self.proxy.shutdown()
        self.proxy.server_close()
        self.backend.shutdown()
        self.backend.server_close()
        self.directory.cleanup()

    def post(self, path='/v1/chat/completions', body=None):
        body = body if body is not None else b'{"model":"stable-alias","stream":true,"messages":[]}'
        request = Request(self.url + path, body,
                          {'Content-Type': 'application/json', 'Authorization': 'Bearer test-placeholder'})
        try:
            return urlopen(request, timeout=3)
        except HTTPError as error:
            return error

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_full_sse_stream_finishes_before_text_container_stops(self):
        received, first_seen = [], threading.Event()
        def client():
            with self.post() as response:
                first = response.readline() + response.readline()
                first_seen.set()
                received.append(first + response.read())
        client_thread = threading.Thread(target=client)
        client_thread.start()
        self.assertTrue(self.started.wait(1))
        self.assertTrue(first_seen.wait(1), 'First SSE event must arrive before completion')
        self.assertEqual(self.controller.gate.status()['active_requests'], 1)
        self.controller.submit('start')
        self.assertFalse(self.controller.llm_status()['available'])
        self.wait(lambda: not self.controller.gate.status()['available'])
        self.assertEqual(self.host.events, [])
        with self.post() as denied:
            self.assertEqual(denied.status, 503)
            self.assertEqual(denied.headers['Retry-After'], '30')
        self.assertEqual(self.backend_count, 1)
        self.release.set()
        client_thread.join(2)
        self.controller.worker.join(2)
        self.assertEqual(received, [b'data: first\n\ndata: [DONE]\n\n'])
        self.assertEqual(self.host.events, [('stop', TEXT), ('start', COMFY)])
        self.assertEqual(self.received[0], ('/v1/chat/completions', 'Bearer test-placeholder',
                                           b'{"model":"stable-alias","stream":true,"messages":[]}'))
        self.assertEqual(self.controller.gate.status()['active_requests'], 0)

    def test_large_request_body_is_forwarded_without_changing_bytes(self):
        body = json.dumps({'model': 'stable-alias', 'prompt': 'x' * 200000}).encode()
        self.release.set()
        with self.post('/v1/responses', body) as response:
            self.assertEqual(response.status, 200)
            response.read()
        self.assertEqual(self.received[0][2], body)
        self.assertEqual(self.backend_count, 1)

    def test_rollback_reopens_admission_after_text_is_ready(self):
        self.host.fail_image = True
        self.controller.submit('start')
        self.controller.worker.join(2)
        self.assertFalse(self.controller.worker.is_alive())
        self.assertTrue(self.controller.llm_status()['available'])
        self.assertEqual(self.host.events[-2:], [('stop', COMFY), ('start', TEXT)])
        with urlopen(self.url + '/v1/models') as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.controller.gate.status()['active_requests'], 0)

    def test_health_requests_do_not_hold_inference_leases(self):
        with urlopen(self.url + '/health') as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.controller.gate.status()['active_requests'], 0)
        self.controller.gate.close('image')
        try:
            urlopen(self.url + '/health')
        except HTTPError as response:
            with response:
                self.assertEqual(response.status, 503)

    def test_failed_upstream_is_not_replayed(self):
        self.backend.shutdown()
        self.backend.server_close()
        with self.post('/v1/responses') as response:
            self.assertEqual(response.status, 502)
        self.assertEqual(self.backend_count, 0)
        self.assertEqual(self.controller.gate.status()['active_requests'], 0)


class ConfigTests(unittest.TestCase):
    def test_generic_container_and_fixed_loopback_endpoint(self):
        config = TextConfig('another-model-v3', 'http://127.0.0.1:19000', 'Future model', '/ready')
        self.assertEqual(config.container, 'another-model-v3')
        self.assertEqual(config.port, 19000)
        for name in ('-option', 'two names', 'bad/name', ''):
            with self.assertRaises(ValueError):
                TextConfig(name)
        for endpoint in ('https://127.0.0.1:19000', 'http://example.com:80',
                         'http://127.0.0.1:19000/override', 'http://user@127.0.0.1:19000'):
            with self.assertRaises(ValueError):
                TextConfig('model', endpoint)

    def test_activity_is_pinned_to_core_https_endpoint(self):
        self.assertIsNone(WorkflowActivity('https://home-core.tail479ad.ts.net:8443/llm/activity').ca_file)
        with self.assertRaises(ValueError):
            WorkflowActivity('https://ai.home.arpa/llm/activity')
        self.assertEqual(WorkflowActivity('https://ai.home.arpa/llm/activity', '/etc/public-ca.crt').url,
                         'https://ai.home.arpa/llm/activity')
        for endpoint in ('http://ai.home.arpa/llm/activity', 'https://example.com/llm/activity',
                         'https://ai.home.arpa/other', 'https://user@ai.home.arpa/llm/activity'):
            with self.assertRaises(ValueError):
                WorkflowActivity(endpoint, '/etc/public-ca.crt')


if __name__ == '__main__':
    unittest.main()
