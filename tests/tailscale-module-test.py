#!/usr/bin/env python3
"""Evaluate the private Daythread Serve unit without building a NixOS system."""

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("nix-instantiate"), "Nix evaluation requires nix-instantiate")
class PrivateDaythreadServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = json.dumps(str(ROOT / "modules/nixos/tailscale.nix"))
        expression = (
            f"import {module} {{ "
            'config.services.tailscale.port = 41641; '
            'pkgs.tailscale = "/test/tailscale"; }'
        )
        cls.configuration = json.loads(subprocess.check_output(
            ["nix-instantiate", "--eval", "--strict", "--json", "--expr", expression],
            text=True,
        ))
        cls.unit = cls.configuration["systemd"]["services"]["homecompute-daythread-tailscale"]

    def test_listener_is_private_and_uses_the_existing_application(self):
        command = shlex.split(self.unit["serviceConfig"]["ExecStart"])
        self.assertEqual(command[:2], ["/test/tailscale/bin/tailscale", "serve"])
        self.assertIn("--https=18787", command)
        self.assertEqual(command[-1], "http://192.168.30.122:8787")
        self.assertNotIn("funnel", command)
        self.assertNotIn("--service", " ".join(command))

    def test_boot_restores_the_background_listener(self):
        self.assertIn("--bg", shlex.split(self.unit["serviceConfig"]["ExecStart"]))
        self.assertEqual(self.unit["serviceConfig"]["Type"], "oneshot")
        self.assertTrue(self.unit["serviceConfig"]["RemainAfterExit"])
        self.assertIn("tailscaled.service", self.unit["after"])
        self.assertIn("docker.service", self.unit["after"])
        self.assertIn("multi-user.target", self.unit["wantedBy"])

    def test_stopping_daythread_preserves_other_serve_and_funnel_routes(self):
        command = shlex.split(self.unit["serviceConfig"]["ExecStop"])
        self.assertEqual(command, [
            "/test/tailscale/bin/tailscale", "serve", "--yes", "--https=18787", "off",
        ])

    def test_node_identity_and_existing_subnet_settings_are_preserved(self):
        settings = self.configuration["services"]["tailscale"]
        self.assertEqual(settings["extraSetFlags"], [
            "--advertise-exit-node", "--advertise-routes=192.168.30.0/24",
        ])
        self.assertFalse(settings["openFirewall"])
        self.assertEqual(self.configuration["networking"]["firewall"]["allowedUDPPorts"], [41641])


if __name__ == "__main__":
    unittest.main()
