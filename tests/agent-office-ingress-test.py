"""Scoped patching preserves unrelated live routes, cards and file bind mounts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('office_ingress',ROOT/'scripts/setup-agent-office-ingress.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class IngressTest(unittest.TestCase):
    def setUp(self):
        self.source_caddy=(ROOT/'deploy/control-plane/Caddyfile').read_text()
        self.source_home=(ROOT/'deploy/homepage/config/services.yaml').read_text()
        self.caddy='http://:8080 {\n\trespond "Existing"\n}\n\n192.168.30.122 {\n\treverse_proxy existing-native:18791\n}\n'
        self.home='- AI assistants:\n    - OpenClaw:\n        href: https://192.168.30.122/\n\n- Other:\n    - Retained:\n        description: Private native link\n'

    def test_only_office_route_and_card_added_and_idempotent(self):
        c,h=m.office_patch(self.caddy,self.home,self.source_caddy,self.source_home)
        self.assertIn('reverse_proxy existing-native:18791',c)
        self.assertIn('description: Private native link',h)
        self.assertIn('href: https://192.168.30.122/',h)
        self.assertIn('header_up X-HomeCompute-Office-Origin https://agents.home.arpa',c)
        self.assertIn('homecore-agent-office',h)
        self.assertEqual(m.office_patch(c,h,self.source_caddy,self.source_home),(c,h))
        self.assertNotIn('Mads\' Hermes',h)

    def test_conflicting_routes_cards_and_listener_shapes_refused(self):
        for c,h in ((self.caddy+'agents.home.arpa {\nrespond "unknown"\n}\n',self.home),
                    (self.caddy,self.home+'    - Agent office:\n        href: https://other.invalid/\n'),
                    (self.caddy.replace('8080','8081'),self.home),
                    (self.caddy,self.home.replace('AI assistants','Other assistants'))):
            with self.assertRaises(ValueError):m.office_patch(c,h,self.source_caddy,self.source_home)

    def test_exact_previous_route_upgrades_to_the_existing_trusted_default_lan(self):
        c,h=m.office_patch(self.caddy,self.home,self.source_caddy,self.source_home)
        legacy=c.replace('192.168.30.0/24 192.168.10.0/24 100.64.0.0/10','192.168.30.0/24 100.64.0.0/10')
        self.assertEqual(m.office_patch(legacy,h,self.source_caddy,self.source_home),(c,h))
        self.assertIn('-s 192.168.10.0/24 -j RETURN',(ROOT/'modules/nixos/automation-network.nix').read_text())
        unsafe=legacy.replace('192.168.30.0/24 100.64.0.0/10','0.0.0.0/0')
        with self.assertRaises(ValueError):m.office_patch(unsafe,h,self.source_caddy,self.source_home)

    def test_file_mount_inode_preserved_and_concurrent_change_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'Caddyfile';path.write_bytes(b'before');ino=path.stat().st_ino
            m.in_place(path,b'before',b'after')
            self.assertEqual(path.stat().st_ino,ino);self.assertEqual(path.read_bytes(),b'after')
            with self.assertRaises(ValueError):m.in_place(path,b'before',b'overwrite')
            self.assertEqual(path.read_bytes(),b'after')

if __name__=='__main__':unittest.main()
