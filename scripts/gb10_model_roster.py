#!/usr/bin/env python3
"""Validate the final, bounded ASUS GX10 model roster."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REVISION = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_SERVICES = {
    "stt_danish": ("syvai/hviske-v5.3", "cc-by-nc-4.0", "da"),
    "stt_english": ("nvidia/parakeet-tdt-0.6b-v2", "cc-by-4.0", "en"),
    "tts_danish": ("syvai/plapre-nano-v2", "cc-by-4.0", "da"),
    "tts_english": ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "apache-2.0", "en"),
}
REQUIRED_EXCLUSIONS = {
    "nvidia/DeepSeek-V4-Flash-NVFP4",
    "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
    "nvidia/Qwen3.6-35B-A3B-NVFP4",
    "openai/whisper-large-v3",
    "openai/whisper-large-v3-turbo",
    "syvai/hviske-v5-tiny",
    "syvai/Qwen3.8-27B-DFlash2-W4A16",
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
}


class RosterError(ValueError):
    """The roster violates the single-GB10 operating contract."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RosterError(f"{name} must be an object")
    return value


def _revision(value: Any, name: str) -> str:
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise RosterError(f"{name} must be a lowercase 40-hex commit")
    return value


def _model(entry: Any, name: str) -> dict[str, Any]:
    model = _object(entry, name)
    if not isinstance(model.get("model_id"), str) or "/" not in model["model_id"]:
        raise RosterError(f"{name}.model_id must be a repository ID")
    _revision(model.get("revision"), f"{name}.revision")
    if not isinstance(model.get("license_id"), str) or not model["license_id"]:
        raise RosterError(f"{name}.license_id must be non-empty")
    return model


def validate_roster(roster: dict[str, Any]) -> None:
    expected = {
        "schema_version", "roster_version", "hardware", "text_models", "services",
        "excluded_models", "operating_profiles", "deployment_order",
    }
    if set(roster) != expected:
        raise RosterError("roster has unsupported or missing top-level keys")
    if roster["schema_version"] != 2:
        raise RosterError("schema_version must be 2")

    hardware = _object(roster["hardware"], "hardware")
    if hardware.get("accelerator") != "NVIDIA GB10" or hardware.get("unified_memory_gib") != 128:
        raise RosterError("roster must target one 128 GiB NVIDIA GB10")
    if hardware.get("resident_text_model_limit") != 1:
        raise RosterError("only one text model may be resident")
    if hardware.get("retained_text_model_limit") != 2:
        raise RosterError("the final stack retains exactly two general-purpose text models")

    text_models = _object(roster["text_models"], "text_models")
    if set(text_models) != {"primary", "heavy"}:
        raise RosterError("text_models must contain exactly primary and heavy")
    primary = _model(text_models["primary"], "text_models.primary")
    heavy = _model(text_models["heavy"], "text_models.heavy")
    if primary["model_id"] != "unsloth/Qwen3.8-27B-NVFP4":
        raise RosterError("the production workhorse must be unsloth/Qwen3.8-27B-NVFP4")
    if primary.get("disposition") != "production-workhorse":
        raise RosterError("the primary model must be the production workhorse")
    if primary.get("default_thinking_mode") != "disabled":
        raise RosterError("the primary default must be non-thinking mode")

    runtimes = _object(primary.get("runtime_profiles"), "text_models.primary.runtime_profiles")
    if set(runtimes) != {"baseline", "performance"}:
        raise RosterError("the primary model must define baseline and performance runtimes")
    baseline = _object(runtimes["baseline"], "text_models.primary.runtime_profiles.baseline")
    if baseline != {
        "runtime": "vllm", "speculative_method": "qwen3_5_mtp",
        "draft_model_id": None, "status": "implement-first",
    }:
        raise RosterError("the baseline runtime must be vLLM with native qwen3_5_mtp")
    performance = _object(runtimes["performance"], "text_models.primary.runtime_profiles.performance")
    if performance.get("runtime") != "sglang" or performance.get("speculative_method") != "dflash":
        raise RosterError("the performance runtime must be SGLang with DFlash")
    if performance.get("draft_model_id") != "incoai/Qwen3.8-27B-DFlash2":
        raise RosterError("the DFlash runtime must use the incoai draft checkpoint")
    _revision(performance.get("draft_revision"), "text_models.primary.runtime_profiles.performance.draft_revision")

    if heavy["model_id"] != "RadixArk/Qwen3.8-Flash-Next-NVFP4":
        raise RosterError("the heavy lane must use the RadixArk Flash-Next checkpoint")
    if heavy.get("disposition") != "cold-swap-heavy" or heavy.get("activation") != "operator-exclusive":
        raise RosterError("Flash-Next must remain an operator-exclusive cold swap")
    recipe = _object(heavy.get("recipe"), "text_models.heavy.recipe")
    if recipe.get("url") != "https://github.com/blazux/qwen3.8-Flash-DGX":
        raise RosterError("Flash-Next must use the Blazux single-GB10 recipe")
    _revision(recipe.get("revision"), "text_models.heavy.recipe.revision")

    services = _object(roster["services"], "services")
    if set(services) != set(EXPECTED_SERVICES):
        raise RosterError("services must contain exactly two STT and two TTS models")
    active_ids = {primary["model_id"], heavy["model_id"], performance["draft_model_id"]}
    for name, (model_id, license_id, language) in EXPECTED_SERVICES.items():
        service = _model(services[name], f"services.{name}")
        if (service["model_id"], service["license_id"], service.get("language")) != (model_id, license_id, language):
            raise RosterError(f"services.{name} does not match the final checkpoint contract")
        if service.get("disposition") != "install":
            raise RosterError(f"services.{name} disposition must be install")
        if service["model_id"] in active_ids:
            raise RosterError("model IDs must not be reused across roles")
        active_ids.add(service["model_id"])

    excluded = roster["excluded_models"]
    if not isinstance(excluded, list) or len(excluded) != len(set(excluded)):
        raise RosterError("excluded_models must be a unique list")
    if set(excluded) & active_ids:
        raise RosterError("an excluded model cannot also appear in the final roster")
    missing = REQUIRED_EXCLUSIONS - set(excluded)
    if missing:
        raise RosterError("required exclusions are missing: " + ", ".join(sorted(missing)))

    profiles = _object(roster["operating_profiles"], "operating_profiles")
    if set(profiles) != {"normal", "heavy"}:
        raise RosterError("operating_profiles must contain normal and heavy")
    normal = _object(profiles["normal"], "operating_profiles.normal")
    if normal.get("text_model") != "primary" or normal.get("allowed_text_runtime_profiles") != ["baseline", "performance"]:
        raise RosterError("normal mode must use the primary model with either qualified runtime")
    if normal.get("speech_services") != list(EXPECTED_SERVICES):
        raise RosterError("normal mode must list all four speech services")
    heavy_profile = _object(profiles["heavy"], "operating_profiles.heavy")
    if heavy_profile != {
        "text_model": "heavy", "stop_text_model_first": "primary",
        "restart_text_model_after": "primary", "dynamic_router_activation": False,
    }:
        raise RosterError("heavy mode must serialize the primary/Flash-Next cold swap")

    order = roster["deployment_order"]
    if not isinstance(order, list) or order[:4] != [
        "primary.runtime_profiles.baseline", "benchmark.baseline",
        "primary.runtime_profiles.performance", "benchmark.runtime-comparison",
    ] or order[-1:] != ["heavy"]:
        raise RosterError("deployment_order must establish baseline, compare runtimes, then install heavy last")


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
