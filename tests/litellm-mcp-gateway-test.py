#!/usr/bin/env python3
"""Guard the centralized MCP catalog, least-privilege grants, and network path."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / "deploy/control-plane/litellm-config.yaml").read_text()
COMPOSE = (ROOT / "deploy/control-plane/compose.yaml").read_text()
AUTOMATION = (ROOT / "deploy/automation/production.yaml").read_text()
CADDY = (ROOT / "deploy/control-plane/Caddyfile").read_text()
NETWORK = (ROOT / "modules/nixos/automation-network.nix").read_text()
DEPLOY = (ROOT / "scripts/deploy-home-core.sh").read_text()
DEFAULT_DENY = (ROOT / "scripts/setup-litellm-mcp-default-deny.py").read_text()


class LiteLLMMCPGatewayTests(unittest.TestCase):
    def test_only_reviewed_local_mcp_servers_are_registered(self) -> None:
        self.assertRegex(CONFIG, r"(?ms)^mcp_servers:\s*\n\s+aula:")
        self.assertIn("url: http://aula-mcp:7878/mcp", CONFIG)
        self.assertIn("url: http://tilbudstrolden-mcp:8000/mcp", CONFIG)
        self.assertEqual(len(re.findall(r"(?m)^  (?:aula|tilbudstrolden):$", CONFIG)), 2)

    def test_server_registration_is_never_public_and_uses_named_access_groups(self) -> None:
        self.assertIn("allow_all_keys: false", CONFIG)
        self.assertIn("access_groups: [aula-read]", CONFIG)
        self.assertIn("access_groups: [offers-read]", CONFIG)
        self.assertIn("require_key_mcp_access_defined: true", CONFIG)
        self.assertIn("mcp_disabled_capabilities: [prompts, resources]", CONFIG)

    def test_gateway_has_dedicated_internal_client_and_backend_networks(self) -> None:
        self.assertIn("mcp-web:", COMPOSE)
        self.assertIn("mcp-clients:", COMPOSE)
        self.assertIn("mcp-backends:", COMPOSE)
        self.assertIn("LITELLM_MCP_BACKENDS_IP", COMPOSE)
        self.assertIn("mcp-clients:", AUTOMATION)
        self.assertIn("mcp-backends:", AUTOMATION)
        self.assertIn('name: homecompute-control-plane_mcp-clients', AUTOMATION)
        self.assertIn('name: homecompute-control-plane_mcp-backends', AUTOMATION)

    def test_private_front_door_and_backend_firewall_are_present(self) -> None:
        self.assertIn("{$MCP_FQDN}", CADDY)
        self.assertIn("import litellm_proxy", CADDY)
        self.assertIn("HC-MCP-BACKENDS", NETWORK)
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.3/32 -p tcp --dport 7878", NETWORK)
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.4/32 -p tcp --dport 8000", NETWORK)
        self.assertIn("-j REJECT", NETWORK)

    def test_existing_unscoped_keys_are_denied_before_caddy_starts(self) -> None:
        self.assertIn("/key/list", DEFAULT_DENY)
        self.assertIn("'no-mcp-servers'", DEFAULT_DENY)
        self.assertIn("/key/update", DEFAULT_DENY)
        self.assertIn("setup-litellm-mcp-default-deny.py", DEPLOY)
        self.assertLess(DEPLOY.index("setup-litellm-mcp-default-deny.py"), DEPLOY.index('"${gateway[@]}" up -d --wait --wait-timeout 180\n'))
        self.assertNotIn("print(json.dumps({'key':", DEFAULT_DENY)

    def test_gateway_configuration_does_not_add_a_second_mcp_service(self) -> None:
        self.assertNotIn("mcphub:", COMPOSE.lower())
        self.assertNotIn("MCPHUB_", COMPOSE)


if __name__ == "__main__":
    unittest.main()
