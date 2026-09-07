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
MODEL = POLICY["default_model"]


def runtime(state: str = "active", healthy: bool = True) -> dict:
    return {"models": {MODEL: {"state": state, "healthy": healthy}}}


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
        self.assertEqual("shadow", POLICY["mode"])
        self.assertFalse(POLICY["load_on_demand"])
        self.assertFalse(POLICY["activation"]["enabled"])

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
    def test_manual_alias_bypasses_classifier(self) -> None:
        decision = ROUTER.decide(POLICY, request("coding"), runtime())
        self.assertEqual("manual_alias", decision["selection_mode"])
        self.assertFalse(decision["classifier_invoked"])
        self.assertEqual(MODEL, decision["selected_model"])
        self.assertEqual("coding", decision["upstream_model"])

    def test_exact_model_is_operator_only_and_bypasses_classifier(self) -> None:
        with self.assertRaisesRegex(ROUTER.PolicyError, "exact model is not authorized"):
            ROUTER.decide(POLICY, request(MODEL), runtime())
        decision = ROUTER.decide(POLICY, request(MODEL, client="operator"), runtime())
        self.assertEqual("manual_exact", decision["selection_mode"])
        self.assertFalse(decision["classifier_invoked"])

    def test_inactive_manual_model_fails_without_activation(self) -> None:
        with self.assertRaisesRegex(ROUTER.PolicyError, "model_not_active"):
            ROUTER.decide(POLICY, request("assistant"), runtime("inactive", False))

    def test_manual_override_cannot_bypass_hard_compatibility(self) -> None:
        oversized = request("coding")
        oversized["requirements"]["context_tokens"] = 32769
        with self.assertRaisesRegex(ROUTER.PolicyError, "model_not_compatible"):
            ROUTER.decide(POLICY, oversized, runtime())

    def test_shadow_auto_serves_default_and_records_proposal(self) -> None:
        decision = ROUTER.decide(
            POLICY,
            request("auto", classifier={"status": "ok", "model": MODEL, "confidence": 0.91, "reason_code": "tool_task"}),
            runtime(),
        )
        self.assertEqual("shadow", decision["selection_mode"])
        self.assertEqual(MODEL, decision["selected_model"])
        self.assertEqual(MODEL, decision["shadow_model"])
        self.assertTrue(decision["classifier_invoked"])
        self.assertFalse(decision["selector_fallback"])

    def test_classifier_failure_falls_back_without_activation(self) -> None:
        decision = ROUTER.decide(
            POLICY,
            request("auto", classifier={"status": "timeout"}),
            runtime(),
        )
        self.assertEqual(MODEL, decision["selected_model"])
        self.assertTrue(decision["selector_fallback"])
        self.assertFalse(decision["activation_requested"])

    def test_disabled_auto_does_not_inspect_classifier_output(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["mode"] = "disabled"
        decision = ROUTER.decide(
            policy,
            request("auto", classifier={"status": "not-a-valid-status"}),
            runtime(),
        )
        self.assertEqual("disabled", decision["selection_mode"])
        self.assertFalse(decision["classifier_invoked"])

    def test_auto_fails_closed_when_default_is_not_eligible(self) -> None:
        with self.assertRaisesRegex(ROUTER.PolicyError, "no authorized compatible active default"):
            ROUTER.decide(POLICY, request("auto"), runtime("inactive", False))

    def test_context_requirement_is_a_hard_eligibility_rule(self) -> None:
        oversized = request("auto")
        oversized["requirements"]["context_tokens"] = 32769
        with self.assertRaisesRegex(ROUTER.PolicyError, "no authorized compatible active default"):
            ROUTER.decide(POLICY, oversized, runtime())


if __name__ == "__main__":
    unittest.main()
