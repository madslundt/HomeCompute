#!/usr/bin/env python3
"""Static checks for the isolated Craft Anything toolbox guest."""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "modules/nixos/toolbox-vm.nix").read_text()
HOST = (ROOT / "hosts/home-core/default.nix").read_text()
RECOVERY = (ROOT / "scripts/application-recovery.py").read_text()
HOMEPAGE = (ROOT / "deploy/homepage/config/services.yaml").read_text()
PINS = json.loads((ROOT / "config/craft-anything-toolbox.json").read_text())


class ToolboxVmModuleTest(unittest.TestCase):
    def test_home_core_enables_a_dedicated_guest(self):
        self.assertIn("../../modules/nixos/toolbox-vm.nix", HOST)
        self.assertIn("homecompute.toolboxVm = {", HOST)
        self.assertIn("enable = true;", HOST)
        self.assertIn("sshKeys =", HOST)
        self.assertIn("nasRecoveryBackup.includeApplicationState", MODULE)
        self.assertIn("10.77.30.2", MODULE)
        self.assertIn("br-hc-toolbox", MODULE)
        self.assertIn("4 GiB RAM", MODULE)

    def test_guest_isolated_and_egress_bounded(self):
        self.assertIn("HC-TOOLBOX-EGRESS", MODULE)
        self.assertIn("--dports 80,443", MODULE)
        for subnet in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"):
            self.assertIn(subnet, MODULE)
        self.assertIn("ip6tables -w -C FORWARD -i", MODULE)
        self.assertIn("ProtectSystem = \"strict\";", MODULE)
        self.assertIn("DeviceAllow = [\"/dev/kvm rw\" \"/dev/net/tun rw\"];", MODULE)
        self.assertIn("MemAvailable:", MODULE)
        self.assertIn('required_kib="$(( (', MODULE)
        self.assertIn("plus 4 GiB host headroom", MODULE)

    def test_guest_disk_and_seed_are_recoverable(self):
        self.assertIn("'toolbox-vm/root.qcow2'", RECOVERY)
        self.assertIn("homecompute-toolbox-vm.service", RECOVERY)
        self.assertIn("TOOLBOX_SEED_PATTERN", RECOVERY)
        self.assertIn("state/toolbox-vm/seed.iso", RECOVERY)
        self.assertIn("toolbox_seed_verified", RECOVERY)

    def test_homepage_links_upstream_and_release_is_pinned(self):
        self.assertIn("https://github.com/storytold/craft-anything", HOMEPAGE)
        self.assertEqual(PINS["craft_anything"]["version"], "0.6.0")
        self.assertRegex(PINS["craft_anything"]["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
