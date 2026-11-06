"""Synthetic invariants for acoustic proposals and native language gating."""
import copy
import importlib.util
import io
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "language_boundaries", ROOT / "deploy/whisper-cpp-stt/language_boundaries.py")
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


def wav(samples, *, channels=1, width=2, rate=16000):
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setnchannels(channels); target.setsampwidth(width); target.setframerate(rate)
        target.writeframes(samples)
    return out.getvalue()


def pcm(audio):
    with wave.open(io.BytesIO(audio), "rb") as source:
        return source.readframes(source.getnframes())


def source(frames=160000):
    return wav(struct.pack(f"<{frames}h", *(index % 2000 - 1000 for index in range(frames))))


def probabilities(frames, *runs):
    result = [0.8] * ((frames + 511) // 512)
    for begin, end in runs:
        result[begin:end] = [0.1] * (end - begin)
    return result


def probe(selected="da", probability=0.9, detected=None):
    return {"language_selection": {"mode": "candidate_auto", "candidates": ["da", "en"],
            "selected": selected, "selected_probability": probability,
            "detected": detected or selected, "detected_probability": 0.95}}


class PartitionTests(unittest.TestCase):
    def test_internal_pause_uses_exact_midpoint_and_conserves_original_pcm(self):
        original = source()
        observe = Mock(return_value=probabilities(160000, (100, 125)))
        children = B.partitions(original, observe=observe)
        cut = (100 + 125) * 256
        self.assertEqual([(start, seconds) for start, seconds, _ in children],
                         [(0, cut / 16000), (cut / 16000, (160000 - cut) / 16000)])
        self.assertEqual(b"".join(pcm(data) for _, _, data in children), pcm(original))
        self.assertEqual(len(observe.call_args.args[0]), 320000)
        self.assertNotEqual(observe.call_args.args[0], pcm(original))

    def test_greedy_cuts_retain_all_samples_and_minimum_children(self):
        frames = 480000
        original = source(frames)
        children = B.partitions(original, observe=lambda _: probabilities(
            frames, (100, 125), (150, 175), (300, 325), (850, 875)))
        counts = [len(pcm(data)) // 2 for _, _, data in children]
        self.assertEqual(counts, [57600, 102400, 320000])
        self.assertTrue(all(count >= 48000 for count in counts))
        self.assertEqual(b"".join(pcm(data) for _, _, data in children), pcm(original))
        self.assertEqual(sum(counts), frames)

    def test_short_edge_and_entire_low_probability_runs_preserve_exact_wav(self):
        frames = 160000
        original = source(frames)
        cases = [(), ((100, 124),), ((0, 125),), ((100, 313),), ((0, 313),),
                 ((1, 26),), ((285, 310),)]
        for runs in cases:
            with self.subTest(runs=runs):
                self.assertEqual(B.partitions(original, observe=lambda _: probabilities(frames, *runs)),
                                 [(0.0, 10.0, original)])

    def test_half_probability_is_voiced_on_both_pause_edges(self):
        original = source()
        values = [0.5] * 313
        values[100:125] = [0.499] * 25
        self.assertEqual(len(B.partitions(original, observe=lambda _: values)), 2)
        self.assertEqual(B.partitions(original, observe=lambda _: [0.5] * 313),
                         [(0.0, 10.0, original)])

    def test_partial_final_frame_cannot_close_a_pause(self):
        frames = 160001
        values = probabilities(frames, (280, 312))
        values[312] = 0.9  # Frame312 is incomplete.
        original = source(frames)
        self.assertEqual(B.partitions(original, observe=lambda _: values), [(0.0, frames / 16000, original)])

    def test_invalid_probabilities_are_rejected_even_after_eligible_cut(self):
        frames = 160001
        good = probabilities(frames, (100, 125))
        for value in [math.nan, math.inf, -0.1, 1.01, True, "0.5", None]:
            values = good.copy(); values[-1] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                B.partitions(source(frames), observe=lambda _: values)

    def test_wrong_frame_counts_and_detector_failures_raise(self):
        for values in [[], [0.9], None, "bad", iter([0.9])]:
            with self.subTest(values=type(values).__name__), self.assertRaises(ValueError):
                B.partitions(source(), observe=lambda _: values)
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            B.partitions(source(), observe=Mock(side_effect=RuntimeError("synthetic")))

    def test_strict_wav_geometry_and_truncation_are_validated_before_observation(self):
        invalid = [b"bad", bytearray(source()), wav(b""), wav(b"\0" * 32000, channels=2),
                   wav(b"\0" * 32000, width=1), wav(b"\0" * 32000, rate=8000),
                   wav(b"\0" * (480001 * 2)), wav(b"\0\0\1"), source()[:-2]]
        for audio in invalid:
            observe = Mock()
            with self.subTest(kind=type(audio).__name__, size=len(audio)), self.assertRaises(ValueError):
                B.partitions(audio, observe=observe)
            observe.assert_not_called()


class NormalizationTests(unittest.TestCase):
    def observed(self, values):
        raw = struct.pack(f"<{len(values)}h", *values)
        observe = Mock(return_value=[0.1] * ((len(values) + 511) // 512))
        original = wav(raw)
        self.assertEqual(B.partitions(original, observe=observe)[0][2], original)
        return struct.unpack(f"<{len(values)}h", observe.call_args.args[0])

    def test_silence_and_loud_signed_little_endian_pcm_are_not_changed(self):
        self.assertEqual(self.observed([0] * 16000), (0,) * 16000)
        values = [-32768, 32767, -8192, 8192] * 4000
        self.assertEqual(self.observed(values), tuple(values))

    def test_one_second_blocks_are_independent_and_final_partial_block_is_normalized(self):
        values = [100] * 16000 + [5000] * 16000 + [-1]
        result = self.observed(values)
        self.assertEqual(result[:16000], (round(B.TARGET_RMS),) * 16000)
        self.assertEqual(result[16000:32000], (5000,) * 16000)
        self.assertEqual(result[-1], -round(10 ** (30 / 20)))

    def test_gain_is_capped_and_outlier_samples_saturate_without_wrapping(self):
        result = self.observed([1] * 16000)
        self.assertEqual(result, (32,) * 16000)
        result = self.observed([32767, -32768] + [1] * 15998)
        self.assertEqual(result[:2], (32767, -32768))
        self.assertGreater(result[-1], 1)
        self.assertLessEqual(max(result), 32767)
        self.assertGreaterEqual(min(result), -32768)


class LanguageDecisionTests(unittest.TestCase):
    def test_two_confident_different_languages_are_required(self):
        for probes in [[], [probe()], [probe(), probe()], [probe(), probe("en", 0.499)],
                       [probe("en", 0.4), probe("da", 0.4)]]:
            self.assertFalse(B.has_confident_language_change(probes, ["da", "en"]))
        self.assertTrue(B.has_confident_language_change([probe("da", 0.5), probe("en", 0.5)], ["da", "en"]))
        self.assertTrue(B.has_confident_language_change([probe(), probe("en", detected="cy")], ["da", "en"]))

    def test_invalid_candidates_and_nonlist_probes_raise(self):
        for candidates in [[], ["en", "da"], ["da", "da"], ["DA", "en"], ["d", "en"], ("da", "en")]:
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                B.has_confident_language_change([], candidates)
        with self.assertRaises(ValueError):
            B.has_confident_language_change(iter([]), ["da", "en"])

    def test_malformed_language_observations_cannot_be_hidden_after_a_change(self):
        bad = [None, {}, {"language_selection": None}]
        mutations = {"mode": "explicit", "candidates": ["en", "da"], "selected": "cy", "detected": "EN",
                     "selected_probability": math.nan, "detected_probability": True}
        for key, value in mutations.items():
            row = probe(); row["language_selection"][key] = value; bad.append(row)
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                B.has_confident_language_change([probe(), probe("en"), row], ["da", "en"])

    def test_all_probability_fields_are_bounded_finite_numbers(self):
        for key in ("selected_probability", "detected_probability"):
            for value in [-0.1, 1.1, math.nan, math.inf, True, None, "0.9"]:
                row = copy.deepcopy(probe()); row["language_selection"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    B.has_confident_language_change([row], ["da", "en"])


if __name__ == "__main__":
    unittest.main()
