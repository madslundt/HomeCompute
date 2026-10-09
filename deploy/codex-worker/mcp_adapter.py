"""Finite, stateless MCP surface for NemoClaw's endpoint-bound bearer provider.

HTTPS termination must expose only /mcp; this module never handles credential
values. The trusted broker authenticates the assistant role before dispatch.
Transport: MCP 2025-06-18 Streamable HTTP, JSON responses, no receive stream.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

MAX_BODY_BYTES = 131072
MAX_RESPONSE_BYTES = 262144
VERSIONS = {"2025-03-26", "2025-06-18"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
PROJECT = r"[a-z0-9][a-z0-9-]{0,63}"
ISSUE = r"[A-Za-z0-9_.:-]{1,128}"
PUBLIC = {"id", "project", "issue_key", "state", "created", "updated", "session_id", "commit", "pr_url", "error"}


class Tasks(Protocol):
    def submit(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, task_id: str) -> dict[str, Any]: ...
    def action(self, task_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def list_tasks(self) -> list[dict[str, Any]]: ...


def string(minimum: int, maximum: int, pattern: str | None = None) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum,
            **({"pattern": "^" + pattern + "$"} if pattern else {})}


SUBMIT = {"type": "object", "additionalProperties": False,
          "properties": {"project": string(1, 64, PROJECT), "issue_key": string(1, 128, ISSUE),
                         "summary": string(1, 1000), "context": string(0, 16000)},
          "required": ["project", "issue_key", "summary", "context"]}
TASK_ID = {"type": "object", "additionalProperties": False,
           "properties": {"task_id": string(36, 36, UUID.pattern)}, "required": ["task_id"]}
STATUS = {**TASK_ID, "required": []}
TOOLS = [
    {"name": "homecompute_task_submit", "description": "Queue a deduplicated coding investigation. A person separately approves execution and publication.",
     "inputSchema": SUBMIT, "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "homecompute_task_status", "description": "Read one task by optional task_id, or up to 100 recent tasks. Returns public metadata without prompts, files, credentials or worker transcripts.",
     "inputSchema": STATUS, "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_task_cancel", "description": "Cancel a pending, queued, running or review task. This never authorizes execution or publication.",
     "inputSchema": TASK_ID, "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]


class RpcError(ValueError):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def validate_arguments(arguments: object, schema: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(arguments, dict) or set(arguments) - set(schema["properties"])
            or not set(schema["required"]) <= set(arguments)):
        raise RpcError(-32602, "Invalid tool arguments")
    for key, rule in schema["properties"].items():
        if key not in arguments:
            continue
        value = arguments[key]
        if (not isinstance(value, str) or not rule["minLength"] <= len(value) <= rule["maxLength"]
                or ("pattern" in rule and not re.fullmatch(rule["pattern"], value, re.I if key == "task_id" else 0))):
            raise RpcError(-32602, "Invalid tool arguments")
    return arguments


def metadata(task: dict[str, Any]) -> dict[str, Any]:
    # Never let future backend fields expand the agent-facing information scope.
    return {key: (value[:2048] if isinstance(value, str) else value)
            for key, value in task.items() if key in PUBLIC
            and (value is None or isinstance(value, (str, bool, int, float)))}


def recent_tasks(ledger: Tasks) -> dict[str, Any]:
    rows = ledger.list_tasks()[:100]
    tasks: list[dict[str, Any]] = []
    def encoded(selected: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": json.dumps({"tasks": selected, "truncated": truncated})}], "isError": False}
    for row in rows:
        candidate = encoded(tasks + [metadata(row)], True)
        if len(json.dumps(candidate).encode()) > MAX_RESPONSE_BYTES - 1024:
            break
        tasks.append(metadata(row))
    return encoded(tasks, len(tasks) < len(rows))


def call_tool(params: dict[str, Any], ledger: Tasks) -> dict[str, Any]:
    if set(params) - {"name", "arguments", "_meta"} or not isinstance(params.get("name"), str):
        raise RpcError(-32602, "Invalid tool call")
    tool = next((item for item in TOOLS if item["name"] == params["name"]), None)
    if tool is None:
        raise RpcError(-32602, "Unknown tool")
    arguments = validate_arguments(params.get("arguments", {}), tool["inputSchema"])
    try:
        if tool["name"] == "homecompute_task_submit":
            task = ledger.submit(arguments)
        elif tool["name"] == "homecompute_task_status":
            if "task_id" not in arguments:
                return recent_tasks(ledger)
            task = ledger.get(arguments["task_id"])
        else:
            task = ledger.action(arguments["task_id"], "cancel", {})
        return {"content": [{"type": "text", "text": json.dumps({"task": metadata(task)})}], "isError": False}
    except (KeyError, ValueError, TypeError):
        # Context, repository output, SQL diagnostics and credentials are private.
        return {"content": [{"type": "text", "text": "Task request rejected; check task status and operator policy."}], "isError": True}


def dispatch(message: object, ledger: Tasks) -> tuple[int, dict[str, Any] | None]:
    """Return an HTTP status and JSON-RPC response, or 202/no body notification."""
    request_id = None
    try:
        if not isinstance(message, dict):
            raise RpcError(-32600, "JSON-RPC object required")
        candidate = message.get("id")
        if type(candidate) is int or (isinstance(candidate, str) and len(candidate) <= 128):
            request_id = candidate
        if (set(message) - {"jsonrpc", "id", "method", "params"} or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or ("id" in message and request_id is None)):
            raise RpcError(-32600, "Invalid JSON-RPC request")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise RpcError(-32602, "Object parameters required")
        method = message["method"]
        if "id" not in message:
            if method in {"notifications/initialized", "notifications/cancelled"}:
                # Protocol cancellation cannot change durable task state. Only
                # the explicit bounded cancellation tool performs that action.
                return 202, None
            raise RpcError(-32600, "Request id required")
        if method == "initialize":
            if not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                raise RpcError(-32602, "Invalid initialization")
            version = params["protocolVersion"] if params["protocolVersion"] in VERSIONS else "2025-06-18"
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "homecompute-task-broker", "version": "1.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if set(params) - {"_meta"}:
                raise RpcError(-32602, "No pagination cursor is supported")
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(params, ledger)
        else:
            raise RpcError(-32601, "Method not available")
        return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as error:
        status = 400 if error.code == -32600 else 200
        return status, {"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": error.message}}


def handle_http(handler: Any, ledger: Tasks, role: str) -> None:
    """HTTP boundary called only after broker authentication, at exactly /mcp."""
    if role != "assistant":
        return handler.respond(403, {"error": "assistant credential required"})
    # Native managed clients have no browser Origin; any browser origin is
    # refused. The endpoint intentionally has no cross-origin browser UI.
    if handler.headers.get("Origin") is not None:
        return handler.respond(403, {"error": "browser origin not permitted"})
    if handler.headers.get("MCP-Protocol-Version", "2025-03-26") not in VERSIONS:
        return handler.respond(400, {"error": "unsupported protocol version"})
    if handler.command != "POST":
        return handler.respond(405, {"error": "POST required"})
    if handler.headers.get_content_type() != "application/json":
        return handler.respond(415, {"error": "JSON content type required"})
    accepts = {part.split(";", 1)[0].strip().lower() for part in handler.headers.get("Accept", "").split(",")}
    if not {"application/json", "text/event-stream"} <= accepts:
        return handler.respond(406, {"error": "JSON and event-stream acceptance required"})
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) != 1 or handler.headers.get("Transfer-Encoding") is not None:
        return handler.respond(400, {"error": "one bounded content length required"})
    try:
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY_BYTES:
            return handler.respond(413, {"error": "body budget exceeded"})
        raw = handler.rfile.read(size)
        if len(raw) != size:
            return handler.respond(400, {"error": "incomplete request"})
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return handler.respond(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}})
    status, response = dispatch(message, ledger)
    if response is None:
        handler.send_response(status)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    else:
        handler.respond(status, response)
