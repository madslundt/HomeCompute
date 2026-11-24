#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("migration", ROOT / "scripts/migrate-private-models.py")
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class PrivateConsumerRoutingTest(unittest.TestCase):
    def workflow(self):
        return {
            "id": MIGRATION.AULA_WORKFLOW, "versionId": "active", "activeVersionId": "active",
            "nodes": [{
                "id": MIGRATION.AULA_NODE, "name": "Local Aula summary reviewer",
                "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
                "credentials": {"openAiApi": {"name": MIGRATION.AULA_CREDENTIAL}},
                "parameters": {
                    "model": {"mode": "id", "value": "automation-moe", "cachedResultName": "automation-moe"},
                    "options": {
                        "maxRetries": 0, "timeout": 90000,
                        "extraBody": '{"chat_template_kwargs":{"enable_thinking":false}}',
                    },
                },
            }],
        }

    def test_only_reviewer_alias_and_provider_override_change(self):
        workflow = self.workflow()
        ops = MIGRATION.aula_operations(workflow, "plan")
        self.assertEqual(len(ops), 1)
        operation, = ops
        self.assertEqual(operation["type"], "updateNodeParameters")
        self.assertFalse(operation["replace"])
        parameters = operation["parameters"]
        self.assertEqual(parameters["model"]["value"], "local-only")
        self.assertEqual(json.loads(parameters["options"]["extraBody"]), {})
        self.assertEqual(parameters["options"]["maxRetries"], 0)
        self.assertEqual(parameters["options"]["timeout"], 90000)
        self.assertEqual(workflow["nodes"][0]["parameters"]["model"]["value"], "automation-moe")

    def test_refuses_to_overwrite_pending_draft(self):
        workflow = self.workflow()
        workflow["versionId"] = "concurrent-draft"
        with self.assertRaises(ValueError):
            MIGRATION.aula_operations(workflow, "plan")


if __name__ == "__main__":
    unittest.main()
