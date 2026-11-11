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
DNS = json.loads((ROOT / "config/craft-anything-dns.json").read_text())
CADDY = (ROOT / "deploy/control-plane/Caddyfile").read_text()
DESKTOP_INSTALLER = (ROOT / "scripts/install-craft-anything-desktop.sh").read_text()


class ToolboxVmModuleTest(unittest.TestCase):
    def test_home_core_enables_a_dedicated_guest(self):
        self.assertIn("../../modules/nixos/toolbox-vm.nix", HOST)
        self.assertIn("homecompute.toolboxVm = {", HOST)
        self.assertIn("enable = true;", HOST)
        self.assertIn("sshKeys =", HOST)
        self.assertIn("nasRecoveryBackup.includeApplicationState", MODULE)
        self.assertIn("10.77.30.2", MODULE)
        self.assertIn("br-hc-toolbox", MODULE)
        self.assertIn('match: {macaddress: "52:54:00:77:30:02"}', MODULE)
        self.assertIn("set-name: ens3", MODULE)
        self.assertIn("4 GiB RAM", MODULE)

    def test_guest_isolated_and_egress_bounded(self):
        self.assertIn("HC-TOOLBOX-EGRESS", MODULE)
        self.assertIn("--dports 80,443", MODULE)
        self.assertIn("desktopProxy", MODULE)
        self.assertIn("--dport 6080 -j ACCEPT", MODULE)
        self.assertIn("-s ${cfg.network.desktopProxy}/32 -d ${guest}/32", MODULE)
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

    def test_desktop_ui_is_pinned_and_reached_through_the_private_edge(self):
        self.assertIn("https://craft-anything.home.arpa/vnc.html?autoconnect=1", HOMEPAGE)
        self.assertIn("craft-anything.home.arpa {", CADDY)
        self.assertIn("reverse_proxy 10.77.30.2:6080", CADDY)
        self.assertIn("-rfbauth", DESKTOP_INSTALLER)
        self.assertIn("10.77.30.2:6080", DESKTOP_INSTALLER)
        self.assertIn("libxcursor1 libxkbcommon-x11-0", DESKTOP_INSTALLER)
        self.assertNotIn("ReadWritePaths=$operator_home/.local/share/craft-anything /tmp/.X11-unix", DESKTOP_INSTALLER)
        self.assertEqual(DESKTOP_INSTALLER.count("JoinsNamespaceOf=craft-anything-display.service"), 2)
        self.assertEqual(PINS["craft_anything"]["version"], "0.6.0")
        self.assertRegex(PINS["craft_anything"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(PINS["craft_anything"]["appimage_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(DNS["records"], [{"name": "craft-anything.home.arpa", "type": "A",
                                           "value": "192.168.30.122", "ttl": 300}])


if __name__ == "__main__":
    unittest.main()
