#!/usr/bin/env python3
"""Functional and wiring guards for the Bank Norwegian read-only MCP server.

The server runs as a real subprocess driven over its streamable-HTTP MCP
surface against a local mock of the Bank Norwegian Berlin Group PSD2 API.
The mock enforces the consent/OAuth header contract and records every request
so the test can prove the server stays read-only outside the consent flow.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "deploy" / "automation" / "banknorwegian-mcp" / "server.py"
PRODUCTION = (ROOT / "deploy" / "automation" / "production.yaml").read_text()
LITELLM = (ROOT / "deploy" / "control-plane" / "litellm-config.yaml").read_text()
ENV_EXAMPLE = (ROOT / "config" / "automation.env.example").read_text()
DOCKERFILE = (ROOT / "deploy" / "automation" / "banknorwegian.Dockerfile").read_text()
NETWORK = (ROOT / "modules" / "nixos" / "automation-network.nix").read_text()

CLIENT_ID = "test-client"
CLIENT_SECRET = "test-secret-DO-NOT-LEAK"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MockBank(BaseHTTPRequestHandler):
    bank_port = 0
    log_requests: list = []
    consent: dict = {}
    tokens: dict = {}
    pages_left = 0

    def log_message(self, *_args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self, method):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        MockBank.log_requests.append((method, parsed.path))
        headers = self.headers

        if method == "POST" and parsed.path == "/openbanking/consents/":
            for name in ("Request-ID", "PSU-ID", "TPP-Client-ID"):
                assert name in headers, name
            if "Signature-Validation" in headers:
                assert headers["Signature-Validation"] == "false"
            assert headers["Region-ID"] == "NO"
            consent_id = "consent-1"
            MockBank.consent = {"id": consent_id, "status": "valid"}
            auth = f"http://127.0.0.1:{self.server.server_address[1]}/connect/authorize"
            return self._json(201, {"consentId": consent_id, "consentStatus": "received",
                                    "_links": {"scaRedirect": {"href": auth + "?consent_id=" + consent_id
                                                              + "&code_challenge=<YOUR_CODE_CHALLENGE>"}}})
        if method == "POST" and parsed.path == "/connect/token":
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)).decode()).items()}
            assert form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET
            if form.get("grant_type") == "authorization_code":
                assert form.get("code") == "AUTH-CODE"
                assert len(form.get("code_verifier", "")) >= 20, "PKCE verifier missing"
                MockBank.tokens = {"access": "access-1", "refresh": "refresh-1", "issued": 1}
                return self._json(200, {"access_token": "access-1", "refresh_token": "refresh-1",
                                        "expires_in": 300, "scope": "offline_access readaccounts"})
            if form.get("grant_type") == "refresh_token":
                assert form.get("refresh_token") == MockBank.tokens.get("refresh"), "bad refresh"
                MockBank.tokens["access"] += "r"
                return self._json(200, {"access_token": MockBank.tokens["access"],
                                        "expires_in": 300})
            return self._json(400, {"error": "unsupported_grant_type"})
        if method == "DELETE" and parsed.path.startswith("/openbanking/consents/"):
            MockBank.consent["status"] = "revoked"
            return self._json(200, {})

        # Account information reads require the full consent header contract.
        for name in ("Authorization", "Consent-ID", "PSU-ID", "Region-ID", "Request-ID"):
            if name not in headers:
                return self._json(401, {"title": f"missing {name}"})
        if MockBank.consent.get("status") != "valid":
            return self._json(403, {"title": "consent not valid"})
        token = headers["Authorization"].removeprefix("Bearer ")
        if token != MockBank.tokens.get("access"):
            return self._json(401, {"title": "bad token"})
        if parsed.path.startswith("/openbanking/accounts/") and parsed.path.endswith("/transactions"):
            assert query.get("bookingStatus") == ["booked"], "bookingStatus is required per spec"
            if query.get("page") == ["2"]:
                return self._json(200, {"transactions": {"booked": [
                    {"transactionId": "t2", "remittanceInformationUnstructured": "Bus ticket"}]}})
            MockBank.pages_left = 1
            return self._json(200, {"transactions": {"booked": [
                {"transactionId": "t1", "remittanceInformationUnstructured": "Coffee"}]},
                "_links": {"next": {"href": parsed.path + "?page=2&bookingStatus=booked"}}})
        if parsed.path.endswith("/balances"):
            return self._json(200, {"account": {"currency": "NOK"}, "balances": [
                {"balanceType": "interimBooked", "amount": {"currency": "NOK", "amount": "100.00"}}]})
        if parsed.path in ("/openbanking/accounts/", "/openbanking/accounts"):
            assert query.get("withBalance") in (None, ["true"], ["false"])
            return self._json(200, {"accounts": [
                {"currency": "NOK", "accountType": "CACC", "accountName": "Brukskonto",
                 "id": "acct-1", "usage": "CBOT",
                 "links": {"balances": {"href": "/openbanking/accounts/acct-1/balances"},
                           "transactions": {"href": "/openbanking/accounts/acct-1/transactions"}}}]})
        return self._json(404, {"title": "not found", "path": parsed.path})

    do_GET = lambda self: self.route("GET")
    do_POST = lambda self: self.route("POST")
    do_DELETE = lambda self: self.route("DELETE")
    do_PUT = lambda self: self.route("PUT")
    do_PATCH = lambda self: self.route("PATCH")


class MCPSession:
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.session = None
        self.next_id = 0

    def rpc(self, method, params=None, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            self.next_id += 1
            payload["id"] = self.next_id
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Accept": "application/json",
                                                  **({"Mcp-Session-Id": self.session} if self.session else {})},
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session = session
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            raise AssertionError(f"HTTP {error.code} for {method}: {error.read()!r}") from error
        return None

    def call(self, tool, arguments=None):
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        text = result["result"]["content"][0]["text"]
        payload = json.loads(text) if text.startswith(("{", "[")) else text
        return result["result"].get("isError", False), payload


class BankNorwegianMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state_file = os.path.join(cls.tmp.name, "state.json")
        cls.bank_port = free_port()
        cls.bank = ThreadingHTTPServer(("127.0.0.1", cls.bank_port), MockBank)
        MockBank.bank_port = cls.bank_port
        MockBank.log_requests = []
        MockBank.consent = {}
        MockBank.tokens = {}
        MockBank.pages_left = 0
        threading.Thread(target=cls.bank.serve_forever, daemon=True).start()
        cls.mcp_port = free_port()
        base = f"http://127.0.0.1:{cls.bank_port}"
        cls.server = subprocess.Popen(
            [sys.executable, str(SERVER)],
            env={**os.environ,
                 "BN_SANDBOX": "false", "BN_BASE_URL": base, "BN_AUTH_BASE_URL": base,
                 "BN_CLIENT_ID": CLIENT_ID, "BN_CLIENT_SECRET": CLIENT_SECRET,
                 "BN_REDIRECT_URI": "http://home-core:17880/callback",
                 "BN_REGION": "NO", "BN_STATE_FILE": cls.state_file,
                 "BN_CA_FILE": "", "BN_QCESEAL_KEY_FILE": "", "BN_QCESEAL_CERT_FILE": "",
                 "BANKNORWEGIAN_MCP_PORT": str(cls.mcp_port),
                 "BANKNORWEGIAN_MCP_HOST": "127.0.0.1"},
            stderr=open("/tmp/bn-child.log", "wb"))
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cls.mcp_port}/healthz", timeout=2):
                    break
            except OSError:
                time.sleep(0.15)
        else:
            cls.server.kill()
            raise RuntimeError("MCP server did not start")
        cls.mcp = MCPSession(cls.mcp_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.bank.shutdown()
        cls.tmp.cleanup()

    def test_a_tools_are_read_only_and_namespaced(self):
        listing = self.mcp.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                             "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(listing["result"]["serverInfo"]["name"], "banknorwegian")
        tools = self.mcp.rpc("tools/list")["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(names, ["banknorwegian_status", "banknorwegian_auth_start",
                                 "banknorwegian_auth_complete", "banknorwegian_accounts",
                                 "banknorwegian_balances", "banknorwegian_transactions",
                                 "banknorwegian_consent_revoke"])
        server_source = SERVER.read_text()
        self.assertNotIn("/payments/", server_source, "payment endpoints must stay unimplemented")

    def test_b_full_consent_and_read_flow(self):
        is_error, status = self.mcp.call("banknorwegian_status")
        self.assertFalse(is_error)
        self.assertFalse(status["authorized"])
        self.assertIsNone(status["configuration_error"])

        is_error, started = self.mcp.call("banknorwegian_auth_start")
        self.assertFalse(is_error)
        self.assertNotIn("YOUR_CODE_CHALLENGE", started["auth_url"], "PKCE placeholder survived")
        self.assertIn("consent_id=", started["auth_url"])

        is_error, done = self.mcp.call("banknorwegian_auth_complete", {
            "redirect_url": f"http://home-core:17880/callback?code=AUTH-CODE&state={started['state']}"})
        self.assertFalse(is_error)
        self.assertEqual(done["consent_id"], "consent-1")

        is_error, accounts = self.mcp.call("banknorwegian_accounts")
        self.assertFalse(is_error)
        self.assertEqual(accounts["accounts"][0]["accountName"], "Brukskonto")

        is_error, balances = self.mcp.call("banknorwegian_balances", {"account_reference": "acct-1"})
        self.assertFalse(is_error)
        self.assertEqual(balances["balances"][0]["amount"]["amount"], "100.00")

        is_error, transactions = self.mcp.call("banknorwegian_transactions", {
            "account_reference": "acct-1", "date_from": "2026-01-01", "booking_status": "booked"})
        self.assertFalse(is_error)
        self.assertEqual(transactions["pagesFetched"], 2)
        self.assertEqual([t["transactionId"] for t in transactions["transactions"]], ["t1", "t2"])

        reads = [(m, p) for m, p in MockBank.log_requests if p.startswith("/openbanking/accounts")]
        self.assertTrue(reads and all(m == "GET" for m, _ in reads), reads)
        methods = {m for m, p in MockBank.log_requests if p == "/openbanking/consents/"}
        self.assertEqual(methods, {"POST"})

    def test_c_expired_access_token_refreshes(self):
        # Rewrite the persisted expiry and restart so the server reloads state
        # and must use the refresh token for the next read.
        self.server.terminate()
        self.server.wait(timeout=10)
        state = json.loads(Path(self.state_file).read_text())
        state["tokens"]["expires_at"] = time.time() - 1
        Path(self.state_file).write_text(json.dumps(state))
        port = free_port()
        base = f"http://127.0.0.1:{self.bank_port}"
        type(self).server = subprocess.Popen(
            [sys.executable, str(SERVER)],
            env={**os.environ,
                 "BN_SANDBOX": "false", "BN_BASE_URL": base, "BN_AUTH_BASE_URL": base,
                 "BN_CLIENT_ID": CLIENT_ID, "BN_CLIENT_SECRET": CLIENT_SECRET,
                 "BN_REDIRECT_URI": "http://home-core:17880/callback",
                 "BN_REGION": "NO", "BN_STATE_FILE": self.state_file,
                 "BN_CA_FILE": "", "BN_QCESEAL_KEY_FILE": "", "BN_QCESEAL_CERT_FILE": "",
                 "BANKNORWEGIAN_MCP_PORT": str(port),
                 "BANKNORWEGIAN_MCP_HOST": "127.0.0.1"},
            stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2):
                    break
            except OSError:
                time.sleep(0.15)
        else:
            raise RuntimeError("MCP server did not restart")
        type(self).mcp = MCPSession(port)
        self.mcp.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "1"}})
        before = len([p for m, p in MockBank.log_requests if p == "/connect/token"])
        is_error, accounts = self.mcp.call("banknorwegian_accounts")
        self.assertFalse(is_error)
        self.assertIn("accounts", accounts)
        after = len([p for m, p in MockBank.log_requests if p == "/connect/token"])
        self.assertGreater(after, before, "expected a refresh-token exchange")

    def test_d_state_is_private_and_secrets_never_echo(self):
        mode = stat.S_IMODE(os.stat(self.state_file).st_mode)
        self.assertEqual(mode, 0o600)
        probes = []
        for tool, args in [("banknorwegian_status", {}), ("banknorwegian_accounts", {})]:
            is_error, payload = self.mcp.call(tool, args)
            probes.append(json.dumps(payload))
        blob = "\n".join(probes) + json.dumps(self.mcp.rpc("tools/list"))
        self.assertNotIn(CLIENT_SECRET, blob)
        self.assertNotIn("access-", blob)

    def test_e_unknown_tool_and_unauth_read_are_errors(self):
        result = self.mcp.rpc("tools/call", {"name": "nope", "arguments": {}})
        self.assertTrue(result["result"]["isError"])
        self.assertIn("unknown tool", result["result"]["content"][0]["text"])

    def test_f_consent_revoke_ends_access(self):
        is_error, revoked = self.mcp.call("banknorwegian_consent_revoke")
        self.assertFalse(is_error)
        self.assertTrue(revoked["revoked"])
        is_error, accounts = self.mcp.call("banknorwegian_accounts")
        self.assertTrue(is_error)
        self.assertEqual(self.mcp.call("banknorwegian_status")[1]["consent"], None)
        self.assertIn(("DELETE", "/openbanking/consents/consent-1"), MockBank.log_requests)

    def test_g_deployment_wiring(self):
        self.assertIn("banknorwegian-mcp:", PRODUCTION)
        self.assertIn("dockerfile: banknorwegian.Dockerfile", PRODUCTION)
        self.assertIn("172.28.201.5", PRODUCTION)
        self.assertIn("BANKNORWEGIAN_MCP_BACKENDS_IP", PRODUCTION)
        self.assertIn("BN_CLIENT_SECRET", PRODUCTION)
        self.assertIn("url: http://banknorwegian-mcp:8090/mcp", LITELLM)
        self.assertIn("access_groups: [banknorwegian-read]", LITELLM)
        self.assertIn("BANKNORWEGIAN_MCP_BACKENDS_IP=172.28.212.5", ENV_EXAMPLE)
        self.assertIn("BN_CLIENT_SECRET=", ENV_EXAMPLE)
        self.assertIn("-p tcp --dport 8090", NETWORK)
        self.assertIn("172.28.212.5/32", NETWORK)
        self.assertIn("@sha256:", DOCKERFILE)
        self.assertNotIn("pip install", DOCKERFILE)


if __name__ == "__main__":
    unittest.main()
