#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "gb10_model_roster.py"
SPEC = importlib.util.spec_from_file_location("gb10_model_roster", MODULE_PATH)
assert SPEC and SPEC.loader
ROSTER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROSTER_MODULE)
ROSTER = json.loads((ROOT / "config" / "gb10-model-roster.json").read_text(encoding="utf-8"))
ROUTER_POLICY = json.loads((ROOT / "config" / "model-router-policy.json").read_text(encoding="utf-8"))


class RosterTests(unittest.TestCase):
    def test_checked_in_roster_is_valid_and_bounded(self) -> None:
        ROSTER_MODULE.validate_roster(ROSTER)
        self.assertEqual(1, ROSTER["hardware"]["resident_text_model_limit"])
        self.assertEqual(2, ROSTER["hardware"]["retained_text_model_limit"])
        self.assertEqual({"primary", "heavy"}, set(ROSTER["text_models"]))

    def test_primary_and_draft_checkpoints_are_fixed(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["primary"]["model_id"] = "RadixArk/Qwen3.8-27B-NVFP4"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "production workhorse"):
            ROSTER_MODULE.validate_roster(roster)

        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["primary"]["runtime_profiles"]["performance"]["draft_model_id"] = (
            "syvai/Qwen3.8-27B-DFlash2-W4A16"
        )
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "incoai draft"):
            ROSTER_MODULE.validate_roster(roster)

    def test_runtime_order_is_baseline_then_performance(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["deployment_order"][0:2] = ["benchmark.baseline", "primary.runtime_profiles.baseline"]
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "deployment_order"):
            ROSTER_MODULE.validate_roster(roster)

    def test_flash_next_is_exclusive_and_uses_blazux(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["heavy"]["activation"] = "load-on-demand"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "operator-exclusive cold swap"):
            ROSTER_MODULE.validate_roster(roster)

        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["heavy"]["recipe"]["url"] = "https://example.invalid/recipe"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "Blazux"):
            ROSTER_MODULE.validate_roster(roster)

    def test_required_exclusions_cannot_be_removed(self) -> None:
        for model_id in ROSTER_MODULE.REQUIRED_EXCLUSIONS:
            with self.subTest(model_id=model_id):
                roster = copy.deepcopy(ROSTER)
                roster["excluded_models"].remove(model_id)
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "exclusions are missing"):
                    ROSTER_MODULE.validate_roster(roster)

    def test_service_models_licenses_and_languages_are_contractual(self) -> None:
        for role in ROSTER_MODULE.EXPECTED_SERVICES:
            with self.subTest(role=role):
                roster = copy.deepcopy(ROSTER)
                roster["services"][role]["model_id"] = "example/incorrect-model"
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, f"services.{role}"):
                    ROSTER_MODULE.validate_roster(roster)

    def test_speech_runtime_groups_are_isolated(self) -> None:
        groups = [entry["runtime_profile"]["isolation_group"] for entry in ROSTER["services"].values()]
        self.assertEqual(len(groups), len(set(groups)))
        self.assertEqual(">=0.19,<0.20", ROSTER["services"]["stt_danish"]["runtime_profile"]["version_constraint"])
        self.assertEqual(">=0.15,<0.16", ROSTER["services"]["tts_danish"]["runtime_profile"]["version_constraint"])

    def test_supporting_services_are_not_primary_routes(self) -> None:
        supporting = ROSTER["supporting_services"]
        self.assertEqual("evaluate-later-not-primary", supporting["stt_danish_later_evaluation"]["disposition"])
        self.assertNotIn("speaker_diarization", ROSTER["operating_profiles"]["normal"]["speech_services"])

    def test_router_contains_only_the_two_selected_text_models(self) -> None:
        primary = ROSTER["text_models"]["primary"]
        heavy = ROSTER["text_models"]["heavy"]
        expected = {
            f"qwen3.8-27b-nvfp4@{primary['revision']}",
            f"qwen3.8-flash-next-nvfp4@{heavy['revision']}",
        }
        self.assertEqual(expected, set(ROUTER_POLICY["models"]))
        self.assertEqual(1, ROUTER_POLICY["qualification"]["resident_text_model_limit"])
        self.assertEqual("pending", ROUTER_POLICY["qualification"]["status"])
        self.assertEqual(next(name for name in expected if name.startswith("qwen3.8-27b")), ROUTER_POLICY["qualification"]["selected_primary"])


if __name__ == "__main__":
    unittest.main()
