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


class RosterInvariantTests(unittest.TestCase):
    def test_current_roster_is_valid(self) -> None:
        ROSTER_MODULE.validate_roster(ROSTER)

    def test_model_replacement_does_not_change_validator_contract(self) -> None:
        roster = copy.deepcopy(ROSTER)
        replacement = copy.deepcopy(roster["text_models"]["primary"])
        replacement["model_id"] = "example/next-generation-model"
        replacement["revision"] = "a" * 40
        replacement["license_id"] = "apache-2.0"
        replacement["disposition"] = "candidate"
        replacement["roles"] = ["coding"]
        roster["text_models"] = {
            "coding_candidate": replacement,
            "small_helper": {
                "model_id": "example/small-helper",
                "revision": "sha256:" + "b" * 64,
                "license_id": "mit",
                "disposition": "candidate",
                "runtime_profile": {"runtime": "llamacpp-gguf"},
            },
        }
        roster["operating_profiles"] = {
            "normal": {"text_model": "coding_candidate"},
            "candidate": {"text_model": "small_helper", "dynamic_router_activation": False},
        }
        ROSTER_MODULE.validate_roster(roster)

    def test_runtime_parser_is_data_not_a_fixed_platform_requirement(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["primary"]["runtime_profiles"]["baseline"] = {
            "runtime": "sglang-qwen",
            "tool_call_parser": "new-parser",
            "status": "candidate",
        }
        ROSTER_MODULE.validate_roster(roster)

    def test_revision_must_be_immutable(self) -> None:
        for invalid in ("main", "", "sha256:1234", "A" * 40):
            with self.subTest(invalid=invalid):
                roster = copy.deepcopy(ROSTER)
                roster["text_models"]["primary"]["revision"] = invalid
                with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "immutable"):
                    ROSTER_MODULE.validate_roster(roster)

    def test_conflicting_revisions_for_same_model_are_rejected(self) -> None:
        roster = copy.deepcopy(ROSTER)
        second = copy.deepcopy(roster["text_models"]["primary"])
        second["revision"] = "a" * 40
        roster["services"]["additional_role"] = second
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "conflicting immutable revisions"):
            ROSTER_MODULE.validate_roster(roster)

    def test_route_references_must_resolve(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["operating_profiles"]["normal"]["text_model"] = "missing-artifact"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "unknown artifact"):
            ROSTER_MODULE.validate_roster(roster)

    def test_memory_and_lifecycle_limits_are_consistent(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["hardware"]["resident_text_model_limit"] = 4
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "retained_text_model_limit"):
            ROSTER_MODULE.validate_roster(roster)

    def test_configuration_rejects_secret_bearing_fields(self) -> None:
        roster = copy.deepcopy(ROSTER)
        roster["text_models"]["primary"]["api_key"] = "never-commit"
        with self.assertRaisesRegex(ROSTER_MODULE.RosterError, "secret-bearing"):
            ROSTER_MODULE.validate_roster(roster)


if __name__ == "__main__":
    unittest.main()
