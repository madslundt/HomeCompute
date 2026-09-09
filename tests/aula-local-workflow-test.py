#!/usr/bin/env python3
"""Static safety checks for the local-only Aula qualification scaffold."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "automations" / "aula-local" / "qualification.workflow.ts"
POLICY = ROOT / "automations" / "aula-local" / "retry-policy.json"
PUBLISHED_PROMPT_SOURCE = (
    ROOT / "automations" / "model-benchmark" / "aula-real-mcp-evaluation.workflow.ts"
)


class AulaLocalWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text()

    def test_qualification_graph_has_only_test_webhooks(self) -> None:
        self.assertEqual(self.source.count("type: 'n8n-nodes-base.webhook'"), 2)
        self.assertEqual(self.source.count("authentication: 'headerAuth'"), 2)
        self.assertEqual(
            self.source.count("newCredential('HomeCompute qualification webhook')"), 2
        )
        # The sole unauthenticated edge is the internal Aula MCP transport;
        # both externally callable test webhooks use header authentication.
        self.assertEqual(self.source.count("authentication: 'none'"), 1)
        forbidden = (
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.telegram",
            "n8n-nodes-base.notion",
            "n8n-nodes-base.microsoftOutlook",
            "n8n-nodes-base.emailSend",
        )
        for node_type in forbidden:
            self.assertNotIn(node_type, self.source)

    def test_model_is_fixed_to_local_automation_credential(self) -> None:
        self.assertIn("const LOCAL_MODEL_ALIAS = 'automation'", self.source)
        self.assertEqual(
            self.source.count("newCredential('HomeCompute local automation only')"), 2
        )
        self.assertNotIn("lmChatOpenRouter", self.source)
        self.assertNotIn("openRouterApi", self.source)
        self.assertNotIn("api.openai.com", self.source)
        self.assertNotIn("anthropic", self.source.lower())
        self.assertNotIn("nodeJson(", self.source)

    def test_model_retries_are_bounded_and_results_declare_no_actions(self) -> None:
        self.assertIn("const LOCAL_RETRY_LIMIT = 2", self.source)
        self.assertEqual(self.source.count("maxRetries: LOCAL_RETRY_LIMIT"), 2)
        self.assertEqual(self.source.count("notifications_sent: false"), 2)
        self.assertEqual(self.source.count("writes_performed: false"), 2)

    def test_live_path_is_read_only_and_replay_has_no_tools(self) -> None:
        self.assertEqual(self.source.count("mcpClientTool"), 1)
        self.assertIn("Read-only Aula MCP (local shadow)", self.source)
        self.assertIn("subnodes: { model: replayModel }", self.source)
        self.assertIn("captured_context exceeds the 200000 character limit", self.source)
        self.assertIn("captured_user_prompt must be a non-empty string", self.source)
        self.assertIn("captured_system_prompt must be a non-empty string", self.source)
        self.assertIn("real-aula-data-local-only-no-side-effects", self.source)
        self.assertIn("captured-aula-data-local-only-no-side-effects", self.source)

    def test_qualification_reuses_published_prompt_node(self) -> None:
        self.assertIn(
            "import { productionContext } from '../model-benchmark/aula-real-mcp-evaluation.workflow'",
            self.source,
        )
        prompt_source = PUBLISHED_PROMPT_SOURCE.read_text()
        self.assertIn("export const productionContext = node({", prompt_source)

    def test_scheduled_retry_policy_is_local_only_and_bounded(self) -> None:
        policy = json.loads(POLICY.read_text())
        self.assertEqual(policy["inference_policy"], "local-only")
        self.assertEqual(policy["model_alias"], "automation")
        self.assertEqual(policy["request"]["max_retries"], 2)
        self.assertEqual(policy["scheduled_run"]["retry_delays_minutes"], [15, 30, 60])
        self.assertEqual(policy["scheduled_run"]["failure_alert_after_minutes"], 120)
        self.assertTrue(
            policy["scheduled_run"]["preserve_input_until_success_or_terminal_failure"]
        )
        self.assertFalse(policy["scheduled_run"]["cloud_fallback"])


if __name__ == "__main__":
    unittest.main()
