"""Credential-free workflow acceptance; no live assistant, Codex, or GitHub writes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("action_flow", ROOT / "scripts/verify-openclaw-action-flow.py")
FLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOW)


class SyntheticActionFlowTests(unittest.TestCase):
    def test_approved_issue_to_real_fixture_tests_review_and_simulated_draft(self):
        report = FLOW.probe()
        self.assertTrue(report["passed"])
        self.assertEqual(report["external_calls"], {"paid_model_calls": 0, "GitHub_writes": 0})
        self.assertFalse(report["capabilities"]["authenticated_Codex"])
        self.assertFalse(report["capabilities"]["namespace_isolation"])
        self.assertFalse(report["capabilities"]["live_OpenClaw_integration"])
        self.assertFalse(report["capabilities"]["real_GitHub_PR"])
        self.assertGreater(report["worker"]["baseline_exit"], 0)
        self.assertEqual(report["worker"]["postcheck_exit"], 0)
        self.assertNotEqual(report["worker"]["process_id"], report["controller_process_id"])
        self.assertTrue(report["worker"]["session_id"].startswith("synthetic-session-"))
        self.assertEqual(report["task"]["state"], "completed")
        self.assertEqual(report["task"]["commit"], report["local_git"]["proposed_commit"])
        self.assertEqual(report["task"]["session_id"], report["worker"]["session_id"])
        self.assertIn("github.invalid", report["task"]["pr_url"])
        self.assertTrue(report["draft_payload"]["draft"])
        self.assertEqual(report["draft_payload"]["head"], "codex/repair-" + report["task"]["id"])
        self.assertEqual(report["states"], ["pending", "queued", "running", "review", "publishing", "completed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["review"]["result"]["files"][0]["path"], "src/calc.py")
        self.assertEqual(report["publisher_methods"], ["GET", "GET", "POST", "POST", "POST", "POST", "POST"])
        self.assertIn("AssertionError: -1 != 5", report["worker"]["baseline_log"])
        self.assertIn("OK", report["worker"]["postcheck_log"])

    def test_committed_local_only_classification_fails_before_fixed_backend(self):
        report = FLOW.probe(classification="local_only")
        self.assertFalse(report["passed"])
        self.assertEqual(report["task"]["state"], "failed")
        self.assertEqual(report["worker"]["error"], "ValueError")
        self.assertFalse(report["worker"]["fixed_backend_called"])
        self.assertEqual(report["publisher_methods"], [])
        self.assertEqual(report["external_calls"], {"paid_model_calls": 0, "GitHub_writes": 0})

    def test_broker_worker_policy_mismatch_fails_before_checkout_or_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = FLOW.Worker(Path(directory), "unused", {"synthetic-demo": {"classification": "cloud_allowed"}})
            with patch.object(worker, "sandbox_preflight") as preflight, patch.object(worker, "clone") as clone:
                with self.assertRaisesRegex(ValueError, "broker policy differs"):
                    worker.execute({"project": "synthetic-demo", "policy": {"classification": "local_only"}})
            preflight.assert_not_called()
            clone.assert_not_called()

    def test_internal_fixture_protocol_cannot_supply_shell_test_argv(self):
        with self.assertRaisesRegex(ValueError, "policy is fixed"):
            FLOW.worker_process({"root": "/unused", "fixture": "/unused", "broker": "http://127.0.0.1:1",
                                 "token": "unused", "projects": {"synthetic-demo": {
                                     "repository": "example/synthetic", "classification": "cloud_allowed",
                                     "tests": ["sh", "-c", "false"], "write_prefixes": ["src/", "tests/"]}}})


if __name__ == "__main__":
    unittest.main()
