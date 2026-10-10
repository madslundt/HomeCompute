#!/usr/bin/env python3
"""Recovery routing, identity fences, retry bounds, and secret isolation."""
import importlib.util
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "supervisor", ROOT / "scripts/supervise-openclaw-nemoclaw.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
ID = "aabbccdd-0000-4000-8000-000000000001"
GEN = "aabbccdd-0000-4000-8000-000000000002"


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = dict(MOD.REQUIRED_ENV, HOME=self.tmp.name, PATH="/usr/bin:/bin",
                   OPENCLAW_EXPECTED_SANDBOX_ID=ID, OPENCLAW_EXPECTED_GENERATION=GEN,
                   OPENCLAW_NEMOCLAW_MODEL_KEY_FILE="/etc/homecompute-openclaw/secrets/litellm-agent-openclaw",
                   NEMOCLAW_RECREATE_SANDBOX="1", NEMOCLAW_FRESH="1",
                   NEMOCLAW_RESTORE_LATEST_BACKUP_ON_RECREATE="1",
                   NEMOCLAW_GATEWAY_MANAGEMENT="/wrong/owner", COMPATIBLE_API_KEY="unrelated")
        self.supervisor = MOD.Supervisor(env)
        self.supervisor.report = Mock()
        self.supervisor.check_registry = Mock()
        self.supervisor.check_container = Mock()
        self.supervisor.check_native_database = Mock()
        self.supervisor.observe = Mock(return_value="Ready")
        self.supervisor.ready = Mock(return_value=True)

    def test_runtime_down_requires_confirmation_and_bounded_backoff(self):
        s = self.supervisor
        s.ready.return_value = False
        s.recover = Mock()
        for now in (0, 60, 120, 180, 240, 300, 480, 600, 900):
            s.tick(now)
        self.assertEqual(s.recover.call_count, 3)
        self.assertEqual(s.attempts, [60, 180, 480])
        s.tick(1861)
        self.assertEqual(s.recover.call_count, 4)

    def test_native_database_fences_host_gateway_recovery(self):
        s = self.supervisor
        s.check_native_database.side_effect = MOD.Refused("native-database-identity-mismatch")
        s.run = Mock()
        with self.assertRaises(MOD.Refused):
            s.recover("host-gateway-down")
        s.run.assert_not_called()

    def test_offline_native_record_requires_exact_identity_nonterminal_phase(self):
        s = self.supervisor
        del s.check_native_database
        path = s.home / ".local/state/nemoclaw/openshell-docker-gateway-9123/openshell.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE objects (id TEXT, object_type TEXT, name TEXT, workspace TEXT, payload BLOB)")
        def field(number, text):
            raw = text.encode()
            return bytes([number * 8 + 2, len(raw)]) + raw
        metadata = field(1, ID) + field(2, MOD.NAME)
        for phase in (2, 7, 3, 4, 5, 8):
            payload = bytes([10, len(metadata)]) + metadata + bytes([26, 2, 48, phase])
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM objects")
                db.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?)",
                           (ID, "sandbox", MOD.NAME, "default", payload))
            if phase in (2, 3, 7):
                s.check_native_database()
            else:
                with self.assertRaises(MOD.Refused):
                    s.check_native_database()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DELETE FROM objects")
        with self.assertRaises(MOD.Refused):
            s.check_native_database()

    def test_protobuf_guard_rejects_truncation_duplicates_and_wire_drift(self):
        for payload in (b"\x30", b"\x32\x05x", b"\x30\x02\x30\x03", b"\x35abcd"):
            with self.assertRaises(MOD.Refused):
                MOD.protobuf_field(payload, 6, 0)

    def test_error_and_unknown_never_start_or_recreate(self):
        s = self.supervisor
        s.recover = Mock()
        for phase in ("Missing", "Pending", "unavailable"):
            s.observe.return_value = phase
            for now in (0, 60, 120, 180):
                s.tick(now)
        s.recover.assert_not_called()

    def test_error_routes_to_transaction_only_after_confirmation(self):
        s = self.supervisor
        s.observe.return_value = "Error"
        s.recover = Mock()
        s.tick(0)
        s.recover.assert_not_called()
        s.tick(60)
        s.recover.assert_called_once_with("Error")

    def test_healthy_checks_never_send_model_probes(self):
        s = self.supervisor
        s.recover = Mock()
        for now in (0, 60, 120):
            s.tick(now)
        s.recover.assert_not_called()

    def test_registry_failure_fences_every_recovery(self):
        s = self.supervisor
        s.check_registry.side_effect = MOD.Refused("registry-identity-mismatch")
        s.recover = Mock()
        with self.assertRaises(MOD.Refused):
            s.tick(0)
        s.observe.assert_not_called()
        s.recover.assert_not_called()

    def test_managed_gateway_query_and_scoped_lifecycle_only(self):
        s = self.supervisor
        s.run = Mock(return_value=(0, b""))
        for phase, command in (("Stopped", "start"), ("Ready", "recover")):
            s.recover(phase)
            self.assertEqual(s.run.call_args.args[0], [s.cli, MOD.NAME, command])
        s.recover("host-gateway-down")
        argv = s.run.call_args.args[0]
        self.assertEqual(argv, [s.cli, "credentials", "list"])
        self.assertNotIn("--fresh", argv)
        self.assertNotIn("--recreate-sandbox", argv)
        self.assertNotIn("--yes", argv)
        self.assertNotIn("onboarding", s.run.call_args.kwargs)

    def test_recover_falls_back_to_native_gateway_restart_under_same_identity(self):
        s = self.supervisor
        s.ready.return_value = False
        s.run = Mock(return_value=(1, b""))
        s.recover("Ready")
        self.assertEqual(s.run.call_args.args[0],
                         [s.cli, MOD.NAME, "gateway", "restart", "--quiet"])
        self.assertEqual(s.run.call_count, 3)

    def test_missing_forward_never_restarts_a_running_native_gateway(self):
        s = self.supervisor
        s.ready.return_value = False
        s.run = Mock(side_effect=[(1, b""), (0, b"423\n")])
        s.recover("Ready")
        self.assertEqual(s.run.call_count, 2)
        self.assertEqual(s.run.call_args.args[0][1:3], ["exec", s.container])
        s.report.assert_called_with("recovery-incomplete")

    def test_boot_restores_retained_stopped_sandbox_and_forward_without_restart(self):
        s = self.supervisor
        s.run = Mock(side_effect=[(1, b""), (0, b""), (0, b"")])
        s.observe.side_effect = ["Stopped", "Ready", "Ready"]
        s.ready.side_effect = [False, True]
        s.recover("host-gateway-down")
        calls = [call.args[0] for call in s.run.call_args_list]
        self.assertEqual(calls[1:], [[s.cli, MOD.NAME, "start"], [s.cli, MOD.NAME, "recover"]])
        self.assertEqual(s.check_registry.call_count, 3)
        self.assertEqual(s.check_container.call_count, 3)
        s.report.assert_called_with("ready")

    def test_boot_identity_change_prevents_retained_start(self):
        s = self.supervisor
        s.run = Mock(return_value=(0, b""))
        s.check_registry.side_effect = MOD.Refused("registry-identity-mismatch")
        with self.assertRaises(MOD.Refused):
            s.recover("host-gateway-down")
        self.assertEqual(s.run.call_count, 1)

    def test_positive_native_ready_survives_nonzero_start_finalization(self):
        s = self.supervisor
        s.run = Mock(return_value=(1, b"private incomplete bookkeeping"))
        s.recover("Stopped")
        s.report.assert_called_with("ready")
        self.assertEqual(s.run.call_count, 1)

    def test_changed_identity_after_recover_prevents_gateway_restart(self):
        s = self.supervisor
        s.ready.return_value = False
        s.run = Mock(return_value=(1, b""))
        s.check_registry.side_effect = MOD.Refused("registry-identity-mismatch")
        with self.assertRaises(MOD.Refused):
            s.recover("Ready")
        self.assertEqual(s.run.call_count, 1)

    def test_destructive_env_and_external_ownership_are_excluded(self):
        s = self.supervisor
        for name in ("NEMOCLAW_RECREATE_SANDBOX", "NEMOCLAW_FRESH",
                     "NEMOCLAW_RESTORE_LATEST_BACKUP_ON_RECREATE",
                     "NEMOCLAW_GATEWAY_MANAGEMENT", "COMPATIBLE_API_KEY"):
            self.assertNotIn(name, s.env)

    def test_private_key_rejects_world_readable_and_symlink(self):
        path = Path(self.tmp.name) / "key"
        path.write_text("synthetic")
        path.chmod(0o644)
        with self.assertRaises(MOD.Refused):
            MOD.private_file(path)
        path.chmod(0o600)
        self.assertEqual(MOD.private_file(path), b"synthetic")
        link = Path(self.tmp.name) / "link"
        link.symlink_to(path)
        with self.assertRaises(MOD.Refused):
            MOD.private_file(link)

    def test_observation_rejects_different_sandbox_id(self):
        s = self.supervisor
        del s.observe
        s.run = Mock(return_value=(0, json.dumps({"name": MOD.NAME,
                                                "id": GEN, "phase": "Ready"}).encode()))
        with self.assertRaises(MOD.Refused):
            s.observe()

    def test_registry_checks_generation_workload_and_gateway(self):
        s = self.supervisor
        del s.check_registry
        s.registry.parent.mkdir(parents=True)
        entry = {"name": MOD.NAME, "agent": "openclaw", "gatewayName": MOD.GATEWAY,
                 "gatewayPort": 9123, "dashboardPort": 18791,
                 "provider": "compatible-endpoint", "model": "automation-moe",
                 "endpointUrl": "http://ai.home.arpa:18080/v1",
                 "agentVersion": "2026.9.1", "openshellVersion": "0.0.116",
                 "lifecycleGeneration": GEN,
                 "workload": {"reference": MOD.IMAGE, "sourceRevision": MOD.REVISION}}
        s.registry.write_text(json.dumps({"sandboxes": {MOD.NAME: entry}}))
        s.check_registry()
        for field, wrong in (("lifecycleGeneration", ID), ("gatewayName", "nemoclaw"),
                             ("agentVersion", "later")):
            modified = dict(entry, **{field: wrong})
            s.registry.write_text(json.dumps({"sandboxes": {MOD.NAME: modified}}))
            with self.assertRaises(MOD.Refused):
                s.check_registry()

    def test_readiness_rejects_html_and_checks_native_ready_json(self):
        s = self.supervisor
        del s.ready
        s.run = Mock(return_value=(1, b""))
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            for data, expected in ((b"<html>login</html>", False),
                                   (b'{"ok":false}', False), (b'{"ok":true}', True)):
                response.read.return_value = data
                self.assertEqual(s.ready(), expected)
        self.assertEqual(opener.open.call_args.args[0], "http://127.0.0.1:18791/readyz")

    def test_native_authenticated_read_proves_readiness_without_dashboard(self):
        s = self.supervisor
        del s.ready
        s.run = Mock(return_value=(0, b"private native response"))
        opener = Mock()
        opener.open.side_effect = OSError("unavailable")
        with patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            self.assertTrue(s.ready())
        self.assertEqual(s.run.call_args.args[0][-6:],
                         ["gateway", "call", "exec.approvals.get", "--params", "{}", "--json"])

    def test_stopping_prevents_new_cli_and_leaves_no_stop_command(self):
        s = self.supervisor
        s.stop()
        with self.assertRaises(MOD.Refused):
            s.run([s.cli, MOD.NAME, "recover"], "stopped")

    def test_stop_signals_only_the_current_cli_process_group(self):
        s = self.supervisor
        s.child = Mock(pid=123)
        s.child.poll.return_value = None
        with patch.object(MOD.os, "killpg") as kill:
            s.stop()
        kill.assert_called_once_with(123, MOD.signal.SIGTERM)

    def test_cli_timeout_is_bounded_and_keeps_private_diagnostics(self):
        s = self.supervisor
        child = Mock(pid=123, returncode=0)
        child.communicate.return_value = (b"private timeout diagnostic", b"")
        with patch.object(MOD.subprocess, "Popen", return_value=child), \
                patch.object(MOD.os, "killpg") as kill:
            code, _ = s.run([s.cli, MOD.NAME, "recover"], "timeout", timeout=0)
        self.assertEqual(code, 124)
        kill.assert_called_once_with(123, MOD.signal.SIGTERM)

    def test_run_excludes_key_from_argv_and_private_diagnostics(self):
        s = self.supervisor
        child = Mock(pid=123, returncode=0)
        child.communicate.return_value = (b"synthetic diagnostic", b"")
        with patch.object(MOD, "private_file", return_value=b"synthetic-key"), \
                patch.object(MOD.subprocess, "Popen", return_value=child) as popen:
            s.run([s.cli, "onboard"], "native-recovery", onboarding=True)
        self.assertNotIn("synthetic-key", " ".join(popen.call_args.args[0]))
        self.assertEqual(popen.call_args.kwargs["env"]["COMPATIBLE_API_KEY"], "synthetic-key")
        self.assertNotIn("COMPATIBLE_API_KEY", s.env)
        diagnostic = s.state / "last-command.log"
        self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
