#!/usr/bin/env python3
"""Exercise the managed routes via pinned LiteLLM and loopback-only fixtures.

Run with LiteLLM's proxy dependencies available. The fixtures make no cloud
requests and use a synthetic authenticator with the shape of virtual-key grants.
"""

from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest

import httpx
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/control-plane"
LOCAL = "automation-qualification"
SOL = "gpt-6.2-sol"
LUNA = "gpt-6-luna"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
AUTH_FIXTURE = """from fastapi import HTTPException
from litellm.proxy._types import UserAPIKeyAuth

async def auth(request, api_key):
    scopes = {
        'sk-fixture-enrolled': ['agent-assistant', 'agent-fast', 'agent-coder', 'cloud-first'],
        'sk-fixture-private': ['local-only'],
        'sk-fixture-unscoped': [],
        'sk-fixture-network': ['agent-assistant', 'local-only'],
    }
    if api_key not in scopes:
        raise HTTPException(401, 'Missing fixture key')
    owner = 'lan-catalog-inference' if api_key == 'sk-fixture-network' else 'fixture'
    return UserAPIKeyAuth(api_key=api_key, models=scopes[api_key], user_id=owner, key_alias=owner)
"""


class Relay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state):
        super().__init__(("127.0.0.1", 0), Upstream)
        self.state = state
        self.worker = Thread(target=self.serve_forever, daemon=True)
        self.worker.start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server_port}/v1"

    def close(self):
        self.shutdown()
        self.server_close()
        self.worker.join(timeout=5)


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = body["model"]
        state = self.server.state
        state["calls"].append(body)
        mode = state["modes"].get(model, 200)
        if mode == "slow":
            sleep(4)
        if isinstance(mode, int) and mode != 200:
            self.write_json(mode, {"error": {"type": "fixture", "message": "SENSITIVE-FIXTURE-ERROR"}})
        elif self.path.endswith("/responses"):
            self.respond(body, cut=mode == "cut")
        elif body.get("stream"):
            self.write_chat_stream(model, mode)
        else:
            message = {"role": "assistant", "content": '{"ok":true}'}
            finish = "stop"
            if body.get("tools"):
                finish = "tool_calls"
                message = {"role": "assistant", "tool_calls": [{
                    "id": "call_fixture", "type": "function",
                    "function": {"name": "lookup_fixture", "arguments": "{}"},
                }]}
            self.write_json(200, {
                "id": "chatcmpl-fixture", "object": "chat.completion", "model": model,
                "created": 1, "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })

    def write_json(self, status, data):
        payload = json.dumps(data).encode()
        with suppress(OSError):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def respond(self, body, cut):
        output = [{
            "id": "msg_fixture", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}],
        }]
        if body.get("tools"):
            output = [{
                "id": "fc_fixture", "type": "function_call", "status": "completed",
                "call_id": "call_fixture", "name": "lookup_fixture", "arguments": "{}",
            }]
        response = {
            "id": "resp_fixture", "object": "response", "model": body["model"],
            "created_at": 1, "status": "completed", "output": output,
            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        }
        if not body.get("stream"):
            return self.write_json(200, response)
        events = [
            {"type": "response.output_text.delta", "item_id": "msg_fixture", "output_index": 0,
             "content_index": 0, "delta": "fixture", "sequence_number": 0},
            {"type": "response.completed", "response": response, "sequence_number": 1},
        ]
        with suppress(OSError):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in (events[:1] if cut else events):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())

    def write_chat_stream(self, model, mode):
        with suppress(OSError):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                "model": model, "choices": [{"index": 0, "delta": {"content": "fixture"}, "finish_reason": None}],
            }
            if mode == "tool-cut":
                chunk["choices"][0]["delta"] = {"tool_calls": [{
                    "index": 0, "id": "call_fixture", "type": "function",
                    "function": {"name": "lookup_fixture", "arguments": "{}"},
                }]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            if mode not in {"cut", "tool-cut"}:
                chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


class AgentRoutingTest(unittest.TestCase):
    def test_pinned_proxy_fails_over_and_keeps_private_routes_private(self):
        expected_version = (ROOT / "config/control-plane.env.example").read_text().split(
            "litellm-non_root:v", 1
        )[1].split("@", 1)[0]
        self.assertEqual(importlib.metadata.version("litellm"), expected_version)
        state = {"calls": [], "modes": {}}
        local, cloud = Relay(state), Relay(state)
        self.addCleanup(local.close)
        self.addCleanup(cloud.close)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tmp = Path(directory.name)
        (tmp / "fixture_auth.py").write_text(AUTH_FIXTURE)
        config = yaml.safe_load((DEPLOY / "litellm-config.yaml").read_text())
        config["general_settings"] = {"master_key": "sk-fixture-master", "custom_auth": "fixture_auth.auth"}
        config.pop("mcp_servers")
        for row in config["model_list"]:
            params = row["litellm_params"]
            model = params["model"]
            # Run the production ordering, callback, and timeouts against a
            # loopback fixture. The OAuth adapter is checked separately live.
            params["api_base"] = "os.environ/FIXTURE_LOCAL" if model.startswith("openai/") else "os.environ/FIXTURE_CLOUD"
            params["model"] = model.replace("chatgpt/", "openai/")
            params["api_key"] = "fixture"
            if model.startswith("chatgpt/"):
                params["allowed_openai_params"] = ["tool_choice", "reasoning_effort"]
            info = row.setdefault("model_info", {})
            if "homecompute_alias" in info:
                info["cooldown_time"] = 1
                if row["model_name"] == "local-only":
                    info["homecompute_deadline"] = 2
        config_path = tmp / "config.yaml"
        config_path.write_text(yaml.safe_dump(config))
        env = dict(
            os.environ, PYTHONPATH=str(DEPLOY), LITELLM_CONFIG=str(config_path),
            FIXTURE_LOCAL=local.endpoint, FIXTURE_CLOUD=cloud.endpoint,
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        with socket.socket() as server_address:
            server_address.bind(("127.0.0.1", 0))
            port = server_address.getsockname()[1]
        log_path = tmp / "proxy.log"
        with log_path.open("w") as log:
            proc = subprocess.Popen(
                [sys.executable, "-c", "from homecompute_routing import run; run()",
                 "--config", str(config_path), "--host", "127.0.0.1", "--port", str(port)],
                env=env, stdout=log, stderr=log,
            )

        def stop():
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
        self.addCleanup(stop)

        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=12) as client:
            for _ in range(200):
                if proc.poll() is not None:
                    self.fail("The pinned proxy exited before readiness")
                with suppress(httpx.ConnectError):
                    if client.get("/health/liveliness").status_code == 200:
                        break
                sleep(0.1)
            else:
                self.fail("The pinned proxy did not become ready")

            def request(alias="agent-assistant", *, api="chat/completions", key="enrolled", **extra):
                state["calls"].clear()
                inputs = ({"input": "fixture"} if api == "responses" else
                          {"messages": [{"role": "user", "content": "fixture"}]})
                return client.post(f"/v1/{api}", json={"model": alias, **inputs, **extra},
                                   headers={"Authorization": f"Bearer sk-fixture-{key}"})

            def tried():
                return [call["model"] for call in state["calls"]]

            primary = request()
            self.assertEqual(primary.status_code, 200)
            self.assertEqual(primary.headers["x-litellm-model-id"], "agent-assistant:flash-next")
            self.assertEqual(tried(), [LOCAL])

            legacy = request("automation-moe")
            self.assertEqual(legacy.status_code, 200)
            self.assertAlmostEqual(float(legacy.headers["x-litellm-response-cost"]), 0.00000077, places=12)

            state["modes"][LOCAL] = 503
            fallback = request(reasoning_effort="xhigh")
            self.assertEqual(fallback.status_code, 200)
            self.assertEqual(fallback.headers["x-litellm-model-id"], "agent-assistant:luna:high")
            self.assertEqual(fallback.headers["x-litellm-attempted-fallbacks"], "1")
            self.assertEqual(fallback.headers["x-litellm-attempted-retries"], "0")
            self.assertEqual(tried(), [LOCAL, LUNA])
            self.assertEqual(state["calls"][-1]["reasoning_effort"], "high")

            cooled = request()
            self.assertEqual(cooled.headers["x-litellm-model-id"], "agent-assistant:luna:high")
            self.assertEqual(tried(), [LUNA])

            fast = request("agent-fast", api="responses", reasoning={"effort": "xhigh"})
            self.assertEqual(fast.status_code, 200)
            self.assertEqual(state["calls"][-1]["reasoning"], {"effort": "low"})
            self.assertEqual(fast.headers["x-litellm-model-id"], "agent-fast:luna:low")

            state["modes"][LUNA] = 429
            streamed = request(stream=True)
            self.assertEqual(streamed.headers["x-litellm-model-id"], "agent-assistant:sol:high")
            self.assertIn("data: [DONE]", streamed.text, (streamed.text, tried()))
            self.assertNotIn('"error"', streamed.text)
            self.assertEqual(tried()[-2:], [LUNA, SOL])

            state["modes"][LUNA] = 503
            state["modes"][SOL] = 503
            outage = request("cloud-first")
            self.assertGreaterEqual(outage.status_code, 500)
            self.assertEqual(tried(), [SOL, LUNA])

            private = request("local-only", key="private")
            self.assertEqual(private.status_code, 503)
            self.assertEqual(tried(), [LOCAL])
            for field, value in (
                ("fallbacks", ["agent-assistant"]), ("context_window_fallbacks", ["agent-assistant"]),
                ("default_fallbacks", ["agent-assistant"]), ("api_base", cloud.endpoint),
                ("user_config", {"fallbacks": ["agent-assistant"]}), ("routing_strategy", "semantic-router"),
                ("tools", [{"type": "mcp", "server_url": cloud.endpoint}]),
            ):
                refused = request("local-only", key="private", **{field: value})
                self.assertEqual(refused.status_code, 400, field)
                self.assertFalse(tried(), field)
            for key in ("network", "unscoped"):
                self.assertEqual(request(key=key).status_code, 403)
                self.assertFalse(tried())

            state["modes"].clear()
            sleep(1.2)
            self.assertEqual(request().headers["x-litellm-model-id"], "agent-assistant:flash-next")

            state["modes"][LOCAL] = "cut"
            cut = request(stream=True)
            self.assertIn("fixture", cut.text)
            self.assertIn('"error"', cut.text)
            self.assertEqual(tried(), [LOCAL])

            state["modes"][LOCAL] = "tool-cut"
            sleep(1.2)
            cut_tool = request(stream=True)
            self.assertEqual(cut_tool.text.count("lookup_fixture"), 1)
            self.assertIn('"error"', cut_tool.text)
            self.assertEqual(tried(), [LOCAL])

            state["modes"][LOCAL] = "cut"
            sleep(1.2)
            cut_response = request(api="responses", stream=True)
            self.assertIn('"delta":"fixture"', cut_response.text.replace(" ", ""))
            self.assertNotIn('"type":"response.completed"', cut_response.text.replace(" ", ""))
            self.assertEqual(tried(), [LOCAL])

            state["modes"][LOCAL] = "slow"
            sleep(1.2)
            start = monotonic()
            self.assertEqual(request("local-only", key="private").status_code, 504)
            self.assertLess(monotonic() - start, 3)

            state["modes"].clear()
            sleep(1.2)
            structured = request("local-only", key="private", response_format={
                "type": "json_schema", "json_schema": {"name": "fixture", "strict": True, "schema": SCHEMA},
            })
            self.assertEqual(structured.status_code, 200, structured.text)
            self.assertEqual(json.loads(structured.json()["choices"][0]["message"]["content"]), {"ok": True})
            self.assertEqual(state["calls"][-1]["response_format"]["json_schema"]["schema"], SCHEMA)

            local.close()
            state["modes"].clear()
            sleep(1.2)
            tools = [{"type": "function", "function": {
                "name": "lookup_fixture", "parameters": {"type": "object", "properties": {}},
            }}]
            tool = request("agent-coder", tools=tools, tool_choice="required")
            self.assertEqual(tool.status_code, 200, (tool.text, state["calls"]))
            self.assertEqual(tool.headers["x-litellm-model-id"], "agent-coder:sol:high")
            self.assertEqual(tried(), [SOL])
            self.assertEqual(tool.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "lookup_fixture")

            structured = request("agent-coder", response_format={
                "type": "json_schema", "json_schema": {"name": "fixture", "strict": True, "schema": SCHEMA},
            })
            self.assertGreaterEqual(structured.status_code, 400)
            self.assertFalse(tried())

        stop()
        output = log_path.read_text()
        self.assertIn("reason=RateLimitError", output)
        self.assertIn("HomeCompute route alias=agent-assistant", output)
        self.assertNotIn("SENSITIVE-FIXTURE-ERROR", output)


if __name__ == "__main__":
    unittest.main()
