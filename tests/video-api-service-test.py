#!/usr/bin/env python3
"""Durable OpenAI video jobs through real HTTP, with an isolated fake engine."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('video_api', ROOT / 'scripts/video_api_service.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
MP4 = b'\x00\x00\x00\x18ftypisom' + b'fixture MP4 content'


class FakeEngine:
    def __init__(self):
        self.release, self.entered = threading.Event(), threading.Event()
        self.prompts, self.fail, self.data = [], False, MP4

    async def generate(self, prompt, accepted, status):
        self.prompts.append(prompt)
        accepted('our-spark-prompt')
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        await status('Generating the requested video')
        if self.fail:
            raise RuntimeError('Fixture video generation failed')
        return self.data


class VideoAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store, self.engine = m.Store(self.root), FakeEngine()
        self.worker = m.Worker(self.store, self.engine)
        self.server = m.Server(('127.0.0.1', 0), m.handler(self.store, self.worker))
        self.peer = patch.object(m, 'ALLOWED_PEER', '127.0.0.1')
        self.peer.start()
        self.worker.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.engine.release.set()
        self.worker.stop()
        self.server.shutdown()
        self.server.server_close()
        self.peer.stop()
        self.directory.cleanup()

    def request(self, path, value=None, *, body=None, content_type='application/json', headers=None):
        if value is not None:
            body = json.dumps(value).encode()
        request = Request(self.url + path, body, {'Content-Type': content_type, **(headers or {})})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            data = response.read()
            return response.status, json.loads(data) if response.headers.get_content_type() == 'application/json' else data

    def create(self, prompt='A fox in snow'):
        status, value = self.request('/v1/videos', {'model': 'ltx-2.5', 'prompt': prompt, 'seconds': '2', 'size': '768x512'})
        self.assertEqual(status, 202)
        return value

    def terminal(self, identifier):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            code, value = self.request('/v1/videos/' + identifier)
            self.assertEqual(code, 200)
            if value['status'] in {'completed', 'failed'}:
                return value
            time.sleep(0.005)
        self.fail('The fixture video job did not finish')

    @staticmethod
    def multipart(fields, *, file_part=False):
        parts = []
        for name, value in fields:
            parts.append(f'--fixture\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        if file_part:
            parts.append(b'--fixture\r\nContent-Disposition: form-data; name="input_reference"; filename="input.png"\r\nContent-Type: image/png\r\n\r\nimage\r\n')
        return b''.join(parts) + b'--fixture--\r\n'

    def test_fast_accept_status_and_private_completed_content_survive_store_reopen(self):
        began = time.monotonic()
        created = self.create()
        self.assertLess(time.monotonic() - began, 0.5)
        self.assertEqual((created['object'], created['status'], created['seconds'], created['size']),
                         ('video', 'queued', '2', '768x512'))
        identifier = created['id']
        self.assertTrue(self.engine.entered.wait(1))
        self.assertEqual(self.request('/v1/videos/' + identifier + '/content')[0], 409)
        self.assertNotIn('prompt', self.request('/v1/videos/' + identifier)[1])
        self.engine.release.set()
        completed = self.terminal(identifier)
        self.assertEqual((completed['status'], completed['progress'], completed['error']), ('completed', 100, None))
        self.assertEqual(self.request('/v1/videos/' + identifier + '/content'), (200, MP4))
        self.assertEqual((self.root / (identifier + '.mp4')).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'jobs.sqlite3').stat().st_mode & 0o777, 0o600)
        self.worker.stop()
        reopened = m.Store(self.root)
        self.assertEqual(reopened.get(identifier)['status'], 'completed')
        self.assertEqual(reopened.content(identifier).read_bytes(), MP4)

    def test_litellm_style_text_multipart_is_supported_and_files_duplicates_are_rejected(self):
        fields = [('model', 'ltx-2.5'), ('prompt', 'A cinematic fox'), ('seconds', '2'), ('size', '768x512')]
        code, result = self.request('/v1/videos', body=self.multipart(fields), content_type='multipart/form-data; boundary=fixture')
        self.assertEqual(code, 202)
        self.assertEqual(result['model'], 'ltx-2.5')
        for body in (self.multipart(fields, file_part=True), self.multipart(fields + [('prompt', 'duplicate')])):
            self.assertEqual(self.request('/v1/videos', body=body,
                             content_type='multipart/form-data; boundary=fixture')[0], 400)

    def test_unsupported_models_dimensions_lengths_references_and_unknown_fields_are_explicit_errors(self):
        for extra in ({'model': 'arbitrary-model'}, {'seconds': '8'}, {'seconds': True},
                      {'seconds': 2.0}, {'size': '1920x1080'}, {'input_reference': 'image.png'},
                      {'remix': 'some-video'}, {'prompt': ''}, {'prompt': 'x' * 4001}):
            with self.subTest(extra=extra):
                self.assertEqual(self.request('/v1/videos', {'prompt': 'A fox', **extra})[0], 400)
        self.assertEqual(self.engine.prompts, [])
        self.assertEqual(self.store.active(), 0)

    def test_pending_jobs_are_bounded_and_health_reports_them_without_prompt_text(self):
        for index in range(m.MAX_PENDING):
            self.create('Private prompt ' + str(index))
        code, value = self.request('/v1/videos', {'prompt': 'Overflow'})
        self.assertEqual(code, 429)
        self.assertIn('full', value['error']['message'])
        self.assertEqual(self.request('/health'), (200, {'status': 'ok', 'active_jobs': m.MAX_PENDING}))

    def test_engine_failure_and_invalid_mp4_never_publish_content(self):
        self.engine.fail = True
        self.engine.release.set()
        first = self.create()['id']
        failed = self.terminal(first)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error']['code'], 'video_generation_failed')
        self.assertEqual(self.request('/v1/videos/' + first + '/content')[0], 409)
        self.engine.fail, self.engine.data = False, b'not a video'
        second = self.create()['id']
        self.assertEqual(self.terminal(second)['status'], 'failed')
        self.assertFalse((self.root / (second + '.mp4')).exists())

    def test_source_ip_cannot_be_spoofed_with_forwarded_headers(self):
        with patch.object(m, 'ALLOWED_PEER', '172.28.200.2'):
            headers = {'X-Forwarded-For': '172.28.200.2', 'Authorization': 'Bearer local-unused'}
            self.assertEqual(self.request('/health', headers=headers)[0], 403)
            self.assertEqual(self.request('/v1/videos', {'prompt': 'A fox'}, headers=headers)[0], 403)
        self.assertEqual(self.store.active(), 0)

    def test_invalid_or_traversing_ids_never_reach_an_arbitrary_file(self):
        for path in ('/v1/videos/../../jobs.sqlite3/content', '/v1/videos/not-a-uuid',
                     '/v1/videos/00000000-0000-4000-8000-000000000001/content', '/v1/videos'):
            self.assertEqual(self.request(path)[0], 404)
        created = self.create()['id']
        self.assertEqual(self.request('/v1/videos/' + created + '/content?variant=thumbnail')[0], 400)

    def test_restart_fails_processing_jobs_and_resumes_only_never_started_queued_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = m.Store(root)
            processing = original.create('Already accepted on Spark')['id']
            original.claim()
            original.update(processing, backend_id='accepted-spark-id')
            queued = original.create('Never submitted to Spark')['id']
            reopened = m.Store(root)
            self.assertEqual(reopened.get(processing)['status'], 'failed')
            self.assertEqual(reopened.get(queued)['status'], 'queued')
            engine = FakeEngine()
            engine.release.set()
            worker = m.Worker(reopened, engine)
            worker.start()
            deadline = time.monotonic() + 2
            while reopened.get(queued)['status'] != 'completed' and time.monotonic() < deadline:
                time.sleep(0.005)
            worker.stop()
            self.assertEqual(reopened.get(queued)['status'], 'completed')
            self.assertEqual(engine.prompts, ['Never submitted to Spark'])
            self.assertFalse((root / (processing + '.mp4')).exists())


if __name__ == '__main__':
    unittest.main()
