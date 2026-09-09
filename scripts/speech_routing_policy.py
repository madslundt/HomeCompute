#!/usr/bin/env python3
"""Validate and exercise the repo-only speech routing contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class SpeechPolicyError(ValueError):
    pass


def load_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpeechPolicyError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpeechPolicyError("policy must be an object")
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1 or policy.get("mode") != "qualification-only":
        raise SpeechPolicyError("speech routes must remain schema 1 and qualification-only")
    routes = policy.get("routes")
    if not isinstance(routes, dict) or routes.get("stt") != {
        "danish": "stt_danish",
        "english_mixed_or_unknown": "stt_english",
    }:
        raise SpeechPolicyError("STT routing contract changed")
    if routes.get("tts") != {
        "danish": "tts_danish",
        "english_or_supported_non_danish": "tts_english",
        "danish_gpu_fallback": "tts_danish_fallback",
        "non_danish_gpu_failure": "fail-closed",
    }:
        raise SpeechPolicyError("TTS routing contract changed")
    language = policy.get("language_rules")
    if not isinstance(language, dict) or language.get("qwen_danish_forbidden") is not True:
        raise SpeechPolicyError("Qwen3-TTS must be forbidden for Danish")
    if set(language.get("danish_tags", [])) != {"da", "da-DK"}:
        raise SpeechPolicyError("Danish tags must be explicit")
    if set(language.get("qwen_supported_language_tags", [])) != {"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"}:
        raise SpeechPolicyError("Qwen3-TTS supported language tags must match the selected checkpoint")
    gates = policy.get("activation_gates")
    if not isinstance(gates, dict) or not all(
        gates.get(name) is True
        for name in (
            "all_routes_require_qualification",
            "hviske_commercial_use_requires_license_review",
            "arbitrary_voice_cloning_requires_documented_consent_and-policy",
        )
    ):
        raise SpeechPolicyError("speech activation, license, and consent gates are mandatory")


def route(policy: dict[str, Any], operation: str, language: str, gpu_available: bool = True) -> str:
    validate_policy(policy)
    is_danish = language in policy["language_rules"]["danish_tags"]
    if operation == "stt":
        return "stt_danish" if is_danish else "stt_english"
    if operation != "tts":
        raise SpeechPolicyError("operation must be stt or tts")
    if is_danish:
        return "tts_danish" if gpu_available else "tts_danish_fallback"
    if not gpu_available:
        raise SpeechPolicyError("no qualified non-Danish CPU fallback; fail closed")
    base_language = language.lower().split("-", 1)[0]
    if base_language not in policy["language_rules"]["qwen_supported_language_tags"]:
        raise SpeechPolicyError("language is not supported by the selected Qwen3-TTS checkpoint")
    return "tts_english"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--operation", choices=("stt", "tts"))
    parser.add_argument("--language", default="unknown")
    parser.add_argument("--gpu-unavailable", action="store_true")
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        validate_policy(policy)
        result = None if args.operation is None else route(policy, args.operation, args.language, not args.gpu_unavailable)
    except SpeechPolicyError as exc:
        print(f"speech-routing-policy: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"valid": True, "route": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
