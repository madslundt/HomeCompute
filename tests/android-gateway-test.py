#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "deploy/codex-worker"))
spec = importlib.util.spec_from_file_location("android_notifications", ROOT / "scripts/android_notifications.py")
notifications = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = notifications
spec.loader.exec_module(notifications)


class AndroidGatewayTests(unittest.TestCase):
    def frame(self, kind="NOTIFICATION_POSTED", package="com.facebook.orca", text="Hello"):
        return {"type": kind, "timestamp": 1780000000000, "payload": {
            "package": package, "title": "Peter", "text": text, "key": "notif-1", "id": 7,
            "post_time": 1780000000000}}

    def test_normalizes_current_and_legacy_removed_events(self):
        current = notifications.normalize(self.frame(), "tablet-1")
        self.assertEqual(current["event_type"], "NOTIFICATION_POSTED")
        self.assertEqual(current["application_package"], "com.facebook.orca")
        legacy = {"type": "NOTIFICATION", "timestamp": 1780000000000,
                  "payload": {"package": "com.facebook.orca", "key": "notif-1", "removed": True}}
        self.assertEqual(notifications.normalize(legacy, "tablet-1")["event_type"], "NOTIFICATION_REMOVED")

    def test_normalizes_rpc_wrapper(self):
        event = notifications.normalize({"method": "events/device", "params": self.frame()}, "tablet-1")
        self.assertEqual(event["device_id"], "tablet-1")

    def test_missing_systemd_credentials_still_uses_retryable_paths(self):
        portal, webhook = notifications.credential_paths({})
        self.assertEqual(portal.name, "portal-token")
        self.assertEqual(webhook.name, "n8n-webhook-token")
        self.assertIn("homecompute-android-notifications.service", str(portal.parent))

    def test_websocket_auth_uses_the_loaded_portal_credential(self):
        class Socket:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def recv(self, timeout): raise KeyboardInterrupt
        class Server:
            def serve_forever(self): pass
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "android-notifications.json"
            config.write_text(json.dumps({"device_id": "tablet-1", "websocket_url": "ws://tablet:8081",
                "allowed_packages": ["com.facebook.orca"], "webhook_url": "http://127.0.0.1:15678/hook"}))
            credentials = root / "credentials"
            credentials.mkdir()
            portal_token = credentials / "portal-token"
            webhook_token = credentials / "n8n-webhook-token"
            portal_token.write_text("synthetic-portal-token")
            webhook_token.write_text("synthetic-webhook-token")
            websocket = types.ModuleType("websockets")
            websocket.__path__ = []
            sync = types.ModuleType("websockets.sync")
            sync.__path__ = []
            client = types.ModuleType("websockets.sync.client")
            client.connect = Mock(return_value=Socket())
            sync.client = client
            websocket.sync = sync
            modules = {"websockets": websocket, "websockets.sync": sync, "websockets.sync.client": client}
            environment = {"ANDROID_CONFIG": str(config), "CREDENTIALS_DIRECTORY": str(credentials),
                           "STATE_DIRECTORY": str(root / "state")}
            with patch.dict(sys.modules, modules), patch.dict("os.environ", environment), \
                 patch.object(notifications, "health_server", return_value=Server()):
                with self.assertRaises(KeyboardInterrupt):
                    notifications.run(config, portal_token, webhook_token)
            self.assertEqual(client.connect.call_args.kwargs["additional_headers"],
                             {"Authorization": "Bearer synthetic-portal-token"})

    def test_allowlist_and_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            listener = notifications.Listener({"device_id": "tablet-1", "allowed_packages": ["com.facebook.orca"],
                "webhook_url": "http://127.0.0.1/hook"}, "portal-secret", "hook-secret", Path(temp) / "seen.db")
            self.assertFalse(listener.process(self.frame(package="com.other.app")))
            with patch("android_notifications.urllib.request.urlopen") as send:
                send.return_value.__enter__.return_value.status = 202
                self.assertTrue(listener.process(self.frame()))
                self.assertFalse(listener.process(self.frame()))
                self.assertEqual(send.call_count, 1)
            listener.dedup.db.close()

    def test_webhook_failure_does_not_poison_deduplication(self):
        listener = notifications.Listener({"device_id": "tablet-1", "allowed_packages": ["com.facebook.orca"],
            "webhook_url": "http://127.0.0.1/hook"}, "portal-secret", "hook-secret")
        with patch("android_notifications.urllib.request.urlopen", side_effect=[OSError(), _response(202)]) as send:
            with self.assertRaises(OSError):
                listener.process(self.frame())
            self.assertTrue(listener.process(self.frame()))
            self.assertEqual(send.call_count, 2)

    def test_missing_webhook_credential_fails_closed(self):
        listener = notifications.Listener({"device_id": "tablet-1", "allowed_packages": ["com.facebook.orca"],
            "webhook_url": "http://127.0.0.1/hook"}, "portal-secret", "")
        with patch("android_notifications.urllib.request.urlopen") as send:
            with self.assertRaises(OSError):
                listener.process(self.frame())
        send.assert_not_called()

    def test_reconnect_backoff_grows_and_caps(self):
        delays = [1]
        for _ in range(8):
            delays.append(notifications.next_backoff(delays[-1]))
        self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 60, 60, 60])

    def test_persistent_deduplication_contains_only_digests(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "seen.db"
            event = notifications.normalize(self.frame(), "tablet-1")
            first = notifications.Deduplicator(database)
            self.assertTrue(first.accept(event))
            first.db.close()
            second = notifications.Deduplicator(database)
            self.assertFalse(second.accept(event))
            rows = second.db.execute("SELECT digest FROM seen").fetchall()
            self.assertTrue(rows and len(rows[0][0]) == 64)
            self.assertNotIn("Peter", str(rows))
            self.assertNotIn("Hello", str(rows))
            second.db.close()

    def test_android_portal_has_scoped_controls_and_authenticates_requests(self):
        spec = importlib.util.spec_from_file_location("openclaw_capabilities", ROOT / "scripts/openclaw_capabilities.py")
        capabilities = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = capabilities
        spec.loader.exec_module(capabilities)
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "android-gateway.json"
            token = Path(temp) / "portal-token"
            config.write_text(json.dumps({"device_id": "tablet-1", "base_url": "http://127.0.0.1:8080",
                                          "token_file": str(token)}))
            token.write_text("0123456789abcdef0123456789abcdef")
            portal = capabilities.AndroidPortal()
            portal.token_file = token
            portal.base_url = "http://127.0.0.1:8080"
            with patch.object(capabilities, "Path", return_value=config):
                portal = capabilities.AndroidPortal()
            portal.token_file = token
            with patch("openclaw_capabilities.urllib.request.urlopen") as fetch:
                fetch.return_value.__enter__.return_value.read.return_value = b'{"status":"success"}'
                result = portal.request("homecompute_android_status", {})
                request = fetch.call_args.args[0]
                self.assertEqual(request.get_header("Authorization"), "Bearer 0123456789abcdef0123456789abcdef")
                self.assertTrue(result["available"])
                portal.request("homecompute_android_tap", {"x": 100, "y": 200})
                action = fetch.call_args.args[0]
                self.assertEqual(action.method, "POST")
                self.assertEqual(action.full_url, "http://127.0.0.1:8080/tap")
                self.assertEqual(json.loads(action.data), {"x": 100, "y": 200})
                self.assertEqual(action.get_header("Authorization"), "Bearer 0123456789abcdef0123456789abcdef")
                portal.request("homecompute_android_type_text", {"text": "hello"})
                typed = fetch.call_args.args[0]
                self.assertEqual(typed.full_url, "http://127.0.0.1:8080/keyboard/input")
                self.assertEqual(json.loads(typed.data), {"base64_text": "aGVsbG8=", "clear": False})
                portal.request("homecompute_android_swipe", {"start_x": 1, "start_y": 2,
                              "end_x": 3, "end_y": 4, "duration_ms": 500})
                swiped = fetch.call_args.args[0]
                self.assertEqual(json.loads(swiped.data),
                                 {"startX": 1, "startY": 2, "endX": 3, "endY": 4, "duration": 500})
                portal.request("homecompute_android_navigation_key", {"key": "BACK"})
                keyed = fetch.call_args.args[0]
                self.assertEqual(json.loads(keyed.data), {"key_code": 4})
            with self.assertRaises(ValueError): portal.validate("homecompute_android_tap", {"x": -1, "y": 1})
            with self.assertRaises(ValueError): portal.validate("homecompute_android_type_text", {"text": "bad\ninput"})
            with patch("openclaw_capabilities.urllib.request.urlopen", side_effect=capabilities.urllib.error.URLError("offline")):
                unavailable = portal.request("homecompute_android_status", {})
                self.assertFalse(unavailable["available"])
            expected = {row["name"] for row in capabilities.ANDROID_READ_TOOLS + capabilities.ANDROID_ACTION_TOOLS}
            self.assertEqual({row["name"] for row in portal.tools}, expected)
            self.assertTrue(all(row["annotations"].get("readOnlyHint") for row in portal.tools
                                if row["name"] not in capabilities.ANDROID_ACTION_NAMES))
            self.assertTrue(all(not row["annotations"].get("readOnlyHint") for row in portal.tools
                                if row["name"] in capabilities.ANDROID_ACTION_NAMES))
            self.assertFalse(any("sms" in row["name"] or "message" in row["name"] for row in portal.tools))

    def test_openbot_principal_inventory_contains_only_android_tools(self):
        spec = importlib.util.spec_from_file_location("openclaw_capabilities", ROOT / "scripts/openclaw_capabilities.py")
        capabilities = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = capabilities
        spec.loader.exec_module(capabilities)
        names = {row["name"] for row in capabilities.ANDROID_READ_TOOLS + capabilities.ANDROID_ACTION_TOOLS}
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "android-gateway.json"
            config.write_text(json.dumps({"device_id": "tablet-1", "base_url": "http://127.0.0.1:8080",
                                          "token_file": str(Path(temp) / "portal-token")}))
            with patch.object(capabilities, "Path", return_value=config):
                tools = capabilities.AndroidOnlyServices().tools
        adapter_spec = importlib.util.spec_from_file_location("mcp_adapter", ROOT / "deploy/codex-worker/mcp_adapter.py")
        adapter = importlib.util.module_from_spec(adapter_spec)
        sys.modules[adapter_spec.name] = adapter
        adapter_spec.loader.exec_module(adapter)
        ledger = types.SimpleNamespace(read_only_only=True, services=types.SimpleNamespace(tools=tools),
                                       actions=None, infrastructure=None, home_assistant=None,
                                       maintenance=None, operations_search=None)
        self.assertEqual({row["name"] for row in adapter.tool_inventory(ledger)}, names)


def _response(status):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def __init__(self): self.status = status
    return Response()


if __name__ == "__main__":
    unittest.main()
