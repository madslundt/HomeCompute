"""Dedicated guest bootstrap guard tests; no disks, services or packages changed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("codex_guest", ROOT / "scripts/setup-codex-guest.py")
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class GuestBootstrapTests(unittest.TestCase):
    def test_only_dedicated_pinned_guest_identity_is_accepted(self):
        values = ["codex-worker", {"ID": "ubuntu", "VERSION_ID": "24.04"},
                  "homecompute-codex-v1", guest.MARKER, "QEMU"]
        guest.validate_host(*values)
        for index, value in ((0, "home-core"), (0, "agents"), (1, {"ID": "nixos", "VERSION_ID": "24.04"}),
                             (1, {"ID": "ubuntu", "VERSION_ID": "22.04"}), (2, "homecompute-agents-v1"),
                             (3, {**guest.MARKER, "work_device": "/dev/vda"}), (4, "GMKtec")):
            bad = values.copy(); bad[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(guest.BootstrapError):
                guest.validate_host(*bad)

    def test_disk_guard_rejects_root_partition_unknown_or_mounted_filesystems(self):
        disk = {"path": "/dev/vdb", "type": "disk", "size": 12 * 1024 ** 3,
                "fstype": None, "label": None, "mountpoints": [None]}
        self.assertTrue(guest.validate_disk(disk))
        self.assertFalse(guest.validate_disk({**disk, "fstype": "ext4", "label": "HC_CODEX_WORK",
                                             "mountpoints": ["/srv/codex-work"]}))
        for changed in ({"path": "/dev/vda"}, {"size": 32 * 1024 ** 3}, {"type": "part"},
                        {"children": [{}]}, {"fstype": "ext4", "label": "production"},
                        {"mountpoints": ["/"]}, {"fstype": "crypto_LUKS"}, {"label": "unknown"}):
            with self.subTest(changed=changed), self.assertRaises(guest.BootstrapError):
                guest.validate_disk({**disk, **changed})

    def bundle(self, directory):
        manifest = {"schema_version": 1, "files": {}}
        for name in guest.FILES:
            target = directory / name
            target.write_bytes((ROOT / "deploy/codex-worker" / name).read_bytes())
            target.chmod(0o444)
            manifest["files"][name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "manifest.json").chmod(0o444)
        return manifest

    def test_source_hashes_and_exact_names_are_verified_before_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.bundle(directory)
            payloads, digests = guest.read_bundle(directory, os.getuid())
            self.assertEqual(set(payloads), guest.FILES)
            self.assertEqual(digests, manifest["files"])
            target = directory / "worker.py"
            target.chmod(0o600); target.write_text("changed public source"); target.chmod(0o444)
            with self.assertRaisesRegex(guest.BootstrapError, "digest mismatch"):
                guest.read_bundle(directory, os.getuid())
            manifest["files"]["../outside"] = "a" * 64
            path = directory / "manifest.json"; path.chmod(0o600); path.write_text(json.dumps(manifest)); path.chmod(0o444)
            with self.assertRaisesRegex(guest.BootstrapError, "exact reviewed public files"):
                guest.read_bundle(directory, os.getuid())

    def test_source_cannot_be_symlink_or_writable_by_other_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.bundle(directory)
            worker = directory / "worker.py"
            worker.chmod(0o666)
            with self.assertRaisesRegex(guest.BootstrapError, "owner-controlled"):
                guest.read_bundle(directory, os.getuid())
            worker.unlink(); worker.symlink_to(ROOT / "deploy/codex-worker/worker.py")
            with self.assertRaisesRegex(guest.BootstrapError, "symlink"):
                guest.read_bundle(directory, os.getuid())

    def test_host_guard_fails_before_any_subprocess_without_root(self):
        with patch.object(guest.os, "geteuid", return_value=1000), patch.object(guest, "command") as run:
            with self.assertRaisesRegex(guest.BootstrapError, "requires root"):
                guest.guard_guest()
            run.assert_not_called()

    def test_install_qualification_does_not_load_credentials_or_worker_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            unit = directory / "unit"
            unit.write_bytes((ROOT / "deploy/codex-worker/homecompute-codex-worker.service").read_bytes())
            receipt = {"event": "native_worker_synthetic_qualification", "credentials_accessed": False}
            result = subprocess.CompletedProcess([], 0, json.dumps(receipt) + "\n", "")
            with patch.object(guest, "secure_file", return_value=unit.read_bytes()), \
                    patch.object(guest, "STATE", directory), patch.object(guest, "command", return_value=result) as run:
                observed = guest.qualify()
            args = run.call_args.args[0]
            self.assertFalse(any("LoadCredential" in argument or "EnvironmentFile" in argument for argument in args))
            self.assertIn("--property=PrivateNetwork=yes", args)
            environments = [argument for argument in args if argument.startswith("--property=Environment=")]
            self.assertEqual(len(environments), 1)
            self.assertIn("PATH=", environments[0])
            self.assertIn("CODEX_WORKER_ROOT=", environments[0])
            self.assertFalse(observed["worker_enabled"])
            self.assertFalse(observed["production_network_qualified"])

    def test_interrupted_apt_cache_is_preserved_and_not_repaired_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"; state.mkdir()
            lists = root / "lists"; lists.mkdir()
            (lists / "archive_Packages").touch()
            with patch.object(guest, "STATE", state), \
                    patch.object(guest, "Path", side_effect=lambda value: lists if value == "/var/lib/apt/lists" else Path(value)), \
                    patch.object(guest.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123)), \
                    patch.object(guest.os, "chown") as chown:
                guest.repair_interrupted_indexes()
                self.assertTrue((state / "apt-lists-before-recovery/archive_Packages").exists())
                self.assertEqual(lists.stat().st_mode & 0o777, 0o755)
                chown.assert_called_once_with(lists / "partial", 123, 0)
                (lists / "another_Packages").touch()
                with self.assertRaisesRegex(guest.BootstrapError, "already exists"):
                    guest.repair_interrupted_indexes()

    def test_interrupted_package_reinstall_uses_validated_package_identifiers(self):
        def result(stdout="", returncode=0):
            return subprocess.CompletedProcess([], returncode, stdout, "")
        with patch.object(guest, "command", side_effect=[result("interrupted"), result(returncode=1),
                    result("node-esprima\tiHR\n"), result(), result(), result()]) as run:
            guest.repair_interrupted_packages()
        self.assertIn(["apt-get", "install", "--yes", "--no-install-recommends", "--reinstall", "node-esprima"],
                      [call.args[0] for call in run.call_args_list])
        with patch.object(guest, "command", side_effect=[result("interrupted"), result(), result("--invalid\tiHR\n")]):
            with self.assertRaisesRegex(guest.BootstrapError, "invalid package"):
                guest.repair_interrupted_packages()


if __name__ == "__main__":
    unittest.main()
