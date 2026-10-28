#!/usr/bin/env python3
"""Dedicated phone ingress boundaries and restart replay; no live bot credentials."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_notifications import Outbox
spec = importlib.util.spec_from_file_location("telegram_receiver", ROOT / "scripts/openclaw-telegram.py")
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
adapter_spec = importlib.util.spec_from_file_location("phone_adapter", ROOT / "scripts/openclaw-communication.py")
adapter = importlib.util.module_from_spec(adapter_spec)
adapter_spec.loader.exec_module(adapter)
CFG = {"bot_id": "9876", "chat_id": "1234", "user_id": "5678",
       "destination": "operator-private", "conversation": "operator"}


def update(text="hello", identity=42):
    return {"update_id": identity, "message": {"chat": {"id": 1234, "type": "private"},
            "from": {"id": 5678, "is_bot": False}, "text": text}}


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.cursor = receiver.Cursor(self.path / "state", CFG)

    def tearDown(self):
        self.cursor.lock.close()
        self.temp.cleanup()

    def test_plain_text_and_bot_scoped_update_identity(self):
        body = receiver.envelope(update(), CFG)
        self.assertEqual(body["text"], "hello")
        self.assertEqual(body["request_id"], "telegram:9876:42")
        self.assertEqual(body["destination"], "operator-private")

    def test_unapproved_sender_group_bot_and_nontext_are_discarded(self):
        for change in ({"chat": {"id": 9999, "type": "private"}},
                       {"chat": {"id": 1234, "type": "group"}},
                       {"from": {"id": 9999, "is_bot": False}},
                       {"from": {"id": 5678, "is_bot": True}},
                       {"from": {"id": 5678}}, {"text": None}, {"text": " "}, {"text": "x" * 4001}):
            value = update()
            value["message"].update(change)
            self.assertIsNone(receiver.envelope(value, CFG))
        for value in ({"update_id": True}, {"update_id": -1}, {"update_id": "42"}):
            with self.assertRaises(ValueError):
                receiver.envelope(value, CFG)

    def test_cursor_advances_only_after_durable_admission(self):
        seen = []
        def poll(method, body):
            seen.append((method, body))
            return [update()]
        receiver.receive_once(self.cursor, CFG, poll, lambda _: {"state": "completed"})
        self.assertEqual(self.cursor.offset, 43)
        self.assertEqual(seen[0][1]["offset"], 0)
        self.assertEqual(seen[0][1]["allowed_updates"], ["message"])
        saved = json.loads(self.cursor.path.read_text())
        self.assertEqual(saved["offset"], 43)
        self.assertEqual(self.cursor.path.stat().st_mode & 0o777, 0o600)

    def test_lost_adapter_receipt_keeps_same_request_on_restart(self):
        requests = []
        def submit(body):
            requests.append(body)
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], submit)
        self.assertEqual(self.cursor.offset, 0)
        self.cursor.lock.close()
        self.cursor = receiver.Cursor(self.path / "state", CFG)
        receiver.receive_once(self.cursor, CFG, lambda *_: [update()],
                              lambda body: requests.append(body) or {"state": "completed"})
        self.assertEqual(requests[0], requests[1])

    def test_uncertain_turn_persists_pause_and_blocks_restart(self):
        with self.assertRaises(ValueError):
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lambda _: {"state": "uncertain"})
        self.assertTrue(self.cursor.paused)
        self.cursor.lock.close()
        with self.assertRaises(ValueError):
            receiver.Cursor(self.path / "state", CFG)

    def test_supervised_lost_receipt_blocks_automatic_readmission(self):
        self.cursor.lock.close()
        self.cursor = receiver.Cursor(self.path / "state", CFG, supervised=True)
        def lost(_):
            raise TimeoutError("synthetic response loss")
        with self.assertRaises(TimeoutError):
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lost)
        self.assertEqual(self.cursor.offset, 0)
        self.assertTrue(self.cursor.admission.exists())
        self.assertEqual(self.cursor.admission.stat().st_mode & 0o777, 0o600)
        self.cursor.lock.close()
        with self.assertRaises(ValueError):
            receiver.Cursor(self.path / "state", CFG, supervised=True)

    def test_supervised_completed_admission_clears_guard_after_cursor_commit(self):
        self.cursor.lock.close()
        self.cursor = receiver.Cursor(self.path / "state", CFG, supervised=True)
        receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lambda _: {"state": "completed"})
        self.assertEqual(self.cursor.offset, 43)
        self.assertFalse(self.cursor.admission.exists())
        # A crash between cursor commit and guard unlink is also safe to recover.
        self.cursor.admission.write_text(json.dumps({"update_id": 42}))
        os.chmod(self.cursor.admission, 0o600)
        self.cursor.lock.close()
        self.cursor = receiver.Cursor(self.path / "state", CFG, supervised=True)
        self.assertFalse(self.cursor.admission.exists())
        self.assertEqual(self.cursor.offset, 43)

    def test_supervised_reconnects_only_read_only_transient_errors(self):
        for error in (TimeoutError(), urllib.error.URLError("offline"), ConnectionError()):
            self.assertTrue(receiver.reconnect_safe(error))
        for code in (401, 403, 409, 429, 503):
            error = urllib.error.HTTPError("synthetic", code, "synthetic", {}, None)
            self.assertEqual(receiver.reconnect_safe(error), code in (429, 503))
            error.close()
        self.assertFalse(receiver.reconnect_safe(ValueError("pinned identity changed")))

    def test_supervised_exit_code_restarts_poll_failure_but_stops_lost_admission(self):
        directory = self.path / "supervised"
        directory.mkdir(mode=0o700)
        for interrupted in (False, True):
            if interrupted:
                (directory / "telegram-admission.json").write_text('{"update_id": 42}')
            argv = ["receiver", "run", "--tokens", "unused", "--config", "unused",
                    "--state", str(directory), "--supervised"]
            with patch.object(sys, "argv", argv), \
                 patch.object(receiver, "load_tokens", return_value={"bot_token": "synthetic", "conversation_token": "synthetic"}), \
                 patch.object(receiver, "load_config", return_value=CFG), \
                 patch.object(receiver, "qualify", return_value={"id": 9876}), \
                 patch.object(receiver, "Cursor", return_value=self.cursor), \
                 patch.object(receiver, "receive_once", side_effect=TimeoutError()), \
                 patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
                receiver.main()
            self.assertEqual(caught.exception.code, 0 if interrupted else 1)

    def test_bad_receipt_and_old_update_do_not_move_cursor(self):
        with self.assertRaises(ValueError):
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lambda _: {"ok": True})
        self.assertEqual(self.cursor.offset, 0)
        self.cursor.advance(50)
        with self.assertRaises(ValueError):
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lambda _: {"state": "completed"})
        self.assertEqual(self.cursor.offset, 51)

    def test_wrong_sender_is_consumed_without_contacting_assistant(self):
        value = update()
        value["message"]["from"]["id"] = 9999
        def forbidden(_):
            self.fail("unapproved update reached assistant")
        receiver.receive_once(self.cursor, CFG, lambda *_: [value], forbidden)
        self.assertEqual(self.cursor.offset, 43)

    def test_webhook_and_wrong_bot_stop_before_polling(self):
        for webhook, bot in (("https://existing.example/webhook", 9876), ("", 9999)):
            def call(method, _):
                if method == "getMe":
                    return {"id": bot, "is_bot": True}
                return {"url": webhook}
            with self.assertRaises(ValueError):
                receiver.qualify(call, "9876")

    def test_bot_api_cannot_send_or_change_webhook(self):
        for method in ("sendMessage", "setWebhook", "deleteWebhook"):
            with self.assertRaises(ValueError):
                receiver.bot_call("synthetic", method, {})
        with self.assertRaises(ValueError):
            receiver.ClosedRedirect().redirect_request(None, None, None, None, None, None)

    def test_private_credentials_and_pinned_config(self):
        secret = self.path / "tokens.json"
        secret.write_text(json.dumps({"bot_token": "9876:" + "x" * 35, "conversation_token": "y" * 32}))
        os.chmod(secret, 0o600)
        self.assertEqual(len(receiver.load_tokens(secret)), 2)
        os.chmod(secret, 0o644)
        with self.assertRaises(ValueError):
            receiver.load_tokens(secret)
        link = self.path / "link.json"
        link.symlink_to(secret)
        with self.assertRaises(ValueError):
            receiver.load_tokens(link)
        config = self.path / "config.json"
        config.write_text(json.dumps(CFG))
        self.assertEqual(receiver.load_config(config), CFG)
        config.write_text(json.dumps({**CFG, "chat_id": "replace-chat"}))
        with self.assertRaises(ValueError):
            receiver.load_config(config)

    def test_cursor_cannot_switch_destination_or_bot(self):
        self.cursor.advance(42)
        self.cursor.lock.close()
        with self.assertRaises(ValueError):
            receiver.Cursor(self.path / "state", {**CFG, "bot_id": "9999"})

    def test_read_only_poll_retries_with_unchanged_cursor(self):
        calls, waits = [], []
        def call(method, body):
            calls.append((method, dict(body)))
            if len(calls) < 3:
                raise urllib.error.URLError("synthetic transient failure")
            return [update()]
        self.assertEqual(receiver.poll(call, {"offset": 42}, waits.append), [update()])
        self.assertEqual(waits, [2, 4])
        self.assertTrue(all(item == ("getUpdates", {"offset": 42}) for item in calls))
        self.assertEqual(self.cursor.offset, 0)

    def test_auth_or_competing_poller_error_never_retries(self):
        for code in (401, 403, 409):
            waits = []
            def call(*_):
                raise urllib.error.HTTPError("synthetic", code, "synthetic", {}, None)
            with self.assertRaises(urllib.error.HTTPError):
                receiver.poll(call, {}, waits.append)
            self.assertEqual(waits, [])

    def test_cursor_lock_and_temporary_symlinks_are_refused(self):
        victim = self.path / "victim"
        victim.write_text("preserved")
        self.cursor.path.with_suffix(".tmp").symlink_to(victim)
        with self.assertRaises(ValueError):
            self.cursor.advance(42)
        self.assertEqual(victim.read_text(), "preserved")
        other = self.path / "other"
        other.mkdir(mode=0o700)
        (other / "telegram.lock").symlink_to(victim)
        with self.assertRaises(ValueError):
            receiver.Cursor(other, CFG)

    def test_phone_to_http_adapter_replay_produces_one_outbox_reply(self):
        settings = {"destination": CFG["destination"], "conversations": [CFG["conversation"]],
                    "projects": [], "actions": {}, "timezone": "Europe/Copenhagen",
                    "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}
        tokens = {role: role + "x" * 40 for role in ("collector", "conversation", "delivery")}
        store = Outbox(self.path / "transport.sqlite3", settings)
        calls = []
        def model(name, text):
            calls.append((name, text))
            return "Synthetic phone reply"
        server = adapter.serve(store, tokens, {}, model, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def submit(body):
            return receiver.post(f"http://127.0.0.1:{server.server_port}/conversation", body,
                                 tokens["conversation"], 3)
        def lost_receipt(body):
            submit(body)
            raise TimeoutError("synthetic response loss")
        try:
            with self.assertRaises(TimeoutError):
                receiver.receive_once(self.cursor, CFG, lambda *_: [update()], lost_receipt)
            receiver.receive_once(self.cursor, CFG, lambda *_: [update()], submit)
            self.assertEqual(calls, [("operator", "hello")])
            self.assertEqual(self.cursor.offset, 43)
            event = store.claim(time.time())
            self.assertEqual(event["kind"], "reply")
            self.assertEqual(event["destination"], CFG["destination"])
            self.assertEqual(event["text"], "Synthetic phone reply")
            self.assertIsNone(store.claim(time.time()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            store.db.close()

    def test_token_setup_uses_hidden_input_private_file_and_no_network(self):
        path = self.path / "private" / "tokens.json"
        token = "9876:" + "x" * 35
        output = io.StringIO()
        with patch.object(sys, "argv", ["receiver", "store-token", "--tokens", str(path)]), \
             patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(receiver.getpass, "getpass", return_value=token), \
             patch.object(receiver, "post", side_effect=AssertionError("must stay offline")), \
             patch.object(sys, "stdout", output):
            receiver.main()
        self.assertNotIn(token, output.getvalue())
        self.assertEqual(receiver.load_tokens(path, inspect=True), {"bot_token": token})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_token_setup_refuses_noninteractive_input(self):
        with patch.object(sys, "argv", ["receiver", "store-token", "--tokens", str(self.path / "secret")]), \
             patch.object(sys.stdin, "isatty", return_value=False), \
             patch.object(receiver.getpass, "getpass", side_effect=AssertionError("must not prompt")), \
             patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            receiver.main()

    def test_token_setup_refuses_visible_getpass_fallback(self):
        path = self.path / "private" / "tokens.json"
        def visible_fallback(*_):
            warnings.warn("echo suppression unavailable", receiver.getpass.GetPassWarning)
            self.fail("warning must interrupt before visible fallback")
        with patch.object(sys, "argv", ["receiver", "store-token", "--tokens", str(path)]), \
             patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(receiver.getpass, "getpass", side_effect=visible_fallback), \
             patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            receiver.main()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
