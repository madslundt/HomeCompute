#!/usr/bin/env python3
"""Stdlib-only reference engine guards/options and observational delegation tests."""
import contextlib
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/whisper-reference-shadow"))
import engine


class Samples:
    def __init__(self, data):
        self.values = list(struct.unpack("<" + "h" * (len(data) // 2), data))

    def astype(self, dtype):
        return self

    def __truediv__(self, denominator):
        return [value / denominator for value in self.values]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.original = {"text": " synthetic text", "language": "en", "segments": [
            {"text": " synthetic text", "start": 0.0, "end": 0.1, "avg_logprob": -0.2}],
            "extra_upstream_key": {"preserved": True}}
        owner = self
        class Model:
            decoding_observations = []
            def transcribe(self, samples, **options):
                owner.calls.append((samples, options))
                self.decoding_observations.append({"temperature": 0.0, "token_count": 2,
                                                  "effective_sample_len": 224, "budget_reached": False})
                return owner.original
        self.model = Model()
        self.engine = engine.ReferenceEngine("unused")
        self.engine.model = self.model
        self.engine._suppression_tokens = list(engine.DECODING_OPTIONS["suppress_tokens"])
        self.engine.np = types.SimpleNamespace(float32="float32", frombuffer=lambda data, dtype: Samples(data))
        self.cleanups = []
        self.engine.torch = types.SimpleNamespace(
            inference_mode=contextlib.nullcontext,
            cuda=types.SimpleNamespace(synchronize=lambda device: self.cleanups.append("sync"),
                                       empty_cache=lambda: self.cleanups.append("empty")))
        tokenizer = types.ModuleType("whisper.tokenizer"); tokenizer.LANGUAGES = {"en": "english", "da": "danish"}
        self.patch = patch.dict(sys.modules, {"whisper.tokenizer": tokenizer})
        self.patch.start(); self.addCleanup(self.patch.stop)

    def test_fixed_options_pcm_and_upstream_result_preserved(self):
        result = self.engine.transcribe_pcm(struct.pack("<3h", -32768, 0, 16384))
        self.assertEqual(self.calls[0][0], [-1.0, 0.0, 0.5])
        self.assertEqual(self.calls[0][1], {**engine.DECODING_OPTIONS, "language": None})
        self.assertEqual(self.calls[0][1]["temperature"], (0.0,))
        self.assertEqual(self.calls[0][1]["suppress_tokens"], list(range(50258, 50365)))
        for key, value in self.original.items():
            self.assertEqual(result[key], value)
        self.assertNotIn("decoding_observations", self.original)
        self.assertEqual(result["decoding_budget_status"], "not_reached_no_coverage_claim")
        self.assertEqual(self.cleanups, ["sync", "sync", "empty"])

    def test_request_observations_and_options_reset(self):
        first = self.engine.transcribe_pcm(b"\x00\x00", language="da")
        self.calls[0][1]["suppress_tokens"].append(123)
        second = self.engine.transcribe_pcm(b"\x00\x00")
        self.assertEqual(len(first["decoding_observations"]), 1)
        self.assertEqual(len(second["decoding_observations"]), 1)
        self.assertEqual(self.calls[1][1]["suppress_tokens"], list(range(50258, 50365)))
        self.assertIsNone(self.calls[1][1]["language"])
        self.assertEqual(engine.DECODING_OPTIONS["suppress_tokens"], list(range(50258, 50365)))

    def test_constructor_guard_cannot_be_overridden_by_request_guard(self):
        def abort():
            raise engine.RuntimeGuardError("synthetic resource failure")
        self.engine.guard = abort
        with self.assertRaises(engine.RuntimeGuardError):
            self.engine.transcribe_pcm(b"\x00\x00", guard=lambda: None)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.engine._request_lock.locked())

    def test_late_guard_abort_never_returns_partial_success(self):
        count = 0
        def abort_after_decode():
            nonlocal count
            count += 1
            if count == 2:
                raise engine.RuntimeGuardError("synthetic deadline")
        with self.assertRaises(engine.RuntimeGuardError):
            self.engine.transcribe_pcm(b"\x00\x00", guard=abort_after_decode)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.engine._request_lock.locked())

    def test_concurrent_request_refused_even_without_callback(self):
        self.engine._request_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "Concurrent"):
                self.engine.transcribe_pcm(b"\x00\x00")
        finally:
            self.engine._request_lock.release()
        self.assertEqual(self.calls, [])

    def test_malformed_audio_language_and_cpu_fallback_refused(self):
        for data in (b"", b"x", b"x" * 960002):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.engine.transcribe_pcm(data)
        with self.assertRaises(ValueError):
            self.engine.transcribe_pcm(b"xx", sample_rate=8000)
        with self.assertRaises(ValueError):
            self.engine.transcribe_pcm(b"xx", language="Danish")
        with self.assertRaisesRegex(ValueError, "CPU fallback"):
            engine.ReferenceEngine("unused", "cpu")
        self.assertEqual(self.calls, [])

    def test_source_mismatch_refused_before_numpy_or_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary); (path / "__init__.py").write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "source or asset mismatch"):
                engine.verify_reference_assets(path, None)

    def test_budget_flag_preserves_upstream_text_but_blocks_qualification(self):
        def capped(samples, **options):
            self.model.decoding_observations.append({"temperature": 0.0, "token_count": 224,
                                                    "effective_sample_len": 224, "budget_reached": True})
            return self.original
        self.model.transcribe = capped
        result = self.engine.transcribe_pcm(b"\x00\x00")
        self.assertEqual(result["text"], self.original["text"])
        self.assertEqual(result["decoding_budget_status"], "budget_reached")

    def test_cleanup_does_not_initialize_cuda_after_preload_failure(self):
        self.engine.torch.cuda.is_initialized = lambda: False
        self.engine.close()
        self.assertEqual(self.cleanups, [])
        self.assertIsNone(self.engine.model)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.result = types.SimpleNamespace(tokens=[3, 8, 12])
        owner = self
        class Canonical:
            def decode(self, *args, **kwargs):
                owner.calls.append((args, kwargs))
                return owner.result
        self.canonical = Canonical
        cls = engine.observed_model_class(types.SimpleNamespace(Whisper=Canonical))
        self.model = cls()
        self.model.dims = types.SimpleNamespace(n_text_ctx=448)
        self.model.decoding_observations = []

    def test_same_object_tokens_and_options_delegated_unchanged(self):
        mel = object(); options = types.SimpleNamespace(sample_len=None, temperature=0.0)
        self.assertIs(self.model.decode(mel, options), self.result)
        self.assertIs(self.calls[0][0][0], mel)
        self.assertIs(self.calls[0][0][1], options)
        self.assertEqual(self.calls[0][1], {})
        self.assertEqual(self.result.tokens, [3, 8, 12])
        self.assertEqual(self.model.decoding_observations, [{"temperature": 0.0,
            "token_count": 3, "effective_sample_len": 224, "budget_reached": False}])

    def test_canonical_default_arguments_and_effective_override(self):
        self.model.decode("synthetic mel", sample_len=3, temperature=.2)
        self.assertEqual(self.calls, [(("synthetic mel",), {"sample_len": 3, "temperature": .2})])
        self.assertTrue(self.model.decoding_observations[0]["budget_reached"])

    def test_budget_reached_is_observed_without_truncating_or_substituting(self):
        self.result.tokens = list(range(224))
        actual = self.model.decode(None)
        self.assertIs(actual, self.result)
        self.assertEqual(len(actual.tokens), 224)
        self.assertTrue(self.model.decoding_observations[0]["budget_reached"])

    def test_observation_limit_refuses_before_another_decode(self):
        self.model.decoding_observations = [{}] * engine.MAX_DECODE_OBSERVATIONS
        with self.assertRaisesRegex(RuntimeError, "observation limit"):
            self.model.decode(None)
        self.assertEqual(self.calls, [])


