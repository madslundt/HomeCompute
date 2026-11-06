#!/usr/bin/env python3
"""Synthetic language-preference request and per-window metadata regressions."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codec", ROOT / "deploy/whisper-cpp-stt/contract.py")
CODEC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CODEC)
ADAPTER_SPEC = importlib.util.spec_from_file_location("language_adapter", ROOT / "deploy/whisper-cpp-stt/adapter.py")
ADAPTER = importlib.util.module_from_spec(ADAPTER_SPEC)
sys.modules[ADAPTER_SPEC.name] = ADAPTER
ADAPTER_SPEC.loader.exec_module(ADAPTER)


class LanguageContractTests(unittest.TestCase):
    def test_cpu_does_not_silently_ignore_unsupported_candidate_preference(self):
        target = io.BytesIO()
        with wave.open(target, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0" * 3200)
        body, content_type = CODEC.make_native_multipart(
            [("model", "whisper"), ("language_candidates", "da,en")], target.getvalue())
        config = ADAPTER.CandidateConfig(Path("/unused/model"), Path("/unused/key"))
        server = ADAPTER.CandidateServer(config, b"synthetic-secret-value", ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions", data=body,
                headers={"Content-Type": content_type, "Authorization": "Bearer synthetic-secret-value"})
            with patch.object(ADAPTER, "_native_request", return_value=b'{"text":""}') as native:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 422)
                error.exception.close()
                native.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_native_unknown_language_is_a_bounded_client_error(self):
        for status, body, expected in ((400, b'{"error":"invalid language candidates"}', 400),
                                       (400, b'private backend detail', 502),
                                       (500, b'{"error":"invalid language candidates"}', 502)):
            error = urllib.error.HTTPError("http://native", status, "", {}, io.BytesIO(body))
            with patch.object(ADAPTER.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(ADAPTER.RequestError) as caught:
                    ADAPTER._native_request(b"synthetic", "multipart/form-data", 1)
                self.assertEqual(caught.exception.status, expected)

    def test_unreadable_native_error_is_closed_and_bounded(self):
        error = urllib.error.HTTPError("http://native", 400, "", {}, io.BytesIO())
        with patch.object(error, "read", side_effect=TimeoutError()), \
                patch.object(error, "close", wraps=error.close) as close, \
                patch.object(ADAPTER.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ADAPTER.RequestError) as caught:
                ADAPTER._native_request(b"synthetic", "multipart/form-data", 1)
            self.assertEqual(caught.exception.status, 502)
            close.assert_called_once()

    def test_absent_preference_keeps_existing_native_fields(self):
        _, fields = CODEC.normalize_fields({"model": "whisper"})
        self.assertNotIn("language_candidates", dict(fields))
        self.assertEqual(dict(fields)["language"], "auto")

    def test_preference_is_canonical_and_does_not_force_explicit_language(self):
        for value in ("en,da", "da,en", " DA , en "):
            body, content_type = CODEC.make_native_multipart(
                [("model", "whisper"), ("language_candidates", value)], b"synthetic-audio")
            parsed, audio = CODEC.parse_multipart(body, content_type)
            _, fields = CODEC.normalize_fields(parsed)
            self.assertEqual(audio, b"synthetic-audio")
            self.assertEqual(dict(fields)["language_candidates"], "da,en")
            self.assertEqual(dict(fields)["language"], "auto")
        _, fields = CODEC.normalize_fields({"model": "whisper", "language": "fr",
                                            "language_candidates": "da,en"})
        self.assertEqual(dict(fields)["language"], "fr")

    def test_invalid_preference_is_rejected_before_native_dispatch(self):
        for value in ("", "en,", "da,,en", "en,en", "EN,en", "danish", "en\nda",
                      "en\x00", "a", "en," * 17, "xx" * 40):
            with self.subTest(value=value), self.assertRaises(CODEC.RequestError):
                CODEC.normalize_fields({"model": "whisper", "language_candidates": value})

    def test_window_selection_diagnostics_keep_raw_probabilities_and_anchors(self):
        def response(text, language, selected):
            return json.dumps({"task": "transcribe", "language": language, "text": text + "\n",
                               "segments": [{"start": 0, "end": 1, "text": text}],
                               "language_selection": {"mode": "candidate_auto",
                                   "candidates": ["da", "en"], "selected": selected,
                                   "detected": "cy", "detected_probability": 0.88,
                                   "selected_probability": 0.11}}).encode()
        raw, _ = CODEC.combine_native_windows(
            [(0, 30, response("Synthetic English.", "english", "en")),
             (30, 30, response("Syntetisk dansk.", "danish", "da"))], "verbose_json", 60)
        result = json.loads(raw)
        self.assertEqual(result["language"], "mixed")
        self.assertEqual(result["text"], "Synthetic English.\nSyntetisk dansk.\n")
        for window, segment, selected in zip(result["language_windows"], result["segments"],
                                             ("en", "da")):
            self.assertEqual(window["language_selection"]["selected"], selected)
            self.assertEqual(window["language_selection"]["selected_probability"], 0.11)
            self.assertEqual(result["text"][segment["start_offset"]:segment["end_offset"]],
                             segment["text"])

    def test_malformed_selection_fails_with_bounded_backend_error(self):
        valid = {"mode": "candidate_auto", "candidates": ["da", "en"], "selected": "en",
                 "detected": "cy", "detected_probability": 0.88, "selected_probability": 0.11}
        for key, value in (("mode", []), ("candidates", []), ("candidates", ["en", "da"]),
                           ("selected", "cy"), ("detected", "welsh"),
                           ("selected_probability", True), ("selected_probability", float("nan")),
                           ("detected_probability", 1.1)):
            payload = {"text": "", "segments": [], "language_selection": valid | {key: value}}
            with self.subTest(key=key, value=value), self.assertRaises(CODEC.RequestError) as error:
                CODEC.normalize_native_response(json.dumps(payload).encode(), "verbose_json", 30)
            self.assertEqual(error.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
