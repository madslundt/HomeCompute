#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "model_router_policy.py"
SPEC = importlib.util.spec_from_file_location("model_router_policy", MODULE_PATH)
assert SPEC and SPEC.loader
ROUTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTER)
POLICY = json.loads((ROOT / "config" / "model-router-policy.json").read_text(encoding="utf-8"))
MODELS = list(POLICY["models"])
MODEL = MODELS[0]
FLASH_MODEL = next(model for model in MODELS if model.startswith("qwen3.8-flash-next"))


def runtime(state: str = "active", healthy: bool = True, *, model: str = MODEL) -> dict:
    return {"models": {model: {"state": state, "healthy": healthy}}}


def request(model: str, client: str = "ordinary", **overrides: object) -> dict:
    document = {
        "requested_model": model,
        "client_policy": client,
        "requirements": {"capabilities": ["tools"], "protocol": "responses", "context_tokens": 4096},
    }
    document.update(overrides)
    return document


class PolicyValidationTests(unittest.TestCase):
    def test_checked_in_policy_is_valid_and_activation_is_off(self) -> None:
        ROUTER.validate_policy(POLICY)
        self.assertEqual("disabled", POLICY["mode"])
        self.assertFalse(POLICY["load_on_demand"])
        self.assertFalse(POLICY["activation"]["enabled"])
        self.assertEqual("pending", POLICY["qualification"]["status"])
        self.assertIsNone(POLICY["default_model"])
        self.assertTrue(all(model is None for model in POLICY["aliases"].values()))

    def test_activation_cannot_be_enabled_before_it_is_implemented(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["load_on_demand"] = True
        policy["activation"]["enabled"] = True
        with self.assertRaisesRegex(ROUTER.PolicyError, "activation is not implemented"):
            ROUTER.validate_policy(policy)

    def test_six_task_aliases_are_required(self) -> None:
        policy = copy.deepcopy(POLICY)
        del policy["aliases"]["home"]
        with self.assertRaisesRegex(ROUTER.PolicyError, "six task-semantic aliases"):
            ROUTER.validate_policy(policy)


class RoutingDecisionTests(unittest.TestCase):
    def test_all_requests_fail_closed_until_benchmark_winner_is_recorded(self) -> None:
        for requested_model in ("assistant", "coding", "auto", MODEL):
            with self.subTest(requested_model=requested_model):
                with self.assertRaisesRegex(ROUTER.PolicyError, "routing_not_qualified"):
                    ROUTER.decide(POLICY, request(requested_model, client="operator"), runtime())

    def test_pending_policy_cannot_bind_aliases_or_authorize_models(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["aliases"]["assistant"] = MODEL
        with self.assertRaisesRegex(ROUTER.PolicyError, "unbound aliases"):
            ROUTER.validate_policy(policy)

        policy = copy.deepcopy(POLICY)
        policy["client_policies"]["operator"]["allowed_exact_models"] = [MODEL]
        with self.assertRaisesRegex(ROUTER.PolicyError, "cannot authorize"):
            ROUTER.validate_policy(policy)

    def test_pending_policy_cannot_name_a_winner(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["qualification"]["everyday_winner"] = MODEL
        with self.assertRaisesRegex(ROUTER.PolicyError, "cannot select"):
            ROUTER.validate_policy(policy)

    def test_one_resident_limit_is_enforced_after_qualification(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["qualification"]["status"] = "qualified"
        policy["qualification"]["everyday_winner"] = MODEL
        policy["default_model"] = MODEL
        policy["aliases"] = {
            "assistant": MODEL,
            "automation": MODEL,
            "coding": FLASH_MODEL,
            "home": MODEL,
            "meeting": MODEL,
            "research": FLASH_MODEL,
        }
        policy["models"][MODEL]["auto_eligible"] = True
        policy["client_policies"]["operator"]["allowed_exact_models"] = [MODEL, FLASH_MODEL]
        policy["client_policies"]["operator"]["auto_model_allowlist"] = [MODEL]
        multiple = {
            "models": {
                MODEL: {"state": "active", "healthy": True},
                FLASH_MODEL: {"state": "loading", "healthy": False},
            }
        }
        with self.assertRaisesRegex(ROUTER.PolicyError, "resident_text_model_limit_exceeded"):
            ROUTER.decide(policy, request(MODEL, client="operator"), multiple)

    def test_quality_model_cannot_be_recorded_as_everyday_winner(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["qualification"]["status"] = "qualified"
        policy["qualification"]["everyday_winner"] = FLASH_MODEL
        policy["default_model"] = FLASH_MODEL
        policy["aliases"] = {alias: FLASH_MODEL for alias in policy["aliases"]}
        with self.assertRaisesRegex(ROUTER.PolicyError, "everyday candidate"):
            ROUTER.validate_policy(policy)

    def test_everyday_aliases_must_follow_the_recorded_winner(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["qualification"]["status"] = "qualified"
        policy["qualification"]["everyday_winner"] = MODEL
        policy["default_model"] = MODEL
        policy["aliases"] = {
            "assistant": FLASH_MODEL,
            "automation": MODEL,
            "coding": FLASH_MODEL,
            "home": MODEL,
            "meeting": MODEL,
            "research": FLASH_MODEL,
        }
        with self.assertRaisesRegex(ROUTER.PolicyError, "assistant.*everyday winner"):
            ROUTER.validate_policy(policy)


if __name__ == "__main__":
    unittest.main()
