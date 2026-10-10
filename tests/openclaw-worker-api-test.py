"""Dedicated guest facade route, source, role and response isolation."""
from __future__ import annotations
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/codex-worker"))
from broker import Ledger, serve as broker_serve
from worker_api import permitted, serve

TOKEN = "synthetic-worker-" + "x" * 40


class ApiTests(unittest.TestCase):
    def test_finite_routes(self):
        tid = "11111111-1111-4111-8111-111111111111"
        for method, path in (("POST", "/worker/claim"), ("GET", "/healthz"), ("GET", "/tasks/" + tid),
                             ("POST", "/tasks/" + tid + "/result"), ("POST", "/tasks/" + tid + "/heartbeat")):
            self.assertTrue(permitted(method, path))
        for path in ("/tasks", "/mcp", "/worker/claim?url=evil", "/tasks/../../approve",
                     "/tasks/" + tid + "/approve", "/tasks/" + tid + "/publish", "/tasks/" + tid + "/cancel"):
            self.assertFalse(permitted("POST", path))

    def test_source_credential_and_forwarding_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "ledger.sqlite3", {})
            broker = broker_serve(ledger, {"worker": TOKEN, "assistant": "a" * 32}, None, ("127.0.0.1", 0))
            facade = serve(TOKEN, ("127.0.0.1", 0), broker.server_address, "127.0.0.1")
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (broker, facade)]
            for thread in threads: thread.start()
            def request(path, token=TOKEN, body=None):
                connection = http.client.HTTPConnection(*facade.server_address)
                connection.request("GET" if body is None else "POST", path,
                                   body=None if body is None else json.dumps(body), headers={"Authorization": "Bearer " + token})
                response = connection.getresponse(); status=response.status; value=json.loads(response.read()); connection.close()
                return status,value
            try:
                self.assertEqual(request("/healthz"), (200, {"ok": True}))
                self.assertEqual(request("/worker/claim", body={}), (200, None))
                self.assertEqual(request("/healthz", token="a" * 32)[0], 403)
                self.assertEqual(request("/tasks", body={})[0], 403)
                self.assertEqual(request("/mcp", body={})[0], 403)
            finally:
                for server in (facade, broker): server.shutdown(); server.server_close()
                for thread in threads: thread.join()
                ledger.db.close()

    def test_non_guest_source_is_denied_before_upstream(self):
        facade = serve(TOKEN, ("127.0.0.1", 0), ("127.0.0.1", 1), "10.77.21.2")
        thread = threading.Thread(target=facade.serve_forever, daemon=True); thread.start()
        try:
            connection = http.client.HTTPConnection(*facade.server_address)
            connection.request("GET", "/healthz", headers={"Authorization": "Bearer " + TOKEN})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.read()), {"error": "worker access required"})
            connection.close()
        finally:
            facade.shutdown(); facade.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
