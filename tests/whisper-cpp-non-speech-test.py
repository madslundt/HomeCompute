"""Synthetic fixed-threshold truth table; no model or private fixtures."""
import copy
import importlib.util
import io
import math
from pathlib import Path
import unittest
from unittest.mock import Mock
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("non_speech", ROOT / "deploy/whisper-cpp-stt/non_speech.py")
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)


def audio(frames=48000, *, value=1, rate=16000, width=2, channels=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as source:
        source.setnchannels(channels); source.setsampwidth(width); source.setframerate(rate)
        source.writeframes(value.to_bytes(width, "little", signed=True) * frames * channels)
    return stream.getvalue()


def native(*values, text=" Synthetic output"):
    return {"text": text, "segments": [{"text": " Synthetic segment", "no_speech_prob": value} for value in values]}


def observation(frames=48000, value=0.1):
    return [value] * ((frames + 511) // 512)


class NonSpeechTests(unittest.TestCase):
    def test_exact_zero_is_independent_of_models_and_optional_native_confidence(self):
        for frames in (1, 47999, 48000, 480000):
            for result in (native(0), native(), {"text": " Arbitrary output"}):
                observe = Mock(side_effect=AssertionError("No model needed"))
                audit = N.decision(audio(frames, value=0), result, normalized_observation=observe)
                self.assertEqual(audit["method"], "exact_zero_pcm")
                self.assertEqual(audit["frames"], frames)
                self.assertFalse(audit["coverage_claim"])
                self.assertIsNone(audit["native_min_no_speech_probability"])
                self.assertEqual(audit["normalized_vad_probability_count"], 0)
                self.assertNotIn("text", audit)
                observe.assert_not_called()

    def test_already_empty_output_needs_no_new_decision(self):
        for value in (0, 1):
            for text in ("", " \n\t"):
                observe = Mock()
                self.assertIsNone(N.decision(audio(value=value), native(0.9, text=text), normalized_observation=observe))
                observe.assert_not_called()

    def test_fixed_conjunction_truth_table_and_exact_threshold_edges(self):
        for native_confidence, vad_max, expected in [(0.6, 0.499, True), (1, 0, True),
                                                    (0.599, 0.1, False), (0.9, 0.5, False),
                                                    (0.1, 0.9, False), (0.9, 1, False)]:
            observe = Mock(return_value=observation(value=vad_max))
            result = N.decision(audio(), native(native_confidence), normalized_observation=observe)
            with self.subTest(native=native_confidence, vad=vad_max):
                self.assertEqual(result is not None, expected)
                if expected:
                    self.assertEqual(result["native_min_no_speech_probability"], native_confidence)
                    self.assertEqual(result["normalized_vad_max_probability"], vad_max)
                    self.assertEqual(result["normalized_vad_probability_count"], 94)
                    self.assertFalse(result["coverage_claim"])
                if native_confidence < 0.6:
                    observe.assert_not_called()

    def test_quiet_nonzero_speech_positive_native_head_is_preserved(self):
        for sample in (-1, 1):
            observe = Mock(return_value=observation(value=0))
            self.assertIsNone(N.decision(audio(value=sample), native(0.1), normalized_observation=observe))
            observe.assert_not_called()

    def test_every_nonempty_segment_must_have_valid_negative_confidence(self):
        for values in [(0.9, 0.59), (0.9, None), (0.9, True), (0.9, math.nan),
                       (0.9, math.inf), (0.9, -0.1), (0.9, 1.1), (0.9, "0.9")]:
            observe = Mock()
            self.assertIsNone(N.decision(audio(), native(*values), normalized_observation=observe))
            observe.assert_not_called()
        observe = Mock(return_value=observation())
        result = N.decision(audio(), native(0.9, 0.7), normalized_observation=observe)
        self.assertEqual(result["native_min_no_speech_probability"], 0.7)
        self.assertEqual(result["native_segment_count"], 2)

    def test_empty_segments_do_not_supply_vacuous_native_evidence(self):
        for segments in (None, [], [{}], [None], [{"text": ""}], [{"text": " \n"}]):
            observe = Mock()
            self.assertIsNone(N.decision(audio(), {"text": " Synthetic output", "segments": segments}, normalized_observation=observe))
            observe.assert_not_called()
        result = native(0.8)
        result["segments"].append({"text": ""})
        self.assertIsNotNone(N.decision(audio(), result, normalized_observation=lambda _: observation()))

    def test_short_nonzero_audio_is_preserved_and_original_wav_is_observed(self):
        observe = Mock()
        self.assertIsNone(N.decision(audio(47999), native(1), normalized_observation=observe))
        observe.assert_not_called()
        original = audio(48001)
        observe.return_value = observation(48001)
        self.assertIsNotNone(N.decision(original, native(0.9), normalized_observation=observe))
        observe.assert_called_once_with(original)

    def test_malformed_observation_is_never_a_silent_blank_or_fallback(self):
        for values in (None, [], [0.1], "bad", iter(observation())):
            with self.subTest(kind=type(values).__name__), self.assertRaises(ValueError):
                N.decision(audio(), native(0.9), normalized_observation=lambda _: values)
        for bad in (math.nan, math.inf, -0.1, 1.1, True, None, "0.1"):
            values = observation(); values[-1] = bad
            with self.subTest(value=bad), self.assertRaises(ValueError):
                N.decision(audio(), native(0.9), normalized_observation=lambda _: values)
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            N.decision(audio(), native(0.9), normalized_observation=Mock(side_effect=RuntimeError("synthetic")))

    def test_strict_wav_bound_precedes_every_policy_path(self):
        for invalid in (b"bad", bytearray(audio()), audio(0), audio(480001), audio(rate=8000),
                        audio(width=1), audio(channels=2), audio()[:-2]):
            observe = Mock()
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                N.decision(invalid, native(0.9), normalized_observation=observe)
            observe.assert_not_called()

    def test_decision_does_not_inspect_words_or_mutate_native_output(self):
        original = native(0.8)
        saved = copy.deepcopy(original)
        first = N.decision(audio(), original, normalized_observation=lambda _: observation())
        self.assertEqual(original, saved)
        other = native(0.8, text=" Completely different synthetic content")
        other["segments"][0]["text"] = " Another synthetic segment"
        self.assertEqual(N.decision(audio(), other, normalized_observation=lambda _: observation()), first)


if __name__ == "__main__":
    unittest.main()
