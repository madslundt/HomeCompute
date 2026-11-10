"""The owner dashboard has a tailnet-only Desktop route with native auth."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'hermes_ingress', ROOT / 'scripts/setup-hermes-dashboard-ingress.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HermesDashboardIngressTest(unittest.TestCase):
    def test_unused_tailnet_port_is_available(self):
        self.assertEqual(module.serve_port_state({
            'TCP': {'18791': {'HTTPS': True}}, 'Web': {}, 'AllowFunnel': {},
        }), 'free')

    def test_exact_private_serve_route_is_idempotent(self):
        self.assertEqual(module.serve_port_state({
            'TCP': {'18796': {'HTTPS': True}},
            'Web': {'home-core.tail479ad.ts.net:18796': {
                'Handlers': {'/': {'Proxy': 'http://10.77.20.2:18804'}},
            }},
            'AllowFunnel': {},
        }), 'already-configured')

    def test_occupied_or_public_route_is_refused(self):
        states = (
            {'TCP': {'18796': {'HTTPS': True}}, 'Web': {}, 'AllowFunnel': {}},
            {'TCP': {'18796': {'HTTPS': True}},
             'Web': {'home-core.tail479ad.ts.net:18796': {
                 'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8085'}},
             }}, 'AllowFunnel': {}},
            {'TCP': {'18796': {'HTTPS': True}},
             'Web': {'home-core.tail479ad.ts.net:18796': {
                 'Handlers': {'/': {'Proxy': 'http://10.77.20.2:18804'}},
             }}, 'AllowFunnel': {'home-core.tail479ad.ts.net:18796': True}},
        )
        for state in states:
            with self.subTest(state=state), self.assertRaises(ValueError):
                module.serve_port_state(state)

    def test_host_service_uses_existing_authenticated_guest_relay(self):
        module_source = (ROOT / 'modules/nixos/hermes-dashboard.nix').read_text()
        installer_source = (ROOT / 'scripts/setup-hermes-dashboard-ingress.py').read_text()
        self.assertIn('homecompute-hermes-dashboard-serve', module_source)
        self.assertIn('--https=18796 http://10.77.20.2:18804', module_source)
        self.assertIn('tailscale serve --https=18796 off', module_source)
        self.assertIn("'homecompute-hermes-dashboard-serve.service'", installer_source)
        self.assertNotIn('--funnel', module_source.lower())


if __name__ == '__main__':
    unittest.main()
