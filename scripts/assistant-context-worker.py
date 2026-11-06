#!/usr/bin/env python3
"""Guest-side fixed OpenClaw RPC adapter for the core delegation controller."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable

ROLE = "assistant-context-worker"
TASK_TYPES = {"analysis", "summary", "comparison"}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SESSION_PREFIX = f"agent:{ROLE}:hc-"
MAX_INPUT = 24000
MAX_RESULT = 131072
NATIVE = ["openclaw", "gateway", "call"]


class WorkerRefused(ValueError):
    pass


def run_native(method: str, params: dict[str, Any], timeout_ms: int,
               runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if method not in {"agent", "agent.wait", "sessions.abort"} or not 1000 <= timeout_ms <= 35000:
        raise WorkerRefused("native_call_not_allowed")
    command = [*NATIVE, method, "--params", json.dumps(params, separators=(",", ":")),
               "--json", "--timeout", str(timeout_ms)]
    env = {"NEMOCLAW_GATEWAY_PORT": "9123",
           "PATH": "/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"}
    if isinstance(os.environ.get("HOME"), str) and os.environ["HOME"].startswith("/"):
        env["HOME"] = os.environ["HOME"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=(timeout_ms / 1000) + 5,
                        stdin=subprocess.DEVNULL, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise WorkerRefused("native_rpc_outcome_unknown") from None
    if result.returncode != 0 or len(result.stdout.encode()) > MAX_RESULT:
        raise WorkerRefused("native_rpc_returned_no_receipt")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise WorkerRefused("native_rpc_receipt_invalid") from None
    if not isinstance(value, dict):
        raise WorkerRefused("native_rpc_receipt_not_object")
    return value


def handle(value: dict[str, Any], runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if (set(value) != {"schema_version", "operation", "request"} or value["schema_version"] != 1
            or not isinstance(value["operation"], str)
            or value["operation"] not in {"submit", "status", "cancel"}
            or not isinstance(value["request"], dict)):
        raise WorkerRefused("worker_envelope_invalid")
    operation, request = value["operation"], value["request"]
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not UUID.fullmatch(request_id):
        raise WorkerRefused("request_id_invalid")
    session_key = request.get("session_key")
    if session_key != SESSION_PREFIX + request_id:
        raise WorkerRefused("native_session_scope_invalid")
    if operation == "submit":
        if set(request) != {"operation", "request_id", "task_type", "session_key", "prompt", "budget_seconds"}:
            raise WorkerRefused("submit_fields_invalid")
        if not isinstance(request["task_type"], str) or request["task_type"] not in TASK_TYPES:
            raise WorkerRefused("task_type_invalid")
        prompt = request["prompt"]
        budget = request["budget_seconds"]
        if (not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt or len(prompt.encode()) > 16384
                or type(budget) is not int or not 1 <= budget <= 300):
            raise WorkerRefused("submit_budget_invalid")
        params = {"message": prompt, "agentId": ROLE, "sessionKey": session_key,
                  "idempotencyKey": request_id, "timeout": budget, "thinking": "off", "deliver": False}
        native = run_native("agent", params, 10000, runner)
        if (native.get("runId") != request_id or native.get("sessionKey") != session_key
                or native.get("agentId") != ROLE or native.get("status") != "accepted"
                or type(native.get("acceptedAt")) is not int):
            raise WorkerRefused("native_admission_not_confirmed")
    elif operation == "status":
        if set(request) != {"request_id", "session_key", "wait_ms"}:
            raise WorkerRefused("status_fields_invalid")
        wait_ms = request["wait_ms"]
        if type(wait_ms) is not int or not 0 <= wait_ms <= 30000:
            raise WorkerRefused("wait_budget_invalid")
        native = run_native("agent.wait", {"runId": request_id, "timeoutMs": wait_ms}, wait_ms + 1000, runner)
        if native.get("runId") != request_id:
            raise WorkerRefused("native_status_reference_mismatch")
    else:
        if set(request) != {"request_id", "session_key"}:
            raise WorkerRefused("cancel_fields_invalid")
        native = run_native("sessions.abort", {"key": session_key, "runId": request_id,
                                                 "agentId": ROLE, "clearQueued": False}, 10000, runner)
        if native.get("status") not in {"aborted", "no-active-run"}:
            raise WorkerRefused("native_cancel_status_invalid")
        if native.get("abortedRunId") not in {None, request_id}:
            raise WorkerRefused("native_cancel_reference_mismatch")
        native = {**native, "runId": request_id}
    return {"schema_version": 1, "ok": True, "native": native}


def main() -> int:
    try:
        if sys.argv[1:] != ["--stdio"]:
            raise WorkerRefused("stdio_mode_required")
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise WorkerRefused("worker_input_exceeded")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise WorkerRefused("worker_input_invalid")
        result = handle(value)
        sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n")
        return 0
    except Exception as error:
        # Never echo request text, model output, command output, or credentials.
        reason = error.args[0] if isinstance(error, WorkerRefused) and error.args else "worker_failed"
        sys.stderr.write(json.dumps({"error": "worker_refused", "reason": reason}) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
