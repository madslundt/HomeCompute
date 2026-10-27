#!/usr/bin/env python3
"""Real HTTP downloads prove integrity, resumption and preservation of the old lane."""
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('qwen_image21_prepare', ROOT / 'scripts/prepare-qwen-image21-comfyui-spark.py')
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.payload = b'full BF16 fixture model\n' * 100
        self.asset = m.Asset('diffusion_models/fixture.safetensors', len(self.payload),
                             hashlib.sha256(self.payload).hexdigest())
        self.destination = self.root / self.asset.name
        self.partial = self.destination.with_suffix('.safetensors.part')
        self.requests, self.ranges = [], []
        self.support_range, self.cutoff, self.body = True, None, self.payload
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                interval = self.headers.get('Range')
                owner.ranges.append(interval)
                offset = int(interval.removeprefix('bytes=').removesuffix('-')) if interval and owner.support_range else 0
                self.send_response(206 if offset else 200)
                if offset:
                    self.send_header('Content-Range', f'bytes {offset}-{len(owner.body)-1}/{len(owner.body)}')
                self.send_header('Content-Length', str(len(owner.body) - offset))
                self.end_headers()
                self.wfile.write(owner.body[offset:owner.cutoff])

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        actual = build_opener()
        class Opener:
            def open(self, request, timeout):
                owner.requests.append(request.full_url)
                return actual.open(Request(f'http://127.0.0.1:{owner.server.server_port}/model',
                                           headers=dict(request.header_items())), timeout=timeout)
        self.opener = Opener()

    def download(self):
        return m.download(self.root, self.asset, opener=self.opener, report=lambda *_args, **_kwargs: None)

    def write_partial(self, body):
        self.partial.parent.mkdir()
        self.partial.write_bytes(body)

    def test_fresh_download_is_verified_then_atomically_published_without_second_cache_copy(self):
        self.assertEqual(self.download(), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertFalse(self.partial.exists())
        self.assertEqual(self.requests, [self.asset.url])
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_resume_requests_exact_remaining_range(self):
        self.write_partial(self.payload[:17])
        self.download()
        self.assertEqual(self.ranges, ['bytes=17-'])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_ignored_range_restarts_partial_instead_of_appending_duplicate_content(self):
        self.write_partial(self.payload[:17])
        self.support_range = False
        self.download()
        self.assertEqual(self.ranges, ['bytes=17-'])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_interrupted_download_is_not_published_and_next_run_resumes(self):
        self.cutoff = 17
        with self.assertRaisesRegex(RuntimeError, 'size verification'):
            self.download()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.partial.read_bytes(), self.payload[:17])
        self.cutoff = None
        self.download()
        self.assertEqual(self.ranges, [None, 'bytes=17-'])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_same_size_corruption_fails_hash_verification_and_is_never_published(self):
        self.body = b'x' * len(self.payload)
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            self.download()
        self.assertFalse(self.destination.exists())
        self.assertTrue(self.partial.exists())

    def test_verified_final_file_is_reused_and_corrupted_final_is_preserved_for_review(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(self.payload)
        self.download()
        self.assertEqual(self.requests, [])
        self.destination.write_bytes(b'x' * len(self.payload))
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            self.download()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.destination.read_bytes(), b'x' * len(self.payload))

    def test_oversize_and_symlinked_targets_are_refused(self):
        self.body += b'extra'
        with self.assertRaisesRegex(RuntimeError, 'exceeded'):
            self.download()
        self.assertFalse(self.destination.exists())
        self.partial.unlink()
        original = self.root / 'original'
        original.write_bytes(b'preserve')
        self.destination.symlink_to(original)
        with self.assertRaisesRegex(RuntimeError, 'unsafe'):
            self.download()
        self.assertEqual(original.read_bytes(), b'preserve')

    def test_prepare_preserves_old_models_and_does_not_create_huggingface_cache(self):
        old = self.root / 'old-lane.safetensors'
        old.write_bytes(b'existing lane')
        with patch.object(m, 'ASSETS', (self.asset,)), patch.object(m, 'RESERVE_BYTES', 0), \
                patch.object(m, 'build_opener', return_value=self.opener):
            m.prepare(self.root)
        self.assertEqual(old.read_bytes(), b'existing lane')
        self.assertEqual({p.name for p in self.root.iterdir()},
                         {'old-lane.safetensors', '.qwen-image21-prepare.lock', 'diffusion_models'})

    def test_disk_reserve_and_exclusive_lock_fail_before_download(self):
        with patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(RuntimeError, 'disk space'):
                m.prepare(self.root)
        with (self.root / '.qwen-image21-prepare.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                m.prepare(self.root)
        self.assertEqual(self.requests, [])


class ManifestTests(unittest.TestCase):
    def test_default_plan_is_read_only_and_full_precision_non_turbo(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'untouched'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-qwen-image21-comfyui-spark.py'),
                                     '--models', str(missing)], text=True, capture_output=True, check=True)
            plan = json.loads(result.stdout)
            self.assertFalse(missing.exists())
        self.assertEqual(plan['revision'], '0217efd1d9c9452e84f6448903d6e21bfa171ffd')
        self.assertEqual(plan['total_bytes'], 32440124920)
        self.assertEqual([a['name'] for a in plan['assets']], [
            'diffusion_models/qwen_image_2.1_bf16.safetensors',
            'text_encoders/qwen3vl_8b_bf16.safetensors',
            'vae/qwen_image_2.1_vae_bf16.safetensors'])
        self.assertTrue(all(len(a['sha256']) == 64 for a in plan['assets']))

    def test_redirects_strip_optional_token_before_leaving_huggingface(self):
        request = Request(m.ASSETS[0].url, headers={'Authorization': 'Bearer private-placeholder'})
        redirected = m.DownloadRedirect().redirect_request(
            request, None, 302, 'redirect', {}, 'https://cdn.example.invalid/model')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaisesRegex(RuntimeError, 'insecure'):
            m.DownloadRedirect().redirect_request(request, None, 302, 'redirect', {}, 'http://cdn.invalid/model')


if __name__ == '__main__':
    unittest.main()
