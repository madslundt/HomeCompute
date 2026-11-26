#!/usr/bin/env python3
"""Verify the owner Local scripts update preserves agent identity and scope."""

import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_owner_sol_command_refuses_an_unknown_mode(self):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/setup-openbot-guest.sh"), "hermes-sol", "invalid"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Use hermes-sol plan, apply or rollback.", result.stderr)

    def test_catalog_registers_only_missing_providers_and_reuses_the_scoped_key(self):
        configure = self.operations["configure_owner_chatgpt_providers"]
        providers = self.operations["HERMES_CHATGPT_PROVIDERS"]
        aliases = sorted(provider["default_model"] for provider in providers.values())
        configured = {
            "homecompute-sol": providers["homecompute-sol"],
            "homecompute-private": {"default_model": "local-only"},
        }
        writes = []

        def cli(args, **kwargs):
            if args[2] == "get":
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(configured))
            self.assertEqual(args[2], "set")
            self.assertNotIn("sk-fixture", args[-1])
            name = args[3].removeprefix("providers.")
            configured[name] = json.loads(args[-1])
            writes.append(name)
            return subprocess.CompletedProcess(args, 0, stdout="")

        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.StringIO(
            json.dumps({"data": [{"id": alias} for alias in aliases]})
        )
        install_key = Mock()
        with patch.dict(configure.__globals__, {"build_opener": Mock(return_value=opener),
                                               "install_sol_key": install_key}), \
             patch.object(subprocess, "run", side_effect=cli):
            def run(mode):
                with patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b"sk-fixture\n"))):
                    return configure(mode)

            plan = run("plan")
            self.assertEqual(plan["configured"], ["chatgpt-sol-latest"])
            self.assertEqual(plan["to_add"], ["chatgpt-astra-latest", "chatgpt-luna-latest", "chatgpt-terra-latest"])
            self.assertEqual(writes, [])
            install_key.assert_not_called()

            self.assertEqual(run("apply")["configured"], aliases)
            self.assertEqual(writes, ["homecompute-luna", "homecompute-terra", "homecompute-astra"])
            self.assertEqual(configured["homecompute-private"], {"default_model": "local-only"})
            install_key.assert_called_once_with(self.operations["HERMES_ENV"], b"sk-fixture")
            self.assertEqual(run("apply")["to_add"], [])
            self.assertEqual(len(writes), 3)

    def test_catalog_refuses_missing_alias_before_any_write(self):
        configure = self.operations["configure_owner_chatgpt_providers"]
        cli = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="{}"))
        opener = Mock()
        opener.open.return_value = io.StringIO('{"data": [{"id": "chatgpt-sol-latest"}]}')
        install_key = Mock()
        with patch.dict(configure.__globals__, {"build_opener": Mock(return_value=opener),
                                               "install_sol_key": install_key}), \
             patch.object(subprocess, "run", cli), \
             patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b"sk-fixture\n"))):
            with self.assertRaisesRegex(ValueError, "exactly the four"):
                configure("apply")
        self.assertEqual(cli.call_count, 1)
        install_key.assert_not_called()

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


    def test_owner_sol_uses_the_native_hermes_route_and_can_restore_open_code(self):
        agent_id = self.operations["OWNER_HERMES_ID"]
        roster = [{"id": agent_id, "name": "Hermes"}]
        expected = {
            "provider": "hermes", "model": "custom:homecompute-sol:chatgpt-sol-latest",
            "reasoningEffort": "high",
        }
        prior = {
            "provider": "opencode", "model": "homecompute-chatgpt/chatgpt-sol-latest",
            "reasoningEffort": "high",
        }
        target = "hermes-sol-owner"
        request = Mock(side_effect=[roster, {"id": agent_id, **expected}, roster, {"id": agent_id, **prior}])
        setup = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            self.operations["reconcile_hermes_sol"](target, request, "apply", setup)
            setup.assert_called_once_with()
            request.assert_called_with(f"/v1/agents/{agent_id}/settings", expected)
            self.operations["reconcile_hermes_sol"](target, request, "rollback", setup)
            setup.assert_called_once_with()
            request.assert_called_with(f"/v1/agents/{agent_id}/settings", prior)

    def test_owner_sol_stops_before_binding_when_provider_setup_fails(self):
        agent_id = self.operations["OWNER_HERMES_ID"]
        request = Mock(return_value=[{"id": agent_id, "name": "Hermes"}])
        setup = Mock(side_effect=ValueError("unexpected provider entry"))
        with self.assertRaises(ValueError):
            self.operations["reconcile_hermes_sol"]("hermes-sol-owner", request, "apply", setup)
        request.assert_called_once_with("/v1/agents")

    def test_sol_key_install_updates_only_its_private_environment_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.touch(mode=0o600)
            path.write_bytes(b"OTHER_SETTING=fixture\n")
            install = self.operations["install_sol_key"]
            install(path, b"sk-fixture")
            install(path, b"sk-fixture-rotated")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_bytes(), b"OTHER_SETTING=fixture\nHOME_OPENBOT_CHATGPT_API_KEY=sk-fixture-rotated\n")


if __name__ == "__main__":
    unittest.main()
