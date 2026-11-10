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
        self.patch = patch.object(policy, "CONFIG", self.config)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_apply_allows_only_the_fixed_android_mcp_host_port(self):
        calls = []

        def iptables(*arguments, check=True):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0)

        with patch.object(policy, "iptables", side_effect=iptables):
            policy.apply()

        expected = ('-A', policy.CHAIN, '-d', '10.77.20.1/32', '-p', 'tcp',
                    '--dport', '19445', '-j', 'ACCEPT')
        self.assertIn(expected, calls)
        self.assertFalse(any('--dport' in call and call[call.index('--dport') + 1] == '19445'
                             and call != expected for call in calls))

    def test_rejects_unreviewed_android_mcp_destination(self):
        value = json.loads(self.config.read_text())
        value['android_mcp_endpoint']['port'] = 19446
        self.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Android MCP endpoint'):
            policy.load_turn_addresses()


if __name__ == "__main__":
    unittest.main()
