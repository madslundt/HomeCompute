#!/usr/bin/env python3
"""HTTP boundaries and real audio-format normalization for the Spark relay."""
from __future__ import annotations
import contextlib
from email import policy
from email.parser import BytesParser
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stt_relay", ROOT / "scripts/openai-stt-relay.py")
RELAY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RELAY
SPEC.loader.exec_module(RELAY)


def wav(seconds=0.2, rate=48000, channels=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * int(seconds * rate) * channels)
    return output.getvalue()


def upload(audio=b"synthetic", fields=None, files=1):
    boundary = "test-recording-boundary"
    body = b""
    for name, value in fields or [("model", "stt-danish"), ("language", "da")]:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for index in range(files):
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording-{index}.m4a"\r\nContent-Type: audio/mp4\r\n\r\n'.encode() + audio + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, "multipart/form-data; boundary=" + boundary


class UploadBoundaryTests(unittest.TestCase):
    def test_malformed_multipart_and_multiple_files_are_rejected(self):
        examples = [upload(files=2), upload(files=0), (b"not multipart", "multipart/form-data; boundary=x")]
        body, content_type = upload()
        examples.append((body.rsplit(b"--test-recording-boundary--", 1)[0], content_type))
        for body, content_type in examples:
            with self.subTest(body_length=len(body)), self.assertRaises(RELAY.RelayError) as caught:
                RELAY.parse_upload(body, content_type, RELAY.Config())
            self.assertEqual(caught.exception.status, 400)

    def test_only_one_exact_model_is_allowed(self):
        for fields in [[("model", "whisper-1")], [("model", "stt-danish"), ("model", "stt-danish")], [("language", "da")]]:
            body, content_type = upload(fields=fields)
            with self.assertRaises(RELAY.RelayError) as caught:
                RELAY.parse_upload(body, content_type, RELAY.Config())
            self.assertEqual(caught.exception.status, 400)

    def test_upload_and_parameter_limits(self):
        body, content_type = upload()
        with self.assertRaises(RELAY.RelayError) as caught:
            RELAY.parse_upload(body, content_type, RELAY.Config(max_upload_bytes=100))
        self.assertEqual(caught.exception.status, 413)
        body, content_type = upload(fields=[("model", "stt-danish"), ("prompt", "x" * 20_000)])
        with self.assertRaises(RELAY.RelayError) as caught:
            RELAY.parse_upload(body, content_type, RELAY.Config())
        self.assertEqual(caught.exception.status, 400)

    def test_environment_cannot_broaden_runtime_limits(self):
        for name, value in {"STT_MAX_AUDIO_SECONDS": "61", "STT_MAX_CONNECTIONS": "5",
                            "STT_MAX_UPLOAD_BYTES": "10000001", "STT_UPSTREAM_BASE_URL": "http://example.com"}.items():
            with self.subTest(name=name), patch.dict(os.environ, {name: value}), self.assertRaises(ValueError):
                RELAY.load_config()

    def test_model_and_backend_pair_is_fixed_for_each_deployment(self):
        with patch.dict(os.environ, {'STT_MODEL': 'whisper-turbo', 'STT_UPSTREAM_BASE_URL': 'http://whisper-primary:8000'}):
            config = RELAY.load_config()
            body, kind = upload(fields=[('model', 'whisper-turbo')])
            self.assertEqual(RELAY.parse_upload(body, kind, config)[0], [('model', b'whisper-turbo')])
            wrong, kind = upload(fields=[('model', 'stt-danish')])
            with self.assertRaises(RELAY.RelayError):
                RELAY.parse_upload(wrong, kind, config)
        for model, upstream in [('whisper-turbo', RELAY.UPSTREAM), ('stt-danish', 'http://whisper-primary:8000'), ('arbitrary', RELAY.UPSTREAM)]:
            with self.subTest(model=model, upstream=upstream), patch.dict(os.environ, {'STT_MODEL': model, 'STT_UPSTREAM_BASE_URL': upstream}), self.assertRaises(ValueError):
                RELAY.load_config()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed for actual audio normalization")
