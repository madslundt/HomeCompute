from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field


# The Docker image copies this file to /app/app.py and the frontend build to
# /app/dist. Resolve the directory containing this file so StaticFiles mounts
# the UI in the runtime image as well as during local development.
APP_ROOT = Path(__file__).resolve().parent
CATALOG_PATH = Path(os.environ.get("MODEL_MANAGER_CATALOG", "/app/config/model-catalog.json"))
SSH_KEY = Path(os.environ.get("MODEL_MANAGER_SSH_KEY", "/run/secrets/model_manager_ssh_key"))
KNOWN_HOSTS = Path(os.environ.get("MODEL_MANAGER_KNOWN_HOSTS", "/run/secrets/model_manager_known_hosts"))
SSH_TARGET_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,31}@192\.168\.30\.126$")
REMOTE_TIMEOUT_SECONDS = min(max(int(os.environ.get("MODEL_MANAGER_ACTION_TIMEOUT", "21600")), 30), 21600)
REMOTE_HELPER_TIMEOUT_SECONDS = 45
MAX_OUTPUT_BYTES = 128_000
SESSION_COOKIE = "homecompute_model_manager_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

app = FastAPI(title="HomeCompute model manager", docs_url=None, redoc_url=None)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spark-action")
operation_lock = threading.Lock()
operation_guard = threading.Lock()
operations: dict[str, dict[str, Any]] = {}
sessions: dict[str, float] = {}
session_guard = threading.Lock()
login_failures: dict[str, dict[str, float | int]] = {}
login_guard = threading.Lock()
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCK_SECONDS = 300
LOGIN_MAX_CLIENTS = 1024
MAX_OPERATIONS = 80


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["prepare", "load", "unload", "replace"]
    deployment: str | None = Field(default=None, min_length=1, max_length=80)
    source: str | None = Field(default=None, min_length=1, max_length=80)
    target: str | None = Field(default=None, min_length=1, max_length=80)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_file(env_name: str, default_path: str) -> str:
    path = Path(os.environ.get(env_name, default_path))
    try:
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError:
        return ""


def assert_same_origin(origin: str | None) -> None:
    expected = os.environ.get("MODEL_MANAGER_PUBLIC_ORIGIN", "").rstrip("/")
    if not expected or origin != expected:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def catalog() -> dict[str, Any]:
    try:
        value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(value.get("deployments"), dict) or not isinstance(value.get("artifacts"), dict):
            raise ValueError("catalog is missing deployments or artifacts")
        return value
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="The model catalog is unavailable or invalid.") from exc


def config_deployments() -> dict[str, dict[str, Any]]:
    model_catalog = catalog()
    result: dict[str, dict[str, Any]] = {}
    for deployment_id, deployment in model_catalog["deployments"].items():
        artifact = model_catalog["artifacts"].get(deployment.get("artifact"), {})
        runtime = model_catalog.get("runtime_profiles", {}).get(deployment.get("runtime_profile"), {})
        result[deployment_id] = {
            "id": deployment_id,
            "label": deployment.get("served_model_name", deployment_id),
            "artifact": artifact.get("upstream_model_id", ""),
            "revision": artifact.get("revision", ""),
            "runtime": runtime.get("family", "unknown"),
            "lifecycle": deployment.get("lifecycle", "unknown"),
            "availability": deployment.get("availability", "unknown"),
            "qualification": artifact.get("qualification", {}),
        }
    return result


def ssh_target() -> str:
    target = os.environ.get("MODEL_MANAGER_SSH_TARGET", "")
    if target.startswith("-") or not SSH_TARGET_RE.fullmatch(target):
        raise HTTPException(status_code=503, detail="Spark manager connection is not configured.")
    if not SSH_KEY.is_file() or not KNOWN_HOSTS.is_file():
        raise HTTPException(status_code=503, detail="Spark manager SSH credentials are unavailable.")
    return target


