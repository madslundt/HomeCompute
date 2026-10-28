#!/usr/bin/env python3
"""Execute the real model-local probe against a bounded synthetic HTTP server."""
import base64
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('speech_benchmark', ROOT / 'benchmarks/speech_capacity_benchmark.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.content_type = 'audio/pcm'
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                owner.requests.append((self.path, self.headers, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                if self.path.endswith('transcriptions'):
                    body = json.dumps({'text': 'Denne test virker'}).encode()
                    self.send_header('Content-Type', 'application/json')
                else:
                    body = b'\0\0' * 24000
                    self.send_header('Content-Type', owner.content_type)
                    for name, value in [('X-Sample-Rate', '24000'), ('X-Channels', '1'), ('X-Bit-Depth', '16')]:
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.secret = Path(self.directory.name) / 'key'
        self.secret.write_text('synthetic-placeholder-key')

    def probe(self, value):
        code = m.PROBE.replace("8000 if value['kind']=='asr' else 8004", str(self.server.server_port))
        code = code.replace("Path('/run/secrets/vllm_api_key')", 'Path(' + repr(str(self.secret)) + ')')
        result = subprocess.run([sys.executable, '-c', code], input=json.dumps(value),
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertNotIn('synthetic-placeholder-key', result.stdout + result.stderr)
        self.assertNotIn('Denne test virker', result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_asr_multipart_and_word_error_count_are_real_not_escaped_literals(self):
        value = self.probe({'kind': 'asr', 'audio': base64.b64encode(b'synthetic-wav').decode(),
                            'reference': 'Denne prøve virker', 'seconds': 2})
        self.assertEqual(value['errors'], 1)
        self.assertEqual(value['words'], 3)
        self.assertIsNone(value['error'])
        path, headers, body = self.requests[0]
        self.assertEqual(path, '/v1/audio/transcriptions')
        self.assertIn(b'\r\nContent-Disposition: form-data;', body)
        self.assertNotIn(b'\\r\\n', body)
        self.assertIn(b'\r\n\r\nda\r\n', body)
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-placeholder-key')

    def test_tts_validates_pcm_and_accounts_for_preserved_household_tempo(self):
        value = self.probe({'kind': 'tts', 'text': m.TEXTS[0]})
        self.assertIsNone(value['error'])
        self.assertEqual(value['pcm_bytes'], 48000)
        self.assertAlmostEqual(value['audio_seconds_after_tempo'], 1 / 1.2)
        self.assertIn('first_audio_seconds', value)
        self.assertEqual(json.loads(self.requests[0][2]), {'text': m.TEXTS[0], 'speaker': 'tor'})

    def test_wrong_pcm_contract_is_a_countable_failure(self):
        self.content_type = 'audio/wav'
        self.assertEqual(self.probe({'kind': 'tts', 'text': m.TEXTS[0]}), {'error': 'ValueError'})

    def test_summary_preserves_failures_and_nearest_rank_tail(self):
        rows = [{'error': None, 'rtf': value, 'elapsed_seconds': value, 'errors': 1, 'words': 4}
                for value in [.1, .2, .3, .4]] + [{'error': 'timeout'}]
        self.assertEqual(m.summary(rows), {'trials': 5, 'failures': 1, 'p95_elapsed_seconds': .4,
                                           'p95_rtf': .4, 'wer': .25})


if __name__ == '__main__':
    unittest.main()