class MandatoryMaskTests(unittest.TestCase):
    def tokenizer(self):
        class Tokenizer:
            sot = 50258
            translate = 50359
            transcribe = 50360
            sot_lm = 50361
            sot_prev = 50362
            no_speech = 50363
            no_timestamps = 50364
            all_language_tokens = tuple(range(50259, 50359))
            def to_language_token(self, code):
                return 50259 + int(code[4:])
            @property
            def non_speech_tokens(self):
                raise AssertionError("Broad nonspeech token collection must not be accessed")
        return Tokenizer()

    def test_all_languages_and_mandatory_controls_without_broad_mask(self):
        tokenizer = self.tokenizer()
        result = engine.resolve_suppression_tokens(tokenizer, [f"lang{i}" for i in range(100)])
        self.assertEqual(result, list(range(50258, 50365)))
        self.assertNotIn(50257, result)  # EOT remains possible after initial blank suppression.
        self.assertNotIn(50365, result)  # First timestamp remains possible.
        self.assertNotIn(-1, result)
        self.assertEqual(len(result), 107)

    def test_missing_or_changed_language_and_control_inventory_refused(self):
        tokenizer = self.tokenizer()
        with self.assertRaises(RuntimeError):
            engine.resolve_suppression_tokens(tokenizer, [f"lang{i}" for i in range(99)])
        tokenizer.no_speech = 99
        with self.assertRaises(RuntimeError):
            engine.resolve_suppression_tokens(tokenizer, [f"lang{i}" for i in range(100)])


if __name__ == "__main__":
    unittest.main()
