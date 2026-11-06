#!/usr/bin/env python3
"""Keep the context listener off the established OpenClaw Sites port."""
import ast
import importlib.util
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import assistant_context_api
import assistant_context_tools

recovery_spec = importlib.util.spec_from_file_location("application_recovery", ROOT / "scripts/application-recovery.py")
recovery = importlib.util.module_from_spec(recovery_spec)
assert recovery_spec.loader is not None
sys.modules[recovery_spec.name] = recovery
recovery_spec.loader.exec_module(recovery)

sync_spec = importlib.util.spec_from_file_location("assistant_context_sync", ROOT / "scripts/assistant-context-sync.py")
sync = importlib.util.module_from_spec(sync_spec)
assert sync_spec.loader is not None
sync_spec.loader.exec_module(sync)

bridge_spec = importlib.util.spec_from_file_location("assistant_context_bridge", ROOT / "scripts/setup-assistant-context-bridge.py")
bridge = importlib.util.module_from_spec(bridge_spec)
assert bridge_spec.loader is not None
sys.modules[bridge_spec.name] = bridge
bridge_spec.loader.exec_module(bridge)


def assigned_integer(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = ast.literal_eval(node.value)
            if type(value) is int:
                return value
    raise AssertionError(f"missing integer source declaration: {name}")


class AssistantContextPortTests(unittest.TestCase):
    def test_context_route_is_consistent_and_separate_from_existing_sites(self):
        context_port = assistant_context_api.DEFAULT_CONTEXT_PORT
        site_port = assigned_integer(ROOT / "scripts/openclaw-site-service.py", "OPENCLAW_SITES_PORT")
        self.assertEqual(context_port, 18826)
        self.assertEqual(site_port, 18816)
        self.assertNotEqual(context_port, site_port)
        self.assertEqual(assistant_context_api.serve.__defaults__[0], ("127.0.0.1", context_port))
        self.assertEqual(urlsplit(assistant_context_tools.ORIGIN).port, context_port)
        self.assertEqual(urlsplit(sync.INGEST_BASE).port, context_port)
        self.assertEqual(bridge.CONTEXT_ENDPOINT, ("127.0.0.1", context_port))
        self.assertIn(context_port, recovery.MAIN_HTTP_PORTS)
        self.assertIn(site_port, recovery.MAIN_HTTP_PORTS)

    def test_nix_service_health_and_private_routine_proxy_use_context_port(self):
        nix = (ROOT / "modules/nixos/assistant-context.nix").read_text()
        self.assertIn("contextPort = 18826;", nix)
        self.assertIn("--port ${toString contextPort}", nix)

        policy_health = (ROOT / "scripts/assistant_context_improvements.py").read_text()
        self.assertIn('HEALTH="http://127.0.0.1:18826/v1/health"', policy_health)

        caddy = (ROOT / "deploy/openclaw/communication/Caddyfile").read_text()
        routine = caddy.split("@assistantRoutine", 1)[1].split("@bounded", 1)[0]
        self.assertIn("reverse_proxy 127.0.0.1:18826", routine)
        self.assertNotIn("127.0.0.1:18816", routine)


if __name__ == "__main__":
    unittest.main()
