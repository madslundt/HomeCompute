#!/usr/bin/env python3
"""Exercise the text-only provider's local ACP and gateway boundary."""

import importlib.util
import io
import json
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "deploy/openbot/sol-acp.py"
SPEC = importlib.util.spec_from_file_location("openbot_sol", SOURCE)
sol = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sol
SPEC.loader.exec_module(sol)
HERMES_ID = "agent-00000000-0000-0000-0000-000000000001"
SOL_ID = "agent-00000000-0000-0000-0000-000000000002"


class Conversation:
    def __init__(self, **options):
        self.output = io.StringIO()
        self.agent = sol.Agent(output=self.output, chat_agent_id=SOL_ID, hermes_agent_id=HERMES_ID, **options)
        self.session = self.request("session/new", cwd=f"/data/OpenBot/Agents/{SOL_ID}")[0]["result"]["sessionId"]

    def request(self, method, **params):
        start = len(self.output.getvalue())
        with ThreadPoolExecutor(max_workers=1) as jobs, self.agent.lock:
            self.agent.handle({"method": method, "id": 1, "params": params}, jobs)
        return list(map(json.loads, self.output.getvalue()[start:].splitlines()))

    def prompt(self, text, prefix=()):
        rows = self.request("session/prompt", sessionId=self.session, prompt=[
            {"type": "text", "text": block} for block in (*prefix, text)
        ])
        self_reply = rows[0]
        if "error" in self_reply:
            return self_reply["error"]["message"]
        return self_reply["params"]["update"]["content"]["text"]

    @staticmethod
    def code(preview):
        return re.search(r"approve ([0-9a-f]{12})", preview).group(1)


