#!/usr/bin/env python3
"""Verify the owner Local scripts update preserves agent identity and scope."""

import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PersonalModelsTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/openbot-personal-team.json").read_bytes())
        self.targets = self.config["fast_local_agents"]
        self.reconcile = runpy.run_path(str(ROOT / "scripts/configure-openbot-personal-models.py"))["reconcile"]
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
        effort = Mock()
        with patch.dict(self.reconcile.__globals__, {"set_embedded_effort": effort}):
            with self.assertRaises(ValueError):
                self.reconcile(self.config, request, "apply")
        request.assert_called_once_with("/v1/agents")
        effort.assert_not_called()

    def test_apply_and_rollback_scope(self):
        mutations = []

        def request(route, data=None):
            if route == "/v1/agents":
                return self.roster
            agent = route.split("/")[3]
            self.assertIn(agent, {row["id"] for row in self.roster})
            mutations.append((agent, data))
            return {"id": agent, **data}

        effort = Mock()
        with (contextlib.redirect_stdout(io.StringIO()),
              patch.dict(self.reconcile.__globals__, {"set_embedded_effort": effort})):
            self.reconcile(self.config, request, "plan")
            self.assertEqual(mutations, [])
            self.reconcile(self.config, request, "apply")
            self.assertEqual([data for _, data in mutations], [self.config["model"]] * 3)
            mutations.clear()
            self.reconcile(self.config, request, "rollback")
            self.assertEqual([data for _, data in mutations], [target["rollback_model"] for target in self.targets])
        self.assertEqual([call.args for call in effort.call_args_list], [("none",), ("xhigh",)])


if __name__ == "__main__":
    unittest.main()