def client_address(request: Request) -> str:
    client = request.client
    return client.host if client and client.host else "unknown"


def login_lock_remaining(address: str) -> int:
    current = time.time()
    with login_guard:
        state = login_failures.get(address)
        if not state:
            return 0
        locked_until = float(state.get("locked_until", 0))
        if locked_until > current:
            return max(1, int(locked_until - current))
        if float(state.get("window_started", 0)) + LOGIN_WINDOW_SECONDS <= current:
            login_failures.pop(address, None)
    return 0


def record_login_failure(address: str) -> None:
    current = time.time()
    with login_guard:
        state = login_failures.get(address)
        if not state or float(state.get("window_started", 0)) + LOGIN_WINDOW_SECONDS <= current:
            state = {"count": 0, "window_started": current, "locked_until": 0}
            login_failures[address] = state
        state["count"] = int(state.get("count", 0)) + 1
        if int(state["count"]) >= LOGIN_LIMIT:
            state["locked_until"] = current + LOGIN_LOCK_SECONDS
        if len(login_failures) > LOGIN_MAX_CLIENTS:
            # Bound memory if clients rotate source IPs; stale entries are cheap to evict.
            oldest = min(login_failures, key=lambda key: float(login_failures[key].get("window_started", 0)))
            login_failures.pop(oldest, None)


def clear_login_failures(address: str) -> None:
    with login_guard:
        login_failures.pop(address, None)


def run_remote(request: dict[str, str], timeout: int = REMOTE_HELPER_TIMEOUT_SECONDS) -> dict[str, Any]:
    target = ssh_target()
    # The SSH key on the Spark must be restricted to the no-argument adapter.
    # No remote command is sent: the authorized_keys forced command receives this JSON on stdin.
    argv = [
        "ssh", "-F", "/dev/null", "-T", "-i", str(SSH_KEY),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes", target,
    ]
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        completed = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The Spark operation timed out.") from exc
    except OSError as exc:
        raise RuntimeError("Could not start the Spark manager connection.") from exc
    stdout = completed.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace").strip()
    if completed.returncode != 0:
        # Suppress stderr because SSH diagnostics may contain deployment details.
        raise RuntimeError("The Spark adapter rejected the request or returned an error.")
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The Spark adapter returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The Spark adapter returned an unexpected response.")
    if value.get("ok") is False:
        error = value.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        raise RuntimeError(f"The Spark adapter rejected the request ({code or 'operation failed'}).")
    data = value.get("data", value)
    if not isinstance(data, dict):
        raise RuntimeError("The Spark adapter returned an unexpected response.")
    return data


def require_auth(request: Request) -> str:
    expected_user = secret_file("MODEL_MANAGER_USERNAME_FILE", "/run/secrets/model_manager_username")
    expected_password = secret_file("MODEL_MANAGER_PASSWORD_FILE", "/run/secrets/model_manager_password")
    if not expected_user or not expected_password:
        raise HTTPException(status_code=503, detail="Operator authentication is not configured.")
    token = request.cookies.get(SESSION_COOKIE, "")
    with session_guard:
        expiry = sessions.get(token)
        if expiry is not None and expiry > time.time():
            return expected_user
        if token:
            sessions.pop(token, None)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")


