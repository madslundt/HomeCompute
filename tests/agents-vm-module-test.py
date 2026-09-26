#!/usr/bin/env python3
"""Static safety checks for the NixOS-owned household agents VM boundary."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "modules/nixos/agents-vm.nix").read_text(encoding="utf-8")
HOST = (ROOT / "hosts/home-core/default.nix").read_text(encoding="utf-8")


class AgentsVmModuleTest(unittest.TestCase):
    def test_module_is_imported_but_disabled_by_default(self) -> None:
        self.assertIn("../../modules/nixos/agents-vm.nix", HOST)
        self.assertIn("homecompute.agentsVm.enable = false;", HOST)

    def test_image_is_immutable_and_checksum_pinned(self) -> None:
        self.assertRegex(MODULE, r"release-[0-9]{8}/ubuntu-24\.04-server-cloudimg-amd64\.img")
        self.assertRegex(MODULE, r'default = "sha256-[A-Za-z0-9+/]{43}=";')
        self.assertNotIn("/releases/noble/release/ubuntu-24.04", MODULE)
        self.assertIn("qemu-img convert -f qcow2 -O qcow2", MODULE)
        self.assertNotIn("qemu-img create -f qcow2 -F qcow2 -b", MODULE)

    def test_activation_requires_backup_and_operator_key(self) -> None:
        self.assertIn("config.homecompute.backups.enable", MODULE)
        self.assertIn('path == "/srv/state" || path == statePath', MODULE)
        self.assertIn('statePath = "/srv/state/agents-vm"', MODULE)
        self.assertIn("cfg.sshAuthorizedKeys != [ ]", MODULE)

    def test_guest_cannot_forward_to_private_or_compute_networks(self) -> None:
        self.assertIn("-d 10.77.10.0/24 -j REJECT", MODULE)
        for subnet in (
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "100.64.0.0/10",
            "169.254.0.0/16",
        ):
            self.assertIn(subnet, MODULE)
        self.assertRegex(MODULE, r"HC-AGENTS-EGRESS -j REJECT")
        self.assertIn("iptables -w -I FORWARD 1 -i ${bridge} -j REJECT", MODULE)

    def test_no_vm_or_agent_management_port_is_published(self) -> None:
        allowed_match = re.search(
            r"networking\.firewall\.interfaces\.\$\{bridge\} = \{(?P<body>.*?)\n    \};",
            MODULE,
            re.DOTALL,
        )
        self.assertIsNotNone(allowed_match)
        body = allowed_match.group("body")
        self.assertIn("53", body)
        self.assertIn("443", body)
        for forbidden_port in (22, 8642, 5900):
            self.assertNotRegex(body, rf"\b{forbidden_port}\b")

    def test_vm_shutdown_is_graceful_before_bounded_fallback(self) -> None:
        self.assertIn("rm -f ${statePath}/qmp.sock", MODULE)
        self.assertIn("-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny", MODULE)
        self.assertIn('"execute":"system_powerdown"', MODULE)
        self.assertIn('UNIX-CONNECT:${statePath}/qmp.sock', MODULE)
        self.assertIn('seq 1 110', MODULE)
        self.assertIn('TimeoutStopSec = "2m"', MODULE)

    def test_vm_start_refuses_known_large_memory_peer(self) -> None:
        self.assertIn("homecompute-agents-memory-preflight", MODULE)
        self.assertIn("homecompute-control-plane-automation-backup-1", MODULE)
        self.assertIn("docker inspect --format '{{.State.Running}}'", MODULE)


if __name__ == "__main__":
    unittest.main()
