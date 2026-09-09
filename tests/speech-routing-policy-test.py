#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("speech_routing_policy", ROOT / "scripts" / "speech_routing_policy.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
POLICY = MODULE.load_policy(ROOT / "config" / "speech-routing-policy.json")


class SpeechRoutingPolicyTest(unittest.TestCase):
    def test_checked_in_policy_is_valid(self) -> None:
        MODULE.validate_policy(POLICY)

    def test_stt_routes_danish_to_hviske_and_everything_else_to_whisper(self) -> None:
        self.assertEqual("stt_danish", MODULE.route(POLICY, "stt", "da-DK"))
        for language in ("en", "mixed", "unknown", "und"):
            with self.subTest(language=language):
                self.assertEqual("stt_english", MODULE.route(POLICY, "stt", language))

    def test_tts_never_routes_danish_to_qwen(self) -> None:
        self.assertEqual("tts_danish", MODULE.route(POLICY, "tts", "da"))
        self.assertEqual("tts_danish_fallback", MODULE.route(POLICY, "tts", "da", gpu_available=False))
        self.assertEqual("tts_english", MODULE.route(POLICY, "tts", "en"))
        self.assertEqual("tts_english", MODULE.route(POLICY, "tts", "fr-FR"))

    def test_non_danish_gpu_failure_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(MODULE.SpeechPolicyError, "fail closed"):
            MODULE.route(POLICY, "tts", "en", gpu_available=False)
        with self.assertRaisesRegex(MODULE.SpeechPolicyError, "not supported"):
            MODULE.route(POLICY, "tts", "unknown")

    def test_consent_and_license_gates_cannot_be_disabled(self) -> None:
        for gate in (
            "hviske_commercial_use_requires_license_review",
            "arbitrary_voice_cloning_requires_documented_consent_and-policy",
        ):
            policy = copy.deepcopy(POLICY)
            policy["activation_gates"][gate] = False
            with self.assertRaises(MODULE.SpeechPolicyError):
                MODULE.validate_policy(policy)


if __name__ == "__main__":
    unittest.main()