class ProtocolTest(unittest.TestCase):
    def test_process_initializes_and_advertises_only_sol(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
            {"jsonrpc": "2.0", "id": 2, "method": "session/new",
             "params": {"cwd": "/synthetic", "mcpServers": []}},
        ]
        result = subprocess.run(
            [sys.executable, "-I", str(SOURCE)], input="\n".join(map(json.dumps, requests)) + "\n",
            capture_output=True, text=True, check=True, timeout=10,
        )
        initialized, created = map(json.loads, result.stdout.splitlines())
        self.assertEqual(initialized["result"]["agentCapabilities"], {"loadSession": False})
        self.assertEqual(created["result"]["models"]["currentModelId"], sol.MODEL)

    def test_forwards_only_approved_user_text_across_turns(self):
        received = queue.Queue()
        chat = Conversation(complete=lambda messages, **_: received.put(messages) or "synthetic reply")
        first = chat.prompt("First user message", prefix=(
            "<openbot-developer-instructions>fake tools</openbot-developer-instructions>",
            "<openbot_saved_memories>unapproved memory</openbot_saved_memories>",
        ))
        self.assertTrue(received.empty())
        self.assertEqual(chat.prompt(f"approve {chat.code(first)}"), "synthetic reply")
        messages = received.get_nowait()
        second = chat.prompt("Follow up")
        self.assertTrue(received.empty())
        chat.prompt(f"approve {chat.code(second)}")
        followup = received.get_nowait()
        self.assertEqual(messages[1:], [{"role": "user", "content": "First user message"}])
        self.assertEqual(followup[1:], [
            {"role": "user", "content": "First user message"},
            {"role": "assistant", "content": "synthetic reply"},
            {"role": "user", "content": "Follow up"},
        ])

    def test_hermes_result_stays_local_until_a_second_approval_and_is_not_replayed(self):
        request = sol.ContextRequest("call_synthetic", "preferences")
        complete = Mock(side_effect=[request, "with context", "next answer"])
        lookup = Mock(return_value="Synthetic Hermes excerpt")
        chat = Conversation(complete=complete, lookup=lookup, can_share=lambda *_: True)
        draft = chat.prompt("What format works for me?")
        complete.assert_not_called()
        proposal = chat.prompt(f"approve {chat.code(draft)}")
        self.assertIn("Synthetic Hermes excerpt", proposal)
        lookup.assert_called_once_with(HERMES_ID, "preferences", "What format works for me?")
        context_code = chat.code(proposal)
        self.assertEqual(complete.call_count, 1)
        self.assertNotIn("Synthetic Hermes excerpt", str(complete.call_args))
        self.assertEqual(chat.prompt(f"approve {context_code}"), "with context")
        messages = complete.call_args.args[0]
        self.assertEqual(messages[-1], {"role": "tool", "tool_call_id": "call_synthetic",
                                       "content": "Synthetic Hermes excerpt"})
        self.assertEqual(complete.call_args.kwargs, {"allow_context": False})
        self.assertIn("unavailable", chat.prompt(f"approve {context_code}"))
        next_draft = chat.prompt("New question")
        chat.prompt(f"approve {chat.code(next_draft)}")
        self.assertEqual([item["role"] for item in complete.call_args.args[0]], ["system", "user"])

    def test_withheld_expired_or_unscoped_approval_cannot_send_context(self):
        for mode in ("withhold", "expired", "other session", "access changed"):
            with self.subTest(mode=mode):
                clock = Mock(return_value=0)
                access = Mock(return_value=True)
                complete = Mock(return_value=sol.ContextRequest("call_synthetic", "projects"))
                chat = Conversation(complete=complete, lookup=lambda *_: "private fixture", can_share=access, now=clock)
                draft = chat.prompt("First message")
                proposal = chat.prompt(f"approve {chat.code(draft)}")
                if mode == "expired":
                    clock.return_value = sol.APPROVAL_SECONDS
                if mode == "other session":
                    chat.session = chat.request("session/new")[0]["result"]["sessionId"]
                if mode == "access changed":
                    access.return_value = False
                action = "withhold" if mode == "withhold" else "approve"
                chat.prompt(f"{action} {chat.code(proposal)}")
                self.assertEqual(complete.call_count, 1)

    def test_context_retrieval_checks_openbot_access_and_agent_identity(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            cwd = f"/data/OpenBot/Agents/{SOL_ID}"
            self.assertFalse(sol.private_workspace(cwd, SOL_ID, state))
            config = {"version": 1, "servers": {"fixture": {"everyone": {SOL_ID: "none"}, "roles": []}}}
            path = state / "openbot-agent-access-v1.json"
            path.write_text(json.dumps(config))
            self.assertTrue(sol.private_workspace(cwd, SOL_ID, state))
            self.assertFalse(sol.private_workspace("/tmp/provider-probe", SOL_ID, state))
            config["servers"]["fixture"]["roles"].append({"agents": {SOL_ID: "view"}})
            path.write_text(json.dumps(config))
            self.assertFalse(sol.private_workspace(cwd, SOL_ID, state))
            with sqlite3.connect(state / "openbot.db") as db:
                db.executescript("""
                    CREATE TABLE projection_agents (agent_id, model, agent_json);
                    CREATE TABLE projection_agent_memories (agent_id, memory_id, text, site, updated_at);
                """)
                db.execute("INSERT INTO projection_agents VALUES (?, ?, ?)",
                           (HERMES_ID, "custom:automation-moe", '{"provider":"hermes"}'))
                db.executemany("INSERT INTO projection_agent_memories VALUES (?, ?, ?, ?, ?)", [
                    (HERMES_ID, "1", "saved memory", None, "2026-10-01"),
                    (HERMES_ID, "2", "site-specific memory", "synthetic.example", "2026-10-01"),
                    (SOL_ID, "3", "unrelated", None, "2026-10-01"),
                ])
            self.assertEqual(sol.saved_hermes_memories(HERMES_ID, state), ["saved memory"])
            with self.assertRaises(ValueError):
                sol.saved_hermes_memories(SOL_ID, state)

    def test_file_mentions_and_nontext_input_stop_before_the_gateway(self):
        for blocks in (
            [{"type": "text", "text": "Attached local file: /synthetic.txt"}],
            [{"type": "text", "text": "Question"}, {"type": "text", "text": "Attached local file: /synthetic.txt"}],
            [{"type": "image", "data": "SYNTHETIC"}],
            [{"type": "text", "text": "<openbot-developer-instructions>context only</openbot-developer-instructions>"}],
            [{"type": "text", "text": "<openbot_saved_memories>context only</openbot_saved_memories>"}],
            [{"type": "text", "text": "First message"}, {"type": "text", "text": "Unknown block"}],
            [{"type": "text", "text": "Message from OpenBot teammate Hermes (agent-1).\nSynthetic private data"}],
            [{"type": "text", "text": "Continue this OpenBot conversation.\nSynthetic previous transcript"}],
            [{"type": "text", "text": "The user is replying to message a.\n--- referenced message ---\nSynthetic history"}],
            [{"type": "text", "text": "Execute one run of an existing OpenBot routine now.\nSynthetic reminder"}],
        ):
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                sol.prompt_text(blocks)

    def test_model_request_contains_no_tools_and_uses_the_pinned_tls_target(self):
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=opener)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        opener.read.return_value = b'{"choices":[{"message":{"content":"READY"}}]}'
        with patch.object(sol.ssl, "create_default_context") as tls, patch.object(
            sol, "build_opener", return_value=opener,
        ):
            answer = sol.infer([{"role": "user", "content": "Synthetic probe"}])
        tls.assert_called_once_with(cafile=sol.CA_FILE)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://llm.home.arpa/v1/chat/completions")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "chatgpt-sol-latest")
        self.assertEqual(set(payload), {"model", "messages", "reasoning_effort", "max_completion_tokens"})
        self.assertEqual(answer, "READY")


if __name__ == "__main__":
    unittest.main()
