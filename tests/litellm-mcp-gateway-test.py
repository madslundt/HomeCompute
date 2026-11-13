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
    def test_reviewed_local_and_hosted_mcp_servers_are_registered(self) -> None:
        self.assertRegex(CONFIG, r"(?ms)^mcp_servers:\s*\n\s+context7:")
        self.assertIn("url: https://mcp.context7.com/mcp", CONFIG)
        self.assertIn("url: http://n8n:5678/mcp-server/http", CONFIG)
        self.assertEqual(CONFIG.count("url: https://homecompute-capabilities.home.arpa:19445/mcp"), 3)
        self.assertIn("url: http://aula-mcp:7878/mcp", CONFIG)
        aula = CONFIG.split("  aula:", 1)[1].split("  tilbudstrolden:", 1)[0]
        self.assertNotIn("allowed_tools:", aula)
        self.assertNotIn("disallowed_tools:", aula)
        aula_patch = (ROOT / "deploy/automation/aula-n8n.patch").read_text()
        attachment_patch = (ROOT / "deploy/automation/aula-attachment-extract.patch").read_text()
        for tool in ("aula_messages_get_attachment", "aula_posts_get_attachment", "aula_utils_extract_pdf_text"):
            self.assertIn(tool, aula_patch)
        self.assertIn("aula_utils_extract_attachment_text", attachment_patch)
        self.assertIn("'catdoc'", attachment_patch)
        self.assertIn("'xls2csv'", attachment_patch)
        self.assertIn("'catppt'", attachment_patch)
        self.assertIn("catdoc=1:0.95-6~deb12u1", (ROOT / "deploy/automation/aula.Dockerfile").read_text())
        self.assertIn("setup-litellm-harness-mcp-access.py", DEPLOY)
        harness_access = (ROOT / "scripts/setup-litellm-harness-mcp-access.py").read_text()
        for alias in ("hermes-desktop-mcp", "hermes-agent", "openbot-agent", "opencode-home-core"):
            self.assertIn(alias, harness_access)
        self.assertIn("mcp_access_groups", harness_access)
        self.assertIn("'all-harness-mcps'", harness_access)
        self.assertIn("'no-mcp-servers'", harness_access)
        self.assertNotIn("print(master", harness_access)
        for group in ("docs-public", "n8n-owner", "hermes-owner-tools",
                      "openclaw-agent-tools", "openbot-android-bounded-ui",
                      "aula-read", "offers-read"):
            self.assertIn(f"access_groups: [{group}, all-harness-mcps]", CONFIG)
        self.assertIn("url: http://tilbudstrolden-mcp:8000/mcp", CONFIG)
        server_names = re.findall(r"(?m)^  ([a-zA-Z0-9_-]+):$", CONFIG[CONFIG.index("mcp_servers:"):])
        self.assertEqual(len(server_names), 7)
        self.assertTrue(all(re.fullmatch(r"[a-zA-Z0-9_]+", name) for name in server_names))
        self.assertEqual(len(re.findall(r"(?m)^      - (?:[a-z0-9_]+)$", CONFIG[CONFIG.index("  n8n:"):CONFIG.index("  homecompute_hermes:")])), 55)
        self.assertIn("auth_value: os.environ/N8N_MCP_TOKEN", CONFIG)

    def test_server_registration_is_never_public_and_uses_named_access_groups(self) -> None:
        self.assertIn("allow_all_keys: false", CONFIG)
        self.assertEqual(CONFIG.count("all-harness-mcps]"), 7)
        self.assertEqual(len(re.findall(r"(?m)^      - homecompute_android_[a-z_]+$", CONFIG[CONFIG.index("  homecompute_openbot_android:"):CONFIG.index("  aula:")])), 10)
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
        self.assertIn("com.docker.network.bridge.name: br-hc-mcpcl", COMPOSE)
        self.assertLessEqual(len("br-hc-mcpcl"), 15)
        self.assertIn("N8N_MCP_TOKEN_FILE", COMPOSE)
        self.assertIn("n8n_mcp_token", COMPOSE)
        self.assertIn("homecompute-capabilities.home.arpa:10.77.20.1", COMPOSE)
        self.assertIn("home-core-root.crt", COMPOSE)
        self.assertIn("/etc/homecompute/control-plane/home-core-root.crt:/run/homecompute/home-core-root.crt:ro", COMPOSE)
        self.assertIn("install -D -m 0644", DEPLOY)
        self.assertIn("/etc/homecompute/control-plane/home-core-root.crt", DEPLOY)
        self.assertIn("N8N_MCP_TOKEN", (ROOT / "deploy/control-plane/litellm-entrypoint.sh").read_text())
        self.assertIn("SSL_CERT_FILE", (ROOT / "deploy/control-plane/litellm-entrypoint.sh").read_text())

    def test_private_front_door_and_backend_firewall_are_present(self) -> None:
        self.assertIn("{$MCP_FQDN}", CADDY)
        self.assertIn("import litellm_proxy", CADDY)
        self.assertIn("HC-MCP-BACKENDS", NETWORK)
        self.assertIn("172.28.200.3/32", (ROOT / "scripts/setup-native-capabilities.py").read_text())
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.3/32 -p tcp --dport 7878", NETWORK)
        self.assertIn("-s 172.28.212.2/32 -d 172.28.212.4/32 -p tcp --dport 8000", NETWORK)
        self.assertIn("-j REJECT", NETWORK)

    def test_existing_unscoped_keys_are_denied_before_caddy_starts(self) -> None:
        self.assertIn("/key/list", DEFAULT_DENY)
        self.assertIn("item.get('token')", DEFAULT_DENY)
        self.assertNotIn("item.get('token_id')", DEFAULT_DENY)
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
