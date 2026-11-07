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
import struct
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
    def test_large_v3_compatibility_aliases_preserve_request_parameters(self):
        with patch.dict(os.environ, {'STT_MODEL': 'whisper-large-v3',
                                    'STT_UPSTREAM_BASE_URL': 'http://whisper-primary:8000'}):
            config = RELAY.load_config()
        for alias in ('whisper', 'whisper-large-v3', 'stt-danish', 'whisper-turbo'):
            body, kind = upload(fields=[('model', alias), ('language', 'da'),
                                       ('response_format', 'verbose_json'),
                                       ('timestamp_granularities[]', 'segment'), ('temperature', '0')])
            fields, audio = RELAY.parse_upload(body, kind, config)
            self.assertEqual(fields, [('model', b'whisper-large-v3'), ('language', b'da'),
                                     ('response_format', b'verbose_json'),
                                     ('timestamp_granularities[]', b'segment'), ('temperature', b'0')])
            self.assertEqual(audio, b'synthetic')

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
                            "STT_MAX_UPLOAD_BYTES": "10000001", "STT_UPSTREAM_BASE_URL": "http://example.com",
                            "STT_RECORDING_MAX_UPLOAD_BYTES": "40000001",
                            "STT_RECORDING_MAX_AUDIO_SECONDS": "1201",
                            "STT_RECORDING_UPSTREAM_TIMEOUT_SECONDS": "1801"}.items():
            with self.subTest(name=name), patch.dict(os.environ, {name: value}), self.assertRaises(ValueError):
                RELAY.load_config()

    def test_model_and_backend_pair_is_fixed_for_the_full_model(self):
        self.assertEqual(RELAY.load_config().model, 'whisper-large-v3')
        for model, upstream in [('whisper-turbo', RELAY.UPSTREAM),
                                ('stt-danish', 'http://hviske-primary:8000'),
                                ('whisper-large-v3', 'http://hviske-primary:8000'), ('arbitrary', RELAY.UPSTREAM)]:
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
    def test_maximum_longform_pcm_payload_fits_the_bounded_request_cap(self):
        audio = wav(seconds=1200, rate=16000, channels=1)
        body, content_type = upload(audio, [('model', 'whisper'), ('response_format', 'verbose_json')])
        config = RELAY.Config(recording_max_upload_bytes=40_000_000, recording_max_audio_seconds=1200)
        parse_config = RELAY.replace(config, max_upload_bytes=config.recording_max_upload_bytes)
        _fields, parsed = RELAY.parse_upload(body, content_type, parse_config)
        self.assertEqual(parsed, audio)
        self.assertLessEqual(len(body), config.recording_max_upload_bytes)
        RELAY.validate_recording_pcm(parsed, config)

    def test_recording_route_forwards_exact_pcm_multipart_and_six_provenance_headers(self):
        self.assert_recording_route_provenance()

    def test_recording_route_forwards_optional_actual_backend_device(self):
        self.assert_recording_route_provenance(device='cuda')

    def assert_recording_route_provenance(self, device=None):
        recording = wav(seconds=0.2, rate=16000, channels=1)
        body, content_type = upload(recording, [
            ('model', 'whisper'), ('response_format', 'verbose_json'),
            ('language', 'auto'), ('temperature', '0'), ('max_context', '0'),
        ])
        captured = []
        provenance = {
            'X-HomeCompute-STT-Model-SHA256': 'model-pin',
            'X-HomeCompute-STT-Model-Snapshot': 'model-snapshot',
            'X-HomeCompute-STT-Runtime': 'whisper.cpp',
            'X-HomeCompute-STT-Runtime-Commit': 'runtime-pin',
            'X-HomeCompute-STT-HomeCompute-Revision': 'source-pin',
            'X-HomeCompute-STT-Image-ID': 'image-pin',
        }
        if device is not None:
            provenance['X-HomeCompute-STT-Device'] = device

        class Backend(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((self.path, self.headers.get('Authorization'), self.headers.get('Content-Type'),
                                 self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                for key, value in provenance.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(b'{"text":"synthetic"}')

            def log_message(self, *args):
                pass

        upstream = self.start_server(ThreadingHTTPServer(('127.0.0.1', 0), Backend))
        with tempfile.NamedTemporaryFile() as credential:
            credential.write(b'production-role-secret-value')
            credential.flush()
            config = RELAY.Config(
                upstream=upstream,
                recording_upstream=upstream,
                recording_authorization_file=Path(credential.name),
            )
            relay = self.start_server(RELAY.RelayServer(('127.0.0.1', 0), config))
            request = urllib.request.Request(relay + RELAY.RECORDING_PATH, data=body,
                                             headers={'Content-Type': content_type,
                                                      'Authorization': 'Bearer production-role-secret-value'})
            with patch.object(RELAY, 'recording_key', return_value=b'production-role-secret-value'):
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.load(response), {'text': 'synthetic'})
                    self.assertEqual({key: response.headers[key] for key in provenance}, provenance)
        path, auth, upstream_type, upstream_body = captured[0]
        self.assertEqual(path, '/v1/audio/transcriptions')
        self.assertEqual(auth, 'Bearer production-role-secret-value')
        self.assertEqual(upstream_type, content_type)
        self.assertEqual(upstream_body, body)

    def test_recording_route_rejects_missing_wrong_or_non_pcm_credentials_before_forwarding(self):
        valid_audio = wav(seconds=0.2, rate=16000, channels=1)
        body, content_type = upload(valid_audio)
        with tempfile.NamedTemporaryFile() as credential:
            credential.write(b'production-role-secret-value')
            credential.flush()
            server = RELAY.RelayServer(('127.0.0.1', 0), RELAY.Config(
                recording_authorization_file=Path(credential.name)))
            base = self.start_server(server)
            with patch.object(RELAY, 'recording_key', return_value=b'production-role-secret-value'):
                for headers, payload in [({}, body),
                                         ({'Authorization': 'Bearer wrong'}, body),
                                         ({'Authorization': 'Bearer production-role-secret-value'},
                                          upload(wav(seconds=0.2), fields=[('model', 'whisper')])[0])]:
                    request = urllib.request.Request(base + RELAY.RECORDING_PATH, data=payload,
                                                     headers={'Content-Type': content_type, **headers})
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertIn(caught.exception.code, (400, 401))
                    caught.exception.close()

    def test_recording_route_rejects_truncated_pcm_even_when_wav_header_claims_valid_duration(self):
        complete = wav(seconds=0.2, rate=16000, channels=1)
        claimed_frames = len(complete) - 44 + 3200
        truncated = bytearray(complete)
        struct.pack_into('<I', truncated, 4, len(truncated) - 8 + 3200)
        struct.pack_into('<I', truncated, 40, claimed_frames)
        with self.assertRaises(RELAY.RelayError) as caught:
            RELAY.validate_recording_pcm(bytes(truncated), RELAY.Config())
        self.assertEqual(caught.exception.status, 400)

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
        self.assertEqual(fields["model"], b"whisper-large-v3")
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
