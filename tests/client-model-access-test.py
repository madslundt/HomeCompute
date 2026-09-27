#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("client_access", ROOT / "scripts/verify_client_model_access.py")
assert SPEC and SPEC.loader
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)


class ClientModelAccessTests(unittest.TestCase):
    def test_audit_checks_exact_alias_set_without_printing_credential(self) -> None:
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append(self.headers.get("Authorization"))
                payload = json.dumps({"data": [{"id": "automation"}]}).encode()
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
        key = "secret-test-credential"
        expectation = ACCESS.Expectation("ACCESS_TEST_KEY", ("automation",))
        output = io.StringIO()
        with patch.dict(os.environ, {"ACCESS_TEST_KEY": key}), redirect_stdout(output):
            failures = ACCESS.audit(f"http://127.0.0.1:{server.server_port}", [expectation], ca_file=None, timeout=2)
        self.assertEqual([], failures)
        self.assertEqual([f"Bearer {key}"], captured)
        self.assertNotIn(key, output.getvalue())

    def test_an_added_alias_is_detected_as_permission_expansion(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps({"data": [{"id": "automation"}, {"id": "new-model"}]}).encode()
                self.send_response(200)
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
        expectation = ACCESS.Expectation("ACCESS_TEST_KEY", ("automation",))
        with patch.dict(os.environ, {"ACCESS_TEST_KEY": "credential"}):
            failures = ACCESS.audit(f"http://127.0.0.1:{server.server_port}", [expectation], ca_file=None, timeout=2)
        self.assertEqual(1, len(failures))
        self.assertIn("observed ['automation', 'new-model']", failures[0])

    def test_expectation_parser_rejects_malformed_values(self) -> None:
        for value in ("", "bad", "1KEY=automation", "KEY=automation,automation"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ACCESS.parse_expectation(value)


if __name__ == "__main__":
    unittest.main()
