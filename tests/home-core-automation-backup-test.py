#!/usr/bin/env python3
"""Static and rendered-Compose tests for the on-demand CPU automation standby."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "control-plane" / "compose.yaml"
ENV_FILE = ROOT / "config" / "control-plane.env.example"
SCRIPT = ROOT / "scripts" / "setup-home-core-automation-backup.sh"
NORMAL_CONFIG = ROOT / "deploy" / "control-plane" / "litellm-config.yaml"
BACKUP_CONFIG = ROOT / "deploy" / "control-plane" / "litellm-config-automation-backup.yaml"


class HomeCoreAutomationBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--profile",
                "automation-backup",
                "--env-file",
                str(ENV_FILE),
                "-f",
                str(COMPOSE),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.document = json.loads(result.stdout)

    def test_standby_is_profiled_private_and_bounded(self) -> None:
        service = self.document["services"]["automation-backup"]
        self.assertEqual(service["profiles"], ["automation-backup"])
        self.assertNotIn("ports", service)
        self.assertEqual(set(service["networks"]), {"automation-backup"})
        self.assertTrue(self.document["networks"]["automation-backup"]["internal"])
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["mem_limit"], "30064771072")
        self.assertEqual(service["memswap_limit"], "30064771072")
        self.assertEqual(service["cpus"], 8.0)
        self.assertEqual(service["pids_limit"], 384)

    def test_identity_model_and_privacy_flags_are_pinned(self) -> None:
        service = self.document["services"]["automation-backup"]
        self.assertEqual(
            service["image"],
            "ghcr.io/ggml-org/llama.cpp:server@sha256:"
            "b74a168a10b13129ce8973582a5c699fadecde45945a8b8b004b79e34f4ff1ab",
        )
        command = service["command"]
        joined = " ".join(command)
        self.assertIn("Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", joined)
        self.assertIn("--reasoning off", joined)
        self.assertIn("--log-disable", command)
        self.assertIn("--no-webui", command)
        self.assertIn("--no-slots", command)
        self.assertEqual(service["secrets"][0]["source"], "compute_api_key")
        self.assertTrue(all(volume["read_only"] for volume in service["volumes"]))

    def test_download_tuple_is_exact_and_standby_defaults_off(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for expected in (
            "a483e9e6cbd595906af30beda3187c2663a1118c",
            "22134528992",
            "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61",
            "maintenance-start",
            "maintenance-stop",
        ):
            self.assertIn(expected, text)
        default = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(ENV_FILE),
                "-f",
                str(COMPOSE),
                "config",
                "--services",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        self.assertNotIn("automation-backup", default)

    def test_cold_standby_is_only_present_in_the_explicit_maintenance_config(self) -> None:
        normal = NORMAL_CONFIG.read_text(encoding="utf-8")
        maintenance = BACKUP_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("automation-backup", normal)
        self.assertRegex(
            maintenance,
            r"(?s)model: openai/automation-backup.*?order: 1.*?"
            r"model: openai/automation\s+order: 2",
        )
        litellm = self.document["services"]["litellm"]
        config_mount = next(
            mount for mount in litellm["volumes"] if mount["target"] == "/etc/litellm/config.yaml"
        )
        self.assertEqual(config_mount["source"], str(NORMAL_CONFIG))

    def test_switch_keeps_gateway_secret_out_of_process_arguments(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('open("/run/secrets/litellm_master_key"', text)
        self.assertNotRegex(text, r"python3\s+-\s+.*litellm_master_key")
        self.assertIn("switch_gateway \"$BACKUP_LITELLM_CONFIG\"", text)
        self.assertIn("switch_gateway \"$NORMAL_LITELLM_CONFIG\"", text)

    def test_standby_refuses_to_compete_with_agents_vm(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "systemctl is-active --quiet homecompute-agents-vm.service",
            text,
        )
        self.assertIn("stop the agents VM first", text)


if __name__ == "__main__":
    unittest.main()
