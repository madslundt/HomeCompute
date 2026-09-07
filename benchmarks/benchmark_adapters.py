"""Benchmark provider and Codex execution adapters."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ALLOWED_ORIGINS_ENV = "HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _validated_endpoint(url: str, candidate: dict[str, Any]) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise BenchmarkError(f"candidate {candidate['id']} has an invalid endpoint") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise BenchmarkError(f"candidate {candidate['id']} endpoint must be an HTTP(S) URL without credentials or fragments")
    host = parsed.hostname.lower()
    origin = f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")
    if candidate["adapter"] == "openrouter":
        if origin != "https://openrouter.ai":
            raise BenchmarkError("OpenRouter candidates must use https://openrouter.ai")
        return url
    configured = {
        value.strip().rstrip("/")
        for value in os.environ.get(ALLOWED_ORIGINS_ENV, "").split(",")
        if value.strip()
    }
    if origin not in configured:
        raise BenchmarkError(
            f"candidate {candidate['id']} endpoint origin {origin} is not approved; "
            f"add it to {ALLOWED_ORIGINS_ENV} outside the benchmark plan"
        )
    return url


def _open_no_redirect(request: urllib.request.Request, timeout: int):
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)

if __package__:
    from .benchmark_core import (
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _reject_unsafe_tree,
        _safe_path,
        _safe_reference,
        read_json,
    )
else:
    from benchmark_core import (
        BenchmarkError,
        LoadedPlan,
        _absolute,
        _reject_unsafe_tree,
        _safe_path,
        _safe_reference,
        read_json,
    )

def api_key_for(candidate: dict[str, Any]) -> str | None:
    env_name = candidate.get("api_key_env")
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise BenchmarkError(f"candidate {candidate['id']} requires environment variable {env_name}")
    return value


def post_chat(candidate: dict[str, Any], messages: list[dict[str, str]], response_format: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = candidate.get("base_url", "https://openrouter.ai/api/v1")
    _validated_endpoint(base_url, candidate)
    body = {
        "model": candidate["model"],
        "messages": messages,
        **candidate.get("request", {}),
    }
    if response_format is not None:
        body["response_format"] = response_format
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = api_key_for(candidate)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if candidate["adapter"] == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/HomeCompute"
        headers["X-Title"] = "HomeCompute benchmark"
        headers["X-OpenRouter-Cache"] = "false"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with _open_no_redirect(request, timeout=candidate.get("timeout_seconds", 300)) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000]
        raise BenchmarkError(f"HTTP {exc.code} from {candidate['id']}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BenchmarkError(f"request failed for {candidate['id']}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid JSON response from {candidate['id']}: {exc}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BenchmarkError(f"unexpected response shape from {candidate['id']}") from exc
    return {
        "text": text or "",
        "duration_ms": elapsed_ms,
        "generation_id": payload.get("id"),
        "resolved_model": payload.get("model"),
        "resolved_provider": payload.get("provider"),
        "usage": payload.get("usage", {}),
    }


def post_n8n(candidate: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    webhook_url = candidate.get("webhook_url")
    if not webhook_url:
        env_name = candidate["webhook_url_env"]
        webhook_url = os.environ.get(env_name)
        if not webhook_url:
            raise BenchmarkError(f"candidate {candidate['id']} requires environment variable {env_name}")
    _validated_endpoint(webhook_url, candidate)
    body = {
        **candidate.get("webhook_body", {}),
        "safety_mode": candidate["safety_acknowledgement"],
        "case_id": case["id"],
        "track": case["track"],
        "model": candidate["model"],
        "messages": case["messages"],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth_env = candidate.get("webhook_auth_env")
    if auth_env:
        token = os.environ.get(auth_env)
        if not token:
            raise BenchmarkError(f"candidate {candidate['id']} requires environment variable {auth_env}")
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with _open_no_redirect(request, timeout=candidate.get("timeout_seconds", 300)) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000]
        raise BenchmarkError(f"HTTP {exc.code} from n8n for {candidate['id']}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BenchmarkError(f"n8n request failed for {candidate['id']}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid JSON response from n8n for {candidate['id']}: {exc}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise BenchmarkError(f"unexpected n8n response shape for {candidate['id']}")
    return {
        "text": payload["text"],
        "duration_ms": elapsed_ms,
        "generation_id": payload.get("generation_id"),
        "resolved_model": payload.get("model", candidate["model"]),
        "resolved_provider": payload.get("provider", "n8n/OpenRouter"),
        "usage": payload.get("usage", {}),
        "workflow_execution_id": payload.get("execution_id"),
        "workflow": payload.get("workflow"),
        "workflow_trace": payload.get("tool_trace", []),
        "notifications_sent": payload.get("notifications_sent"),
        "notion_writes": payload.get("notion_writes"),
    }


def run_process(
    arguments: list[str],
    cwd: Path,
    timeout: int,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError(f"command timed out after {timeout}s: {arguments[0]}") from exc
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise BenchmarkError(f"cannot execute command {arguments[0]}: {exc}") from exc


def _minimal_command_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("LANG", "LC_ALL", "PATH")
        if os.environ.get(key)
    }
    path_entries = [
        entry
        for entry in environment.get("PATH", os.defpath).split(os.pathsep)
        if entry and Path(entry).is_absolute()
    ]
    environment["PATH"] = os.pathsep.join(path_entries) or os.defpath
    return environment


def _sandbox_state(workspace: Path, network: str) -> dict[str, Any]:
    resolved_workspace = workspace.resolve(strict=True)
    return {
        "permissionProfile": {
            "type": "managed",
            "file_system": {
                "type": "restricted",
                "entries": [
                    {
                        "path": {
                            "type": "special",
                            "value": {"kind": "minimal"},
                        },
                        "access": "read",
                    },
                    {
                        "path": {
                            "type": "path",
                            "path": str(resolved_workspace),
                        },
                        "access": "write",
                    },
                ],
            },
            "network": network,
        },
        "codexLinuxSandboxExe": None,
        "sandboxCwd": resolved_workspace.as_uri(),
    }


def _require_codex_sandbox(environment: dict[str, str]) -> None:
    if shutil.which("codex", path=environment["PATH"]) is None:
        raise BenchmarkError("Codex sandbox is unavailable; benchmark execution fails closed")


def _run_managed_sandbox(
    arguments: list[str],
    workspace: Path,
    timeout: int,
    environment: dict[str, str],
    *,
    network: str,
) -> subprocess.CompletedProcess[str]:
    workspace = _absolute(workspace)
    _safe_path(
        workspace,
        workspace,
        "disposable coding workspace",
        directory=True,
        recursive=True,
    )
    _require_codex_sandbox(environment)
    completed = run_process(
        [
            "codex",
            "sandbox",
            "--sandbox-state-json",
            json.dumps(_sandbox_state(workspace, network), separators=(",", ":")),
            *arguments,
        ],
        workspace.resolve(strict=True),
        timeout,
        environment=environment,
    )
    _safe_path(
        workspace,
        workspace,
        "disposable coding workspace",
        directory=True,
        recursive=True,
    )
    return completed


def run_sandboxed_command(
    arguments: list[str],
    workspace: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return _run_managed_sandbox(
        arguments,
        workspace,
        timeout,
        _minimal_command_environment(),
        network="restricted",
    )


def invoke_codex(
    candidate: dict[str, Any],
    case: dict[str, Any],
    benchmark_root: Path,
) -> dict[str, Any]:
    workspace_config = case.get("workspace")
    if not isinstance(workspace_config, dict) or "source" not in workspace_config:
        raise BenchmarkError(f"coding case {case['id']} needs workspace.source")
    source = _safe_reference(
        Path(case["_source"]).parent,
        workspace_config["source"],
        benchmark_root,
        f"case {case['id']} workspace source",
        directory=True,
        recursive=True,
    )
    if candidate["api_key_env"] in {
        "CODEX_HOME", "HOME", "LANG", "LC_ALL", "PATH", "TMPDIR",
    }:
        raise BenchmarkError(
            f"Codex candidate {candidate['id']} api_key_env conflicts with isolated runtime"
        )
    api_key = api_key_for(candidate)
    if api_key is None:
        raise BenchmarkError(f"Codex candidate {candidate['id']} has no API key")
    _validated_endpoint(candidate["base_url"], candidate)
    temporary_root = Path(tempfile.mkdtemp(prefix="homecompute-code-benchmark-"))
    workspace = temporary_root / "workspace"
    shutil.copytree(source, workspace)
    workspace.chmod(0o700)
    _reject_unsafe_tree(workspace, "disposable coding workspace")
    if not (workspace / ".git").exists():
        for git_arguments in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.name", "HomeCompute benchmark"],
            ["git", "config", "user.email", "benchmark@example.invalid"],
            ["git", "add", "--all"],
            ["git", "commit", "--quiet", "-m", "benchmark baseline"],
        ):
            initialized = run_process(git_arguments, workspace, 60)
            if initialized.returncode != 0:
                shutil.rmtree(temporary_root)
                raise BenchmarkError(f"failed to initialize disposable coding workspace: {initialized.stderr}")
    runtime_dir = workspace / ".homecompute-runtime"
    runtime_dir.mkdir(mode=0o700)
    home_dir = runtime_dir / "home"
    temporary_dir = runtime_dir / "tmp"
    codex_home = runtime_dir / "codex-home"
    for private_directory in (home_dir, temporary_dir, codex_home):
        private_directory.mkdir(mode=0o700)
    final_message = runtime_dir / "final-message.txt"
    provider_id = f"benchmark_{re.sub(r'[^a-zA-Z0-9_]', '_', candidate['id'])}"
    prompt = "\n\n".join(
        f"{message['role'].upper()}: {message['content']}" for message in case["messages"]
    )
    arguments = [
        candidate.get("codex_binary", "codex"),
        "exec",
        "--ignore-user-config",
        "--strict-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "--output-last-message",
        str(final_message),
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "--model",
        candidate["model"],
        "--config",
        f'model_provider="{provider_id}"',
        "--config",
        f'model_providers.{provider_id}.name="HomeCompute benchmark candidate"',
        "--config",
        f'model_providers.{provider_id}.base_url="{candidate["base_url"]}"',
        "--config",
        f'model_providers.{provider_id}.env_key="{candidate["api_key_env"]}"',
        "--config",
        f'model_providers.{provider_id}.wire_api="responses"',
        "--config",
        f"model_providers.{provider_id}.requires_openai_auth=false",
        prompt,
    ]
    for key, value in candidate.get("codex_config", {}).items():
        arguments[2:2] = ["--config", f"{key}={json.dumps(value)}"]
    started = time.perf_counter()
    try:
        candidate_environment = _minimal_command_environment()
        candidate_environment.update({
            candidate["api_key_env"]: api_key,
            "HOME": str(home_dir),
            "TMPDIR": str(temporary_dir),
            "CODEX_HOME": str(codex_home),
        })
        completed = _run_managed_sandbox(
            arguments,
            workspace,
            int(candidate.get("timeout_seconds", 1800)),
            candidate_environment,
            network="enabled",
        )
    except BenchmarkError:
        shutil.rmtree(temporary_root)
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if completed.returncode != 0:
        detail = completed.stderr[-4000:] or completed.stdout[-4000:]
        shutil.rmtree(temporary_root)
        raise BenchmarkError(f"Codex exited {completed.returncode} for {candidate['id']}: {detail}")
    try:
        runtime_dir = _safe_path(
            runtime_dir,
            workspace,
            "private Codex runtime",
            directory=True,
            recursive=True,
        )
        if final_message.exists():
            final_message = _safe_path(
                final_message,
                workspace,
                "Codex final message",
            )
            text = final_message.read_text(encoding="utf-8")
        else:
            text = ""
        shutil.rmtree(runtime_dir)
        _safe_path(
            workspace,
            workspace,
            "candidate-modified coding workspace",
            directory=True,
            recursive=True,
        )
        run_sandboxed_command(["git", "add", "--intent-to-add", "--all"], workspace, 60)
        patch_result = run_sandboxed_command(
            ["git", "diff", "--binary", "--no-ext-diff"],
            workspace,
            60,
        )
    except BenchmarkError:
        shutil.rmtree(temporary_root)
        raise
    except (OSError, UnicodeError) as exc:
        shutil.rmtree(temporary_root)
        raise BenchmarkError(f"cannot retain Codex workspace evidence: {exc}") from exc
    events = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    usage = next((event["usage"] for event in reversed(events) if isinstance(event.get("usage"), dict)), {})
    generation_id = next(
        (event.get("thread_id") for event in events if event.get("type") == "thread.started"),
        None,
    )
    return {
        "text": text,
        "duration_ms": elapsed_ms,
        "generation_id": generation_id,
        "resolved_model": candidate["model"],
        "resolved_provider": candidate["base_url"],
        "usage": usage,
        "agent_events_jsonl": completed.stdout,
        "agent_stderr": completed.stderr,
        "patch": patch_result.stdout if patch_result.returncode == 0 else "",
        "_workspace": str(workspace),
        "_temporary_root": str(temporary_root),
    }


def invoke(candidate: dict[str, Any], case: dict[str, Any], plan: LoadedPlan) -> dict[str, Any]:
    if candidate["adapter"] == "mock":
        responses_path = _safe_reference(
            plan.path.parent,
            candidate["responses_file"],
            plan.root,
            f"mock candidate {candidate['id']} response fixture",
        )
        responses = read_json(responses_path)
        if case["id"] not in responses:
            raise BenchmarkError(f"mock response missing for case {case['id']}")
        return {
            "text": responses[case["id"]],
            "duration_ms": 0.0,
            "generation_id": f"mock-{uuid.uuid4()}",
            "resolved_model": candidate["model"],
            "resolved_provider": "mock",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
        }
    if candidate["adapter"] == "n8n_webhook":
        return post_n8n(candidate, case)
    if candidate["adapter"] == "codex_exec":
        return invoke_codex(candidate, case, plan.root)
    return post_chat(candidate, case["messages"])
