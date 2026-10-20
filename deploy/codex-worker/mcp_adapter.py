"""Finite, stateless MCP surface for NemoClaw's endpoint-bound bearer provider.

HTTPS termination must expose only /mcp; this module never handles credential
values. The trusted broker authenticates the assistant role before dispatch.
Transport: MCP 2025-06-18 Streamable HTTP, JSON responses, no receive stream.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol
from policy import validate_proposal

MAX_BODY_BYTES = 131072
MAX_RESPONSE_BYTES = 262144
VERSIONS = {"2025-03-26", "2025-06-18"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
PROJECT = r"[a-z0-9][a-z0-9-]{0,63}"
ISSUE = r"[A-Za-z0-9_.:-]{1,128}"
PUBLIC = {"id", "project", "issue_key", "state", "status", "phase", "progress", "approval_required", "approval_kind",
          "created", "updated", "session_id", "commit", "pr_url", "error", "error_code"}
PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*")


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
    {"name": "homecompute_task_submit", "description": "Request deduplicated isolated coding preparation under reviewed project policy. This never authorizes publication, deployment or host changes.",
     "inputSchema": SUBMIT, "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "homecompute_task_status", "description": "Read one task by optional task_id, or up to 100 recent tasks. Returns public metadata without prompts, files, credentials or worker transcripts.",
     "inputSchema": STATUS, "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_task_cancel", "description": "Cancel a pending, queued, running or review task. This never authorizes execution or publication.",
     "inputSchema": TASK_ID, "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
ACTION_TOOLS = [
    {"name": "homecompute_action_propose", "description": "Propose one operator-defined maintenance or recovery action using a trusted evidence ID. A person reviews the exact proposal and separately approves execution.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"action_id": string(1, 128, ISSUE), "target": string(1, 128, ISSUE),
                                    "incident_key": string(1, 128, ISSUE), "evidence_id": string(1, 128, ISSUE)},
                     "required": ["action_id", "target", "incident_key", "evidence_id"]},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "homecompute_action_status", "description": "Read bounded action metadata by optional task_id. Evidence ingestion, review, approval and execution are operator-only.",
     "inputSchema": STATUS, "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_action_cancel", "description": "Cancel a maintenance proposal before execution. This cannot undo or retry an action.",
     "inputSchema": TASK_ID, "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]
ACTION_PUBLIC = {"id", "action_id", "target", "incident_key", "evidence_id", "state", "created", "updated",
                 "attempts", "error", "policy_sha256"}
READ_TOOLS = [
    {"name": "homecompute_systems", "description": "List the operator-configured systems available for bounded metadata reads.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_system_read", "description": "Collect scoped service health, host metrics, update metadata and approved Home Assistant metadata. Unknown or stale checks remain unknown. This cannot change services or devices.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"system_id": {**string(1, 64), "enum": ["home-core", "home-spark", "home-assistant", "all"]}},
                     "required": ["system_id"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
]
HA_ACTION = {"type": "object", "additionalProperties": False,
             "properties": {"domain": {"type": "string", "enum": ["light", "climate", "cover"]},
                            "service": {"type": "string", "enum": ["turn_on", "turn_off", "set_temperature", "set_hvac_mode", "open_cover", "close_cover", "stop_cover"]},
                            "entity_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                                           "items": string(3, 140, r"[a-z_]+\.[a-z0-9_]{1,128}")},
                            "data": {"type": "object", "additionalProperties": False,
                                     "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 255},
                                                    "transition": {"type": "number", "minimum": 0, "maximum": 30},
                                                    "temperature": {"type": "number", "minimum": 5, "maximum": 35},
                                                    "hvac_mode": {"type": "string", "enum": ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]}}}},
             "required": ["domain", "service", "entity_ids", "data"]}
HA_ID = {"type": "object", "additionalProperties": False,
         "properties": {"proposal_id": string(32, 32, r"[a-f0-9]{32}")}, "required": ["proposal_id"]}
HA_TOOLS = [
    {"name": "homecompute_ha_propose", "description": "Propose a bounded exact-target light, climate or cover action. A human separately reviews and approves it; this tool cannot execute device changes.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"action": HA_ACTION}, "required": ["action"]},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "homecompute_ha_status", "description": "Read your own HA action proposal status. This cannot review or approve it.",
     "inputSchema": HA_ID, "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_ha_cancel", "description": "Cancel your own HA proposal before execution. This cannot execute, undo or retry an action.",
     "inputSchema": HA_ID, "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
]


MAINTENANCE_ID = {"type": "object", "additionalProperties": False,
                  "properties": {"plan_id": string(32, 32, r"[a-f0-9]{32}")}, "required": ["plan_id"]}
MAINTENANCE_TOOLS = [
    {"name": "homecompute_maintenance_prepare", "description": "Prepare a trusted exact update plan without installing it. Select home-core nixos or a reviewed Docker service, home-spark packages, or Home Assistant an approved update entity. Read preparation status and list plans afterward. No commands or versions accepted.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"target": {**string(1, 64), "enum": ["home-core", "home-spark", "home-assistant"]},
                                    "component": string(1, 128, r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}")},
                     "required": ["target", "component"]},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}},
    {"name": "homecompute_maintenance_preparation", "description": "Read the latest maintenance plan preparation status and resulting plan ID or failure. Preparing does not install updates.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_maintenance_list", "description": "List trusted prepared update and repair plans for home-core, home-spark and Home Assistant, including versions and approval requirements.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_maintenance_status", "description": "Read one trusted maintenance plan and its result. Human approval is required for fixes, major upgrades and uncertain versions.",
     "inputSchema": MAINTENANCE_ID, "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "homecompute_maintenance_run", "description": "Run one root-prepared exact maintenance plan. Verified routine updates use standing authorization; fixes and major or uncertain upgrades are blocked until exact human approval. Commands and versions cannot be supplied by this tool.",
     "inputSchema": MAINTENANCE_ID, "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}},
]


def tool_inventory(ledger: Tasks) -> list[dict[str, Any]]:
    return (TOOLS + (ACTION_TOOLS if getattr(ledger, "actions", None) is not None else [])
            + (READ_TOOLS if getattr(ledger, "infrastructure", None) is not None else [])
            + (HA_TOOLS if getattr(ledger, "home_assistant", None) is not None else [])
            + (MAINTENANCE_TOOLS if getattr(ledger, "maintenance", None) is not None else []))


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
                or ("enum" in rule and value not in rule["enum"])
                or ("pattern" in rule and not re.fullmatch(rule["pattern"], value, re.I if key == "task_id" else 0))):
            raise RpcError(-32602, "Invalid tool arguments")
    return arguments


def metadata(task: dict[str, Any]) -> dict[str, Any]:
    # Never let future backend fields expand the agent-facing information scope.
    public = {key: (value[:2048] if isinstance(value, str) else value)
            for key, value in task.items() if key in PUBLIC
            and (value is None or isinstance(value, (str, bool, int, float)))}
    result = task.get("result")
    if isinstance(result, dict):
        safe = {}
        for key in ("ok", "tests_passed"):
            if type(result.get(key)) is bool:
                safe[key] = result[key]
        if type(result.get("changed_files")) is int and 0 <= result["changed_files"] <= 1000:
            safe["changed_files"] = result["changed_files"]
        if result.get("session_id") is None:
            if "session_id" in result:
                safe["session_id"] = None
        elif isinstance(result["session_id"], str) and re.fullmatch(r"[a-zA-Z0-9-]{1,80}", result["session_id"]):
            safe["session_id"] = result["session_id"]
        public["result"] = safe
        if "proposal" in result:
            safe["proposal"] = validate_proposal(result["proposal"])
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list):
        public["artifacts"] = [{"kind": "pull_request", "url": item["url"]} for item in artifacts[:10]
                               if isinstance(item, dict) and item.get("kind") == "pull_request"
                               and isinstance(item.get("url"), str) and PR_URL.fullmatch(item["url"])]
    return public


def bounded_result(value: dict[str, Any]) -> dict[str, Any]:
    result = {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}], "isError": False}
    if len(json.dumps(result).encode()) > MAX_RESPONSE_BYTES - 1024:
        raise ValueError("tool response exceeds metadata budget")
    return result


def action_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {key: (value[:2048] if isinstance(value, str) else value)
            for key, value in row.items() if key in ACTION_PUBLIC
            and (value is None or isinstance(value, (str, bool, int))
                 or isinstance(value, float) and math.isfinite(value))}


def recent_metadata(rows: list[dict[str, Any]], key: str, project: Any) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    def encoded(selected: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
        return bounded_result({key: selected, "truncated": truncated})
    for row in rows[:100]:
        public = project(row)
        try:
            candidate = encoded(selected + [public], True)
        except ValueError:
            break
        if len(json.dumps(candidate).encode()) > MAX_RESPONSE_BYTES - 1024:
            break
        selected.append(public)
    return encoded(selected, len(selected) < len(rows))


def recent_tasks(ledger: Tasks) -> dict[str, Any]:
    return recent_metadata(ledger.list_tasks(), "tasks", metadata)


def call_tool(params: dict[str, Any], ledger: Tasks) -> dict[str, Any]:
    if set(params) - {"name", "arguments", "_meta"} or not isinstance(params.get("name"), str):
        raise RpcError(-32602, "Invalid tool call")
    tool = next((item for item in tool_inventory(ledger) if item["name"] == params["name"]), None)
    if tool is None:
        raise RpcError(-32602, "Unknown tool")
    if tool["name"] == "homecompute_ha_propose":
        raw = params.get("arguments")
        if not isinstance(raw, dict) or set(raw) != {"action"}:
            raise RpcError(-32602, "Invalid tool arguments")
        try:
            arguments = {"action": ledger.home_assistant.validate(raw["action"])}
        except (ValueError, TypeError, KeyError):
            raise RpcError(-32602, "Invalid bounded HA action") from None
    else:
        arguments = validate_arguments(params.get("arguments", {}), tool["inputSchema"])
    try:
        if tool["name"].startswith("homecompute_maintenance_"):
            operation = tool["name"].removeprefix("homecompute_maintenance_")
            return bounded_result(ledger.maintenance.request(operation, arguments))
        elif tool["name"].startswith("homecompute_ha_"):
            operation = tool["name"].removeprefix("homecompute_ha_")
            return bounded_result({"proposal": ledger.home_assistant.request(operation, arguments)})
        elif tool["name"] in {"homecompute_systems", "homecompute_system_read"}:
            reader = ledger.infrastructure
            if tool["name"] == "homecompute_systems":
                return bounded_result({"systems": [{"id": row["id"], "read_enabled": row["enabled"]}
                    for row in reader.registry["systems"]], "metadata_only": True})
            return bounded_result({"report": reader.compact(reader.read(arguments["system_id"])), "metadata_only": True})
        elif tool["name"].startswith("homecompute_action_"):
            actions = ledger.actions
            if tool["name"] == "homecompute_action_propose":
                value = actions.propose(arguments)
            elif tool["name"] == "homecompute_action_cancel":
                value = actions.cancel(arguments["task_id"])
            else:
                if "task_id" not in arguments:
                    return recent_metadata(actions.list_actions(), "actions", action_metadata)
                value = actions.get(arguments["task_id"])
            return bounded_result({"actions": [action_metadata(value)]})
        elif tool["name"] == "homecompute_task_submit":
            task = ledger.submit(arguments)
        elif tool["name"] == "homecompute_task_status":
            if "task_id" not in arguments:
                return recent_tasks(ledger)
            task = ledger.get(arguments["task_id"])
        else:
            task = ledger.action(arguments["task_id"], "cancel", {})
        return bounded_result({"task": metadata(task)})
    except (KeyError, ValueError, TypeError, OSError, TimeoutError):
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
            result = {"tools": tool_inventory(ledger)}
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
    except (ValueError, UnicodeError, RecursionError):
        return handler.respond(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}})
    version = handler.headers.get("MCP-Protocol-Version", "2025-03-26")
    if version not in VERSIONS:
        # An initial version offer is negotiated by initialize, before the
        # subsequent-request version header becomes authoritative. SDK clients
        # may seed that header with their newer offer. Counter-offer June2025;
        # never accept an unsupported version for tools or notifications.
        offer = (isinstance(message, dict) and message.get("method") == "initialize"
                 and "id" in message and isinstance(message.get("params"), dict)
                 and message["params"].get("protocolVersion") == version
                 and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", version)
                 and handler.headers.get("MCP-Session-Id") is None)
        if not offer:
            return handler.respond(400, {"error": "unsupported protocol version"})
    status, response = dispatch(message, ledger)
    if response is None:
        handler.send_response(status)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    else:
        handler.respond(status, response)
