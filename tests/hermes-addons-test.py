"""Ingress migration guards and preservation of the established client paths."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hermes_ingress', ROOT / 'scripts/setup-hermes-dashboard-ingress.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class DashboardIngressTest(unittest.TestCase):
    def setUp(self):
        self.source_caddy = (ROOT / 'deploy/control-plane/Caddyfile').read_text()
        self.source_home = (ROOT / 'deploy/homepage/config/services.yaml').read_text()
        self.caddy = 'http://:8080 {\n\trespond "existing"\n}\n\nmattermost.home.arpa {\n\treverse_proxy existing:9443\n}\n' + module.LEGACY_OFFICE
        self.home = ('- AI assistants:\n    - Hermes:\n        id: homecore-hermes\n'
                     '        icon: mdi-robot-happy-outline\n'
                     '        href: https://mattermost.home.arpa/homecompute/messages/@hermes-agent\n'
                     '        description: Personal Hermes assistant in Mattermost\n\n'
                     '    - Mattermost:\n        href: https://mattermost.home.arpa/\n')

    def patch(self, caddy=None, home=None):
        return module.patch(caddy or self.caddy, home or self.home, self.source_caddy, self.source_home)

    def test_existing_chat_links_and_routes_survive_dashboard_migration(self):
        caddy, home = self.patch()
        self.assertIn('reverse_proxy existing:9443', caddy)
        self.assertIn('href: https://mattermost.home.arpa/', home)
        self.assertIn('href: https://agents.home.arpa/hermes/', home)
        self.assertEqual(home.count('    - Hermes:'), 1)
        self.assertEqual(module.patch(caddy, home, self.source_caddy, self.source_home), (caddy, home))

    def test_unknown_route_or_operator_card_refused(self):
        with self.assertRaises(ValueError):
            self.patch(caddy=self.caddy.replace('172.28.200.1:18802', 'unknown:18802'))
        with self.assertRaises(ValueError):
            self.patch(home=self.home.replace('Personal Hermes assistant in Mattermost', 'Personal edit'))

    def test_private_route_preserves_public_host_for_native_authentication(self):
        caddy, _ = self.patch()
        block = caddy.split('handle_path /hermes/* {')[-1].split('\n\t\thandle {')[0]
        self.assertIn('reverse_proxy 172.28.200.1:18805', block)
        self.assertIn('remote_ip 192.168.30.0/24 192.168.10.0/24 100.64.0.0/10', caddy)
        self.assertIn('header_up X-Forwarded-Prefix /hermes', block)
        self.assertNotIn('header_up Host', block)
        self.assertNotIn('Authorization', block)


if __name__ == '__main__':
    unittest.main()
