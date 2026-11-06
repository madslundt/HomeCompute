#!/usr/bin/env python3
"""Synthetic guarded authorized_keys installation contract."""
from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_assistant_context_worker_ssh",
    ROOT / "scripts/setup-assistant-context-worker-ssh.py")
installer_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = installer_module
spec.loader.exec_module(installer_module)


def synthetic_key() -> bytes:
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
    return b"ssh-ed25519 " + base64.b64encode(blob) + b" synthetic-fixture\n"


class WorkerSSHTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.account = pwd.getpwuid(os.geteuid())
        self.ssh = self.root / "operator" / ".ssh"
        self.ssh.mkdir(parents=True, mode=0o700)
        self.keys = self.ssh / "authorized_keys"
        self.keys.write_bytes(b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOTHER unrelated-operator-key\n")
        self.keys.chmod(0o600)
        self.key_path = self.root / "worker.pub"
        self.key_path.write_bytes(synthetic_key())
        self.key_path.chmod(0o600)
        self.state = self.root / "rollback"
        self.revision = "a" * 40
        self.installer = installer_module.WorkerSSHInstaller(
            authorized_keys=self.keys, public_key=self.key_path, state_root=self.state,
            repo=self.root, test_mode=True, account=self.account)

    def tearDown(self):
        self.tmp.cleanup()

    def test_template_forces_only_worker_command_with_ssh_restrictions(self):
        blob, fingerprint = installer_module.parse_public_key(synthetic_key())
        line = installer_module.authorized_key_line(blob)
        self.assertTrue(line.startswith(b'restrict,command="exec /usr/bin/env '))
        self.assertIn(b"NEMOCLAW_GATEWAY_PORT=9123", line)
        self.assertIn(b"nemoclaw agent-openclaw exec -- python3 /sandbox/.openclaw/homecompute-assistant-context-worker.py --stdio", line)
        self.assertTrue(line.endswith((b"ssh-ed25519 " + blob.encode() + b" homecompute-assistant-context-worker")))
        self.assertTrue(fingerprint.startswith("SHA256:"))

    def test_plan_apply_and_rollback_preserve_unrelated_keys(self):
        before = self.keys.read_bytes()
        plan = self.installer.plan(self.revision)
        self.assertTrue(plan["changed"])
        result = self.installer.apply(self.revision, plan["authorized_keys_sha256"])
        self.assertTrue(result["applied"])
        installed = self.keys.read_bytes()
        self.assertTrue(installed.startswith(before))
        self.assertEqual(installed.count(b"unrelated-operator-key"), 1)
        self.assertEqual(sum(line.endswith(b" homecompute-assistant-context-worker")
                             for line in installed.splitlines()), 1)
        again = self.installer.plan(self.revision)
        self.assertFalse(again["changed"])
        rollback = self.installer.rollback(self.revision, result["backup_id"], result["authorized_keys_sha256"])
        self.assertTrue(rollback["rolled_back"])
        self.assertEqual(self.keys.read_bytes(), before)

    def test_stale_plan_and_unknown_managed_entry_are_refused(self):
        plan = self.installer.plan(self.revision)
        self.keys.write_bytes(self.keys.read_bytes() + b"# concurrent operator change\n")
        with self.assertRaises(installer_module.Refused):
            self.installer.apply(self.revision, plan["authorized_keys_sha256"])
        blob, _ = installer_module.parse_public_key(synthetic_key())
        self.keys.write_bytes(b"restrict,command=\"/bin/sh\" ssh-ed25519 " + blob.encode() +
                              b" homecompute-assistant-context-worker\n")
        with self.assertRaises(installer_module.Refused):
            self.installer.plan(self.revision)

    def test_rollback_refuses_concurrent_authorized_key_change(self):
        plan = self.installer.plan(self.revision)
        applied = self.installer.apply(self.revision, plan["authorized_keys_sha256"])
        self.keys.write_bytes(self.keys.read_bytes() + b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXTRA unrelated-new-key\n")
        with self.assertRaises(installer_module.Refused):
            self.installer.rollback(self.revision, applied["backup_id"], applied["authorized_keys_sha256"])

    def test_host_guard_requires_root_ubuntu_agents_and_kvm(self):
        host = self.root / "hostname"
        release = self.root / "os-release"
        host.write_text("agents\n")
        release.write_text('ID="ubuntu"\n')
        account = pwd.struct_passwd(("hermes-operator", "x", 1001, 1001, "", "/home/hermes-operator", "/bin/bash"))
        with patch.object(installer_module.pwd, "getpwnam", return_value=account):
            result = installer_module.ensure_host(system="Linux", euid=0, hostname_file=host,
                os_release=release, virt="kvm")
        self.assertEqual(result.pw_name, "hermes-operator")
        with self.assertRaises(installer_module.Refused):
            installer_module.ensure_host(system="Linux", euid=1000, hostname_file=host,
                os_release=release, virt="kvm")
        host.write_text("home-core\n")
        with self.assertRaises(installer_module.Refused):
            installer_module.ensure_host(system="Linux", euid=0, hostname_file=host,
                os_release=release, virt="kvm")
        host.write_text("agents\n")
        with self.assertRaises(installer_module.Refused):
            installer_module.ensure_host(system="Linux", euid=0, hostname_file=host,
                os_release=release, virt="docker")

    def test_source_release_requires_full_sha_and_exact_published_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for relative in installer_module.SOURCE_PATHS:
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Synthetic Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", installer_module.REMOTE], check=True)
            subprocess.run(["git", "-C", str(repo), "add", *installer_module.SOURCE_PATHS], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/master", revision], check=True)
            installer_module.verify_release(revision, repo=repo)
            with self.assertRaises(installer_module.Refused):
                installer_module.verify_release("not-a-full-sha", repo=repo)
            (repo / installer_module.SOURCE_PATHS[0]).write_bytes(b"drift")
            with self.assertRaises(installer_module.Refused):
                installer_module.verify_release(revision, repo=repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
