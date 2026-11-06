"""Ingress migration guards and preservation of the established client paths."""
import importlib.util
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertIn('keepalive off', block)

    def test_exact_previous_dashboard_route_upgrades_without_replacing_other_sites(self):
        caddy, home = self.patch()
        previous = caddy.replace('\t\t\t\t\t# Avoid stale sockets across the native forwarding chain.\n'
                                 '\t\t\t\t\tkeepalive off\n', '')
        self.assertEqual(module.patch(previous, home, self.source_caddy, self.source_home), (caddy, home))


class NativeTransferTest(unittest.TestCase):
    def test_large_wheel_uses_bounded_verified_parts_and_refuses_corruption(self):
        spec = importlib.util.spec_from_file_location('addons', ROOT / 'scripts/setup-hermes-addons.py')
        addons = importlib.util.module_from_spec(spec); spec.loader.exec_module(addons)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'synthetic.whl'
            source.write_bytes(b'synthetic-wheel-data' * 450000)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            received = []

            def native(args, data=None):
                if data is not None:
                    self.assertLessEqual(len(data), 512 * 1024)
                    received.append(len(data))
                index = args.index('-c')
                return subprocess.check_output([sys.executable, *args[index:]], input=data, stderr=subprocess.PIPE)

            with patch.object(addons, 'REMOTE', str(root / 'sandbox')), patch.object(addons, 'call', native):
                addons.upload_asset(['native'], source, expected)
                self.assertGreater(len(received), 1)
                self.assertEqual((root / 'sandbox/synthetic.whl').read_bytes(), source.read_bytes())
                addons.upload_asset(['native'], source, expected)
                (root / 'sandbox/synthetic.whl.part-0').write_bytes(b'unknown edit')
                with self.assertRaises(subprocess.CalledProcessError):
                    addons.upload_asset(['native'], source, expected)


class ConfigSerializationTest(unittest.TestCase):
    def test_root_provider_list_is_indented_and_deferred_credentials_survive(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML runtime integration also runs in the isolated OMH fixture')
        spec = importlib.util.spec_from_file_location('payload', ROOT / 'deploy/hermes/omh/configure.py')
        payload = importlib.util.module_from_spec(spec); spec.loader.exec_module(payload)
        original = {'custom_providers': [{'name': 'local', 'api_key': '${COMPATIBLE_API_KEY}'}],
                    'plugins': {'enabled': ['nemoclaw']}, 'memory': {'memory_enabled': True}}
        self.assertIn('\n- ', yaml.safe_dump(original))
        encoded = payload.encode_config(original, yaml)
        self.assertNotIn(b'\n- ', encoded)
        self.assertEqual(yaml.safe_load(encoded), original)


if __name__ == '__main__':
    unittest.main()