def list_entries() -> list[dict[str, Any]]:
    raw = run_remote({"action": "list"})
    local = config_deployments()
    remote_items: Any = raw.get("deployments", raw.get("models", []))
    if isinstance(remote_items, dict):
        remote_items = [dict(value, id=key) if isinstance(value, dict) else {"id": key} for key, value in remote_items.items()]
    if not isinstance(remote_items, list):
        raise RuntimeError("The Spark adapter list response has no deployment list.")
    remote_by_id: dict[str, dict[str, Any]] = {}
    for item in remote_items:
        if isinstance(item, dict):
            deployment_id = item.get("id", item.get("deployment"))
            if isinstance(deployment_id, str) and deployment_id in local:
                remote_by_id[deployment_id] = item
    result = []
    for deployment_id, entry in local.items():
        remote = remote_by_id.get(deployment_id)
        eligible = bool(remote and remote.get("eligible", False))
        enabled = bool(remote and remote.get("enabled", eligible))
        qualified = bool(remote and remote.get("qualified", eligible))
        result.append({
            **entry,
            "enabled": enabled,
            "qualified": qualified,
            "selectable": enabled and qualified,
            "adapter_label": remote.get("name") if remote else None,
            "reason": remote.get("reason") if remote else "Not returned by the Spark adapter.",
        })
    return result


def parse_status(raw: dict[str, Any], models: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {m["id"]: {"id": m["id"], "state": "unknown", "runtime": m["runtime"]} for m in models}
    entries: Any = raw.get("deployments", raw.get("workloads", raw.get("models", [])))
    if isinstance(entries, dict):
        entries = [dict(value, id=key) if isinstance(value, dict) else {"id": key, "state": value} for key, value in entries.items()]
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            deployment_id = item.get("id", item.get("deployment", item.get("deployment_id")))
            if deployment_id not in by_id:
                continue
            state = item.get("state", item.get("status", item.get("phase", "unknown")))
            by_id[deployment_id].update({
                "state": str(state).lower(),
                "runtime": item.get("runtime", by_id[deployment_id]["runtime"]),
                "detail": item.get("detail", item.get("message", "")),
                "port_available": item.get("port_available"),
            })
    memory = raw.get("gpu_memory", raw.get("memory", {}))
    if not isinstance(memory, dict):
        memory = {}
    return {
        "connected": True,
        "cluster": raw.get("cluster"),
        "deployments": list(by_id.values()),
        "gpu_memory": {
            "total": memory.get("total", memory.get("total_bytes")),
            "used": memory.get("used", memory.get("used_bytes")),
            "free": memory.get("free", memory.get("free_bytes")),
        },
        "reported_at": now(),
    }


def run_operation(operation_id: str, payload: dict[str, str]) -> None:
    with operation_lock:
        with operation_guard:
            operations[operation_id]["state"] = "running"
            operations[operation_id]["started_at"] = now()
        try:
            result = run_remote(payload, timeout=REMOTE_TIMEOUT_SECONDS)
            with operation_guard:
                operations[operation_id].update({"state": "succeeded", "result": result, "finished_at": now()})
        except Exception as exc:  # noqa: BLE001 - surface safe, bounded operator message
            with operation_guard:
                operations[operation_id].update({"state": "failed", "error": str(exc), "finished_at": now()})


def public_operation(value: dict[str, Any]) -> dict[str, Any]:
    result = {k: v for k, v in value.items() if k != "future"}
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/api/session")
def create_session(body: LoginRequest, request: Request) -> Any:
    assert_same_origin(request.headers.get("origin"))
    expected_user = secret_file("MODEL_MANAGER_USERNAME_FILE", "/run/secrets/model_manager_username")
    expected_password = secret_file("MODEL_MANAGER_PASSWORD_FILE", "/run/secrets/model_manager_password")
    if not expected_user or not expected_password:
        raise HTTPException(status_code=503, detail="Operator authentication is not configured.")
    address = client_address(request)
    remaining = login_lock_remaining(address)
    if remaining:
        raise HTTPException(status_code=429, detail="Sign in temporarily unavailable. Try again later.", headers={"Retry-After": str(remaining)})
    if not (secrets.compare_digest(body.username, expected_user) and secrets.compare_digest(body.password, expected_password)):
        record_login_failure(address)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in failed.")
    clear_login_failures(address)
    token = secrets.token_urlsafe(32)
    with session_guard:
        sessions[token] = time.time() + SESSION_TTL_SECONDS
    secure_cookie = os.environ.get("MODEL_MANAGER_COOKIE_SECURE", "true").lower() == "true"
    from fastapi.responses import JSONResponse
    response = JSONResponse({"authenticated": True})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True, secure=secure_cookie, samesite="strict", path="/")
    return response


