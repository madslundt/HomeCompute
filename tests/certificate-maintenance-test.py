#!/usr/bin/env python3
"""Verify that failed alert delivery is retried and outages are deduplicated."""
import importlib.util
import io
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "certificate_maintenance", Path(__file__).resolve().parents[1] / "scripts/certificate-maintenance.py",
)
assert spec and spec.loader
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class MonitorTest(unittest.TestCase):
    def test_monitor_ssh_requires_its_own_ed25519_host_pin(self):
        with patch.object(maintenance, "command",
                          return_value=CompletedProcess([], 0, "https-ok\n", "")) as run:
            self.assertEqual(maintenance.probe(), "https-ok")
        args, _ = run.call_args.args
        self.assertIn(f"UserKnownHostsFile={maintenance.STATE / 'probe_known_hosts'}", args)
        self.assertIn("GlobalKnownHostsFile=/dev/null", args)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", args)
        self.assertIn("StrictHostKeyChecking=yes", args)

    def test_inbox_accepts_new_message_receipt(self):
        class CreatedResponse(io.BytesIO):
            status = 201

        with patch.object(maintenance.INBOX_SECRET.__class__, "read_text",
                          return_value="INBOX_WEBHOOK_ID=" + "a" * 32), \
            patch.object(maintenance.request, "build_opener") as build:
            build.return_value.open.return_value = CreatedResponse(b'{"id": "new-alert"}')
            self.assertEqual(maintenance.inbox({"action": "ingest"}), {"id": "new-alert"})

    def test_failure_retry_deduplication_and_recovery(self):
        owner = "test-user"
        with tempfile.TemporaryDirectory() as temp, \
            patch.object(maintenance, "STATE", Path(temp)), \
            patch.object(maintenance, "probe", return_value="gateway-https-failed") as probe, \
            patch.object(maintenance, "recipient", return_value=owner), \
            patch.object(maintenance, "inbox", side_effect=[RuntimeError("unavailable"), {"id": "receipt"}]) as inbox, \
            patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                maintenance.monitor()
            self.assertFalse(maintenance.monitor())
            self.assertFalse(maintenance.monitor())
            self.assertEqual(inbox.call_count, 2)
            first, retry = [call.args[0] for call in inbox.call_args_list]
            self.assertEqual(first, retry)
            self.assertEqual(first["recipients"], {"type": "users", "user_ids": [owner]})
            self.assertTrue(first["send_push"])

            probe.return_value = "https-ok"
            self.assertTrue(maintenance.monitor())
            self.assertFalse((Path(temp) / "openbot-incident.json").exists())
            self.assertEqual(inbox.call_count, 2)

    def test_pending_failure_survives_recovery_until_delivery(self):
        with tempfile.TemporaryDirectory() as temp, \
            patch.object(maintenance, "STATE", Path(temp)), \
            patch.object(maintenance, "probe", side_effect=["gateway-https-failed", "https-ok"]), \
            patch.object(maintenance, "recipient", return_value="test-user"), \
            patch.object(maintenance, "inbox", side_effect=[{}, {"id": "receipt"}]) as inbox, \
            patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                maintenance.monitor()
            self.assertTrue(maintenance.monitor())
            self.assertEqual(inbox.call_args_list[0], inbox.call_args_list[1])


if __name__ == "__main__":
    unittest.main()
