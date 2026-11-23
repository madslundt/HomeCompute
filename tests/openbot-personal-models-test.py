#!/usr/bin/env python3
"""Verify the owner Local scripts update preserves agent identity and scope."""

import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class PersonalModelsTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/openbot-personal-team.json").read_bytes())
        self.targets = self.config["fast_local_agents"]
        self.operations = runpy.run_path(str(ROOT / "scripts/configure-openbot-personal-models.py"))
        self.reconcile = self.operations["reconcile"]
        self.roster = [
            {"id": f"agent-00000000-0000-0000-0000-{index:012x}", "name": agent["existing_names"][0]}
            for index, agent in enumerate(self.targets)
        ]

    def test_cli_passes_a_mode_to_the_personal_models_subcommand(self):
        command = ROOT / "scripts/setup-openbot-guest.sh"
        result = subprocess.run(
            ["bash", str(command), "personal-models", "invalid"], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Use personal-models plan, apply or rollback.", result.stderr)

    def test_missing_agent_stops_before_any_updates(self):
        request = Mock(return_value=self.roster[:-1])
        with self.assertRaises(ValueError):
            self.reconcile(self.config, request, "apply")
        request.assert_called_once_with("/v1/agents")

    def test_apply_and_rollback_scope(self):
        mutations = []

        def request(route, data=None):
            if route == "/v1/agents":
                return self.roster
            agent = route.split("/")[3]
            self.assertIn(agent, {row["id"] for row in self.roster})
            mutations.append((agent, data))
            return {"id": agent, **data}

        with contextlib.redirect_stdout(io.StringIO()):
            self.reconcile(self.config, request, "plan")
            self.assertEqual(mutations, [])
            self.reconcile(self.config, request, "apply")
            self.assertEqual([data for _, data in mutations], [self.config["model"]] * len(self.targets))
            mutations.clear()
            self.reconcile(self.config, request, "rollback")
            self.assertEqual([data for _, data in mutations], [target["rollback_model"] for target in self.targets])

    def test_sol_update_targets_only_the_requested_existing_hermes(self):
        request = Mock()
        target = "hermes-sol-partner"
        agent_id = "agent-00000000-0000-0000-0000-000000000001"
        settings = self.operations["SOL_SETTINGS"]
        request.side_effect = [[{"id": agent_id, "name": "Hermes"}], {"id": agent_id, **settings}]
        with contextlib.redirect_stdout(io.StringIO()):
            self.operations["reconcile_hermes_sol"](target, request, "apply")
        self.assertEqual(request.call_count, 2)
        request.assert_called_with(f"/v1/agents/{agent_id}/settings", settings)


if __name__ == "__main__":
    unittest.main()
