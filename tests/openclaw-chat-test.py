#!/usr/bin/env python3
"""Fixed operator transport, private durable sessions and uncertain-turn handling."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openclaw_chat", ROOT / "scripts/openclaw-chat.py")
CHAT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHAT)


def receipt(session_id, text="42"):
    return {"status": "ok", "secret": "PRIVATE", "result": {
        "payloads": [{"text": text}], "meta": {
            "replayInvalid": False, "aborted": False, "private": "PRIVATE",
            "agentMeta": {"sessionId": session_id, "provider": "inference", "model": "automation-moe",
                "terminalReceipt": {"sessionId": session_id, "rerouted": False,
                    "successfulToolNames": [], "effective": {"provider": "inference", "model": "automation-moe"}}}}}}


class ChatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "private"
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        remote = shlex.split(argv[-1])
        session = remote[remote.index("--session-id") + 1]
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt(session)), "PRIVATE")

    def test_destination_auth_and_route_are_fixed(self):
        answer = CHAT.send("42?", "test", directory=self.directory, runner=self.runner)
        argv, kw = self.calls[0]
        self.assertEqual(argv[-2], "hermes-operator@10.77.20.2")
        self.assertEqual(argv[argv.index("-J") + 1], "home-core")
        self.assertEqual(argv[argv.index("-i") + 1], str(Path.home() / ".ssh/id_ed25519_ai-services-01"))
        for option in ("BatchMode=yes", "StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "ConnectionAttempts=1"):
            self.assertIn(option, argv)
        remote = shlex.split(argv[-1])
        self.assertEqual(remote[:len(CHAT.PREFIX)], CHAT.PREFIX)
        self.assertEqual(remote[remote.index("--agent") + 1], "main")
        self.assertEqual(remote[remote.index("--timeout") + 1], "45")
        for denied in ("--local", "--deliver", "--model", "--url", "--token", "--reply-to"):
            self.assertNotIn(denied, remote)
        self.assertEqual(kw["timeout"], 65)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertNotIn("PRIVATE", json.dumps(answer))

    def test_message_is_one_shell_quoted_argument(self):
        message = "quote' $(touch /tmp/escape); `hostname`\n--deliver --model evil"
        CHAT.send(message, "quote", directory=self.directory, runner=self.runner)
        remote = shlex.split(self.calls[0][0][-1])
        self.assertEqual(remote[remote.index("--message") + 1], message)
        self.assertEqual(remote.count("--message"), 1)

    def test_session_persists_privately_and_conversations_are_separate(self):
        a = CHAT.send("first", "one", directory=self.directory, runner=self.runner)
        b = CHAT.send("second", "one", directory=self.directory, runner=self.runner)
        c = CHAT.send("third", "two", directory=self.directory, runner=self.runner)
        self.assertEqual(a["session_id"], b["session_id"])
        self.assertNotEqual(a["session_id"], c["session_id"])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / "one.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads((self.directory / "one.json").read_text())["uncertain"])

    def test_names_and_message_budgets_fail_before_ssh(self):
        for message, name in (("hi", "../bad"), ("hi", "--host"), ("", "ok"), ("x\0y", "ok"), ("é" * 8193, "ok")):
            with self.assertRaises(CHAT.ChatError):
                CHAT.send(message, name, directory=self.directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_private_state_rejects_symlink_and_insecure_mode(self):
        self.directory.mkdir(mode=0o700)
        target = self.directory / "target"
        target.write_text("PRIVATE")
        (self.directory / "test.json").symlink_to(target)
        with self.assertRaises(OSError):
            CHAT.send("hi", "test", directory=self.directory, runner=self.runner)
        self.directory.chmod(0o755)
        with self.assertRaises(CHAT.ChatError):
            CHAT.send("hi", "test", directory=self.directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_concurrent_conversation_turn_fails_without_dispatch(self):
        with CHAT.conversation("same", self.directory):
            with self.assertRaises(CHAT.ChatError):
                CHAT.send("hi", "same", directory=self.directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_unknown_native_failure_is_durable_and_never_retried(self):
        calls = []
        def failing(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 65, output="PRIVATE")
        with self.assertRaises(CHAT.ChatError) as failure:
            CHAT.send("hi", "timeout", directory=self.directory, runner=failing)
        self.assertNotIn("PRIVATE", str(failure.exception))
        with self.assertRaises(CHAT.ChatError):
            CHAT.send("hi", "timeout", directory=self.directory, runner=failing)
        self.assertEqual(len(calls), 1)
        self.assertTrue(json.loads((self.directory / "timeout.json").read_text())["uncertain"])

    def test_receipt_rejects_replay_side_effects_reroutes_and_session_mismatch(self):
        session = "683dfe77-bdc2-4db7-a544-a9a213b98292"
        data = receipt(session)
        mutations = [lambda d: d.update(status="error"),
            lambda d: d["result"]["meta"].update(replayInvalid=True),
            lambda d: d["result"]["meta"].update(aborted=True),
            lambda d: d["result"]["meta"]["agentMeta"].update(sessionId="other"),
            lambda d: d["result"]["meta"]["agentMeta"].update(model="other"),
            lambda d: d["result"]["meta"]["agentMeta"]["terminalReceipt"].update(rerouted=True),
            lambda d: d["result"]["meta"]["agentMeta"]["terminalReceipt"].update(successfulToolNames=["write"]),
            lambda d: d["result"].update(payloads=[])]
        for mutate in mutations:
            candidate = copy.deepcopy(data); mutate(candidate)
            with self.assertRaises(CHAT.ChatError): CHAT.parse_turn(candidate, session)
        self.assertEqual(CHAT.parse_turn(data, session)["text"], "42")

    def test_native_failures_do_not_expose_diagnostics(self):
        for returncode, stdout in ((1, "PRIVATE"), (0, "not-json PRIVATE"), (0, "[]"), (0, "x" * (CHAT.MAX_RECEIPT + 1))):
            def runner(argv, **kw): return subprocess.CompletedProcess(argv, returncode, stdout, "PRIVATE")
            with self.assertRaises(CHAT.ChatError) as failure: CHAT.run_native(["gateway", "health"], runner)
            self.assertNotIn("PRIVATE", str(failure.exception))

    def test_status_projects_only_health_and_local_session(self):
        def health(argv, **kw):
            remote = shlex.split(argv[-1])
            self.assertEqual(remote[len(CHAT.PREFIX):], ["gateway", "health", "--timeout", "10000", "--json"])
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "sessions": "PRIVATE", "token": "SECRET"}), "")
        result = CHAT.status("status", directory=self.directory, runner=health)
        self.assertTrue(result["gateway_healthy"])
        self.assertNotIn("PRIVATE", json.dumps(result)); self.assertNotIn("SECRET", json.dumps(result))

    def test_terminal_control_characters_are_filtered(self):
        self.assertEqual(CHAT.visible("a\x1b[2J\x07\nb\t\x7f\x9b"), "a[2J\nb\t")


if __name__ == "__main__":
    unittest.main()
