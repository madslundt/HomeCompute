"""Synthetic HTTP/resource tests; no model, hardware, or private audio required."""
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shadow_adapter', ROOT / 'deploy/parakeet-shadow/adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def wav(pcm=b'\x00\x00' * 1600, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def multipart(fields=None, audio=None):
    fields = [('model', adapter.MODEL), ('response_format', 'verbose_json')] if fields is None else fields
    parts = []
    for name, value in fields:
        parts.append(f'--boundary\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="synthetic.wav"\r\nContent-Type: audio/wav\r\n\r\n' + (wav() if audio is None else audio) + b'\r\n--boundary--\r\n')
    return b''.join(parts)


class Runtime:
    ready = True
    calls = 0
    entered = None
    release = None
    error = None

    def identity(self):
        return dict(model_sha256='a' * 64, model_snapshot='b' * 40, runtime='transformers',
                    runtime_commit='c' * 40, device='cpu', parameter_bytes=42)

    def transcribe_pcm(self, pcm, sample_rate, guard):
        self.calls += 1
        self.pcm = pcm
        if self.entered:
            self.entered.set()
            self.release.wait(3)
        guard()
        if self.error:
            raise self.error
        return dict(text='Synthetic output.', token_ids=[1], token_durations=[2], token_timestamps=[0.0],
                    encoder_frames=1, advanced_encoder_frames=2, decoder_completed=True)


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.guard = adapter.ResourceGuard(lambda: (1000, 200))
        self.server = adapter.ShadowServer(('127.0.0.1', 0), self.runtime, 'k' * 32, 'd' * 40, 'sha256:' + 'e' * 64, self.guard)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()

    def tearDown(self):
        if self.runtime.release:
            self.runtime.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.guard.close()

    def request(self, method='POST', path='/v1/audio/transcriptions', body=None, authorized=True, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        selected = {'Content-Type': 'multipart/form-data; boundary=boundary'}
        if authorized:
            selected['Authorization'] = 'Bearer ' + 'k' * 32
        selected.update(headers or {})
        connection.request(method, path, body if body is not None else multipart(), selected)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def test_valid_pcm_and_no_invented_language_or_speech_timing(self):
        status, headers, data = self.request()
        self.assertEqual(status, 200)
        value = json.loads(data)
        self.assertEqual(value['segments'], [])
        self.assertNotIn('language', value)
        self.assertEqual(value['duration'], 0.1)
        self.assertEqual(value['pcm_sha256'], hashlib.sha256(b'\0\0' * 1600).hexdigest())
        self.assertEqual(self.runtime.pcm, b'\0\0' * 1600)
        self.assertEqual(headers['X-HomeCompute-STT-Image-ID'], 'sha256:' + 'e' * 64)
        self.assertEqual(headers['X-HomeCompute-STT-Runtime'], 'transformers')
        self.assertTrue(value['decoder_completed'])
        self.assertEqual(value['native_token_timing_status'], 'model_predictions_not_qualified_word_alignment')

    def test_authorization_precedes_body(self):
        self.assertEqual(self.request(authorized=False)[0], 401)
        self.assertEqual(self.runtime.calls, 0)

    def test_binary_multipart_preserves_every_pcm_byte(self):
        pcm = bytes(range(256)) * 10
        self.assertEqual(self.request(body=multipart(audio=wav(pcm)))[0], 200)
        self.assertEqual(self.runtime.pcm, pcm)

    def test_reject_unknown_duplicate_and_whisper_options(self):
        for fields in ([('model', adapter.MODEL), ('language', 'da')],
                       [('model', adapter.MODEL), ('model', adapter.MODEL)],
                       [('model', 'whisper-1')], [('model', adapter.MODEL), ('beam_size', '8')]):
            with self.subTest(fields=fields):
                self.assertEqual(self.request(body=multipart(fields))[0], 400)
        self.assertEqual(self.runtime.calls, 0)

    def test_reject_bad_wav_and_sample_rate(self):
        for audio in (wav(rate=8000), wav()[:-2], wav() + b'extra', b'not a wav'):
            self.assertEqual(self.request(body=multipart(audio=audio))[0], 400)
        self.assertEqual(self.runtime.calls, 0)

    def test_incomplete_multipart_rejected(self):
        self.assertEqual(self.request(body=multipart().replace(b'--boundary--\r\n', b''))[0], 400)

    def test_second_audio_chunk_is_not_silently_omitted(self):
        original = wav()
        extra = b'data' + (2).to_bytes(4, 'little') + b'\x01\x00'
        combined = b'RIFF' + (len(original) + len(extra) - 8).to_bytes(4, 'little') + original[8:] + extra
        self.assertEqual(self.request(body=multipart(audio=combined))[0], 400)
        self.assertEqual(self.runtime.calls, 0)

    def test_bounded_body(self):
        self.assertEqual(self.request(headers={'Content-Length': str(adapter.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.runtime.calls, 0)

    def test_busy_has_health_and_rejects_second_request(self):
        self.runtime.entered = threading.Event()
        self.runtime.release = threading.Event()
        first = []
        worker = threading.Thread(target=lambda: first.append(self.request()))
        worker.start()
        self.assertTrue(self.runtime.entered.wait(1))
        status, _, data = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)['inference_busy'])
        self.assertEqual(self.request()[0], 409)
        self.runtime.release.set()
        worker.join()
        self.assertEqual(first[0][0], 200)
        self.assertEqual(self.runtime.calls, 1)

    def test_aborted_or_failed_runtime_never_returns_partial_success(self):
        for error, status in ((adapter.RuntimeGuardError('private'), 503), (adapter.RuntimeInferenceError('private'), 502)):
            self.runtime.error = error
            actual, headers, data = self.request()
            self.assertEqual(actual, status)
            self.assertNotIn(b'private', data)
            self.assertNotIn(b'Synthetic output', data)
            self.assertNotIn('X-HomeCompute-STT-Image-ID', headers)

    def test_low_memory_latches_and_blocks_admission(self):
        self.guard.reader = lambda: (1000, 99)
        self.assertEqual(self.request()[0], 503)
        self.guard.reader = lambda: (1000, 900)
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.runtime.calls, 0)

    def test_drain_refuses_new_calls(self):
        self.guard.stopping.set()
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.request('GET', '/health')[0], 503)

    def test_plain_and_json_formats(self):
        for format_ in ('text', 'json'):
            status, _, data = self.request(body=multipart([('model', adapter.MODEL), ('response_format', format_)]))
            self.assertEqual(status, 200)
            self.assertEqual(data.decode() if format_ == 'text' else json.loads(data),
                             'Synthetic output.' if format_ == 'text' else {'text': 'Synthetic output.'})


class GuardTests(unittest.TestCase):
    def test_deadline(self):
        guard = adapter.ResourceGuard(lambda: (1000, 100))
        with self.assertRaises(adapter.RuntimeGuardError):
            guard.request_guard(time.monotonic() - 1)()

    def test_monitor_latches_low_headroom_between_callbacks(self):
        current = [200]
        guard = adapter.ResourceGuard(lambda: (1000, current[0]))
        guard.start()
        try:
            current[0] = 90
            self.assertTrue(guard.failed.wait(1))
            current[0] = 200
            with self.assertRaises(adapter.RuntimeGuardError):
                guard.check()
        finally:
            guard.close()

    def test_key_permissions_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / 'key'
            key.write_text('x' * 32)
            key.chmod(0o600)
            self.assertEqual(adapter.read_key(key), 'x' * 32)
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                adapter.read_key(key)
            link = Path(temp) / 'link'
            link.symlink_to(key)
            with self.assertRaises(OSError):
                adapter.read_key(link)

    def test_missing_key_wait_is_bounded_and_bad_permissions_are_not_retried(self):
        guard = adapter.ResourceGuard(lambda: (1000, 200))
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / 'key'
            with self.assertRaisesRegex(ValueError, 'not provisioned'):
                adapter.await_key(key, guard, timeout=0)
            key.write_text('x' * 32)
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'Invalid shadow key file'):
                adapter.await_key(key, guard, timeout=30)


if __name__ == '__main__':
    unittest.main()
