#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pairing", ROOT / "scripts/approve-native-openclaw-cli.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
REQUEST = "aaaaaaaa-0000-4000-8000-000000000001"


def devices():
    return {"pending": [{"requestId": REQUEST, "deviceId": "local-device", "clientId": "cli",
                         "clientMode": "cli", "platform": "linux", "role": "operator",
                         "roles": ["operator"], "scopes": ["operator.admin"],
                         "isRepair": True, "remoteIp": None}],
            "paired": [{"deviceId": "local-device", "clientId": "cli", "clientMode": "cli"}]}


class PairingTests(unittest.TestCase):
    def test_exact_trusted_request_accepted(self):
        self.assertEqual(MOD.validate_request(devices(), REQUEST, ["local-device"]), REQUEST)

    def test_foreign_identity_extra_scope_and_remote_request_refused(self):
        for field, value in (("clientId", "unknown"), ("platform", "other"), ("isRepair", False),
                             ("remoteIp", "10.1.2.3"), ("scopes", ["operator.admin", "other"]),
                             ("roles", ["operator", "other"])):
            data = devices()
            data["pending"][0][field] = value
            with self.assertRaises(MOD.PairingRefused):
                MOD.validate_request(data, REQUEST, ["local-device"])
        with self.assertRaises(MOD.PairingRefused):
            MOD.validate_request(devices(), REQUEST, ["different-device"])

    def test_new_unpaired_device_and_ambiguous_request_refused(self):
        data = devices()
        data["paired"] = []
        with self.assertRaises(MOD.PairingRefused):
            MOD.validate_request(data, REQUEST, ["local-device"])
        data = devices()
        data["pending"].append(copy.deepcopy(data["pending"][0]))
        with self.assertRaises(MOD.PairingRefused):
            MOD.validate_request(data, REQUEST, ["local-device"])

    def test_qualified_scope_never_approves_any_device(self):
        ctx = Mock()
        ctx.observe.return_value = "Ready"
        ctx.run.return_value = (0, b'{"approvals":{}}')
        MOD.ensure_cli(ctx)
        self.assertEqual(ctx.run.call_count, 1)
        self.assertNotIn("agent", ctx.run.call_args.args[0])

    def test_approval_only_after_rpc_exact_metadata_and_native_identity(self):
        ctx = Mock(cli="/fixed/nemoclaw")
        ctx.observe.return_value = "Ready"
        ctx.run.side_effect = [(1, f"scope upgrade pending approval (requestId: {REQUEST})".encode()),
                               (0, json.dumps(devices()).encode()), (0, b'["local-device"]'),
                               (0, b'{}'), (0, b'{}')]
        MOD.ensure_cli(ctx)
        self.assertEqual(ctx.run.call_args_list[3].args[0][-4:], ["devices", "approve", REQUEST, "--json"])
        self.assertTrue(all("agent" not in call.args[0] for call in ctx.run.call_args_list))

    def test_identity_drift_fences_read_and_approval(self):
        ctx = Mock()
        ctx.check_registry.side_effect = RuntimeError("identity changed")
        with self.assertRaises(RuntimeError):
            MOD.ensure_cli(ctx)
        ctx.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
