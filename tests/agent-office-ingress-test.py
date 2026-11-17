"""The retired office installer cannot restore its route or Homepage card."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('office_ingress', ROOT / 'scripts/setup-agent-office-ingress.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
retire_spec = importlib.util.spec_from_file_location(
    'retirement', ROOT / 'scripts/retire-agent-dashboard-hosting.py')
retirement = importlib.util.module_from_spec(retire_spec)
retire_spec.loader.exec_module(retirement)


class RetiredIngressTest(unittest.TestCase):
    def test_hermes_route_remains_without_openclaw_observer_proxy(self):
        caddy = (ROOT / 'deploy/control-plane/Caddyfile').read_text()
        homepage = (ROOT / 'deploy/homepage/config/services.yaml').read_text()

        self.assertIn('agents.home.arpa {', caddy)
        self.assertIn('handle_path /hermes/*', caddy)
        self.assertIn('respond "Not found" 404', caddy)
        self.assertNotIn('reverse_proxy 172.28.200.1:18802', caddy)
        self.assertNotIn('homecore-agent-office', homepage)
        self.assertNotIn('homecore-openclaw', homepage)
        self.assertNotIn('172.28.200.1:18792', caddy)
        host = (ROOT / 'hosts/home-core/default.nix').read_text()
        self.assertNotIn('homecompute.agentOffice.enable = true', host)
        self.assertNotIn('homecompute-openclaw-dashboard-', host)
        self.assertNotIn('hc-openclaw-dashboard', host)
        self.assertNotIn('    - Agent office:', homepage)
        self.assertIn('href: https://agents.home.arpa/hermes/', homepage)

    def test_old_installer_fails_closed_after_retirement(self):
        caddy = (ROOT / 'deploy/control-plane/Caddyfile').read_text()
        homepage = (ROOT / 'deploy/homepage/config/services.yaml').read_text()
        with self.assertRaisesRegex(ValueError, 'canonical-office-source-shape'):
            m.office_patch('', '', caddy, homepage)


    def test_scoped_retirement_preserves_other_live_configuration(self):
        caddy = (ROOT / 'deploy/control-plane/Caddyfile').read_text()
        homepage = (ROOT / 'deploy/homepage/config/services.yaml').read_text()
        caddy += '\n# Independent live route\n'
        homepage += '\n- Other:\n    - Example:\n        href: https://other.home.arpa/\n'

        native = ('192.168.30.122 {\n\ttls internal\n\tencode zstd gzip\n'
                  '\treverse_proxy 172.28.200.1:18792\n}\n\n')
        old_caddy = native + caddy.replace(
            '\t\thandle {\n\t\t\trespond "Not found" 404\n\t\t}\n',
            retirement.OFFICE_HANDLE, 1)
        old_homepage = homepage.replace(
            '- AI assistants:\n', '- AI assistants:\n'
            '    - Agent office:\n        id: homecore-agent-office\n'
            '        href: https://agents.home.arpa/\n\n'
            '    - OpenClaw:\n        id: homecore-openclaw\n'
            '        href: https://192.168.30.122/\n\n', 1)
        self.assertEqual(retirement.patch(old_caddy, old_homepage, caddy, homepage),
                         (caddy, homepage))
        self.assertEqual(retirement.patch(caddy, homepage, caddy, homepage),
                         (caddy, homepage))
        with self.assertRaisesRegex(ValueError, 'unrecognized Agent Office route'):
            retirement.patch(old_caddy.replace('response_header_timeout 10s',
                                               'response_header_timeout 11s'),
                             old_homepage, caddy, homepage)


if __name__ == '__main__':
    unittest.main()
