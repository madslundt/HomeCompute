#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("shutdown", ROOT / "scripts/stop-openclaw-before-shutdown.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ShutdownTests(unittest.TestCase):
    def test_native_stop_follows_watchdog_stop_and_verifies_stopped(self):
        ctx = Mock(cli="/fixed/nemoclaw")
        ctx.observe.side_effect = ["Ready", "Stopped"]
        ctx.run.return_value = (0, b"")
        with patch.object(MOD.subprocess, "run") as stop:
            MOD.stop_exact(ctx)
        self.assertEqual(stop.call_args.args[0][-2:], ["stop", "homecompute-openclaw-supervisor.service"])
        self.assertEqual(ctx.run.call_args.args[0], ["/fixed/nemoclaw", "agent-openclaw", "stop"])

    def test_identity_drift_prevents_every_shutdown_mutation(self):
        ctx = Mock()
        ctx.check_registry.side_effect = RuntimeError("identity changed")
        with patch.object(MOD.subprocess, "run") as stop:
            with self.assertRaises(RuntimeError):
                MOD.stop_exact(ctx)
        stop.assert_not_called()
        ctx.run.assert_not_called()

    def test_native_stopped_proof_survives_incomplete_registry_finalization(self):
        ctx = Mock(cli="/fixed/nemoclaw")
        ctx.observe.side_effect = ["Ready", "Stopped"]
        ctx.run.return_value = (1, b"private registry finalization diagnostic")
        with patch.object(MOD.subprocess, "run"):
            MOD.stop_exact(ctx)
        self.assertEqual(ctx.check_registry.call_count, 2)
        self.assertEqual(ctx.check_container.call_count, 2)

    def test_stopped_is_idempotent_and_error_is_not_rebuilt(self):
        for phase in ("Stopped", "Error"):
            ctx = Mock()
            ctx.observe.return_value = phase
            with patch.object(MOD.subprocess, "run"):
                if phase == "Stopped":
                    MOD.stop_exact(ctx)
                else:
                    with self.assertRaises(RuntimeError):
                        MOD.stop_exact(ctx)
            ctx.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