@app.delete("/api/session")
def delete_session(request: Request, _: str = Depends(require_auth)) -> Any:
    assert_same_origin(request.headers.get("origin"))
    token = request.cookies.get(SESSION_COOKIE, "")
    with session_guard:
        sessions.pop(token, None)
    from fastapi.responses import JSONResponse
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=os.environ.get("MODEL_MANAGER_COOKIE_SECURE", "true").lower() == "true", httponly=True, samesite="strict")
    return response


@app.get("/api/models")
def models(_: str = Depends(require_auth)) -> dict[str, Any]:
    try:
        return {"models": list_entries(), "reported_at": now()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read approved models from the Spark adapter.") from exc


@app.get("/api/status")
def status_snapshot(_: str = Depends(require_auth)) -> dict[str, Any]:
    try:
        model_list = list_entries()
        remote = run_remote({"action": "status"})
        return parse_status(remote, model_list)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read status from the Spark adapter.") from exc


@app.get("/api/operations")
def recent_operations(_: str = Depends(require_auth)) -> dict[str, Any]:
    with operation_guard:
        values = sorted(operations.values(), key=lambda item: item.get("submitted_at", ""), reverse=True)
        return {"operations": [public_operation(v) for v in values[:24]]}


@app.get("/api/operations/{operation_id}")
def operation(operation_id: str, _: str = Depends(require_auth)) -> dict[str, Any]:
    with operation_guard:
        value = operations.get(operation_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Operation not found. It may have expired after a service restart.")
        return public_operation(value)


@app.post("/api/actions", status_code=status.HTTP_202_ACCEPTED)
def submit_action(request: ActionRequest, http_request: Request, _: str = Depends(require_auth)) -> dict[str, Any]:
    assert_same_origin(http_request.headers.get("origin"))
    # Resolve IDs from the read-only HomeCompute catalog here. Do not invoke
    # the Spark list/status path: the adapter serializes every request behind
    # its lifecycle flock, so an action submit must remain responsive while a
    # long prepare/load action owns that lock. The adapter checks eligibility
    # again when this queued action executes.
    model_index = config_deployments()
    if request.action == "replace":
        if request.deployment is not None or request.source is None or request.target is None:
            raise HTTPException(status_code=422, detail="Replace requires only source and target deployment IDs.")
        if request.source not in model_index or request.target not in model_index:
            raise HTTPException(status_code=422, detail="Choose deployments from the approved model catalog.")
        if request.source == request.target:
            raise HTTPException(status_code=422, detail="Source and replacement must be different deployments.")
        payload = {"action": "replace", "from": request.source, "to": request.target}
    else:
        if request.deployment is None or request.source is not None or request.target is not None:
            raise HTTPException(status_code=422, detail="This action requires only one deployment ID.")
        if request.deployment not in model_index:
            raise HTTPException(status_code=422, detail="Choose a deployment from the approved model catalog.")
        payload = {"action": request.action, "deployment": request.deployment}
    operation_id = str(uuid.uuid4())
    entry: dict[str, Any] = {"id": operation_id, "action": request.action, "state": "queued", "request": payload, "submitted_at": now()}
    with operation_guard:
        if any(v["state"] in {"queued", "running"} for v in operations.values()):
            raise HTTPException(status_code=409, detail="A model lifecycle operation is already running.")
        operations[operation_id] = entry
        if len(operations) > MAX_OPERATIONS:
            oldest = sorted(operations.values(), key=lambda item: item["submitted_at"])[0]
            operations.pop(oldest["id"], None)
        entry["future"] = executor.submit(run_operation, operation_id, payload)
    return {"operation": public_operation(entry)}


frontend = APP_ROOT / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