class AudioNormalizationTests(unittest.TestCase):
    def test_stereo_wav_and_iphone_aac_become_bounded_pcm_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            aac = Path(directory) / "iphone.m4a"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=48000:cl=stereo", "-t", "0.2", "-c:a", "aac", str(aac)], check=True)
            for recording in (wav(), aac.read_bytes()):
                with wave.open(io.BytesIO(RELAY.normalize_audio(recording, RELAY.Config())), "rb") as decoded:
                    self.assertEqual((decoded.getnchannels(), decoded.getsampwidth(), decoded.getframerate()), (1, 2, 16000))
                    self.assertGreater(decoded.getnframes(), 0)

    def test_long_audio_is_rejected_instead_of_silently_truncated(self):
        with self.assertRaises(RELAY.RelayError) as caught:
            RELAY.normalize_audio(wav(seconds=2), RELAY.Config(max_audio_seconds=1))
        self.assertEqual(caught.exception.status, 413)

    def test_invalid_audio_and_decoder_failure_do_not_log_private_material(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(RELAY.RelayError) as caught:
                RELAY.normalize_audio(b"private recording sk-secret-test", RELAY.Config())
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("private", caught.exception.message)
        with patch.object(RELAY.subprocess, "run", side_effect=subprocess.TimeoutExpired("decoder", 20)), \
             self.assertRaises(RELAY.RelayError) as caught:
            RELAY.normalize_audio(wav(), RELAY.Config())
        self.assertEqual(caught.exception.status, 400)


class HttpRelayTests(unittest.TestCase):
    def test_backend_timeouts_and_failures_do_not_expose_backend_error_material(self):
        for failure, status in [
            (urllib.error.URLError(socket.timeout("private upstream sk-key")), 504),
            (urllib.error.URLError("private upstream sk-key"), 502),
            (urllib.error.HTTPError("http://private", 400, "sk-key", {}, io.BytesIO(b"private audio")), 400),
        ]:
            with self.subTest(status=status), patch.object(RELAY.urllib.request, "urlopen", side_effect=failure), \
                 self.assertRaises(RELAY.RelayError) as caught:
                RELAY.upstream_request(RELAY.Config(), "/health", None)
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn("private", caught.exception.message)
            self.assertNotIn("sk-key", caught.exception.message)

    def test_backend_response_is_bounded_before_forwarding(self):
        class LargeResponse(io.BytesIO):
            status = 200
            headers = {"Content-Type": "application/json"}

        with patch.object(RELAY.urllib.request, "urlopen", return_value=LargeResponse(b"x" * (RELAY.MAX_RESPONSE_BYTES + 1))), \
             self.assertRaises(RELAY.RelayError) as caught:
            RELAY.upstream_request(RELAY.Config(), "/health", None)
        self.assertEqual(caught.exception.status, 502)

    def start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed for the complete HTTP request")
    def test_upstream_receives_normalized_audio_parameters_and_authorization_without_logs(self):
        captured = []

        class Backend(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                message = BytesParser(policy=policy.default).parsebytes(
                    ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + body)
                captured.append((self.path, self.headers.get("Authorization"), list(message.iter_parts())))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"text":"Hej verden"}')

            def log_message(self, *args):
                pass

        upstream = self.start_server(ThreadingHTTPServer(("127.0.0.1", 0), Backend))
        relay = self.start_server(RELAY.RelayServer(("127.0.0.1", 0), RELAY.Config(upstream=upstream)))
        body, content_type = upload(wav(), [("model", "stt-danish"), ("language", "da"), ("response_format", "json")])
        request = urllib.request.Request(relay + "/v1/audio/transcriptions", data=body,
                                         headers={"Content-Type": content_type, "Authorization": "Bearer private-test-key"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response), {"text": "Hej verden"})
        self.assertEqual(output.getvalue(), "")
        path, auth, parts = captured[0]
        self.assertEqual(path, "/v1/audio/transcriptions")
        self.assertEqual(auth, "Bearer private-test-key")
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in parts}
        self.assertEqual(fields["language"], b"da")
        self.assertEqual(fields["response_format"], b"json")
        with wave.open(io.BytesIO(fields["file"]), "rb") as decoded:
            self.assertEqual(decoded.getframerate(), 16000)

    def test_busy_service_and_nonallowlisted_paths_fail_without_forwarding(self):
        server = RELAY.RelayServer(("127.0.0.1", 0), RELAY.Config(max_connections=1))
        base = self.start_server(server)
        server.inflight.acquire()
        self.addCleanup(server.inflight.release)
        for path, status in [("/v1/models", 429), ("/key/generate", 404), ("/v1/models?upstream=example.com", 404)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + path, timeout=2)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()

    def test_oversized_content_length_is_rejected_before_reading_body(self):
        base = self.start_server(RELAY.RelayServer(("127.0.0.1", 0), RELAY.Config(max_upload_bytes=100)))
        request = urllib.request.Request(base + "/v1/audio/transcriptions", data=b"", headers={"Content-Length": "1000"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 413)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
