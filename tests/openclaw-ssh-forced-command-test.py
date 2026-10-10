#!/usr/bin/env python3
"""Forced SSH grammar, environment isolation, and child resource bounds."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("forced", ROOT / "scripts/openclaw-ssh-forced-command.py")
FORCED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FORCED)
CHAT_SPEC = importlib.util.spec_from_file_location("chat", ROOT / "scripts/openclaw-chat.py")
CHAT = importlib.util.module_from_spec(CHAT_SPEC)
CHAT_SPEC.loader.exec_module(CHAT)
SESSION = "683dfe77-bdc2-4db7-a544-a9a213b98292"


def agent(message="42?"):
    return ["agent", "--agent", "main", "--session-id", SESSION, "--message", message,
            "--timeout", "45", "--thinking", "off", "--json"]


def remote(arguments):
    return shlex.join(FORCED.PREFIX + arguments)


class ForcedCommandTests(unittest.TestCase):
    def test_actual_console_wire_grammar_and_health_are_accepted(self):
        self.assertEqual(FORCED.PREFIX, CHAT.PREFIX)
        for arguments in (agent(), FORCED.HEALTH):
            self.assertEqual(FORCED.validate_command(CHAT.ssh_command(arguments)[-1]), arguments)

    def test_shell_metacharacters_remain_only_message_data(self):
        text = "' $(touch /tmp/escape); `hostname`\n--deliver --model evil"
        self.assertEqual(FORCED.validate_command(remote(agent(text)))[6], text)

    def test_foreign_command_environment_and_shell_chains_are_refused(self):
        commands = ["", "bash", remote(agent()) + "; hostname", remote(agent()) + " && true",
                    remote(agent()).replace("NEMOCLAW_GATEWAY_PORT=9123", "NEMOCLAW_GATEWAY_PORT=8080"),
                    remote(agent()).replace("PATH=" + FORCED.PATH, "PATH=/tmp/attacker"),
                    "env LD_PRELOAD=/tmp/evil " + remote(agent()), remote(agent()) + "\0"]
        for command in commands:
            with self.subTest(command=command[:40]), self.assertRaises(FORCED.Refused):
                FORCED.validate_command(command)

    def test_session_agent_flags_timeouts_and_health_overrides_are_refused(self):
        variants = [agent() + ["--deliver"], agent() + ["--model", "foreign"],
                    ["gateway", "call", "config.get", "--json"], ["devices", "approve", SESSION]]
        for index, value in ((2, "other"), (4, "not-uuid"), (4, SESSION.upper()), (8, "120"), (10, "high")):
            candidate = agent(); candidate[index] = value; variants.append(candidate)
        variants += [FORCED.HEALTH + ["--token", "private"], ["gateway", "health", "--timeout", "20000", "--json"]]
        for arguments in variants:
            with self.subTest(arguments=arguments[:5]), self.assertRaises(FORCED.Refused):
                FORCED.validate_command(remote(arguments))

    def test_utf8_message_and_raw_command_budgets_are_enforced(self):
        self.assertEqual(FORCED.validate_command(remote(agent("é" * 8192)))[6], "é" * 8192)
        for message in ("", " ", "é" * 8193, "x\0y"):
            with self.assertRaises(FORCED.Refused):
                FORCED.validate_command(remote(agent(message)))
        with self.assertRaises(FORCED.Refused):
            FORCED.validate_command("x" * (FORCED.MAX_COMMAND + 1))

    def child_factory(self, program):
        actual = subprocess.Popen
        calls, children = [], []
        def launch(argv, **kwargs):
            calls.append((argv, kwargs))
            child = actual([sys.executable, "-c", program], stdout=kwargs["stdout"],
                           stderr=kwargs["stderr"], stdin=kwargs["stdin"], start_new_session=True)
            children.append(child)
            return child
        return launch, calls, children

    def test_child_uses_absolute_argv_fresh_environment_and_no_shell(self):
        launch, calls, _ = self.child_factory("import sys;print('{\"ok\":true}');sys.exit(1)")
        with patch.dict(os.environ, {"LD_PRELOAD": "PRIVATE", "BASH_ENV": "PRIVATE"}), \
                patch.object(FORCED.subprocess, "Popen", side_effect=launch):
            code, output = FORCED.execute(FORCED.HEALTH)
        argv, kwargs = calls[0]
        self.assertEqual(code, 1)
        self.assertEqual(output.strip(), b'{"ok":true}')
        self.assertEqual(argv, [FORCED.HOME + "/.local/bin/nemoclaw", "agent-openclaw", "exec", "--",
                                "openclaw", *FORCED.HEALTH])
        self.assertEqual(kwargs["env"], FORCED.ENV)
        self.assertNotIn("LD_PRELOAD", kwargs["env"])
        self.assertNotIn("BASH_ENV", kwargs["env"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_output_budget_refusal_terminates_child(self):
        launch, _, children = self.child_factory("import sys,time;print('x'*100,flush=True);time.sleep(30)")
        with patch.object(FORCED.subprocess, "Popen", side_effect=launch), patch.object(FORCED, "MAX_OUTPUT", 32):
            with self.assertRaisesRegex(FORCED.Refused, "output-budget-refused"):
                FORCED.execute(FORCED.HEALTH)
        self.assertIsNotNone(children[0].poll())

    def test_deadline_refusal_terminates_child(self):
        launch, _, children = self.child_factory("import time;time.sleep(30)")
        with patch.object(FORCED.subprocess, "Popen", side_effect=launch), \
                patch.object(FORCED.time, "monotonic", side_effect=[0, 26]):
            with self.assertRaisesRegex(FORCED.Refused, "command-timeout"):
                FORCED.execute(FORCED.HEALTH)
        self.assertIsNotNone(children[0].poll())

    def test_refusal_never_echoes_command_or_runs_native(self):
        diagnostics = io.StringIO()
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "PRIVATE secret"}), \
                patch.object(FORCED, "execute") as execute, contextlib.redirect_stderr(diagnostics):
            self.assertEqual(FORCED.main(), 2)
        execute.assert_not_called()
        self.assertNotIn("PRIVATE", diagnostics.getvalue())


if __name__ == "__main__":
    unittest.main()
