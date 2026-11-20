#!/usr/bin/env python3
"""Check scoped gateway rendering and T3 Code's private routing boundary."""

import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t3code_access", ROOT / "scripts/setup-t3code-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
CANONICAL = (ROOT / "deploy/control-plane/Caddyfile").read_text()
CURRENT = "example.home.arpa {\n\trespond 200\n}\n\nhttp://:8080 {\n\trespond 404\n}\n"


class AccessTests(unittest.TestCase):
    def test_scope_preserves_the_existing_gateway(self):
        updated = access.add_route(CURRENT, CANONICAL)
        self.assertIn("redir https://t3code.home.arpa{uri} 308", updated)
        self.assertIn("reverse_proxy t3code:3773", updated)
        self.assertIn("tls internal", updated)
        self.assertIn("remote_ip 192.168.30.0/24 192.168.10.0/24 100.64.0.0/10", updated)
        self.assertIn('respond "Forbidden" 403', updated)
        restored = re.sub(r"^\t@t3code host .*?^\t\}\n", "", updated, flags=re.M | re.S)
        self.assertEqual(restored.split("\n\nt3code.home.arpa", 1)[0] + "\n", CURRENT)
        self.assertEqual(access.add_route(updated, CANONICAL), updated)

    def test_refuses_conflicting_route(self):
        updated = access.add_route(CURRENT, CANONICAL)
        with self.assertRaisesRegex(ValueError, "differs"):
            access.add_route(updated.replace("t3code:3773", "other-service:80"), CANONICAL)


if __name__ == "__main__":
    unittest.main()
