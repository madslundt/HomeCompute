#!/usr/bin/env python3
"""Check the private PodWave route and release wiring."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PodWaveDeploymentTests(unittest.TestCase):
    def test_caddy_routes_podwave_without_basic_auth(self):
        caddyfile = (ROOT / "deploy/control-plane/Caddyfile").read_text()
        site = caddyfile.split("podwave.home.arpa {\n", 1)[1].split("\n}", 1)[0]
        self.assertIn("tls internal", site)
        self.assertIn("reverse_proxy podwave:8080", site)
        self.assertNotIn("basic_auth", site)
        self.assertNotIn("PODWAVE_AUTH_HASH", caddyfile)

    def test_full_release_does_not_provision_or_load_podwave_credentials(self):
        compose = (ROOT / "deploy/control-plane/compose.yaml").read_text()
        deploy = (ROOT / "scripts/deploy-home-core.sh").read_text()
        self.assertNotIn("/srv/state/podwave-auth/auth.env", compose)
        self.assertNotIn("setup-podwave-auth.py", deploy)


if __name__ == "__main__":
    unittest.main()
