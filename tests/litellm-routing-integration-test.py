#!/usr/bin/env python3
"""Failure-inject LiteLLM routing against its exact pinned image.

The integration test skips if the image is not already present. It uses only
synthetic local HTTP backends and never contacts the production gateway.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_SAMPLE = ROOT / "config/control-plane.env.example"
PINNED_IMAGE = re.search(r"^LITELLM_IMAGE=(\S+)$", ENV_SAMPLE.read_text(encoding="utf-8"), re.MULTILINE).group(1)
STATE: dict[str, Any] = {"primary": "healthy", "fallback": "healthy", "calls": []}
STATE_LOCK = threading.Lock()


class SyntheticBackend(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        role = self.server.role
        with STATE_LOCK:
            STATE["calls"].append(role)
            behavior = STATE[role]
        if behavior == "hang":
            time.sleep(30)
            return
        if behavior == "reset":
            self.close_connection = True
            self.connection.close()
            return
        if behavior in {"500", "503"}:
            self.send_response(int(behavior))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if body.get("stream"):
            events = [
                {"id": "synthetic", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": f"{role}_ok"}, "finish_reason": None}]},
                {"id": "synthetic", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            payload = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
            encoded = payload.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        payload = json.dumps(
            {
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": role,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": f"{role}_ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def free_local_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is required for pinned LiteLLM integration test")
class LiteLLMRoutingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        image = subprocess.run(["docker", "image", "inspect", PINNED_IMAGE], capture_output=True, text=True)
        if image.returncode:
            raise unittest.SkipTest("pinned LiteLLM image is not present locally; the test will not pull it")
        cls.backends: list[ThreadingHTTPServer] = []
        for role in ("primary", "fallback"):
            backend = ThreadingHTTPServer(("0.0.0.0", 0), SyntheticBackend)
            backend.role = role
            threading.Thread(target=backend.serve_forever, daemon=True).start()
            cls.backends.append(backend)
        cls.proxy_port = free_local_port()
        cls.tempdir = tempfile.TemporaryDirectory(prefix="homecompute-litellm-routing-")
        config_path = Path(cls.tempdir.name) / "config.yaml"
        config_path.write_text(
            f"""model_list:
  - model_name: automation
    litellm_params:
      model: openai/synthetic-primary
      api_base: http://host.docker.internal:{cls.backends[0].server_port}/v1
      api_key: synthetic
      order: 1
      timeout: 3
      stream_timeout: 3
  - model_name: automation
    litellm_params:
      model: openai/synthetic-fallback
      api_base: http://host.docker.internal:{cls.backends[1].server_port}/v1
      api_key: synthetic
      order: 2
      timeout: 3
      stream_timeout: 3
  - model_name: home
    litellm_params:
      model: openai/synthetic-home
      api_base: http://host.docker.internal:{cls.backends[0].server_port}/v1
      api_key: synthetic
      timeout: 2
      stream_timeout: 2
litellm_settings:
  request_timeout: 600
  num_retries: 0
general_settings:
  master_key: sk-synthetic-master
  store_model_in_db: false
router_settings:
  routing_strategy: simple-shuffle
  allowed_fails: 100
  cooldown_time: 1
""",
            encoding="utf-8",
        )
        cls.container_name = f"hc-litellm-routing-{int(time.time())}"
        subprocess.run(
            [
                "docker", "run", "--rm", "-d", "--name", cls.container_name,
                "-p", f"127.0.0.1:{cls.proxy_port}:4000",
                "-v", f"{config_path}:/tmp/litellm-config.yaml:ro",
                PINNED_IMAGE, "--config", "/tmp/litellm-config.yaml", "--host", "0.0.0.0", "--port", "4000",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        for _ in range(120):
            try:
                urllib.request.urlopen(cls.base_url() + "/health/liveliness", timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError("pinned LiteLLM did not become healthy within 60 seconds")

    @classmethod
    def base_url(cls) -> str:
        return f"http://127.0.0.1:{cls.proxy_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "container_name"):
            subprocess.run(["docker", "stop", "--time", "2", cls.container_name], capture_output=True, text=True)
        for backend in getattr(cls, "backends", []):
            backend.shutdown()
            backend.server_close()
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    def request(self, alias: str = "automation", *, stream: bool = False) -> tuple[int | str, str, float]:
        body = json.dumps(
            {"model": alias, "messages": [{"role": "user", "content": "hello"}], "stream": stream}
        ).encode()
        request = urllib.request.Request(
            self.base_url() + "/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-synthetic-master"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read().decode()
                status: int | str = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            payload = error.read().decode(errors="replace")
            error.close()
        except Exception as error:
            status = type(error).__name__
            payload = ""
        return status, payload, time.monotonic() - started

    def set_modes(self, primary: str = "healthy", fallback: str = "healthy") -> None:
        with STATE_LOCK:
            STATE["primary"] = primary
            STATE["fallback"] = fallback
            STATE["calls"] = []

    def calls(self) -> list[str]:
        with STATE_LOCK:
            return list(STATE["calls"])

    def test_primary_healthy_does_not_call_fallback(self) -> None:
        self.set_modes()
        status, payload, _ = self.request()
        self.assertEqual(200, status)
        self.assertIn("primary_ok", payload)
        self.assertEqual(["primary"], self.calls())

    def test_primary_connection_reset_uses_fallback(self) -> None:
        self.set_modes(primary="reset")
        status, payload, elapsed = self.request()
        self.assertEqual(200, status)
        self.assertIn("fallback_ok", payload)
        self.assertLess(elapsed, 8)
        self.assertIn("fallback", self.calls())

    def test_primary_silent_upstream_uses_fallback_within_timeout(self) -> None:
        self.set_modes(primary="hang")
        status, payload, elapsed = self.request()
        self.assertEqual(200, status)
        self.assertIn("fallback_ok", payload)
        self.assertLess(elapsed, 8)
        self.assertIn("fallback", self.calls())

    def test_primary_500_and_503_use_fallback(self) -> None:
        for status_code in ("500", "503"):
            with self.subTest(status_code=status_code):
                self.set_modes(primary=status_code)
                status, payload, elapsed = self.request()
                self.assertEqual(200, status)
                self.assertIn("fallback_ok", payload)
                self.assertLess(elapsed, 8)
                self.assertIn("fallback", self.calls())

    def test_ordinary_model_answer_does_not_trigger_semantic_failover(self) -> None:
        self.set_modes()
        status, payload, _ = self.request()
        self.assertEqual(200, status)
        self.assertIn("primary_ok", payload)
        self.assertNotIn("fallback", self.calls())

    def test_streaming_silent_upstream_uses_fallback_within_stream_timeout(self) -> None:
        self.set_modes(primary="hang")
        status, payload, elapsed = self.request(stream=True)
        self.assertEqual(200, status)
        self.assertIn("fallback_ok", payload)
        self.assertLess(elapsed, 8)
        self.assertIn("fallback", self.calls())

    def test_both_automation_deployments_down_fail_bounded(self) -> None:
        self.set_modes(primary="503", fallback="503")
        status, _, elapsed = self.request()
        self.assertIn(status, (500, 503))
        self.assertLess(elapsed, 8)

    def test_home_unavailable_does_not_call_automation_fallback(self) -> None:
        self.set_modes(primary="503", fallback="healthy")
        status, _, elapsed = self.request(alias="home")
        self.assertIn(status, (500, 503))
        self.assertLess(elapsed, 4)
        self.assertEqual(["primary"], self.calls())


if __name__ == "__main__":
    unittest.main(verbosity=2)
