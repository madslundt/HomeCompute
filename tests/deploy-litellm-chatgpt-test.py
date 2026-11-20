#!/usr/bin/env python3

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "deploy_litellm_chatgpt", ROOT / "scripts/deploy-litellm-chatgpt.py",
)
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)
SOURCE = (ROOT / DEPLOY.CONFIG).read_text()
ASTRA = DEPLOY.subscription_entry(DEPLOY.MODEL_LIST.search(SOURCE)[0], "astra")[0]
BASELINE = SOURCE.replace(ASTRA, "").replace("gpt-6.2-sol", "gpt-6.1-sol")


class ChatGPTConfigTests(unittest.TestCase):
    def test_updates_only_sol_and_astra_against_running_source(self):
        baseline = BASELINE.replace("timeout: 600", "timeout: 321")
        baseline = baseline.replace("  dinero:\n", "  existing-mcp:\n")
        expected = SOURCE.replace("timeout: 600", "timeout: 321")
        expected = expected.replace("  dinero:\n", "  existing-mcp:\n")

        self.assertEqual(DEPLOY.render_aliases(baseline, SOURCE), expected)

    def test_already_applied_is_idempotent(self):
        self.assertEqual(DEPLOY.render_aliases(SOURCE, SOURCE), SOURCE)

    def test_rejects_a_duplicate_or_unexpected_subscription_entry(self):
        for source in (SOURCE.replace(ASTRA, ASTRA * 2),
                       SOURCE.replace("chatgpt/gpt-6.2-astra", "openai/gpt-6.2-astra")):
            with self.subTest(source=source.count(ASTRA)):
                with self.assertRaisesRegex(ValueError, "chatgpt-astra-latest"):
                    DEPLOY.render_aliases(BASELINE, source)

    def test_recreates_only_litellm_from_the_local_image(self):
        with patch.object(DEPLOY, "compose") as compose, \
             patch.object(DEPLOY, "inspection", return_value={"Image": "pinned"}), \
             patch.object(DEPLOY, "verify_container", return_value="checksum"):
            self.assertEqual(DEPLOY.recreate("release", "config", "pinned"), "checksum")
        compose.assert_called_once_with(
            "release", "config", "up", "-d", "--no-deps", "--no-build",
            "--pull", "never", "--wait", "--wait-timeout", "180", "litellm", timeout=240,
        )

    def test_failed_recreation_restores_previous_config(self):
        with patch.object(DEPLOY, "recreate", side_effect=[RuntimeError("unhealthy"), "checksum"]) as create:
            with self.assertRaisesRegex(RuntimeError, "prior config restored"):
                DEPLOY.change_config("release", "old", "new", "image")
        self.assertEqual(create.call_args_list, [
            call("release", "new", "image", reconcile_legacy_key=True),
            call("release", "old", "image", reconcile_legacy_key=True),
        ])

    def test_reasoning_scope_preserves_the_rest_of_the_running_config(self):
        baseline = BASELINE.replace("      supports_reasoning: true\n", "", 1)
        expected = BASELINE
        self.assertEqual(DEPLOY.render_automation_reasoning(baseline, SOURCE), expected)
        self.assertEqual(DEPLOY.render_automation_reasoning(expected, SOURCE), expected)

    def test_reasoning_scope_skips_legacy_key_reconciliation(self):
        with patch.object(DEPLOY, "compose"), \
             patch.object(DEPLOY, "inspection", return_value={"Image": "pinned"}), \
             patch.object(DEPLOY, "verify_container", return_value="checksum"), \
             patch.object(DEPLOY, "run") as run, \
             patch.object(DEPLOY.Path, "exists", return_value=True):
            DEPLOY.recreate("release", "config", "pinned", reconcile_legacy_key=False)
        run.assert_not_called()

    def test_reasoning_scope_rejects_other_automation_changes(self):
        baseline = SOURCE.replace("      supports_reasoning: true\n", "", 1)
        for candidate in (baseline, SOURCE.replace("      timeout: 600", "      timeout: 321", 1)):
            with self.subTest(candidate_has_flag="supports_reasoning: true" in candidate):
                with self.assertRaisesRegex(ValueError, "reasoning support"):
                    DEPLOY.render_automation_reasoning(baseline, candidate)


if __name__ == "__main__":
    unittest.main()
