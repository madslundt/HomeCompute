#!/usr/bin/env python3
"""Validate the bounded GB10 model roster and its benchmark/removal gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REVISION = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_SERVICES = {
    "embedding": ("nvidia/Nemotron-3-Embed-1B-NVFP4", "enable-with-rag"),
    "stt_recorded": ("CoRal-project/roest-v3-whisper-1.5b", "retain"),
    "stt_streaming": (
        "nvidia/nemotron-3.5-asr-streaming-0.6b",
        "benchmark-if-live-voice-enabled",
    ),
    "tts": ("CoRal-project/roest-v3-chatterbox-350m", "retain"),
    "diarization": (
        "pyannote/speaker-diarization-community-1",
        "enable-with-multi-speaker-meetings",
    ),
    "reranker": (
        "nvidia/llama-nemotron-rerank-vl-1b-v2",
        "enable-after-retrieval-failure",
    ),
}

REQUIRED_EXCLUSIONS = {
    "nvidia/DeepSeek-V4-Flash-NVFP4",
    "nvidia/Qwen3.8-Flash-Next-NVFP4",
    "nvidia/Qwen3.6-35B-A3B-NVFP4",
}


class RosterError(ValueError):
    """The model roster violates the one-GB10 operating contract."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RosterError(f"{name} must be an object")
    return value


def _model(entry: Any, name: str) -> dict[str, Any]:
    model = _object(entry, name)
    model_id = model.get("model_id")
    revision = model.get("revision")
    if not isinstance(model_id, str) or "/" not in model_id:
        raise RosterError(f"{name}.model_id must be a repository ID")
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise RosterError(f"{name}.revision must be a lowercase 40-hex commit")
    for field in ("draft_revision",):
        value = model.get(field)
        if value is not None and (not isinstance(value, str) or not REVISION.fullmatch(value)):
            raise RosterError(f"{name}.{field} must be a lowercase 40-hex commit")
    recipe = model.get("recipe")
    if recipe is not None:
        recipe = _object(recipe, f"{name}.recipe")
        if set(recipe) != {"url", "revision"}:
            raise RosterError(f"{name}.recipe must contain exactly url and revision")
        if not isinstance(recipe["url"], str) or not recipe["url"].startswith("https://"):
            raise RosterError(f"{name}.recipe.url must use HTTPS")
        if not isinstance(recipe["revision"], str) or not REVISION.fullmatch(recipe["revision"]):
            raise RosterError(f"{name}.recipe.revision must be a lowercase 40-hex commit")
    return model


def validate_roster(roster: dict[str, Any]) -> None:
    expected = {
        "schema_version",
        "roster_version",
        "hardware",
        "text_models",
        "services",
        "excluded_text_models",
        "operating_profiles",
    }
    if set(roster) != expected:
        raise RosterError("roster has unsupported or missing top-level keys")
    if roster["schema_version"] != 1:
        raise RosterError("schema_version must be 1")

    hardware = _object(roster["hardware"], "hardware")
    if hardware.get("accelerator") != "NVIDIA GB10" or hardware.get("unified_memory_gib") != 128:
        raise RosterError("roster must target one 128 GiB NVIDIA GB10")
    if hardware.get("resident_text_model_limit") != 1:
        raise RosterError("only one text model may be resident")
    if hardware.get("retained_text_model_limit") != 2:
        raise RosterError("steady state must retain exactly two text models at most")

    text_models = _object(roster["text_models"], "text_models")
    if set(text_models) != {"quality_lane", "everyday_competition"}:
        raise RosterError("text_models must contain the quality lane and everyday competition")
    quality = _model(text_models["quality_lane"], "text_models.quality_lane")
    if quality.get("model_id") != "RadixArk/Qwen3.8-Flash-Next-NVFP4":
        raise RosterError("the quality lane must use the single-GB10-tested RadixArk Flash-Next artifact")
    if quality.get("disposition") != "retain" or quality.get("activation") != "scheduled-exclusive":
        raise RosterError("the quality lane must be retained and scheduled exclusively")

    competition = _object(text_models["everyday_competition"], "text_models.everyday_competition")
    candidates = competition.get("candidates")
    if competition.get("retention_limit") != 1 or competition.get("winner_required") is not True:
        raise RosterError("the everyday competition must retain exactly one winner")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise RosterError("the everyday competition must contain exactly two candidates")
    candidate_models = [
        _model(candidate, f"text_models.everyday_competition.candidates[{index}]")
        for index, candidate in enumerate(candidates)
    ]
    candidate_ids = {candidate["model_id"] for candidate in candidate_models}
    expected_candidates = {
        "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
        "RadixArk/Qwen3.8-27B-NVFP4",
    }
    if candidate_ids != expected_candidates:
        raise RosterError("everyday candidates must be Nemotron Lightning and Qwen3.8-27B")
    if any(candidate.get("disposition") != "benchmark" for candidate in candidate_models):
        raise RosterError("everyday candidates remain benchmark-only until a winner is recorded")

    services = _object(roster["services"], "services")
    if set(services) != set(EXPECTED_SERVICES):
        raise RosterError("services must contain the bounded speech and retrieval roles")
    service_models = []
    for name, expected in EXPECTED_SERVICES.items():
        service = _model(services[name], f"services.{name}")
        expected_model_id, expected_disposition = expected
        if service.get("model_id") != expected_model_id:
            raise RosterError(f"services.{name} must use {expected_model_id}")
        if service.get("disposition") != expected_disposition:
            raise RosterError(f"services.{name} disposition must be {expected_disposition}")
        service_models.append(service)

    all_models = [quality, *candidate_models, *service_models]
    all_ids = [model["model_id"] for model in all_models]
    if len(all_ids) != len(set(all_ids)):
        raise RosterError("model IDs must not be reused across roster roles")

    excluded = roster["excluded_text_models"]
    if not isinstance(excluded, list) or len(excluded) != len(set(excluded)):
        raise RosterError("excluded_text_models must be a unique list")
    if set(excluded) & set(all_ids):
        raise RosterError("an excluded text model cannot also appear in the active roster")
    missing_exclusions = REQUIRED_EXCLUSIONS - set(excluded)
    if missing_exclusions:
        raise RosterError(
            "required single-GB10 exclusions are missing: " + ", ".join(sorted(missing_exclusions))
        )


def load_roster(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RosterError(f"cannot read {path}: {exc}") from exc
    return _object(value, str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        roster = load_roster(args.roster)
        validate_roster(roster)
    except RosterError as exc:
        print(f"gb10-model-roster: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"roster_version": roster["roster_version"], "valid": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
