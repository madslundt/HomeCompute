#!/usr/bin/env python3
"""Private, exact-pin Qwen3.8-27B comparison under the Flash-Next owner.

The caller is setup-compute-flash-next.sh. This helper does not install or
download artifacts, change aliases, or expose output. A pending root-only
journal remains when exact restoration cannot be proven; `recover` resumes it.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = Path("/run/homecompute-gb10-text-model/lock")
DEPLOY_LOCK = Path("/var/lib/homecompute/home-spark-deploy.lock")
STATE_ROOT = Path("/var/lib/homecompute-qwen27b-qualification")
PENDING = STATE_ROOT / "pending.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 1800
WAIT_SECONDS = 1800
TEXT_CONTAINER_NAMES = {
    "gb10-text-edge", "gb10-text-primary", "gb10-automation-edge",
    "gb10-automation-primary", "gb10-automation-nvidia-edge",
    "gb10-automation-nvidia-primary", "gb10-home-edge", "gb10-home-primary",
    "qwen38-flash-blazux", "qwen38-flash-ultrafast",
}
WHISPER_CONTAINERS = ("gb10-whisper-cpp-primary", "gb10-whisper-cpp-http")
MODEL_ID = "unsloth/Qwen3.8-27B-NVFP4"
MODEL_REVISION = "57926baca9a82b4d6906b43f2750d55315f5b10f"
DEPLOYMENT_ID = "general-spark-qwen38"


class QualificationError(RuntimeError):
    def __init__(self, stage: str, classification: str):
        super().__init__(classification)
        self.stage = stage
        self.classification = classification


def fail(stage: str, classification: str) -> None:
    raise QualificationError(stage, classification)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("manifest", "manifest-invalid")
    if not isinstance(value, dict):
        fail("manifest", "manifest-invalid")
    return value


def verify_regular_private(path: Path, *, allowed_uids: set[int], exact_mode: int,
                           max_bytes: int) -> bytes:
    if not path.is_absolute():
        fail("manifest", "manifest-path-invalid")
    try:
        parent = path.parent.lstat()
        before = path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
                or not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
                or before.st_uid not in allowed_uids or parent.st_uid not in allowed_uids
                or stat.S_IMODE(before.st_mode) != exact_mode
                or before.st_size > max_bytes
                or parent.st_mode & 0o077 != 0):
            fail("manifest", "manifest-permissions-invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid not in allowed_uids
                    or stat.S_IMODE(opened.st_mode) != exact_mode
                    or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                    != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
                fail("manifest", "manifest-changed")
            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                chunk = os.read(fd, min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or total > max_bytes):
                fail("manifest", "manifest-changed-or-oversized")
            return b"".join(chunks)
        finally:
            os.close(fd)
    except OSError:
        fail("manifest", "manifest-unavailable")


def validate_request(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        request = json.loads(raw, parse_constant=lambda _value: fail("manifest", "request-invalid-number"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("manifest", "request-invalid")
    if not isinstance(request, dict) or set(request) != {"schema_version", "request"}:
        fail("manifest", "request-schema-invalid")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        fail("manifest", "request-schema-invalid")
    body = request["request"]
    allowed = {
        "messages", "temperature", "top_p", "max_tokens", "max_completion_tokens",
        "stream", "stream_options", "chat_template_kwargs", "thinking_token_budget",
        "reasoning_effort", "stop", "frequency_penalty", "presence_penalty", "seed",
        "response_format",
    }
    if not isinstance(body, dict) or not body or set(body) - allowed or "model" in body:
        fail("manifest", "request-fields-invalid")
    caps = {field for field in ("max_tokens", "max_completion_tokens") if field in body}
    if len(caps) != 1:
        fail("manifest", "request-token-limit-required")
    messages = body.get("messages")
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 40
            or any(not isinstance(row, dict) or set(row) - {"role", "content", "name"}
                   or not isinstance(row.get("role"), str)
                   or row.get("role") not in {"system", "user", "assistant"}
                   or not isinstance(row.get("content"), str) for row in messages)):
        fail("manifest", "request-messages-invalid")
    for row in messages:
        if len(row["content"].encode("utf-8")) > 1_000_000:
            fail("manifest", "request-message-too-large")
        if "name" in row and (not isinstance(row["name"], str) or not 1 <= len(row["name"]) <= 64):
            fail("manifest", "request-message-name-invalid")
    if body.get("stream") is not True:
        fail("manifest", "request-must-stream")
    maximum = body.get("max_completion_tokens", body.get("max_tokens"))
    if type(maximum) is not int or not 1 <= maximum <= 65536:
        fail("manifest", "request-token-limit-invalid")
    budget = body.get("thinking_token_budget")
    if budget is not None and (type(budget) is not int or not 0 <= budget <= 8192):
        fail("manifest", "thinking-budget-invalid")
    kwargs = body.get("chat_template_kwargs", {})
    if (not isinstance(kwargs, dict) or set(kwargs) - {"enable_thinking"}
            or ("enable_thinking" in kwargs and type(kwargs["enable_thinking"]) is not bool)):
        fail("manifest", "template-kwargs-invalid")
    effort = body.get("reasoning_effort")
    if effort is not None and (not isinstance(effort, str)
            or effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}):
        fail("manifest", "reasoning-effort-invalid")
    limits = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0),
              "frequency_penalty": (-2.0, 2.0), "presence_penalty": (-2.0, 2.0)}
    for field, (minimum, maximum_value) in limits.items():
        value = body.get(field)
        if value is not None:
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or not minimum <= value <= maximum_value):
                fail("manifest", "sampling-parameter-invalid")
    if "seed" in body and (type(body["seed"]) is not int or not 0 <= body["seed"] <= 2**31 - 1):
        fail("manifest", "seed-invalid")
    stop = body.get("stop")
    if stop is not None and not (isinstance(stop, str) and 1 <= len(stop) <= 100
            or isinstance(stop, list) and 1 <= len(stop) <= 4
            and all(isinstance(item, str) and 1 <= len(item) <= 100 for item in stop)):
        fail("manifest", "stop-invalid")
    stream_options = body.get("stream_options")
    if (stream_options is not None and (not isinstance(stream_options, dict)
            or set(stream_options) != {"include_usage"}
            or type(stream_options["include_usage"]) is not bool)):
        fail("manifest", "stream-options-invalid")
    if "response_format" in body:
        validate_response_format(body["response_format"])
    return body, sha256(raw)


def validate_response_format(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        fail("manifest", "response-format-invalid")
    if value == {"type": "json_object"}:
        return
    if value.get("type") != "json_schema" or set(value) != {"type", "json_schema"}:
        fail("manifest", "response-format-invalid")
    schema = value["json_schema"]
    if (not isinstance(schema, dict) or set(schema) - {"name", "strict", "schema"}
            or not isinstance(schema.get("name"), str) or not 1 <= len(schema["name"]) <= 64
            or type(schema.get("strict", False)) is not bool or not isinstance(schema.get("schema"), dict)):
        fail("manifest", "response-schema-invalid")
    try:
        if len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 128 * 1024:
            fail("manifest", "response-schema-too-large")
    except (TypeError, ValueError):
        fail("manifest", "response-schema-invalid")
    count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 10_000 or depth > 32:
            fail("manifest", "response-schema-too-complex")
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or len(key) > 256:
                    fail("manifest", "response-schema-invalid")
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                        not isinstance(child, str) or not child.startswith("#/")):
                    fail("manifest", "response-schema-external-reference")
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)
        elif isinstance(node, str):
            if len(node) > 8192:
                fail("manifest", "response-schema-string-too-large")
        elif isinstance(node, float) and not math.isfinite(node):
            fail("manifest", "response-schema-invalid-number")
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            fail("manifest", "response-schema-invalid")

    visit(schema["schema"], 0)


def read_env(path: Path) -> dict[str, str]:
    template = (ROOT / "config/compute-node.env.example").read_text(encoding="utf-8")
    allowed = {match.group(1) for line in template.splitlines()
               if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line))}
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            fail("preflight", "compute-config-untrusted")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        fail("preflight", "compute-config-unavailable")
    parsed: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match or match.group(1) not in allowed or match.group(1) in parsed:
            fail("preflight", "compute-config-invalid")
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value.startswith(("'", '"')) or value.endswith(("'", '"')):
            fail("preflight", "compute-config-invalid")
        parsed[match.group(1)] = value
    return parsed


def read_api_key(path: Path, *, expected_uid: int = 0) -> bytes:
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
                or before.st_uid != expected_uid or stat.S_IMODE(before.st_mode) != 0o440
                or not 16 <= before.st_size <= 4096):
            fail("preflight", "api-key-file-untrusted")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != expected_uid
                    or stat.S_IMODE(opened.st_mode) != 0o440
                    or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                    != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
                fail("preflight", "api-key-file-changed")
            value = os.read(fd, 4097)
        finally:
            os.close(fd)
    except OSError:
        fail("preflight", "api-key-file-unavailable")
    key = value.strip()
    if (not key or len(key) > 4096 or any(byte < 33 or byte > 126 for byte in key)):
        fail("preflight", "api-key-file-invalid")
    return key


def acquire_lock(path: Path = LOCK, *, allow_pending: bool = False, expected_uid: int = 0,
                 pending_path: Path = PENDING) -> int:
    try:
        parent = path.parent.lstat()
    except FileNotFoundError:
        if path.parent != LOCK.parent:
            fail("lock", "text-model-lock-directory-untrusted")
        try:
            run_parent = path.parent.parent.lstat()
            if (not stat.S_ISDIR(run_parent.st_mode) or stat.S_ISLNK(run_parent.st_mode)
                    or run_parent.st_uid != expected_uid or run_parent.st_mode & 0o022):
                fail("lock", "text-model-lock-directory-untrusted")
            path.parent.mkdir(mode=0o700)
            parent = path.parent.lstat()
        except FileExistsError:
            parent = path.parent.lstat()
        except OSError:
            fail("lock", "text-model-lock-unavailable")
    except OSError:
        fail("lock", "text-model-lock-unavailable")
    try:
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != expected_uid or stat.S_IMODE(parent.st_mode) != 0o700):
            fail("lock", "text-model-lock-directory-untrusted")
        if not allow_pending and (pending_path.exists() or pending_path.is_symlink()):
            fail("lock", "qwen-qualification-recovery-pending")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022
                or stat.S_IMODE(info.st_mode) != 0o600):
            os.close(fd)
            fail("lock", "text-model-lock-untrusted")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not allow_pending and (pending_path.exists() or pending_path.is_symlink()):
            os.close(fd)
            fail("lock", "qwen-qualification-recovery-pending")
        return fd
    except BlockingIOError:
        fail("lock", "text-model-lifecycle-busy")
    except OSError:
        fail("lock", "text-model-lock-unavailable")


def acquire_deployment_lock(path: Path = DEPLOY_LOCK, *, expected_uid: int = 0) -> int:
    """Serialize against release deployment, which changes the active source pointer."""
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
                or parent.st_uid != expected_uid or stat.S_IMODE(parent.st_mode) != 0o700):
            fail("deployment-lock", "deployment-lock-directory-untrusted")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022):
            os.close(fd)
            fail("deployment-lock", "deployment-lock-untrusted")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            fail("deployment-lock", "deployment-in-progress")
        return fd
    except QualificationError:
        raise
    except OSError:
        fail("deployment-lock", "deployment-lock-unavailable")


def ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        st = current.lstat()
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            fail("private-state", "private-directory-unsafe")
    st = path.stat()
    if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
        fail("private-state", "private-directory-permissions-invalid")


def write_private(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        fail("private-state", "private-output-exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        fail("private-state", "private-output-write-failed")


def open_export_directory(path: str | Path, *, expected_uid: int) -> int:
    destination = Path(path)
    if not destination.is_absolute():
        fail("export-preflight", "export-directory-invalid")
    try:
        before = destination.lstat()
        if (not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode)
                or before.st_uid != expected_uid or stat.S_IMODE(before.st_mode) != 0o700):
            fail("export-preflight", "export-directory-permissions-invalid")
        fd = os.open(destination, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(fd)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != expected_uid
                or stat.S_IMODE(opened.st_mode) != 0o700
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            os.close(fd)
            fail("export-preflight", "export-directory-changed")
        return fd
    except QualificationError:
        raise
    except OSError:
        fail("export-preflight", "export-directory-unavailable")


def export_private_outputs(export_fd: int, evidence_dir: Path, receipt_path: Path,
                           receipt: dict[str, Any], *, owner_uid: int) -> bool:
    """Copy only final receipt and raw model responses, without replacing caller files."""
    created: list[str] = []

    def write_export(name: str, data: bytes) -> None:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                     0o600, dir_fd=export_fd)
        created.append(name)
        try:
            os.fchown(fd, owner_uid, -1)
            os.fchmod(fd, 0o600)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short export write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    try:
        for name in ("flash.response", "qwen27b.response"):
            source = evidence_dir / name
            if not source.exists() and not source.is_symlink():
                continue
            file_fingerprint(source, exact_mode=0o600, role="captured-response")
            data = source.read_bytes()
            if len(data) > MAX_RESPONSE_BYTES:
                fail("export", "export-artifact-oversized")
            write_export(name, data)
        receipt["export_status"] = "complete"
        receipt_data = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(receipt_data) > MAX_RECEIPT_BYTES:
            fail("export", "export-artifact-oversized")
        write_export("receipt.json", receipt_data)
        os.fsync(export_fd)
        return True
    except (OSError, QualificationError):
        for name in reversed(created):
            try:
                os.unlink(name, dir_fd=export_fd)
            except OSError:
                pass
        receipt["export_status"] = "failed"
        write_receipt(receipt_path, receipt)
        return False


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    write_private(temporary, data)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(argv: list[str], *, timeout: int = 60, stage: str = "preflight") -> str:
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        fail(stage, "managed-command-unavailable-or-timeout")
    if result.returncode:
        fail(stage, "managed-command-failed")
    return result.stdout.strip()


def inspect_container(name: str) -> dict[str, Any] | None:
    try:
        template = (
            "{{.Id}}|{{.Name}}|{{.Image}}|{{.Config.Image}}|{{.State.Status}}|"
            "{{if .State.Health}}{{.State.Health.Status}}{{end}}|"
            "{{.HostConfig.RestartPolicy.Name}}|{{.HostConfig.RestartPolicy.MaximumRetryCount}}|"
            "{{if .Config.Labels}}{{index .Config.Labels \"homecompute.managed\"}}{{end}}"
        )
        result = subprocess.run(["docker", "inspect", "--format", template, name], check=False,
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        fail("preflight", "container-inspection-failed")
    if result.returncode:
        if re.fullmatch(rf"Error: No such (?:object|container): {re.escape(name)}\s*", result.stderr):
            return None
        fail("preflight", "container-inspection-failed")
    try:
        fields = result.stdout.rstrip("\n").split("|")
        if len(fields) != 9:
            fail("preflight", "container-inspection-malformed")
        retry = int(fields[7])
        return {
            "id": fields[0], "name": fields[1].lstrip("/"), "image_id": fields[2],
            "image_ref": fields[3], "status": fields[4], "health": fields[5] or None,
            "restart": {"Name": fields[6], "MaximumRetryCount": retry},
            "managed": fields[8] or None,
        }
    except (ValueError, TypeError):
        fail("preflight", "container-inspection-malformed")


def http_request(url: str, body: bytes | None = None, *, key: bytes | None = None,
                 timeout: int = 15, max_bytes: int = MAX_RESPONSE_BYTES) -> tuple[int, bytes]:
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        if (parsed.scheme != "http" or not (address.is_private or address.is_loopback)
                or parsed.username is not None or parsed.password is not None or parsed.port is None):
            return 0, b""
    except ValueError:
        return 0, b""
    headers = {"Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    if key:
        headers["Authorization"] = "Bearer " + key.decode("ascii")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                             headers: Any, newurl: str) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    response: Any = None
    status = 0
    payload = bytearray()
    if threading.current_thread() is not threading.main_thread():
        return 0, b""
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def deadline_expired(_signum: int, _frame: Any) -> None:
        raise TimeoutError("HTTP total deadline elapsed")

    signal.signal(signal.SIGALRM, deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            response = opener.open(request, timeout=timeout)
            status = response.status
        except urllib.error.HTTPError as error:
            response = error
            status = error.code
        while len(payload) <= max_bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            raw = getattr(getattr(response, "fp", None), "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(64 * 1024, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    except (OSError, TimeoutError, http.client.HTTPException, socket.timeout):
        if 200 <= status < 300:
            status = 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        elapsed = time.monotonic() - started
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, old_timer[0] - elapsed), old_timer[1])
        signal.signal(signal.SIGALRM, old_handler)
        if response is not None:
            response.close()
    return status, bytes(payload)


def validate_sse_response(payload: bytes) -> dict[str, Any]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("stream-validation", "response-not-utf8")
    choices: dict[int, dict[str, Any]] = {}
    seen_done = False
    data_lines: list[str] = []

    def consume(data: str) -> None:
        nonlocal seen_done
        if data == "[DONE]":
            if seen_done:
                fail("stream-validation", "response-duplicate-done")
            seen_done = True
            return
        if seen_done:
            fail("stream-validation", "response-data-after-done")
        try:
            chunk = json.loads(data, parse_constant=lambda _value: fail(
                "stream-validation", "response-invalid-number"))
        except (json.JSONDecodeError, UnicodeError):
            fail("stream-validation", "response-invalid-sse-json")
        if not isinstance(chunk, dict) or "error" in chunk:
            fail("stream-validation", "response-stream-error")
        rows = chunk.get("choices")
        if not isinstance(rows, list):
            fail("stream-validation", "response-choice-shape-invalid")
        if not rows:
            if not isinstance(chunk.get("usage"), dict):
                fail("stream-validation", "response-empty-choice-chunk")
            return
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("index")) is not int
                    or not 0 <= row["index"] <= 7 or not isinstance(row.get("delta"), dict)):
                fail("stream-validation", "response-choice-shape-invalid")
            index = row["index"]
            state = choices.setdefault(index, {"chars": 0, "nonspace": 0, "finish_reason": None})
            if state["finish_reason"] is not None:
                fail("stream-validation", "response-choice-after-finish")
            content = row["delta"].get("content", "")
            if content is not None:
                if not isinstance(content, str):
                    fail("stream-validation", "response-content-shape-invalid")
                state["chars"] += len(content)
                state["nonspace"] += len(content.strip())
            reason = row.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str) or reason not in {
                    "stop", "length", "tool_calls", "function_call", "content_filter",
                }:
                    fail("stream-validation", "response-finish-reason-invalid")
                state["finish_reason"] = reason

    for line in lines:
        if line == "":
            if data_lines:
                consume("\n".join(data_lines))
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        consume("\n".join(data_lines))
    if not seen_done:
        fail("stream-validation", "response-stream-incomplete")
    if not choices:
        fail("stream-validation", "response-no-choices")
    reasons = [state["finish_reason"] for _, state in sorted(choices.items())]
    if any(reason is None for reason in reasons):
        fail("stream-validation", "response-choice-unfinished")
    if any(reason == "length" for reason in reasons):
        fail("stream-validation", "response-truncated-at-token-limit")
    if any(reason == "content_filter" for reason in reasons):
        fail("stream-validation", "response-content-filtered")
    visible_chars = sum(state["chars"] for state in choices.values())
    if visible_chars == 0 or sum(state["nonspace"] for state in choices.values()) == 0:
        fail("stream-validation", "response-has-no-visible-text")
    return {"choices": len(choices), "finish_reasons": reasons,
            "visible_chars": visible_chars, "complete": True}


def host_memory() -> tuple[int, int]:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB$", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
        return values["MemAvailable"], values["MemTotal"]
    except (OSError, KeyError, ValueError):
        fail("capacity", "host-memory-unavailable")


def verify_active_release(source_root: Path, *, current_link: Path = Path("/srv/homecompute/current"),
                          releases_root: Path = Path("/srv/homecompute/releases"),
                          expected_uid: int = 0) -> None:
    try:
        link_stat = current_link.lstat()
        active = current_link.resolve(strict=True)
        expected_root = releases_root.resolve(strict=True)
    except OSError:
        fail("preflight", "active-release-unavailable")
    source = source_root.resolve(strict=True)
    if (not stat.S_ISLNK(link_stat.st_mode) or link_stat.st_uid != expected_uid
            or active != source or expected_root not in active.parents):
        fail("preflight", "helper-not-from-active-release")


def verify_whisper() -> dict[str, dict[str, str]]:
    proof: dict[str, dict[str, str]] = {}
    for name in WHISPER_CONTAINERS:
        row = inspect_container(name)
        if not row or row["status"] != "running" or row["health"] != "healthy":
            fail("whisper-health", "whisper-service-not-healthy")
        proof[name] = {"id": row["id"], "image_id": row["image_id"], "health": row["health"]}
    return proof


def check_whisper_same(before: dict[str, dict[str, str]]) -> None:
    after = verify_whisper()
    if after != before:
        fail("whisper-health", "whisper-service-identity-changed")


FINGERPRINT_ROLES = {
    "owner-state": ("owner-state", "owner-state"),
    "flash-owner-record": ("owner-state", "flash-owner-record"),
    "candidate-cache-manifest": ("cache", "candidate-cache-manifest"),
    "active-cache-manifest": ("cache", "active-cache-manifest"),
    "compute-config": ("preflight", "compute-config"),
    "qualification-journal": ("recovery", "qualification-journal"),
    "captured-response": ("export", "captured-response"),
}


def fingerprint_failure(role: str, state: str) -> None:
    if role not in FINGERPRINT_ROLES or state not in {"missing", "unreadable", "untrusted"}:
        fail("owner-state", "owner-state-file-unavailable")
    stage, label = FINGERPRINT_ROLES[role]
    fail(stage, f"{label}-file-{state}")


def file_fingerprint(path: Path, *, owner: int = 0, mode_mask: int = 0o022,
                     exact_mode: int | None = None, role: str = "owner-state") -> str:
    if role not in FINGERPRINT_ROLES:
        fail("owner-state", "owner-state-file-untrusted")
    try:
        st = path.lstat()
    except FileNotFoundError:
        fingerprint_failure(role, "missing")
    except OSError:
        fingerprint_failure(role, "unreadable")
    if (not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)
                or st.st_uid != owner or st.st_mode & mode_mask
            or (exact_mode is not None and stat.S_IMODE(st.st_mode) != exact_mode)):
        fingerprint_failure(role, "untrusted")
    try:
        return sha256(path.read_bytes())
    except FileNotFoundError:
        fingerprint_failure(role, "missing")
    except OSError:
        fingerprint_failure(role, "unreadable")


def parse_record(path: Path, required: set[str], *, exact_mode: int = 0o440) -> dict[str, str]:
    file_fingerprint(path, exact_mode=exact_mode, role="flash-owner-record")
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
            if not match or match.group(1) in data:
                fail("owner-state", "owner-state-record-invalid")
            data[match.group(1)] = match.group(2)
    except FileNotFoundError:
        fingerprint_failure("flash-owner-record", "missing")
    except OSError:
        fingerprint_failure("flash-owner-record", "unreadable")
    except UnicodeError:
        fail("owner-state", "owner-state-record-invalid")
    if not required <= data.keys():
        fail("owner-state", "owner-state-record-incomplete")
    return data


def verify_model_cache(env: dict[str, str], source_root: Path) -> dict[str, str]:
    catalog = safe_json(source_root / "config/model-catalog.json")
    deployment = catalog.get("deployments", {}).get(DEPLOYMENT_ID)
    if not isinstance(deployment, dict) or deployment.get("artifact") != "qwen38-27b-nvfp4":
        fail("cache", "catalog-pinned-fallback-unavailable")
    artifact = catalog.get("artifacts", {}).get(deployment["artifact"])
    if not isinstance(artifact, dict) or artifact.get("upstream_model_id") != MODEL_ID or artifact.get("revision") != MODEL_REVISION:
        fail("cache", "catalog-pinned-fallback-mismatch")
    expected = {
        "MODEL_DEPLOYMENT_ID": DEPLOYMENT_ID, "MODEL_ID": MODEL_ID,
        "MODEL_REVISION": MODEL_REVISION, "TOKENIZER_REVISION": MODEL_REVISION,
        "CODE_REVISION": MODEL_REVISION,
    }
    if any(env.get(key) != value for key, value in expected.items()):
        fail("cache", "configured-fallback-tuple-mismatch")
    runtime = catalog["runtime_profiles"][deployment["runtime_profile"]]
    image = runtime.get("image")
    if env.get("VLLM_IMAGE") != image or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", str(image)):
        fail("cache", "configured-runtime-image-mismatch")
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=15, stage="cache")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        fail("cache", "runtime-image-not-cached")
    root = Path(env.get("GB10_ROOT", ""))
    if not root.is_absolute():
        fail("cache", "compute-root-invalid")
    tuple_data = (f"schema=1\nmodel={MODEL_REVISION}\ntokenizer={MODEL_REVISION}\n"
                  f"code={MODEL_REVISION}\nrepo={MODEL_ID}\n").encode()
    manifest = root / "manifests" / f"accepted-model-cache-{sha256(tuple_data)}.json"
    active = root / "manifests/accepted-model-cache.json"
    manifest_digest = file_fingerprint(manifest, role="candidate-cache-manifest")
    active_digest = file_fingerprint(active, role="active-cache-manifest")
    if manifest_digest != active_digest:
        fail("cache", "active-cache-manifest-mismatch")
    cache_digest = manifest_digest
    command = [sys.executable, str(source_root / "scripts/model-cache-integrity.py"), "verify",
               "--cache-root", str(root / "cache/huggingface"), "--repo-id", MODEL_ID,
               "--revision", MODEL_REVISION, "--manifest", str(manifest)]
    run(command, timeout=600, stage="cache")
    return {"model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "runtime_image": image, "runtime_image_id": image_id,
            "cache_manifest_sha256": cache_digest}


def running_container_names() -> set[str]:
    output = run(["docker", "ps", "--format", "{{.Names}}"], stage="preflight")
    return {line.strip() for line in output.splitlines() if line.strip()}


def check_current_profile(env: dict[str, str], source_root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    if env.get("FLASH_ULTRAFAST_NAME") != "qwen38-flash-ultrafast":
        fail("preflight", "ultrafast-container-name-mismatch")
    state_dir = Path(env.get("FLASH_ULTRAFAST_STATE_DIR", ""))
    record = parse_record(state_dir / "runtime.env", {"PROFILE", "IMAGE_ID", "MODEL_ID", "MODEL_REVISION", "SERVED_NAME"})
    if record["PROFILE"] != "ultrafast" or record["IMAGE_ID"] != env.get("FLASH_ULTRAFAST_IMAGE_ID", record["IMAGE_ID"]):
        fail("preflight", "ultrafast-runtime-record-mismatch")
    if record["MODEL_ID"] != env.get("FLASH_ULTRAFAST_MODEL_ID") or record["MODEL_REVISION"] != env.get("FLASH_ULTRAFAST_MODEL_REVISION"):
        fail("preflight", "ultrafast-model-pin-mismatch")
    if record["SERVED_NAME"] != env.get("FLASH_ULTRAFAST_SERVED_MODEL_NAME"):
        fail("preflight", "ultrafast-served-name-mismatch")
    candidate = inspect_container(env["FLASH_ULTRAFAST_NAME"])
    if (not candidate or candidate["status"] != "running"
            or candidate["image_id"] != record["IMAGE_ID"] or candidate["managed"] != "flash-next"):
        fail("preflight", "ultrafast-candidate-identity-unverified")
    actual_image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", env["FLASH_ULTRAFAST_IMAGE"]],
                          timeout=15, stage="preflight")
    if actual_image_id != candidate["image_id"]:
        fail("preflight", "ultrafast-image-changed")
    profile_path = Path(env["FLASH_NEXT_STATE_DIR"]) / "active-profile"
    previous_path = Path(env["FLASH_NEXT_STATE_DIR"]) / "previous-text-containers"
    if file_fingerprint(profile_path, exact_mode=0o600, role="flash-owner-record") != sha256(b"ultrafast\n"):
        fail("preflight", "active-profile-not-ultrafast")
    previous_sha = file_fingerprint(previous_path, exact_mode=0o600, role="flash-owner-record")
    prior_names = previous_path.read_text(encoding="utf-8").splitlines()
    if not prior_names or any(name not in TEXT_CONTAINER_NAMES or name == candidate["name"] for name in prior_names):
        fail("preflight", "flash-rollback-record-invalid")
    current = running_container_names()
    if candidate["name"] not in current:
        fail("preflight", "active-flash-candidate-not-running")
    other = {name for name in current if name in TEXT_CONTAINER_NAMES and name != candidate["name"]}
    unknown = {name for name in current if re.search(r"(qwen|vllm|flash|gemma|text-primary|automation-primary|home-primary)", name, re.I)
               and name not in TEXT_CONTAINER_NAMES and not name.startswith("gb10-whisper")}
    if other or unknown:
        fail("preflight", "another-text-runtime-is-running")
    for name in ("gb10-text-edge", "gb10-text-primary"):
        if inspect_container(name) is not None:
            fail("preflight", "standard-runtime-container-already-exists")
    expected_ids = {"id": candidate["id"], "image_id": candidate["image_id"],
                    "image_ref": candidate["image_ref"], "restart": candidate["restart"],
                    "managed": candidate["managed"], "name": candidate["name"]}
    return {"candidate": expected_ids, "profile_sha256": file_fingerprint(profile_path, role="flash-owner-record"),
            "previous_sha256": previous_sha,
            "ultrafast_record_sha256": file_fingerprint(state_dir / "runtime.env", role="flash-owner-record"),
            "profile_path": str(profile_path), "previous_path": str(previous_path)}, record


def validate_whisper_preflight(env: dict[str, str], source_root: Path,
                               env_path: Path) -> dict[str, dict[str, str]]:
    # Use the same read-only host, Compose, cache and catalog checks as the
    # standard owner before any candidate is stopped.
    run(["bash", str(source_root / "scripts/setup-compute-node.sh"), "validate", "--env",
         str(env_path)], timeout=120, stage="preflight")
    run(["bash", str(source_root / "scripts/setup-compute-node.sh"), "preflight", "--env",
         str(env_path)], timeout=120, stage="preflight")
    return verify_whisper()


def write_model_request(body: dict[str, Any], alias: str) -> bytes:
    return json.dumps({"model": alias, **body}, ensure_ascii=False, separators=(",", ":")).encode()


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    write_json_atomic(path, receipt)


def wait_health(url: str, *, timeout: int = WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = http_request(url, timeout=5, max_bytes=2 * 1024 * 1024)
        if status == 200:
            return True
        time.sleep(3)
    return False


def metrics_idle(payload: bytes) -> bool:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError:
        fail("idle-check", "model-metrics-invalid")
    totals = {"vllm:num_requests_running": [], "vllm:num_requests_waiting": []}
    for line in lines:
        metric = line.split(None, 1)[0] if line.strip() else ""
        for name in totals:
            if metric == name or metric.startswith(name + "{"):
                match = re.fullmatch(r"(?:vllm:num_requests_(?:running|waiting))(?:\{[^\n]*\})?\s+([-+0-9.eE]+)", line)
                if not match:
                    fail("idle-check", "model-metrics-invalid")
                value = float(match.group(1))
                if not math.isfinite(value) or value < 0:
                    fail("idle-check", "model-metrics-invalid")
                totals[name].append(value)
                break
    if any(not values for values in totals.values()):
        fail("idle-check", "model-metrics-unavailable")
    return all(value == 0 for values in totals.values() for value in values)


def require_model_idle(url: str, *, stage: str) -> None:
    # Three consecutive metric snapshots catch requests admitted immediately
    # before the operator's maintenance window. No text or labels are logged.
    for index in range(3):
        status, payload = http_request(url, timeout=5)
        if status != 200 or len(payload) > 2 * 1024 * 1024:
            fail(stage, "model-metrics-unavailable")
        if not metrics_idle(payload):
            fail(stage, "text-model-busy")
        if index < 2:
            time.sleep(1)


def wait_model_idle(url: str, *, timeout: int = WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
        status, payload = http_request(url, timeout=5, max_bytes=2 * 1024 * 1024)
        if status != 200 or len(payload) > 2 * 1024 * 1024:
            fail("restore", "text-model-metrics-unavailable")
        if metrics_idle(payload):
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        time.sleep(5)
    fail("restore", "text-model-idle-timeout")


def compose(env_path: str, source_root: Path, *args: str, override: Path | None = None) -> list[str]:
    command = ["docker", "compose", "--env-file", env_path, "-f", str(source_root / "deploy/compute-node/compose.yaml")]
    if override:
        command.extend(["-f", str(override)])
    command.extend(args)
    return command


def memory_gate(*, postload: bool) -> dict[str, int]:
    available, total = host_memory()
    minimum = total // 10 if postload else 12 * 1024**3
    if available < minimum:
        fail("capacity", "postload-memory-headroom-low" if postload else "preload-memory-headroom-low")
    return {"available_bytes": available, "total_bytes": total}


def check_saved_profile(journal: dict[str, Any]) -> None:
    for field in ("profile_path", "previous_path"):
        path = Path(journal["owner_state"][field])
        expected = journal["owner_state"]["profile_sha256" if field == "profile_path" else "previous_sha256"]
        if file_fingerprint(path, role="flash-owner-record") != expected:
            fail("restore", "flash-owner-journal-changed")
    runtime_path = Path(journal["owner_state"]["runtime_path"])
    if file_fingerprint(runtime_path, exact_mode=0o440,
                        role="flash-owner-record") != journal["owner_state"]["runtime_sha256"]:
        fail("restore", "flash-runtime-record-changed")


def current_source_revision(source_root: Path, *, stage: str = "preflight") -> str:
    value = run(["git", "-C", str(source_root), "rev-parse", "HEAD"], timeout=15, stage=stage)
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        fail(stage, "source-revision-unverified")
    return value


def current_compose_config_hash(env_path: Path, source_root: Path, *, stage: str = "preflight") -> str:
    rendered = run(compose(str(env_path), source_root, "config", "--format", "json"),
                    timeout=60, stage=stage)
    return sha256(rendered.encode("utf-8"))


def validate_restore_preflight(journal: dict[str, Any], *, env: dict[str, str],
                               source_root: Path, env_path: Path) -> None:
    if str(env_path) != journal.get("env_path"):
        fail("restore", "compute-config-path-changed")
    if file_fingerprint(env_path, exact_mode=0o600, role="compute-config") != journal.get("env_sha256"):
        fail("restore", "compute-config-changed")
    if current_source_revision(source_root, stage="restore") != journal.get("source_revision"):
        fail("restore", "source-revision-changed")
    if current_compose_config_hash(env_path, source_root, stage="restore") != journal.get("compose_config_sha256"):
        fail("restore", "compose-config-changed")
    check_saved_profile(journal)
    check_whisper_same(journal["whisper_before"])

    original = journal["owner_state"]["candidate"]
    expected_owner_paths = {
        "profile_path": Path(env["FLASH_NEXT_STATE_DIR"]) / "active-profile",
        "previous_path": Path(env["FLASH_NEXT_STATE_DIR"]) / "previous-text-containers",
        "runtime_path": Path(env["FLASH_ULTRAFAST_STATE_DIR"]) / "runtime.env",
    }
    for key, expected_path in expected_owner_paths.items():
        if journal["owner_state"].get(key) != str(expected_path):
            fail("restore", "flash-owner-state-path-changed")
    candidate = inspect_container(original["name"])
    if (not candidate or candidate["id"] != original["id"]
            or candidate["image_id"] != original["image_id"]
            or candidate["image_ref"] != original["image_ref"]
            or candidate["managed"] != original["managed"]
            or candidate["status"] not in {"running", "exited"}):
        fail("restore", "original-flash-container-identity-changed")
    original_restart = original["restart"]
    allowed_restart_states = {json.dumps(original_restart, sort_keys=True),
                              json.dumps({"Name": "no", "MaximumRetryCount": 0}, sort_keys=True)}
    if json.dumps(candidate["restart"], sort_keys=True) not in allowed_restart_states:
        fail("restore", "original-flash-restart-policy-drift")

    standard_running = False
    for name in ("gb10-text-primary", "gb10-text-edge"):
        row = inspect_container(name)
        if row is None:
            continue
        if (row["image_ref"] != env["VLLM_IMAGE"]
                or row["image_id"] != journal["cache"]["runtime_image_id"]
                or row["status"] not in {"running", "exited"}):
            fail("restore", "temporary-model-container-identity-changed")
        standard_running = standard_running or row["status"] == "running"
    running = running_container_names()
    allowed = {original["name"], "gb10-text-primary", "gb10-text-edge"}
    managed_text = {name for name in running if name in TEXT_CONTAINER_NAMES}
    unknown_text = {name for name in running if re.search(
        r"(qwen|vllm|flash|gemma|text-primary|automation-primary|home-primary)", name, re.I)
        and name not in TEXT_CONTAINER_NAMES and not name.startswith("gb10-whisper")}
    if managed_text - allowed or unknown_text or (candidate["status"] == "running" and standard_running):
        fail("restore", "text-runtime-residency-ambiguous")


def restore(journal: dict[str, Any], *, env: dict[str, str], source_root: Path,
            journal_path: Path, env_path: Path) -> None:
    validate_restore_preflight(journal, env=env, source_root=source_root, env_path=env_path)
    journal["phase"] = "restoring-standard"
    journal["updated_at"] = utc_now()
    write_json_atomic(journal_path, journal)
    # The temporary Compose profile is pinned to the no-restart override. Stop
    # only the two exact standard-service names; refuse unexpected images.
    primary = inspect_container("gb10-text-primary")
    if primary is not None and primary["status"] == "running" and journal.get("candidate_inference_started") is True:
        wait_model_idle(f"http://{env['GB10_BIND_ADDRESS']}:{env['VLLM_HOST_PORT']}/metrics")
    for name in ("gb10-text-primary", "gb10-text-edge"):
        row = inspect_container(name)
        if row is not None and row["status"] == "running":
            if (row["image_ref"] != env["VLLM_IMAGE"]
                    or row["image_id"] != journal["cache"]["runtime_image_id"]):
                fail("restore", "temporary-model-container-identity-changed")
            run(["docker", "stop", name], timeout=180, stage="restore")
    # Remove only the ephemeral standard containers this transaction created.
    for name in ("gb10-text-primary", "gb10-text-edge"):
        row = inspect_container(name)
        if row is not None:
            if (row["image_ref"] != env["VLLM_IMAGE"]
                    or row["image_id"] != journal["cache"]["runtime_image_id"]):
                fail("restore", "temporary-model-container-identity-changed")
            run(["docker", "rm", name], timeout=60, stage="restore")

    original = journal["owner_state"]["candidate"]
    current = inspect_container(original["name"])
    if (not current or current["id"] != original["id"] or current["image_id"] != original["image_id"]
            or current["image_ref"] != original["image_ref"]):
        fail("restore", "original-flash-container-identity-changed")
    restart = original["restart"]
    restart_name = restart.get("Name")
    retry_count = restart.get("MaximumRetryCount", 0)
    if restart_name not in {"no", "always", "unless-stopped", "on-failure"}:
        fail("restore", "original-restart-policy-unsupported")
    restart_arg = restart_name if restart_name != "on-failure" or not retry_count else f"on-failure:{retry_count}"
    run(["docker", "update", "--restart", restart_arg, original["name"]], stage="restore")
    if current["status"] != "running":
        run(["docker", "start", original["name"]], timeout=120, stage="restore")
    health_url = f"http://{env['FLASH_ULTRAFAST_HOST_BIND']}:{env['FLASH_ULTRAFAST_HOST_PORT']}/health"
    if not wait_health(health_url):
        fail("restore", "original-flash-health-failed")
    verify = inspect_container(original["name"])
    if (not verify or verify["id"] != original["id"] or verify["image_id"] != original["image_id"]
            or verify["image_ref"] != original["image_ref"] or verify["managed"] != original["managed"]
            or verify["restart"] != original["restart"] or verify["status"] != "running"
            or verify["health"] != "healthy"):
        fail("restore", "original-flash-restore-unverified")
    check_saved_profile(journal)
    check_whisper_same(journal["whisper_before"])
    journal["phase"] = "restored"
    journal["updated_at"] = utc_now()
    write_json_atomic(journal_path, journal)


def load_journal(path: Path) -> dict[str, Any]:
    file_fingerprint(path, exact_mode=0o600, role="qualification-journal")
    value = safe_json(path)
    if (type(value.get("schema_version")) is not int or value.get("schema_version") != 1
            or not isinstance(value.get("owner_state"), dict)
            or value.get("phase") not in {"ready", "baseline-captured", "stopping-flash",
                "flash-stopped", "fallback-started", "fallback-request-running", "restoring-standard", "restored"}
            or not isinstance(value.get("receipt_id"), str)
            or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", value["receipt_id"])
            or not isinstance(value.get("env_path"), str) or not Path(value["env_path"]).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("env_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("compose_config_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(value.get("source_revision", "")))
            or not isinstance(value.get("whisper_before"), dict)
            or not isinstance(value.get("cache"), dict)):
        fail("recovery", "pending-journal-invalid")
    return value


def perform(args: argparse.Namespace) -> int:
    operation_started = time.monotonic()
    if os.geteuid() != 0:
        fail("preflight", "root-required")
    if os.uname().nodename.split(".", 1)[0] != "home-spark":
        fail("preflight", "wrong-host")
    if args.action == "run" and not args.maintenance_window:
        fail("preflight", "maintenance-window-required")
    if args.action == "recover" and args.export_dir:
        fail("export-preflight", "export-supported-only-for-run")
    try:
        stage_uid = int(os.environ.get("SUDO_UID", "0"))
        if stage_uid < 0:
            fail("export-preflight", "export-owner-invalid")
    except ValueError:
        fail("export-preflight", "export-owner-invalid")
    export_fd = (open_export_directory(args.export_dir, expected_uid=stage_uid)
                 if args.export_dir else None)
    try:
        deployment_lock_fd = acquire_deployment_lock()
    except BaseException:
        if export_fd is not None:
            os.close(export_fd)
        raise
    try:
        lock_fd = acquire_lock(allow_pending=args.action == "recover")
    except BaseException:
        os.close(deployment_lock_fd)
        if export_fd is not None:
            os.close(export_fd)
        raise
    source_root = ROOT
    verify_active_release(source_root)
    env_path = Path(args.env).absolute()
    try:
        env_stat = env_path.lstat()
    except OSError:
        fail("preflight", "compute-config-unavailable")
    if not stat.S_ISREG(env_stat.st_mode) or stat.S_ISLNK(env_stat.st_mode):
        fail("preflight", "compute-config-untrusted")
    env = read_env(env_path)
    state_root = STATE_ROOT
    ensure_private_dir(state_root)
    pending = state_root / "pending.json"
    if args.action == "recover":
        if not pending.exists() or pending.is_symlink():
            fail("recovery", "no-pending-qualification")
        journal = load_journal(pending)
        if str(env_path) != journal.get("env_path"):
            fail("recovery", "compute-config-path-changed")
        try:
            restore(journal, env=env, source_root=source_root,
                    journal_path=pending, env_path=env_path)
        except BaseException as error:
            journal["rollback_failure_stage"] = (error.stage if isinstance(error, QualificationError)
                                                 else journal.get("phase", "unknown"))
            journal["rollback_failure_class"] = (error.classification if isinstance(error, QualificationError)
                                                  else type(error).__name__)
            journal["updated_at"] = utc_now()
            write_json_atomic(pending, journal)
            raise
        pending.unlink()
        print(json.dumps({"status": "restored", "receipt_id": journal["receipt_id"]}, separators=(",", ":")))
        os.close(lock_fd)
        os.close(deployment_lock_fd)
        return 0

    if pending.exists() or pending.is_symlink():
        fail("preflight", "pending-qualification-requires-recovery")
    input_bytes = verify_regular_private(Path(args.manifest), allowed_uids={0, stage_uid},
                                         exact_mode=0o600, max_bytes=MAX_MANIFEST_BYTES)
    body, request_hash = validate_request(input_bytes)
    journal_path = pending
    cache = verify_model_cache(env, source_root)
    # Both source owners validate their pinned configuration before any stop.
    run(["bash", str(source_root / "scripts/setup-compute-flash-next.sh"), "validate",
         "--profile", "ultrafast", "--env", str(env_path)], timeout=180, stage="preflight")
    run(["bash", str(source_root / "scripts/setup-compute-node.sh"), "validate", "--env",
         str(env_path)], timeout=120, stage="preflight")
    compose_config_sha256 = current_compose_config_hash(env_path, source_root)
    source_revision = current_source_revision(source_root)
    env_sha256 = file_fingerprint(env_path, exact_mode=0o600, role="compute-config")
    names = {
        "profile_path": str(Path(env["FLASH_NEXT_STATE_DIR"]) / "active-profile"),
        "previous_path": str(Path(env["FLASH_NEXT_STATE_DIR"]) / "previous-text-containers"),
        "runtime_path": str(Path(env["FLASH_ULTRAFAST_STATE_DIR"]) / "runtime.env"),
    }
    owner, _record = check_current_profile(env, source_root)
    owner.update({"runtime_path": names["runtime_path"],
                  "runtime_sha256": file_fingerprint(Path(names["runtime_path"]), role="flash-owner-record")})
    whisper = validate_whisper_preflight(env, source_root, env_path)
    capacity_before = memory_gate(postload=False)
    key_bytes = read_api_key(Path(env["VLLM_API_KEY_FILE"]))
    candidate_health_url = f"http://{env['FLASH_ULTRAFAST_HOST_BIND']}:{env['FLASH_ULTRAFAST_HOST_PORT']}/health"
    if not wait_health(candidate_health_url, timeout=10):
        fail("preflight", "ultrafast-health-failed")
    require_model_idle(candidate_health_url.rsplit("/health", 1)[0] + "/metrics", stage="preflight")
    if run(["docker", "image", "inspect", "--format", "{{.Id}}", env["VLLM_IMAGE"]],
           timeout=15, stage="cache") != cache["runtime_image_id"]:
        fail("cache", "standard-runtime-image-changed")

    # Only persist the private prompt after every no-download/cache/owner/
    # capacity preflight has passed. It is staged before the first inference.
    receipt_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence_dir = state_root / receipt_id
    ensure_private_dir(evidence_dir)
    staged_request = evidence_dir / "request.json"
    write_private(staged_request, input_bytes)
    run_id = {
        "schema_version": 1, "receipt_id": receipt_id, "phase": "preflight",
        "started_at": utc_now(), "request_sha256": request_hash,
    }
    override = evidence_dir / "compose-qualification-override.yaml"
    write_private(override, b"services:\n  text-edge:\n    restart: \"no\"\n  text-primary:\n    restart: \"no\"\n")
    journal = {
        **run_id, "phase": "ready", "updated_at": utc_now(),
        "owner_state": owner, "whisper_before": whisper, "cache": cache,
        "capacity_before": capacity_before,
        "evidence_id": receipt_id,
        "env_path": str(env_path), "env_sha256": env_sha256,
        "compose_config_sha256": compose_config_sha256,
        "source_revision": source_revision,
    }
    write_json_atomic(journal_path, journal)
    receipt_path = evidence_dir / "receipt.json"
    receipt: dict[str, Any] = {
        "schema_version": 1, "receipt_id": receipt_id, "outcome": "running",
        "started_at": run_id["started_at"], "request_sha256": request_hash,
        "source_revision": journal["source_revision"], "flash_candidate": owner["candidate"],
        "qwen27b": cache, "whisper_before": whisper,
        "preflight_elapsed_seconds": round(time.monotonic() - operation_started, 3),
    }
    write_receipt(receipt_path, receipt)
    interrupted = {"signal": None}

    def interrupt(signum: int, _frame: Any) -> None:
        interrupted["signal"] = signum
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, interrupt)
                    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    failure: BaseException | None = None
    try:
        # Snapshot current output before swapping; both calls use the same
        # validated request object and differ only in served model alias.
        flash_body = write_model_request(body, env["FLASH_ULTRAFAST_SERVED_MODEL_NAME"])
        flash_url = f"http://{env['FLASH_ULTRAFAST_HOST_BIND']}:{env['FLASH_ULTRAFAST_HOST_PORT']}/v1/chat/completions"
        started = time.monotonic()
        status, payload = http_request(flash_url, flash_body, timeout=REQUEST_TIMEOUT_SECONDS)
        write_private(evidence_dir / "flash.response", payload)
        receipt["flash"] = {"http_status": status, "elapsed_seconds": round(time.monotonic() - started, 3),
                            "response_sha256": sha256(payload), "response_bytes": len(payload)}
        if status != 200 or len(payload) > MAX_RESPONSE_BYTES:
            fail("flash-request", "baseline-request-failed-or-oversized")
        receipt["flash"]["stream_validation"] = validate_sse_response(payload)
        require_model_idle(f"http://{env['FLASH_ULTRAFAST_HOST_BIND']}:{env['FLASH_ULTRAFAST_HOST_PORT']}/metrics",
                           stage="candidate-stop")
        journal["phase"] = "baseline-captured"
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        # Prevent automatic restart of the production candidate during the
        # exclusive temporary switch. Its exact policy is restored below.
        run(["docker", "update", "--restart", "no", owner["candidate"]["name"]], stage="candidate-stop")
        journal["phase"] = "stopping-flash"
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        run(["docker", "stop", owner["candidate"]["name"]], timeout=180, stage="candidate-stop")
        stopped_candidate = inspect_container(owner["candidate"]["name"])
        if not stopped_candidate or stopped_candidate["status"] != "exited":
            fail("candidate-stop", "flash-candidate-stop-unverified")
        journal["phase"] = "flash-stopped"
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        fallback_start = time.monotonic()
        run(compose(str(env_path), source_root, "up", "--pull", "never", "--no-build", "-d",
                     "text-edge", "text-primary", override=override), timeout=120, stage="fallback-start")
        journal["phase"] = "fallback-started"
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        dense_url = f"http://{env['GB10_BIND_ADDRESS']}:{env['VLLM_HOST_PORT']}"
        if not wait_health(dense_url + "/health"):
            fail("fallback-start", "fallback-health-timeout")
        dense_container = inspect_container("gb10-text-primary")
        if (not dense_container or dense_container["image_ref"] != env["VLLM_IMAGE"]
                or dense_container["status"] != "running"):
            fail("fallback-start", "fallback-container-identity-unverified")
        receipt["fallback_ready_elapsed_seconds"] = round(time.monotonic() - fallback_start, 3)
        capacity_after = memory_gate(postload=True)
        receipt["capacity_after"] = capacity_after
        check_whisper_same(whisper)
        dense_body = write_model_request(body, "general-spark-qwen38")
        started = time.monotonic()
        journal["candidate_inference_started"] = True
        journal["phase"] = "fallback-request-running"
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        status, payload = http_request(dense_url + "/v1/chat/completions", dense_body,
                                       key=key_bytes, timeout=REQUEST_TIMEOUT_SECONDS)
        write_private(evidence_dir / "qwen27b.response", payload)
        receipt["qwen27b_result"] = {"http_status": status,
                                     "elapsed_seconds": round(time.monotonic() - started, 3),
                                     "response_sha256": sha256(payload), "response_bytes": len(payload)}
        if status != 200 or len(payload) > MAX_RESPONSE_BYTES:
            fail("fallback-request", "fallback-request-failed-or-oversized")
        receipt["qwen27b_result"]["stream_validation"] = validate_sse_response(payload)
        receipt["outcome"] = "qualified-output-captured"
        receipt["request_hashes_match_except_model_alias"] = True
    except BaseException as error:
        failure = error
        receipt["outcome"] = "failed"
        receipt["failure_stage"] = error.stage if isinstance(error, QualificationError) else journal.get("phase", "unknown")
        receipt["failure_class"] = (error.classification if isinstance(error, QualificationError)
                                     else type(error).__name__)
        journal["initial_failure_stage"] = receipt["failure_stage"]
        journal["initial_failure_class"] = receipt["failure_class"]
        journal["updated_at"] = utc_now()
        write_json_atomic(journal_path, journal)
        if interrupted["signal"] is not None:
            receipt["interrupted_by_signal"] = interrupted["signal"]
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        try:
            restore_started = time.monotonic()
            restore(journal, env=env, source_root=source_root,
                    journal_path=journal_path, env_path=env_path)
            receipt["restored"] = True
            receipt["restore_elapsed_seconds"] = round(time.monotonic() - restore_started, 3)
            journal_path.unlink()
        except BaseException as rollback_error:
            if "restore_started" in locals():
                receipt["restore_elapsed_seconds"] = round(time.monotonic() - restore_started, 3)
            receipt["restored"] = False
            receipt["rollback_failure_stage"] = (rollback_error.stage if isinstance(rollback_error, QualificationError)
                                                  else journal.get("phase", "unknown"))
            receipt["rollback_failure_class"] = (rollback_error.classification if isinstance(rollback_error, QualificationError)
                                                  else type(rollback_error).__name__)
            journal["rollback_failure_stage"] = receipt["rollback_failure_stage"]
            journal["rollback_failure_class"] = receipt["rollback_failure_class"]
            journal["updated_at"] = utc_now()
            write_json_atomic(journal_path, journal)
            if failure is None:
                failure = rollback_error
        receipt["finished_at"] = utc_now()
        receipt["total_elapsed_seconds"] = round(time.monotonic() - operation_started, 3)
        write_receipt(receipt_path, receipt)
        if export_fd is not None:
            if not export_private_outputs(export_fd, evidence_dir, receipt_path, receipt, owner_uid=stage_uid):
                receipt["export_status"] = "failed"
                if failure is None:
                    failure = QualificationError("export", "evidence-export-failed")
            os.close(export_fd)
        os.close(lock_fd)
        os.close(deployment_lock_fd)

    print(json.dumps({"receipt_id": receipt_id,
                      "status": "restored" if receipt.get("restored") else "rollback-pending",
                      "receipt_path": str(receipt_path),
                      "export_status": receipt.get("export_status", "not-requested")}, separators=(",", ":")))
    if failure:
        if isinstance(failure, QualificationError):
            fail(failure.stage, failure.classification)
        raise failure
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("action", choices=("run", "recover"))
    parser.add_argument("--env", default="/etc/gb10-ai/gb10.env")
    parser.add_argument("--manifest")
    parser.add_argument("--maintenance-window", action="store_true")
    parser.add_argument("--export-dir")
    args = parser.parse_args()
    try:
        if args.action == "run" and not args.manifest:
            fail("manifest", "manifest-required")
        return perform(args)
    except QualificationError as error:
        print(json.dumps({"status": "failed", "stage": error.stage,
                          "classification": error.classification}, separators=(",", ":")), file=sys.stderr)
        return 1
    except BaseException as error:
        print(json.dumps({"status": "failed", "stage": "unknown",
                          "classification": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
