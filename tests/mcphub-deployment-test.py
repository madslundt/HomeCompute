#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class McpHubDeploymentTests(unittest.TestCase):
    def test_only_existing_http_servers_are_declared_and_no_group_is_published(self):
        settings = json.loads((ROOT / "config/mcphub-settings.json").read_text())
        self.assertEqual(set(settings), {"mcpServers"})
        self.assertEqual(set(settings["mcpServers"]), {"aula", "tilbudstrolden"})
        self.assertNotIn("groups", settings)
        for server in settings["mcpServers"].values():
            self.assertEqual(server["type"], "streamable-http")
            self.assertEqual(server["owner"], "admin")
            self.assertTrue(server["enabled"])
            self.assertNotIn("command", server)
            self.assertNotIn("env", server)
            self.assertTrue(server["url"].startswith("http://"))

    def test_source_only_provides_narrow_gateway_network_memberships(self):
        control = (ROOT / "deploy/control-plane/compose.yaml").read_text()
        automation = (ROOT / "deploy/automation/production.yaml").read_text()
        self.assertIn("name: homecompute-mcp-clients", control)
        self.assertIn("name: homecompute-mcp-backends", control)
        self.assertIn("name: homecompute-mcp-web", control)
        self.assertIn("name: homecompute-mcp-state", control)
        self.assertIn("../../config/mcphub-settings.json:/etc/mcphub/mcp_settings.json:ro", control)
        self.assertIn("mcphub_clients:\n    external: true", automation)
        self.assertIn("mcphub_backends:\n    external: true", automation)
        self.assertNotIn("state: {}", control.split("  mcphub:\n", 1)[1].split("\n  automation-backup:", 1)[0])
        self.assertNotIn("/var/run/docker.sock", control)
        self.assertNotIn("privileged: true", control)

    def test_gateway_auth_privacy_and_backend_firewall_are_source_owned(self):
        setup = (ROOT / "scripts/configure-mcphub.py").read_text()
        self.assertIn('"enableBearerAuth": True', setup)
        self.assertIn('"enableGlobalRoute": False', setup)
        self.assertIn('"allowAuthorizationPassthrough": False', setup)
        self.assertIn('"storeToolPayload": False', setup)
        policy = (ROOT / "modules/nixos/automation-network.nix").read_text()
        self.assertIn("HC-MCP-BACKENDS", policy)
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.3/32 -p tcp --dport 7878 -j RETURN", policy)
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.4/32 -p tcp --dport 8000 -j RETURN", policy)

    def test_host_deployment_provisions_dedicated_database_and_security_settings(self):
        deploy = (ROOT / "scripts/deploy-home-core.sh").read_text()
        self.assertIn("provision-mcphub-postgres.sh", deploy)
        self.assertIn("configure-mcphub.py", deploy)
        sql = (ROOT / "deploy/control-plane/mcphub-postgres.sql").read_text()
        self.assertIn("CREATE DATABASE mcphub", sql)
        self.assertIn(":'mcphub_password'", sql)
        self.assertNotRegex(sql, r"PASSWORD '[a-f0-9]{48}'")


if __name__ == "__main__":
    unittest.main()
