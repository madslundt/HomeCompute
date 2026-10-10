"""Synthetic MCP protocol and role tests; no OpenShell mutation or paid APIs."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/codex-worker"))
from broker import Ledger, serve
from mcp_adapter import MAX_BODY_BYTES, MAX_RESPONSE_BYTES, TOOLS

PROJECT = {"repository": "example/synthetic", "base_sha": "a" * 40,
           "base_branch": "main", "classification": "cloud_allowed",
           "tests": ["python3", "-m", "unittest"], "write_prefixes": ["src/", "tests/"]}
SUBMIT = {"project": "demo", "issue_key": "synthetic:mcp:1", "summary": "Synthetic arithmetic issue", "context": "No household information"}
TOKENS = {role: "synthetic-test-token-" + role + "-" * 32 for role in ("assistant", "worker", "operator")}


class NoPublisher:
    def publish(self, *_):
        raise AssertionError("MCP must never call publisher")


class McpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "tasks.sqlite3", {"demo": PROJECT})
        self.server = serve(self.ledger, TOKENS, NoPublisher(), ("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.ledger.db.close()
        self.temp.cleanup()

    def request(self, payload=None, role="assistant", headers=None, method="POST", path="/mcp", raw=None):
        defaults = {"Authorization": "Bearer " + TOKENS[role], "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        defaults.update(headers or {})
        body = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=defaults)
            response = connection.getresponse()
            encoded = response.read()
            return response.status, (json.loads(encoded) if encoded else None), dict(response.getheaders())
        finally:
            connection.close()

    def rpc(self, method, params=None, **kwargs):
        return self.request({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}, **kwargs)

    def tool(self, name, arguments, **kwargs):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, **kwargs)

    def test_initialize_negotiates_json_only_stateless_transport(self):
        for desired, expected in (("2025-03-26", "2025-03-26"), ("2025-06-18", "2025-06-18"), ("2025-11-25", "2025-06-18")):
            status, result, headers = self.rpc("initialize", {"protocolVersion": desired, "capabilities": {}, "clientInfo": {"name": "synthetic", "version": "1"}})
            self.assertEqual(status, 200)
            self.assertEqual(result["result"]["protocolVersion"], expected)
            self.assertEqual(result["result"]["capabilities"], {"tools": {"listChanged": False}})
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertNotIn("Mcp-Session-Id", headers)
        status, body, _ = self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))
        self.assertEqual(self.rpc("ping")[1]["result"], {})
        self.assertEqual(self.request(method="GET")[0], 405)
        self.assertEqual(self.request(method="DELETE")[0], 405)

    def test_authentication_does_not_accept_resolver_placeholder_or_other_roles(self):
        for credential in ("", "Bearer openshell:resolve:env:HOMECOMPUTE_TASK_MCP_TOKEN", "Bearer " + TOKENS["assistant"] + "bad"):
            self.assertEqual(self.rpc("tools/list", headers={"Authorization": credential})[0], 401)
        for role in ("operator", "worker"):
            self.assertEqual(self.rpc("tools/list", role=role)[0], 403)

    def test_tool_inventory_has_exactly_three_finite_schemas(self):
        status, response, _ = self.rpc("tools/list")
        self.assertEqual(status, 200)
        self.assertEqual([tool["name"] for tool in response["result"]["tools"]],
                         ["homecompute_task_submit", "homecompute_task_status", "homecompute_task_cancel"])
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in TOOLS))
        self.assertNotIn("nextCursor", response["result"])

    def test_submission_is_pending_deduplicated_and_cannot_be_approved_from_mcp(self):
        task = json.loads(self.tool("homecompute_task_submit", SUBMIT)[1]["result"]["content"][0]["text"])["task"]
        self.assertEqual(task["state"], "pending")
        repeated = json.loads(self.tool("homecompute_task_submit", SUBMIT)[1]["result"]["content"][0]["text"])["task"]
        self.assertEqual(task["id"], repeated["id"])
        self.assertIsNone(self.ledger.claim())
        for method in ("tasks/update", "tasks/result", "tasks/cancel", "resources/read", "prompts/get", "server/discover"):
            self.assertEqual(self.rpc(method, {"id": task["id"], "state": "queued"})[1]["error"]["code"], -32601)
        for name in ("approve", "publish", "github_publish", "homecompute_task_approve", "homecompute_task_publish"):
            self.assertEqual(self.tool(name, {"task_id": task["id"]})[1]["error"]["code"], -32602)
        self.assertEqual(self.ledger.get(task["id"])["state"], "pending")

    def test_status_filters_private_fields_and_cancellation_is_explicit(self):
        task = self.ledger.submit(SUBMIT)
        result = self.tool("homecompute_task_status", {"task_id": task["id"]})[1]["result"]
        public = json.loads(result["content"][0]["text"])["task"]
        for forbidden in ("body", "context", "summary", "policy", "files"):
            self.assertNotIn(forbidden, public)
        self.assertEqual(public["result"], {})
        self.request({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": task["id"]}})
        self.assertEqual(self.ledger.get(task["id"])["state"], "pending")
        self.assertFalse(self.tool("homecompute_task_cancel", {"task_id": task["id"]})[1]["result"]["isError"])
        self.assertEqual(self.ledger.get(task["id"])["state"], "cancelled")

    def test_optional_status_lists_bounded_metadata_without_fourth_tool(self):
        task = self.ledger.submit(SUBMIT)
        result = self.tool("homecompute_task_status", {})[1]["result"]
        listing = json.loads(result["content"][0]["text"])
        self.assertEqual(listing["tasks"][0]["id"], task["id"])
        self.assertFalse(listing["truncated"])
        self.assertNotIn("body", listing["tasks"][0])
        self.assertEqual(self.tool("homecompute_task_cancel", {})[1]["error"]["code"], -32602)
        self.ledger.list_tasks = lambda: [{**task, "pr_url": "\\" * 4000, "error": "\\" * 4000,
                                           "private": "SECRET"} for _ in range(101)]
        result = self.tool("homecompute_task_status", {})[1]["result"]
        self.assertLessEqual(len(json.dumps(result).encode()), MAX_RESPONSE_BYTES)
        listing = json.loads(result["content"][0]["text"])
        self.assertTrue(listing["truncated"])
        self.assertLessEqual(len(listing["tasks"]), 100)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_tool_arguments_cannot_select_urls_headers_roles_commands_or_paths(self):
        for extra in ("url", "headers", "operator_token", "command", "path", "action"):
            result = self.tool("homecompute_task_submit", {**SUBMIT, extra: "arbitrary"})[1]
            self.assertEqual(result["error"]["code"], -32602)
        for changed in ({"issue_key": "slash/not-supported"}, {"issue_key": "x" * 129}, {"project": "demo_underscore"}, {"context": "x" * 16001}):
            self.assertEqual(self.tool("homecompute_task_submit", {**SUBMIT, **changed})[1]["error"]["code"], -32602)
        self.assertEqual(self.tool("homecompute_task_status", {"task_id": "../../operator"})[1]["error"]["code"], -32602)
        self.assertEqual(self.ledger.list_tasks(), [])

    def test_transport_rejects_browser_origins_bad_protocol_media_and_large_body(self):
        for origin in ("null", "https://evil.example", "https://broker.home.arpa"):
            self.assertEqual(self.rpc("tools/list", headers={"Origin": origin})[0], 403)
        self.assertEqual(self.rpc("tools/list", headers={"MCP-Protocol-Version": "2024-11-05"})[0], 400)
        self.assertEqual(self.rpc("tools/list", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.rpc("tools/list", headers={"Accept": "application/json"})[0], 406)
        self.assertEqual(self.request(raw="x" * (MAX_BODY_BYTES + 1))[0], 413)
        self.assertEqual(self.request(raw="{invalid")[1]["error"]["code"], -32700)
        self.assertEqual(self.request(raw=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode("utf-16"))[1]["error"]["code"], -32700)
        self.assertEqual(self.request([])[1]["error"]["code"], -32600)
        self.assertEqual(self.request({"jsonrpc": "2.0", "id": True, "method": "ping"})[1]["error"]["code"], -32600)
        self.assertEqual(self.rpc("tools/list", path="/mcp?target=operator")[0], 404)

    def test_ledger_error_details_and_private_data_never_reach_tool_result(self):
        self.ledger.submit = lambda _: (_ for _ in ()).throw(ValueError("private token or SQL diagnostic"))
        result = self.tool("homecompute_task_submit", SUBMIT)[1]["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn("private token", json.dumps(result))

    def test_official_sdk_compatibility_when_reviewed_runtime_is_available(self):
        sdk_root = os.environ.get("MCP_SDK_ROOT")
        if not sdk_root:
            self.skipTest("MCP_SDK_ROOT not set; dependency-free HTTP tests still run")
        sdk = Path(sdk_root).resolve() / "dist/esm/client"
        script = "\n".join([
            "import { Client } from " + json.dumps((sdk / "index.js").as_uri()) + ";",
            "import { StreamableHTTPClientTransport } from " + json.dumps((sdk / "streamableHttp.js").as_uri()) + ";",
            "const client = new Client({name: 'synthetic-homecompute-test', version: '1'}, {capabilities: {}});",
            "const transport = new StreamableHTTPClientTransport(new URL(process.env.MCP_TEST_URL), {requestInit: {headers: {Authorization: 'Bearer ' + process.env.MCP_TEST_TOKEN}}});",
            "try {",
            "  await client.connect(transport);",
            "  const inventory = await client.listTools();",
            "  if (inventory.tools.length !== 3) throw Error('finite tool inventory required');",
            "  const result = await client.callTool({name: 'homecompute_task_submit', arguments: " + json.dumps(SUBMIT) + "});",
            "  const task = JSON.parse(result.content[0].text).task;",
            "  if (task.state !== 'pending') throw Error('submission escalated task state');",
            "  const status = await client.callTool({name: 'homecompute_task_status', arguments: {task_id: task.id}});",
            "  if (JSON.parse(status.content[0].text).task.state !== 'pending') throw Error('status mismatch');",
            "  const cancelled = await client.callTool({name: 'homecompute_task_cancel', arguments: {task_id: task.id}});",
            "  if (JSON.parse(cancelled.content[0].text).task.state !== 'cancelled') throw Error('cancellation mismatch');",
            "  console.log('Official SDK initialize/list/submit/status/cancel succeeded');",
            "} finally { await client.close(); }",
        ])
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                timeout=15, env={**os.environ, "MCP_TEST_URL": f"http://127.0.0.1:{self.port}/mcp",
                                                 "MCP_TEST_TOKEN": TOKENS["assistant"]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Official SDK", result.stdout)


if __name__ == "__main__":
    unittest.main()
