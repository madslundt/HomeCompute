#!/usr/bin/env python3
"""The OpenBot bridge receives only its exact reviewed private MCP exception."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openbot_network_policy", ROOT / "deploy/openbot/network-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class OpenBotNetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / "network.json"
        value = json.loads((ROOT / "config/openbot-network.json").read_text())
        self.config.write_text(json.dumps(value))
        self.patch = patch.multiple(policy, CONFIG=self.config, PARTNER_CONFIG=self.config)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_apply_allows_exact_private_mcp_hosts_and_ports(self):
        calls = []

        def iptables(*arguments, check=True):
            calls.append(arguments)
            status = 1 if arguments[0] == '-C' else 0
            return subprocess.CompletedProcess(arguments, status)

        with patch.object(policy, "iptables", side_effect=iptables):
            policy.apply()

        expected = ('-A', policy.CHAIN, '-d', '10.77.20.1/32', '-p', 'tcp',
                    '--dport', '19445', '-j', 'ACCEPT')
        self.assertIn(expected, calls)
        self.assertEqual(sum('--dport' in call and call[call.index('--dport') + 1] == '19445'
                             for call in calls), 1)
        gateway = ('-A', policy.CHAIN, '-d', '10.77.20.1/32', '-p', 'tcp',
                   '--dport', '443', '-j', 'ACCEPT')
        self.assertIn(gateway, calls)
        self.assertEqual(sum('--dport' in call and call[call.index('--dport') + 1] == '443'
                             for call in calls), 1)
        self.assertEqual([call[-1] for call in calls if call[:2] == ('-I', 'DOCKER-USER')],
                         [policy.CHAIN])

    def test_rejects_unreviewed_android_mcp_destination(self):
        value = json.loads(self.config.read_text())
        value['android_mcp_endpoint']['port'] = 19446
        self.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Android MCP endpoint'):
            policy.load_turn_addresses()

    def test_rejects_unreviewed_mcp_gateway_destination(self):
        value = json.loads(self.config.read_text())
        value['mcp_gateway_endpoint']['port'] = 444
        self.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'LiteLLM MCP gateway endpoint'):
            policy.load_turn_addresses()

    def test_partner_bridge_uses_only_dns_and_local_inference(self):
        calls = []

        def iptables(*arguments, check=True):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, int(arguments[0] == '-C'))

        with patch.object(policy, "iptables", side_effect=iptables):
            policy.apply(partner=True)
            policy.remove(partner=True)

        self.assertIn(('-I', 'INPUT', '1', '-i', policy.PARTNER_BRIDGE, '-j', 'REJECT'), calls)
        self.assertIn(('-I', 'DOCKER-USER', '1', '-i', policy.PARTNER_BRIDGE, '-j', policy.PARTNER_CHAIN), calls)
        host_rules = [call for call in calls if call[:5] == ('-A', policy.PARTNER_CHAIN, '-d', '10.77.20.1/32', '-p')]
        self.assertEqual([(call[5], call[7]) for call in host_rules], [('udp', '53'), ('tcp', '53'), ('tcp', '18080')])
        self.assertIn(('-A', policy.PARTNER_CHAIN, '-d', '172.16.0.0/12', '-j', 'REJECT'), calls)
        self.assertNotIn(policy.CHAIN, {field for call in calls for field in call})

    def test_partner_rejects_a_modified_bridge(self):
        value = json.loads(self.config.read_text())
        value['partner']['bridge'] = 'docker0'
        self.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Partner OpenBot Docker network'):
            policy.load_turn_addresses(partner=True)


if __name__ == "__main__":
    unittest.main()
