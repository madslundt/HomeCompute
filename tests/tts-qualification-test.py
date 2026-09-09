#!/usr/bin/env python3
"""Focused tests for the Danish TTS qualification harness."""

from __future__ import annotations

import csv
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tts_qualification", ROOT / "scripts" / "tts-qualification.py")
assert SPEC and SPEC.loader
TTS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TTS
SPEC.loader.exec_module(TTS)
CONFIG = ROOT / "config" / "tts-qualification.json"


class TtsQualificationTest(unittest.TestCase):
    def test_checked_in_config_has_agreed_candidates_and_gate(self) -> None:
        config = TTS.validate_config(TTS.read_json(CONFIG))
        self.assertEqual(config["gates"]["maximum_warm_first_audio_p95_ms"], 750)
        self.assertEqual(config["gates"]["maximum_warm_rtf_p95"], 0.5)
        self.assertEqual(
            {row["id"]: row["role"] for row in config["candidates"]},
            {
                "plapre-nano-v2-gb10": "primary",
                "piper-talesyntese-home-core": "fallback",
            },
        )
        self.assertGreaterEqual(len(config["phrases"]), 20)

    def test_nearest_rank_p95_does_not_interpolate_away_a_slow_tail(self) -> None:
        self.assertEqual(TTS.percentile_nearest_rank(range(1, 21), 0.95), 19)
        self.assertEqual(TTS.percentile_nearest_rank([100] * 19 + [2100], 0.95), 100)

    def test_prepare_blinds_filenames_and_emits_scorecards(self) -> None:
        config = TTS.validate_config(TTS.read_json(CONFIG))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "audio"
            audio.mkdir(mode=0o700)
            for candidate in config["candidates"]:
                directory = audio / candidate["id"]
                directory.mkdir(mode=0o700)
                for phrase in config["phrases"]:
                    for trial in range(1, config["trials_per_phrase"] + 1):
                        path = directory / f"{phrase['id']}--{trial}.wav"
                        path.write_bytes(b"RIFFtest")
                        path.chmod(0o600)
            output = root / "packet"
            TTS.prepare(SimpleNamespace(config=CONFIG, audio_root=audio, output=output))
            clips = list((output / "clips").iterdir())
            self.assertEqual(len(clips), len(config["candidates"]) * len(config["phrases"]) * 3)
            self.assertTrue(all("plapre" not in path.name and "piper" not in path.name for path in clips))
            self.assertTrue((output / "private-mapping.json").is_file())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in clips))
            self.assertEqual(stat.S_IMODE((output / "private-mapping.json").stat().st_mode), 0o600)

    def test_prepare_rejects_symlinked_audio_and_preexisting_output(self) -> None:
        config = TTS.validate_config(TTS.read_json(CONFIG))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "audio"
            audio.mkdir(mode=0o700)
            for candidate in config["candidates"]:
                directory = audio / candidate["id"]
                directory.mkdir(mode=0o700)
                for phrase in config["phrases"]:
                    for trial in range(1, config["trials_per_phrase"] + 1):
                        path = directory / f"{phrase['id']}--{trial}.wav"
                        path.write_bytes(b"RIFFtest")
                        path.chmod(0o600)
            target = audio / config["candidates"][0]["id"] / "confirmation-light--1.wav"
            real = root / "real.wav"
            real.write_bytes(b"RIFFtest")
            real.chmod(0o600)
            target.unlink()
            target.symlink_to(real)
            with self.assertRaisesRegex(TTS.QualificationError, "symlink"):
                TTS.prepare(SimpleNamespace(config=CONFIG, audio_root=audio, output=root / "packet"))

            preexisting = root / "preexisting"
            preexisting.mkdir(mode=0o700)
            with self.assertRaisesRegex(TTS.QualificationError, "pre-existing directory"):
                TTS.create_private_directory(preexisting)

            existing_result = root / "result.json"
            existing_result.write_text("do-not-overwrite")
            existing_result.chmod(0o600)
            with self.assertRaisesRegex(TTS.QualificationError, "pre-existing file"):
                TTS.write_json_exclusive(existing_result, {"replacement": True})
            self.assertEqual(existing_result.read_text(), "do-not-overwrite")
            symlinked_result = root / "result-link.json"
            symlinked_result.symlink_to(existing_result)
            with self.assertRaisesRegex(TTS.QualificationError, "pre-existing file"):
                TTS.write_json_exclusive(symlinked_result, {"replacement": True})
            self.assertEqual(existing_result.read_text(), "do-not-overwrite")

    def test_exact_measurement_matrix_rejects_missing_and_duplicate_rows(self) -> None:
        config = TTS.validate_config(TTS.read_json(CONFIG))
        rows = [
            {
                "candidate_id": candidate["id"],
                "phrase_id": phrase["id"],
                "trial": trial,
                "warm": True,
                "status": "completed",
                "first_audio_ms": 500,
                "rtf": 0.25,
            }
            for candidate in config["candidates"]
            for phrase in config["phrases"]
            for trial in range(1, config["trials_per_phrase"] + 1)
        ]
        TTS.require_exact_matrix(rows, config, "measurements")
        with self.assertRaisesRegex(TTS.QualificationError, "missing=1"):
            TTS.require_exact_matrix(rows[:-1], config, "measurements")
        with self.assertRaisesRegex(TTS.QualificationError, "duplicates=1"):
            TTS.require_exact_matrix(rows + [rows[0]], config, "measurements")

    def test_evaluate_prefers_eligible_primary_and_uses_fallback_only_when_needed(self) -> None:
        config = TTS.validate_config(TTS.read_json(CONFIG))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mapping_rows = []
            measurement_rows = []
            rating_rows = []
            preference_rows = []
            for candidate in config["candidates"]:
                candidate_id = candidate["id"]
                for index, phrase in enumerate(config["phrases"]):
                    latency = 500
                    first_clip_id = ""
                    for trial in range(1, config["trials_per_phrase"] + 1):
                        measurement_rows.append({"candidate_id": candidate_id, "phrase_id": phrase["id"], "trial": trial, "warm": True, "status": "completed", "first_audio_ms": latency, "rtf": 0.25})
                        clip_id = f"{candidate_id}-{index}-{trial}"
                        if trial == 1:
                            first_clip_id = clip_id
                        mapping_rows.append({"clip_id": clip_id, "candidate_id": candidate_id, "phrase_id": phrase["id"], "trial": trial})
                        for reviewer in range(1, 4):
                            rating_rows.append({"reviewer_id": f"reviewer-{reviewer}", "clip_id": clip_id, "pronunciation_pass": "pass", "naturalness_1_to_5": "4"})
                    if candidate_id == "plapre-nano-v2-gb10":
                        for reviewer in range(1, 4):
                            preference_rows.append({"reviewer_id": f"reviewer-{reviewer}", "phrase_id": phrase["id"], "preferred_clip_id": first_clip_id})
            mapping = root / "mapping.json"
            mapping.write_text(json.dumps({"clips": mapping_rows}))
            mapping.chmod(0o600)
            measurements = root / "measurements.jsonl"
            measurements.write_text("".join(json.dumps(row) + "\n" for row in measurement_rows))
            measurements.chmod(0o600)
            ratings = root / "ratings.csv"
            with ratings.open("w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=rating_rows[0])
                writer.writeheader(); writer.writerows(rating_rows)
            ratings.chmod(0o600)
            preferences = root / "preferences.csv"
            with preferences.open("w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=preference_rows[0])
                writer.writeheader(); writer.writerows(preference_rows)
            preferences.chmod(0o600)
            result_path = root / "result.json"
            TTS.evaluate(SimpleNamespace(config=CONFIG, mapping=mapping, measurements=measurements, ratings=ratings, preferences=preferences, output=result_path))
            result = json.loads(result_path.read_text())
            self.assertEqual(result["outcome"], "primary_qualified")
            self.assertEqual(result["winner"], "plapre-nano-v2-gb10")
            for row in measurement_rows:
                if row["candidate_id"] == "plapre-nano-v2-gb10":
                    row["first_audio_ms"] = 2500
            measurements.write_text("".join(json.dumps(row) + "\n" for row in measurement_rows))
            fallback_result_path = root / "fallback-result.json"
            TTS.evaluate(SimpleNamespace(config=CONFIG, mapping=mapping, measurements=measurements, ratings=ratings, preferences=preferences, output=fallback_result_path))
            fallback_result = json.loads(fallback_result_path.read_text())
            self.assertEqual(fallback_result["outcome"], "fallback_only")
            self.assertEqual(fallback_result["winner"], "piper-talesyntese-home-core")


if __name__ == "__main__":
    unittest.main()
