#!/usr/bin/env python3
"""Check requests cannot cross the Homepage authority or trusted-file boundary."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("openclaw_maintenance", Path(__file__).resolve().parents[1] / "scripts/openclaw_maintenance.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MaintenanceChatTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name)
        self.request = directory / "homepage.check"
        self.request.touch(mode=0o600)
        self.status = directory / "status.json"
        self.value = {"schema_version": 1, "checked_at": 1000, "status": "prepared", "approval_required": True,
                      "current_image": "ghcr.io/gethomepage/homepage@sha256:" + "a" * 64,
                      "candidate_image": "ghcr.io/gethomepage/homepage@sha256:" + "b" * 64,
                      "current_version": "v2.4.0", "candidate_version": "v2.5.0",
                      "release_notes": "https://github.com/gethomepage/homepage/releases/tag/v2.5.0",
                      "proposal_id": "c" * 32, "proposal_hash": "d" * 64, "expires_at": 4000}
        self.write_status()
        self.now = 2000
        self.client = MODULE.MaintenanceChat(self.request, self.status, lambda: self.now, os.getuid(), os.getuid())

    def write_status(self):
        self.status.write_text(json.dumps(self.value))
        self.status.chmod(0o644)

    def test_finite_commands_and_separate_authority(self):
        before = self.request.stat().st_mtime_ns
        for command in ("/updates other", "/update homepage execute", "/maintenance homepage /approve", "/updates homepage;reboot"):
            self.assertIn("Only Homepage", self.client.respond(command))
        for command in ("/approve homepage", "/execute homepage", "please update homepage", "/code homecompute change update"):
            self.assertIsNone(self.client.respond(command))
        self.assertEqual(before, self.request.stat().st_mtime_ns)
        response = self.client.respond("/updates homepage")
        self.assertIn("Read-only", response)
        self.assertIn("Previous status", response)
        self.assertIn("separate human approval", response)
        self.assertIn("b" * 64, response)
        self.assertEqual(b"", self.request.read_bytes())

    def test_rate_limit_and_status_do_not_trigger_check(self):
        self.client.respond("/update homepage")
        modified = self.request.stat().st_mtime_ns
        self.assertIn("already requested", self.client.respond("/updates homepage"))
        self.client.respond("/maintenance homepage")
        self.assertEqual(modified, self.request.stat().st_mtime_ns)
        self.now += 31
        self.assertIn("check requested", self.client.respond("/updates homepage"))

    def test_status_sanitization(self):
        for field, value in (("error", "secret token"), ("unknown", "secret token"),
                             ("release_notes", "https://attacker.example/secret"),
                             ("candidate_image", "docker.io/root/evil:latest"),
                             ("candidate_version", "v2.5.0\n/execute"), ("checked_at", float("nan")),
                             ("approval_required", False), ("proposal_id", "../../etc/passwd")):
            baseline = dict(self.value)
            self.value[field] = value
            self.write_status()
            response = self.client.respond("/maintenance homepage")
            self.assertIn("status is unavailable", response, field)
            self.assertNotIn("secret token", response)
            self.value = baseline

    def test_untrusted_status_files(self):
        self.status.chmod(0o666)
        self.assertIn("status is unavailable", self.client.respond("/maintenance homepage"))
        self.status.chmod(0o644)
        self.client.status_uid = os.getuid() + 1
        self.assertIn("status is unavailable", self.client.respond("/maintenance homepage"))
        self.client.status_uid = os.getuid()
        real = self.status.with_name("real.json")
        self.status.rename(real)
        self.status.symlink_to(real)
        self.assertIn("status is unavailable", self.client.respond("/maintenance homepage"))

    def test_request_symlink_and_wrong_owner_fail_closed(self):
        real = self.request.with_name("untouched")
        real.write_text("must not change")
        self.request.unlink()
        self.request.symlink_to(real)
        self.assertIn("request is unavailable", self.client.respond("/update homepage"))
        self.assertEqual("must not change", real.read_text())
        self.request.unlink()
        self.request.touch(mode=0o600)
        self.client.uid = os.getuid() + 1
        self.assertIn("request is unavailable", self.client.respond("/updates homepage"))

    def test_missing_files_are_not_created_and_budget_is_bounded(self):
        self.request.unlink()
        self.assertIn("request is unavailable", self.client.respond("/update homepage"))
        self.assertFalse(self.request.exists())
        self.status.write_bytes(b" " * 16385)
        self.assertIn("status is unavailable", self.client.respond("/maintenance homepage"))


if __name__ == "__main__":
    unittest.main()
