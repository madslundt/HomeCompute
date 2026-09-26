#!/usr/bin/env python3
"""Static safety checks for the NixOS-owned household agents VM boundary."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "modules/nixos/agents-vm.nix").read_text(encoding="utf-8")
BACKUP_MODULE = (ROOT / "modules/nixos/agents-vm-backup.nix").read_text(encoding="utf-8")
HOST = (ROOT / "hosts/home-core/default.nix").read_text(encoding="utf-8")
SECRETS = (ROOT / "modules/nixos/secrets.nix").read_text(encoding="utf-8")


class AgentsVmModuleTest(unittest.TestCase):
    def test_module_is_imported_with_synthetic_pilot_enabled(self) -> None:
        self.assertIn("../../modules/nixos/agents-vm.nix", HOST)
        self.assertIn("homecompute.agentsVm = {", HOST)
        self.assertIn("enable = true;", HOST)
        self.assertIn('dataClassification = "synthetic-only";', HOST)
        self.assertIn("network.maintenanceEgress = true;", HOST)
        self.assertIn("Temporary for the synthetic canary", HOST)

    def test_image_is_immutable_and_checksum_pinned(self) -> None:
        self.assertRegex(MODULE, r"release-[0-9]{8}/ubuntu-24\.04-server-cloudimg-amd64\.img")
        self.assertRegex(MODULE, r'default = "sha256-[A-Za-z0-9+/]{43}=";')
        self.assertNotIn("/releases/noble/release/ubuntu-24.04", MODULE)
        self.assertIn("qemu-img convert -f qcow2 -O qcow2", MODULE)
        self.assertNotIn("qemu-img create -f qcow2 -F qcow2 -b", MODULE)

    def test_activation_requires_backup_and_operator_key(self) -> None:
        self.assertIn("config.homecompute.backups.enable", MODULE)
        self.assertIn('cfg.dataClassification == "synthetic-only"', MODULE)
        self.assertIn("config.homecompute.agentsVm.localBootstrapBackup.riskAccepted", MODULE)
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
        self.assertIn("inferenceBridgePort", body)
        for forbidden_port in (22, 8642, 5900):
            self.assertNotRegex(body, rf"\b{forbidden_port}\b")

    def test_vm_shutdown_is_graceful_before_bounded_fallback(self) -> None:
        self.assertIn("rm -f ${statePath}/qmp.sock", MODULE)
        self.assertIn("-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny", MODULE)
        self.assertIn('"execute":"system_powerdown"', MODULE)
        self.assertIn('UNIX-CONNECT:${statePath}/qmp.sock', MODULE)
        self.assertIn('seq 1 110', MODULE)
        self.assertIn('TimeoutStopSec = "2m"', MODULE)

    def test_vm_can_traverse_only_its_protected_state_parent(self) -> None:
        self.assertIn(
            '"a+ /srv/state - - - - u:homecompute-agents-vm:--x"',
            MODULE,
        )
        self.assertNotIn('extraGroups = [ "kvm" "homecompute-state" ];', MODULE)
        self.assertNotIn('SupplementaryGroups = [ "kvm" "homecompute-state" ];', MODULE)

    def test_ai_proxy_socket_waits_for_bridge_without_boot_cycle(self) -> None:
        socket_match = re.search(
            r"systemd\.sockets\.homecompute-agents-ai-proxy = \{(?P<body>.*?)\n    \};",
            MODULE,
            re.DOTALL,
        )
        self.assertIsNotNone(socket_match)
        body = socket_match.group("body")
        self.assertIn('wantedBy = [ "multi-user.target" ];', body)
        self.assertIn('after = [ "homecompute-agents-network.service" ];', body)
        self.assertIn('Accept = false;', body)
        self.assertNotIn('wantedBy = [ "sockets.target" ];', body)
        self.assertNotRegex(body, r'Service\s*=\s*".*@\.service"')
        self.assertIn("systemd.services.homecompute-agents-ai-proxy = {", MODULE)
        self.assertNotIn('systemd.services."homecompute-agents-ai-proxy@"', MODULE)
        self.assertNotIn('StandardInput = "socket";', MODULE)

    def test_http_inference_bridge_is_host_only_and_verifies_caddy(self) -> None:
        self.assertIn("inferenceBridgePort = 18080;", MODULE)
        self.assertIn("TCP4-LISTEN:${toString inferenceBridgePort},bind=${hostAddress}", MODULE)
        self.assertIn("OPENSSL:192.168.30.122:443,verify=1", MODULE)
        self.assertIn("commonname=ai.home.arpa", MODULE)
        self.assertIn("snihost=ai.home.arpa", MODULE)
        self.assertIn("caddy-data/caddy/pki/authorities/local/root.crt", MODULE)

    def test_vm_start_refuses_known_large_memory_peer(self) -> None:
        self.assertIn("homecompute-agents-memory-preflight", MODULE)
        self.assertIn("homecompute-control-plane-automation-backup-1", MODULE)
        self.assertIn("docker inspect --format '{{.State.Running}}'", MODULE)

    def test_local_bootstrap_mode_is_truthful_and_outside_state(self) -> None:
        self.assertIn('repository = "/srv/backup/restic-homecompute";', BACKUP_MODULE)
        self.assertIn('source = "/srv/state/agents-vm";', BACKUP_MODULE)
        self.assertIn("initialize = true;", BACKUP_MODULE)
        self.assertIn("riskAccepted = true;", HOST)
        self.assertIn('passwordFile = config.sops.secrets."restic/password".path;', HOST)
        self.assertIn("homecompute.backups.enable = false;", HOST)

    def test_backup_quiesces_and_conditionally_restarts_vm(self) -> None:
        marker = "/run/homecompute/agents-vm-backup-was-active"
        self.assertEqual(BACKUP_MODULE.count(marker), 1)
        self.assertIn("systemctl stop homecompute-agents-vm.service", BACKUP_MODULE)
        self.assertIn("systemctl start homecompute-agents-vm.service", BACKUP_MODULE)
        self.assertIn("backupCleanupCommand", BACKUP_MODULE)
        self.assertLess(
            BACKUP_MODULE.index("systemctl is-active --quiet"),
            BACKUP_MODULE.index("systemctl stop"),
        )
        self.assertIn("agents-vm-backup-in-progress", BACKUP_MODULE)
        self.assertIn("agents-vm-backup-in-progress", MODULE)
        self.assertIn("--property=LoadState", BACKUP_MODULE)
        self.assertIn("--property=MainPID", BACKUP_MODULE)
        self.assertIn("qemu-img check -q", BACKUP_MODULE)

    def test_backup_runtime_preflight_is_fail_closed(self) -> None:
        self.assertIn("[ -L ${repository} ]", BACKUP_MODULE)
        self.assertIn("0:0:700", BACKUP_MODULE)
        self.assertIn("[ ! -s ${cfg.passwordFile} ]", BACKUP_MODULE)
        self.assertIn("0:0:400|0:0:600", BACKUP_MODULE)
        self.assertIn("minimumFreeGiB", BACKUP_MODULE)
        self.assertIn("df --output=avail -B1", BACKUP_MODULE)

    def test_restic_secret_is_available_for_either_backup_contract(self) -> None:
        self.assertIn("config.homecompute.backups.enable", SECRETS)
        self.assertIn("config.homecompute.agentsVm.localBootstrapBackup.enable", SECRETS)


if __name__ == "__main__":
    unittest.main()
