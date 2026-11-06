#!/usr/bin/env python3
"""Synthetic API boundary tests; no model, private data, or GPU required."""
from copy import deepcopy
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("reference_api", ROOT / "deploy/whisper-reference-shadow/adapter.py")
API = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(API)
CODEC = API.CODEC
KEY = b"synthetic-private-key-for-tests"
IDENTITY = {"homecompute_revision": "a" * 40, "image_id": "sha256:" + "b" * 64}


def wav(frames=1600, channels=1, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(channels); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(b"\x00\x00" * frames * channels)
    return buffer.getvalue()


def multipart(audio=None, fields=None):
    rows = [("model", "whisper"), ("response_format", "verbose_json")] if fields is None else fields
    body = b""
    for name, value in rows:
        body += (f'--synthetic-boundary\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    body += b'--synthetic-boundary\r\nContent-Disposition: form-data; name="file"; filename="synthetic.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    body += wav() if audio is None else audio
    return body + b"\r\n--synthetic-boundary--\r\n", "multipart/form-data; boundary=synthetic-boundary"


def result():
    return {"text": " Synthetic text.", "language": "en", "segments": [
        {"text": " Synthetic text.", "start": 0.0, "end": 0.1, "avg_logprob": -.2}],
        "decoding_observations": [{"temperature": 0.0, "token_count": 4,
                                   "effective_sample_len": 224, "budget_reached": False}],
        "decoding_budget_status": "not_reached_no_coverage_claim"}


class CodecTests(unittest.TestCase):
    def test_exact_pcm_and_duration(self):
        kind, language, pcm, duration = CODEC.parse_request(*multipart())
        self.assertEqual((kind, language, duration), ("verbose_json", None, .1))
        self.assertEqual(pcm, b"\x00\x00" * 1600)

    def test_ambiguous_and_truncated_riff_are_rejected(self):
        original = wav()
        duplicate = original + b"data" + struct.pack("<I", 2) + b"\x00\x00"
        duplicate = duplicate[:4] + struct.pack("<I", len(duplicate) - 8) + duplicate[8:]
        for audio in (original + b"trailing", original[:-1], duplicate,
                      original[:4] + b"\xff" * 4 + original[8:], wav(channels=2), wav(rate=8000), wav(frames=480001)):
            with self.subTest(size=len(audio)), self.assertRaises(CODEC.InvalidRequest):
                CODEC.parse_request(*multipart(audio))

    def test_profile_override_duplicates_and_unknown_fields_are_rejected(self):
        for addition in (("model", "whisper"), ("prompt", "synthetic"), ("temperature", "1"),
                         ("language_candidates", "da,en"), ("response_format", "text")):
            with self.subTest(addition=addition), self.assertRaises(CODEC.InvalidRequest):
                CODEC.parse_request(*multipart(fields=[("model", "whisper"), ("response_format", "verbose_json"), addition]))

    def test_incomplete_pcm_frame_is_not_silently_discarded(self):
        audio = wav(frames=2)
        # Same RIFF extent: three data bytes and one padding byte instead of four PCM bytes.
        audio = audio[:40] + struct.pack("<I", 3) + audio[44:]
        with self.assertRaises(CODEC.InvalidRequest):
            CODEC.parse_request(*multipart(audio))

    def test_text_and_original_timing_preserved_with_unknown_out_of_bounds(self):
        original = result(); original["segments"][0]["end"] = 3
        actual = CODEC.normalize_result(original, .1)
        self.assertEqual(actual["text"], original["text"])
        self.assertEqual(actual["reference_segments"], original["segments"])
        self.assertIsNone(actual["segments"][0]["end"])
        self.assertFalse(actual["timing_qualified"])

    def test_unmappable_segments_never_lose_canonical_text(self):
        original = result(); original["segments"][0]["text"] = "different synthetic text"
        actual = CODEC.normalize_result(original, .1)
        self.assertEqual(actual["segments"][0]["text"], original["text"])
        self.assertEqual(actual["segment_mapping"], "unknown")
        self.assertIsNone(actual["segments"][0]["start"])

    def test_repeated_text_maps_chronologically(self):
        original = result(); original["text"] = " Same. Same."
        original["segments"] = [{"text": " Same.", "start": n / 20, "end": (n + 1) / 20} for n in range(2)]
        actual = CODEC.normalize_result(original, .1)
        self.assertEqual([row["start_offset"] for row in actual["segments"]], [0, 6])

    def test_budget_evidence_survives_and_inconsistency_fails_closed(self):
        original = result(); row = original["decoding_observations"][0]
        row.update(token_count=224, budget_reached=True)
        original["decoding_budget_status"] = "budget_reached"
        actual = CODEC.normalize_result(original, .1)
        self.assertEqual(actual["decoding_observations"], original["decoding_observations"])
        self.assertEqual(actual["decoding_budget_status"], "budget_reached")
        original["decoding_budget_status"] = "not_reached_no_coverage_claim"
        with self.assertRaises(ValueError):
            CODEC.normalize_result(original, .1)

    def test_missing_or_unbounded_observations_fail(self):
        for observations in (None, [{}], result()["decoding_observations"] * 65):
            original = result(); original["decoding_observations"] = observations
            with self.assertRaises(ValueError):
                CODEC.normalize_result(original, .1)


class Engine:
    def __init__(self):
        self.calls = []; self.started = threading.Event(); self.release = threading.Event()
        self.release.set(); self.error = False

    def describe(self):
        return {**{name: API.PIN[name] for name in ("model_sha256", "model_snapshot", "runtime", "runtime_commit", "loader_version", "loader_sha256")},
                "device": "cuda:0", "persistent_tensor_bytes": API.PIN["tensor_bytes"],
                "persistent_tensor_count": API.PIN["tensor_count"], "persistent_dtype_counts": {"float32": 744, "float16": 515},
                "deterministic_algorithms": True, "decoding_options": API.PIN["decoding_defaults"],
                "language_codes": ["da", "en"] + ["x" + chr(97 + n // 26) + chr(97 + n % 26) for n in range(98)]}

    def transcribe_pcm(self, pcm, language=None):
        self.calls.append((pcm, language)); self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("synthetic timeout")
        if self.error:
            raise ValueError("synthetic private exception MUST NOT LEAK")
        return result()


class APITests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.server = API.ReferenceServer(self.engine, KEY, IDENTITY, address=("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.engine.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, method="POST", path="/v1/audio/transcriptions", fields=None, key=KEY):
        body, content_type = multipart(fields=fields)
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        connection.request(method, path, body if method == "POST" else None,
                           {"Authorization": "Bearer " + key.decode(), "Content-Type": content_type})
        response = connection.getresponse(); data = response.read(); headers = dict(response.getheaders())
        status = response.status; connection.close()
        return status, data, headers

    def test_authenticated_provenance_and_budget_evidence(self):
        status, data, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["decoding_budget_status"], "not_reached_no_coverage_claim")
        self.assertEqual(headers["X-HomeCompute-STT-Image-ID"], IDENTITY["image_id"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(len(self.engine.calls), 1)

    def test_unknown_language_returns_400_without_poisoning_engine(self):
        status, _, _ = self.request(fields=[("model", "whisper"), ("language", "xx")])
        self.assertEqual(status, 400); self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.request(method="GET", path="/health")[0], 200)
        self.assertEqual(self.request()[0], 200)

    def test_bad_auth_never_invokes_engine(self):
        self.assertEqual(self.request(key=b"invalid")[0], 401)
        self.assertEqual(self.engine.calls, [])

    def test_concurrent_request_refused_and_health_remains_responsive(self):
        self.engine.release.clear()
        answers = []
        client = threading.Thread(target=lambda: answers.append(self.request()))
        client.start()
        try:
            self.assertTrue(self.engine.started.wait(2))
            self.assertEqual(self.request()[0], 409)
            status, data, _ = self.request(method="GET", path="/health")
            self.assertEqual(status, 200); self.assertTrue(json.loads(data)["inference_busy"])
        finally:
            self.engine.release.set(); client.join(3)
        self.assertEqual(answers[0][0], 200); self.assertFalse(self.server.admission.locked())

    def test_client_disconnect_does_not_release_active_inference(self):
        self.engine.release.clear()
        body, kind = multipart()
        client = socket.create_connection(self.server.server_address, timeout=3)
        client.sendall((f"POST /v1/audio/transcriptions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {KEY.decode()}\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
        self.assertTrue(self.engine.started.wait(2)); client.close()
        self.assertTrue(self.server.admission.locked())
        self.assertEqual(self.request()[0], 409)
        self.engine.release.set()

    def test_engine_failure_latches_unavailable_without_private_error(self):
        self.engine.error = True
        status, data, _ = self.request()
        self.assertEqual(status, 500); self.assertNotIn(b"MUST NOT LEAK", data)
        self.assertEqual(self.request(method="GET", path="/health")[0], 503)
        self.assertEqual(self.request()[0], 503); self.assertEqual(len(self.engine.calls), 1)

    def test_identity_or_cpu_mismatch_refused(self):
        original = self.engine.describe()
        for field, value in (("device", "cpu"), ("loader_sha256", "changed"), ("deterministic_algorithms", False)):
            proof = deepcopy(original); proof[field] = value
            with self.subTest(field=field), patch.object(self.engine, "describe", return_value=proof), self.assertRaises(RuntimeError):
                API.checked_engine_description(self.engine)


class CredentialTests(unittest.TestCase):
    def test_key_permissions_links_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"; path.write_bytes(KEY); path.chmod(0o600)
            self.assertEqual(API.read_key(str(path)), KEY)
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                API.read_key(str(path))

            path.chmod(0o600); link = Path(directory) / "link"; link.symlink_to(path)
            with self.assertRaises(OSError):
                API.read_key(str(link))
            link.unlink(); os.link(path, link)
            with self.assertRaises(RuntimeError):
                API.read_key(str(path))

    def test_fifo_credential_refused_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fifo"; os.mkfifo(path, 0o600)
            with self.assertRaises(RuntimeError):
                API.read_key(str(path))

    def test_invalid_identity_refused(self):
        with self.assertRaises(RuntimeError):
            API.checked_identity({"HOME_COMPUTE_REVISION": "main", "SHADOW_IMAGE_ID": "latest"})


if __name__ == "__main__":
    unittest.main()
