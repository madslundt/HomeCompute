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
        self.assertEqual(1, ROSTER["text_models"]["everyday_competition"]["retention_limit"])

    def test_cannot_keep_both_everyday_candidates(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["everyday_competition"]["retention_limit"] = 2
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "exactly one winner"):
            ROSTER_MODULE.validate_roster(roster)

    def test_required_text_exclusions_cannot_be_removed(self) -> None:
        for model_id in ROSTER_MODULE.REQUIRED_EXCLUSIONS:
            with self.subTest(model_id=model_id):
                roster = copy.deepcopy(ROSTER)
                roster["excluded_text_models"].remove(model_id)
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "exclusions are missing"):
                    ROSTER_MODULE.validate_roster(roster)

    def test_nvidia_flash_checkpoint_cannot_replace_recipe_artifact(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["quality_lane"]["model_id"] = "nvidia/Qwen3.8-Flash-Next-NVFP4"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "RadixArk Flash-Next"):
            ROSTER_MODULE.validate_roster(roster)

    def test_service_models_and_dispositions_are_part_of_the_contract(self) -> None:
        for role, (model_id, disposition) in ROSTER_MODULE.EXPECTED_SERVICES.items():
            with self.subTest(role=role, field="model_id"):
                roster = copy.deepcopy(ROSTER)
                roster["services"][role]["model_id"] = "example/incorrect-model"
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, f"services.{role} must use"):
                    ROSTER_MODULE.validate_roster(roster)
            with self.subTest(role=role, field="disposition"):
                roster = copy.deepcopy(ROSTER)
                roster["services"][role]["disposition"] = "retain"
                if disposition == "retain":
                    roster["services"][role]["disposition"] = "benchmark"
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, f"services.{role} disposition"):
                    ROSTER_MODULE.validate_roster(roster)

    def test_shadow_router_uses_the_roster_revisions(self) -> None:
        quality = ROSTER["text_models"]["quality_lane"]
        candidates = {
            candidate["model_id"]: candidate
            for candidate in ROSTER["text_models"]["everyday_competition"]["candidates"]
        }
        expected = {
            f"qwen3.8-flash-next-nvfp4@{quality['revision']}",
            "nemotron-3.5-lightning-30b-a3b-nvfp4@"
            + candidates["nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"]["revision"],
            "qwen3.8-27b-nvfp4@" + candidates["RadixArk/Qwen3.8-27B-NVFP4"]["revision"],
        }
        self.assertEqual(expected, set(ROUTER_POLICY["models"]))
        self.assertEqual(1, ROUTER_POLICY["qualification"]["resident_text_model_limit"])
        self.assertEqual("pending", ROUTER_POLICY["qualification"]["status"])
        self.assertIsNone(ROUTER_POLICY["qualification"]["everyday_winner"])


if __name__ == "__main__":
    unittest.main()
