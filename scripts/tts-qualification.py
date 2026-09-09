#!/usr/bin/env python3
"""Measure and evaluate a blinded Danish TTS qualification run."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import ssl
import stat
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


class QualificationError(ValueError):
    """A qualification input is incomplete or invalid."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"cannot read JSON: {path}") from error


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise QualificationError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"cannot read JSONL: {path}") from error
    return rows


def reject_symlink(path: Path, expected: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise QualificationError(f"cannot inspect {expected}: {path}") from error
    if stat.S_ISLNK(mode):
        raise QualificationError(f"{expected} must not be a symlink: {path}")
    if expected == "directory" and not stat.S_ISDIR(mode):
        raise QualificationError(f"expected a directory: {path}")
    if expected == "file" and not stat.S_ISREG(mode):
        raise QualificationError(f"expected a regular file: {path}")


def require_private_directory(path: Path) -> None:
    reject_symlink(path, "directory")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise QualificationError(f"private directory permissions must exclude group/other access: {path}")


def require_private_file(path: Path) -> None:
    reject_symlink(path, "file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise QualificationError(f"private file permissions must exclude group/other access: {path}")


def read_private_bytes(path: Path) -> bytes:
    require_private_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise QualificationError(f"expected a regular file: {path}")
        with os.fdopen(descriptor, "rb") as source:
            return source.read()
    except OSError as error:
        raise QualificationError(f"cannot read private file: {path}") from error


def create_private_directory(path: Path) -> None:
    require_private_directory(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as error:
        raise QualificationError(f"refusing pre-existing directory: {path}") from error
    except OSError as error:
        raise QualificationError(f"cannot create private directory: {path}") from error
    require_private_directory(path)


def open_private_text_exclusive(path: Path) -> Any:
    require_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise QualificationError(f"refusing pre-existing file: {path}") from error
    except OSError as error:
        raise QualificationError(f"cannot create private file: {path}") from error
    return os.fdopen(descriptor, "w", encoding="utf-8", newline="")


def write_private_bytes_exclusive(destination: Path, value: bytes) -> None:
    require_private_directory(destination.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(value)
    except FileExistsError as error:
        raise QualificationError(f"refusing pre-existing file: {destination}") from error
    except OSError as error:
        raise QualificationError(f"cannot write private file: {destination}") from error


def write_json_exclusive(path: Path, value: Any) -> None:
    with open_private_text_exclusive(path) as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise QualificationError("configuration schema_version must be 1")
    candidates = config.get("candidates")
    phrases = config.get("phrases")
    gates = config.get("gates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise QualificationError("at least two candidates are required")
    if not isinstance(phrases, list) or len(phrases) < 20:
        raise QualificationError("at least 20 phrases are required for a p95 gate")
    if not isinstance(gates, dict):
        raise QualificationError("gates must be an object")
    candidate_ids = [item.get("id") for item in candidates if isinstance(item, dict)]
    phrase_ids = [item.get("id") for item in phrases if isinstance(item, dict)]
    if (
        len(candidate_ids) != len(candidates)
        or any(not isinstance(value, str) or not value for value in candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise QualificationError("candidate ids must be unique strings")
    if (
        len(phrase_ids) != len(phrases)
        or any(not isinstance(value, str) or not value for value in phrase_ids)
        or len(set(phrase_ids)) != len(phrase_ids)
    ):
        raise QualificationError("phrase ids must be unique strings")
    if any(not isinstance(item.get("text"), str) or not item["text"].strip() for item in phrases):
        raise QualificationError("every phrase must contain text")
    if any(item.get("role") not in {"primary", "fallback"} for item in candidates):
        raise QualificationError("candidate role must be primary or fallback")
    if not any(item["role"] == "primary" for item in candidates):
        raise QualificationError("at least one primary candidate is required")
    required_gates = {
        "minimum_warm_samples",
        "maximum_warm_first_audio_p95_ms",
        "maximum_warm_rtf_p95",
        "maximum_failure_rate_percent",
        "minimum_reviewers",
        "minimum_pronunciation_pass_rate_percent",
        "minimum_naturalness_mean",
    }
    if set(gates) != required_gates or any(type(value) not in {int, float} for value in gates.values()):
        raise QualificationError("gates do not match the qualification schema")
    for field in ("warmup_requests", "trials_per_phrase"):
        if type(config.get(field)) is not int or config[field] < 1:
            raise QualificationError(f"{field} must be a positive integer")
    if gates["maximum_warm_first_audio_p95_ms"] != 750:
        raise QualificationError("the agreed warm first-audio p95 gate is 750 ms")
    if gates["maximum_warm_rtf_p95"] != 0.5:
        raise QualificationError("the agreed warm RTF p95 gate is 0.5")
    recovery = config.get("recovery_qualification")
    if not isinstance(recovery, dict) or recovery != {
        "asr_verifier": "stt_danish",
        "maximum_resample_attempts": 1,
        "fallback_after_second_mismatch": "piper-talesyntese-home-core",
        "required_scenarios": [
            "asr-match-accept-first-synthesis",
            "asr-mismatch-resample-once-and-pass",
            "asr-second-mismatch-fallback-to-piper",
            "plapre-timeout-fallback-to-piper",
        ],
    }:
        raise QualificationError("ASR verification, one resample, and Piper recovery scenarios are mandatory")
    return config


def wav_duration_ms(audio: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getframerate() <= 0:
                raise QualificationError("WAV sample rate must be positive")
            return 1000 * wav.getnframes() / wav.getframerate()
    except (EOFError, wave.Error) as error:
        raise QualificationError("endpoint returned an invalid WAV") from error


def percentile_nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise QualificationError("cannot calculate a percentile without samples")
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def request_audio(endpoint: str, api_key: str, model: str, voice: str, text: str) -> tuple[bytes, float, float]:
    body = json.dumps(
        {"model": model, "voice": voice, "input": text, "response_format": "wav"}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120, context=ssl.create_default_context()) as response:
        first = response.read(1)
        first_audio_ms = (time.perf_counter() - started) * 1000
        audio = first + response.read()
    total_ms = (time.perf_counter() - started) * 1000
    if not audio.startswith(b"RIFF"):
        raise QualificationError("endpoint did not return a WAV stream")
    return audio, first_audio_ms, total_ms


def measure(args: argparse.Namespace) -> None:
    config = validate_config(read_json(args.config))
    candidates = {item["id"]: item for item in config["candidates"]}
    if args.candidate not in candidates:
        raise QualificationError(f"unknown candidate: {args.candidate}")
    key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not key or any(character.isspace() for character in key):
        raise QualificationError("API key file must contain one token")
    if args.audio_root.exists() or args.audio_root.is_symlink():
        require_private_directory(args.audio_root)
    else:
        create_private_directory(args.audio_root)
    audio_dir = args.audio_root / args.candidate
    create_private_directory(audio_dir)
    phrases = config["phrases"]
    with open_private_text_exclusive(args.output) as output:
        for index in range(config["warmup_requests"]):
            request_audio(args.endpoint, key, args.model_alias, args.voice_alias, phrases[index % len(phrases)]["text"])
        for trial in range(1, config["trials_per_phrase"] + 1):
            for phrase in phrases:
                row = {"candidate_id": args.candidate, "phrase_id": phrase["id"], "trial": trial, "warm": True}
                try:
                    audio, first_audio_ms, total_ms = request_audio(
                        args.endpoint, key, args.model_alias, args.voice_alias, phrase["text"]
                    )
                    duration_ms = wav_duration_ms(audio)
                    row.update(
                        status="completed",
                        first_audio_ms=round(first_audio_ms, 3),
                        total_ms=round(total_ms, 3),
                        audio_duration_ms=round(duration_ms, 3),
                        rtf=round(total_ms / duration_ms, 6),
                    )
                except (OSError, urllib.error.URLError, QualificationError) as error:
                    row.update(status="failed", error_type=type(error).__name__)
                else:
                    write_private_bytes_exclusive(
                        audio_dir / f"{phrase['id']}--{trial}.wav", audio
                    )
                output.write(json.dumps(row, sort_keys=True) + "\n")
                output.flush()


def prepare(args: argparse.Namespace) -> None:
    config = validate_config(read_json(args.config))
    require_private_directory(args.audio_root)
    create_private_directory(args.output)
    clips_dir = args.output / "clips"
    create_private_directory(clips_dir)
    mapping: list[dict[str, Any]] = []
    groups: dict[str, list[str]] = defaultdict(list)
    for candidate in config["candidates"]:
        for phrase in config["phrases"]:
            for trial in range(1, config["trials_per_phrase"] + 1):
                source = args.audio_root / candidate["id"] / f"{phrase['id']}--{trial}.wav"
                require_private_directory(source.parent)
                audio = read_private_bytes(source)
                if not audio.startswith(b"RIFF"):
                    raise QualificationError(f"missing or invalid WAV: {source}")
                clip_id = f"clip-{uuid.uuid4().hex[:12]}"
                write_private_bytes_exclusive(clips_dir / f"{clip_id}.wav", audio)
                mapping.append({"clip_id": clip_id, "candidate_id": candidate["id"], "phrase_id": phrase["id"], "trial": trial})
                if trial == 1:
                    groups[phrase["id"]].append(clip_id)
    write_json_exclusive(args.output / "private-mapping.json", {"schema_version": 1, "clips": mapping})
    with open_private_text_exclusive(args.output / "ratings.csv") as output:
        writer = csv.writer(output)
        writer.writerow(["reviewer_id", "clip_id", "pronunciation_pass", "naturalness_1_to_5"])
        for row in sorted(mapping, key=lambda item: item["clip_id"]):
            writer.writerow(["", row["clip_id"], "", ""])
    with open_private_text_exclusive(args.output / "preferences.csv") as output:
        writer = csv.writer(output)
        writer.writerow(["reviewer_id", "phrase_id", "eligible_clip_ids", "preferred_clip_id"])
        for phrase_id, clip_ids in sorted(groups.items()):
            writer.writerow(["", phrase_id, ";".join(sorted(clip_ids)), ""])


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))
    except OSError as error:
        raise QualificationError(f"cannot read CSV: {path}") from error


def require_exact_matrix(
    rows: list[dict[str, Any]], config: dict[str, Any], document_name: str
) -> None:
    expected = {
        (candidate["id"], phrase["id"], trial)
        for candidate in config["candidates"]
        for phrase in config["phrases"]
        for trial in range(1, config["trials_per_phrase"] + 1)
    }
    keys: list[tuple[Any, Any, Any]] = []
    for row in rows:
        key = (row.get("candidate_id"), row.get("phrase_id"), row.get("trial"))
        keys.append(key)
        if document_name == "measurements":
            if row.get("warm") is not True or row.get("status") not in {"completed", "failed"}:
                raise QualificationError("every measurement must be warm and have completed/failed status")
            if row.get("status") == "completed":
                for field in ("first_audio_ms", "rtf"):
                    value = row.get(field)
                    if type(value) not in {int, float} or value < 0:
                        raise QualificationError(f"completed measurements require non-negative {field}")
    counts = Counter(keys)
    duplicates = [key for key, count in counts.items() if count != 1]
    actual = set(keys)
    if duplicates or actual != expected:
        missing = len(expected - actual)
        unexpected = len(actual - expected)
        raise QualificationError(
            f"{document_name} must contain the exact unique candidate x phrase x trial matrix "
            f"(missing={missing}, unexpected={unexpected}, duplicates={len(duplicates)})"
        )


def evaluate(args: argparse.Namespace) -> None:
    config = validate_config(read_json(args.config))
    for private_input in (args.mapping, args.measurements, args.ratings, args.preferences):
        require_private_file(private_input)
    mapping_doc = read_json(args.mapping)
    mapping_rows = mapping_doc.get("clips", [])
    if not isinstance(mapping_rows, list) or any(not isinstance(row, dict) for row in mapping_rows):
        raise QualificationError("mapping clips must be an array of objects")
    require_exact_matrix(mapping_rows, config, "mapping")
    mapping = {row.get("clip_id"): row for row in mapping_rows}
    if None in mapping or len(mapping) != len(mapping_rows):
        raise QualificationError("mapping clip ids must be present and unique")
    measurements = read_jsonl(args.measurements)
    require_exact_matrix(measurements, config, "measurements")
    ratings = read_csv(args.ratings)
    preferences = read_csv(args.preferences)
    rating_keys = [(row.get("reviewer_id"), row.get("clip_id")) for row in ratings if row.get("reviewer_id")]
    if len(rating_keys) != len(set(rating_keys)):
        raise QualificationError("ratings must contain at most one row per reviewer and clip")
    preference_keys = [(row.get("reviewer_id"), row.get("phrase_id")) for row in preferences if row.get("reviewer_id")]
    if len(preference_keys) != len(set(preference_keys)):
        raise QualificationError("preferences must contain at most one row per reviewer and phrase")
    candidate_config = {row["id"]: row for row in config["candidates"]}
    preference_reviewers_by_phrase: dict[str, set[str]] = defaultdict(set)
    for row in preferences:
        if not row.get("reviewer_id"):
            continue
        preferred = mapping.get(row.get("preferred_clip_id", ""))
        if preferred is None or preferred.get("phrase_id") != row.get("phrase_id") or preferred.get("trial") != 1:
            raise QualificationError("each preference must name a trial-one clip from the same phrase")
        preference_reviewers_by_phrase[row["phrase_id"]].add(row["reviewer_id"])
    preference_complete = all(
        len(preference_reviewers_by_phrase[phrase["id"]]) >= config["gates"]["minimum_reviewers"]
        for phrase in config["phrases"]
    )
    evidence: dict[str, dict[str, Any]] = {}
    for candidate_id, candidate in candidate_config.items():
        samples = [row for row in measurements if row.get("candidate_id") == candidate_id and row.get("warm") is True]
        completed = [row for row in samples if row.get("status") == "completed"]
        latencies = [float(row["first_audio_ms"]) for row in completed]
        rtfs = [float(row["rtf"]) for row in completed]
        candidate_clips = {clip_id for clip_id, row in mapping.items() if row["candidate_id"] == candidate_id}
        candidate_ratings = [row for row in ratings if row.get("clip_id") in candidate_clips and row.get("reviewer_id")]
        valid_pass_values = {"0", "1", "false", "true", "no", "yes", "fail", "pass"}
        if any(row["pronunciation_pass"].strip().lower() not in valid_pass_values for row in candidate_ratings):
            raise QualificationError("pronunciation_pass must be pass/fail or a boolean value")
        pronunciation = [row["pronunciation_pass"].strip().lower() in {"1", "true", "yes", "pass"} for row in candidate_ratings]
        try:
            naturalness = [float(row["naturalness_1_to_5"]) for row in candidate_ratings]
        except ValueError as error:
            raise QualificationError("naturalness scores must be numeric") from error
        if any(not 1 <= score <= 5 for score in naturalness):
            raise QualificationError("naturalness scores must be between 1 and 5")
        reviewers_by_clip: dict[str, set[str]] = defaultdict(set)
        for row in candidate_ratings:
            reviewers_by_clip[row["clip_id"]].add(row["reviewer_id"])
        preference_votes = sum(
            1 for row in preferences
            if row.get("reviewer_id") and row.get("preferred_clip_id") in candidate_clips
        )
        p95 = percentile_nearest_rank(latencies, 0.95) if latencies else None
        rtf_p95 = percentile_nearest_rank(rtfs, 0.95) if rtfs else None
        failure_rate = 100 * (len(samples) - len(completed)) / len(samples) if samples else 100.0
        pronunciation_rate = 100 * sum(pronunciation) / len(pronunciation) if pronunciation else 0.0
        naturalness_mean = statistics.fmean(naturalness) if naturalness else 0.0
        reasons: list[str] = []
        gates = config["gates"]
        expected_clip_count = len(config["phrases"]) * config["trials_per_phrase"]
        listening_complete = (
            len(candidate_clips) == expected_clip_count
            and all(len(reviewers_by_clip[clip_id]) >= gates["minimum_reviewers"] for clip_id in candidate_clips)
        )
        if len(completed) < gates["minimum_warm_samples"]:
            reasons.append("insufficient_warm_samples")
        if p95 is None or p95 > gates["maximum_warm_first_audio_p95_ms"]:
            reasons.append("warm_first_audio_p95_exceeded")
        if rtf_p95 is None or rtf_p95 > gates["maximum_warm_rtf_p95"]:
            reasons.append("warm_rtf_p95_exceeded")
        if failure_rate > gates["maximum_failure_rate_percent"]:
            reasons.append("failure_rate_exceeded")
        if not listening_complete:
            reasons.append("insufficient_listening_coverage")
        if not preference_complete:
            reasons.append("insufficient_preference_coverage")
        if pronunciation_rate < gates["minimum_pronunciation_pass_rate_percent"]:
            reasons.append("pronunciation_pass_rate_below_gate")
        if naturalness_mean < gates["minimum_naturalness_mean"]:
            reasons.append("naturalness_below_gate")
        evidence[candidate_id] = {
            "candidate_id": candidate_id,
            "role": candidate["role"],
            "eligible": not reasons,
            "ineligibility_reasons": reasons,
            "warm_samples": len(completed),
            "warm_first_audio_p95_ms": p95,
            "warm_rtf_p95": rtf_p95,
            "failure_rate_percent": round(failure_rate, 3),
            "pronunciation_pass_rate_percent": round(pronunciation_rate, 3),
            "naturalness_mean": round(naturalness_mean, 3),
            "blinded_preference_votes": preference_votes,
            "listening_coverage_complete": listening_complete,
            "preference_coverage_complete": preference_complete,
        }
    rank_key = lambda row: (-row["blinded_preference_votes"], -row["naturalness_mean"], row["warm_first_audio_p95_ms"], row["candidate_id"])
    primary = sorted((row for row in evidence.values() if row["role"] == "primary" and row["eligible"]), key=rank_key)
    fallback = sorted((row for row in evidence.values() if row["role"] == "fallback" and row["eligible"]), key=rank_key)
    winner = primary[0] if primary else (fallback[0] if fallback else None)
    result = {
        "schema_version": 1,
        "qualification_id": config["qualification_id"],
        "outcome": "primary_qualified" if primary else ("fallback_only" if fallback else "no_candidate_qualified"),
        "winner": winner["candidate_id"] if winner else None,
        "candidates": sorted(evidence.values(), key=lambda row: row["candidate_id"]),
    }
    if args.output:
        write_json_exclusive(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    measure_parser = subparsers.add_parser("measure", help="measure one already-running candidate")
    measure_parser.add_argument("--config", type=Path, required=True)
    measure_parser.add_argument("--candidate", required=True)
    measure_parser.add_argument("--endpoint", required=True)
    measure_parser.add_argument("--api-key-file", type=Path, required=True)
    measure_parser.add_argument("--model-alias", default="tts")
    measure_parser.add_argument("--voice-alias", default="danish-default")
    measure_parser.add_argument("--audio-root", type=Path, required=True)
    measure_parser.add_argument("--output", type=Path, required=True)
    measure_parser.set_defaults(function=measure)
    prepare_parser = subparsers.add_parser("prepare", help="create an anonymous listening packet")
    prepare_parser.add_argument("--config", type=Path, required=True)
    prepare_parser.add_argument("--audio-root", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.set_defaults(function=prepare)
    evaluate_parser = subparsers.add_parser("evaluate", help="apply gates and select a candidate")
    evaluate_parser.add_argument("--config", type=Path, required=True)
    evaluate_parser.add_argument("--measurements", type=Path, required=True)
    evaluate_parser.add_argument("--mapping", type=Path, required=True)
    evaluate_parser.add_argument("--ratings", type=Path, required=True)
    evaluate_parser.add_argument("--preferences", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path)
    evaluate_parser.set_defaults(function=evaluate)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.function(args)
        return 0
    except QualificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
