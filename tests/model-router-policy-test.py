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
PRIMARY = POLICY["qualification"]["selected_primary"]
HEAVY = next(name for name, model in POLICY["models"].items() if model["selection_lane"] == "heavy")


def runtime(*models: str) -> dict:
    return {"models": {name: {"state": "active", "healthy": True} for name in models}}


def request(model: str, client: str = "ordinary") -> dict:
    return {
        "requested_model": model,
        "client_policy": client,
        "requirements": {"capabilities": ["tools"], "protocol": "responses", "context_tokens": 4096},
    }


def qualified_policy() -> dict:
    policy = copy.deepcopy(POLICY)
    policy["qualification"]["status"] = "qualified"
    policy["default_model"] = PRIMARY
    policy["aliases"] = {alias: PRIMARY for alias in policy["aliases"]}
    policy["models"][PRIMARY]["auto_eligible"] = True
    policy["client_policies"]["operator"]["allowed_exact_models"] = [PRIMARY, HEAVY]
    for client in policy["client_policies"].values():
        client["auto_model_allowlist"] = [PRIMARY]
    return policy


class PolicyValidationTests(unittest.TestCase):
    def test_checked_in_policy_records_selection_but_remains_live_gated(self) -> None:
        ROUTER.validate_policy(POLICY)
        self.assertEqual("disabled", POLICY["mode"])
        self.assertEqual("local_only", POLICY["provider_scope"])
        self.assertEqual("fixed_default", POLICY["auto"]["strategy"])
        self.assertEqual("pending", POLICY["qualification"]["status"])
        self.assertEqual("primary", POLICY["models"][PRIMARY]["selection_lane"])
        self.assertFalse(POLICY["load_on_demand"])
        self.assertFalse(POLICY["activation"]["enabled"])
        self.assertIsNone(POLICY["default_model"])
        self.assertTrue(all(model is None for model in POLICY["aliases"].values()))

    def test_pending_policy_cannot_activate_aliases_or_models(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["aliases"]["assistant"] = PRIMARY
        with self.assertRaisesRegex(ROUTER.PolicyError, "unbound aliases"):
            ROUTER.validate_policy(policy)

        policy = copy.deepcopy(POLICY)
        policy["client_policies"]["operator"]["allowed_exact_models"] = [PRIMARY]
        with self.assertRaisesRegex(ROUTER.PolicyError, "cannot authorize"):
            ROUTER.validate_policy(policy)

    def test_selected_primary_must_reference_primary_lane(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["qualification"]["selected_primary"] = HEAVY
        with self.assertRaisesRegex(ROUTER.PolicyError, "selected primary"):
            ROUTER.validate_policy(policy)

    def test_activation_cannot_be_dynamic(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["load_on_demand"] = True
        policy["activation"]["enabled"] = True
        with self.assertRaisesRegex(ROUTER.PolicyError, "activation is not implemented"):
            ROUTER.validate_policy(policy)

    def test_fixed_default_strategy_cannot_enable_classifier_mode(self) -> None:
        policy = qualified_policy()
        policy["mode"] = "shadow"
        with self.assertRaisesRegex(ROUTER.PolicyError, "fixed_default"):
            ROUTER.validate_policy(policy)

    def test_gateway_scope_must_remain_local_only(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["provider_scope"] = "local_and_cloud"
        with self.assertRaisesRegex(ROUTER.PolicyError, "local_only"):
            ROUTER.validate_policy(policy)


class RoutingDecisionTests(unittest.TestCase):
    def test_requests_fail_closed_until_live_qualification(self) -> None:
        for requested_model in ("assistant", "coding", "auto", PRIMARY):
            with self.subTest(requested_model=requested_model):
                with self.assertRaisesRegex(ROUTER.PolicyError, "routing_not_qualified"):
                    ROUTER.decide(POLICY, request(requested_model, client="operator"), runtime(PRIMARY))

    def test_all_semantic_aliases_use_primary_after_qualification(self) -> None:
        policy = qualified_policy()
        ROUTER.validate_policy(policy)
        for alias in policy["aliases"]:
            decision = ROUTER.decide(policy, request(alias), runtime(PRIMARY))
            self.assertEqual(PRIMARY, decision["selected_model"])

    def test_auto_is_a_deterministic_default_without_classifier(self) -> None:
        policy = qualified_policy()
        decision = ROUTER.decide(
            policy,
            {
                **request("auto"),
                "classifier": {
                    "status": "ok",
                    "model": HEAVY,
                    "confidence": 1.0,
                    "reason_code": "must_be_ignored",
                },
            },
            runtime(PRIMARY),
        )
        self.assertEqual(PRIMARY, decision["selected_model"])
        self.assertEqual("auto", decision["upstream_model"])
        self.assertFalse(decision["classifier_invoked"])
        self.assertEqual("not_called", decision["classifier_status"])

    def test_heavy_cannot_be_bound_to_an_alias(self) -> None:
        policy = qualified_policy()
        policy["aliases"]["coding"] = HEAVY
        with self.assertRaisesRegex(ROUTER.PolicyError, "operator-swapped"):
            ROUTER.validate_policy(policy)

    def test_one_resident_limit_blocks_overlapping_cold_swap(self) -> None:
        policy = qualified_policy()
        with self.assertRaisesRegex(ROUTER.PolicyError, "resident_text_model_limit_exceeded"):
            ROUTER.decide(policy, request(PRIMARY, client="operator"), runtime(PRIMARY, HEAVY))


if __name__ == "__main__":
    unittest.main()
