#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHAT = load_module("homecompute_chat", ROOT / "scripts" / "homecompute_chat.py")
SMOKE = load_module("tool_call_smoke", ROOT / "scripts" / "tool_call_smoke.py")


class ToolCallSmokeTests(unittest.TestCase):
    def test_chat_example_keeps_plain_chat_default_and_exposes_tool_calls(self) -> None:
        plain = CHAT.build_body("auto", "Hi")
        self.assertNotIn("tools", plain)
        self.assertNotIn("tool_choice", plain)

        for choice in ("required", "auto"):
            with self.subTest(choice=choice):
                request = CHAT.build_body("automation", "Read kitchen temperature", include_tools=True, tool_choice=choice)
                self.assertEqual("get_temperature", request["tools"][0]["function"]["name"])
                self.assertEqual(choice, request["tool_choice"])

        call = {"id": "call_1", "type": "function", "function": {"name": "get_temperature", "arguments": "{\"room\":\"kitchen\"}"}}
        rendered = json.loads(CHAT.render_message({"content": None, "tool_calls": [call]}))
        self.assertEqual([call], rendered["tool_calls"])
        self.assertEqual("ordinary answer", CHAT.render_message({"content": "ordinary answer"}))

    def test_route_probe_sends_required_and_auto_and_parses_calls(self) -> None:
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(body)
                response = {
                    "model": body["model"],
                    "choices": [{"message": {"content": None, "tool_calls": [{
                        "id": "call_1", "type": "function", "function": {
                            "name": "get_temperature", "arguments": "{\"room\":\"kitchen\"}"
                        }
                    }]}}],
                }
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_port}"

        for choice in ("required", "auto"):
            result = SMOKE.probe(base_url, "automation", "test-key", choice, None)
            self.assertEqual(choice, result["tool_choice"])
            self.assertEqual({"room": "kitchen"}, result["arguments"])

        self.assertEqual(["required", "auto"], [body["tool_choice"] for body in received])
        self.assertTrue(all(body["tools"][0]["function"]["name"] == "get_temperature" for body in received))

    def test_compute_default_uses_qwen_parser_for_internal_deployment(self) -> None:
        compose = (ROOT / "deploy" / "compute-node" / "compose.yaml").read_text()
        self.assertIn('--tool-call-parser "${VLLM_TOOL_CALL_PARSER:-qwen3_coder}"', compose)
        self.assertIn("--enable-auto-tool-choice", compose)
        setup = (ROOT / "scripts" / "setup-compute-node.sh").read_text()
        self.assertIn('python3 "$MODELCTL" validate --deployment "$MODEL_DEPLOYMENT_ID"', setup)
        self.assertNotIn("Only the pinned Qwen3.8-27B NVFP4 artifact is supported", setup)
        smoke = (ROOT / "scripts" / "setup-compute-node.sh").read_text()
        self.assertIn("general-spark-qwen38", smoke)
        for alias in ("auto", "coding", "automation", "research", "home", "meeting", "assistant"):
            self.assertNotIn(f'--model "{alias}"', smoke)
        self.assertIn("--choices required auto", smoke)

    def test_other_serving_profiles_have_tool_parsers_and_smoke_coverage(self) -> None:
        home_compose = (ROOT / "deploy" / "compute-node" / "home-assistant-model" / "compose.yaml").read_text()
        self.assertIn("--tool-call-parser gemma4", home_compose)
        self.assertIn("--enable-auto-tool-choice", home_compose)
        self.assertIn("--choices required auto", (ROOT / "scripts" / "setup-compute-home-assistant-model.sh").read_text())

        automation_compose = (ROOT / "deploy" / "compute-node" / "compose.yaml").read_text()
        self.assertIn('--tool-call-parser "${AUTOMATION_TOOL_CALL_PARSER:-qwen3_coder}"', automation_compose)
        self.assertIn("--choices required auto", (ROOT / "scripts" / "setup-compute-automation-moe.sh").read_text())

        backup_compose = (ROOT / "deploy" / "control-plane" / "compose.yaml").read_text()
        self.assertIn("--jinja", backup_compose)
        backup_setup = (ROOT / "scripts" / "setup-home-core-automation-backup.sh").read_text()
        self.assertIn('"tool_choice":"required"', backup_setup)
        self.assertIn('"tool_choice":"auto"', backup_setup)


if __name__ == "__main__":
    unittest.main()
