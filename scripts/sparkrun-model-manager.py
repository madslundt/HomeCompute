#!/usr/bin/env python3
"""Narrow JSON control interface for catalog-backed sparkrun workloads.

The program intentionally accepts no command-line arguments. One request is
read from stdin and every model, host, image, path, and sparkrun option is
resolved from the checked-in HomeCompute catalog and fixed host policy below.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/model-catalog.json"
CLUSTER = "home-spark"
GB10_ROOT = Path("/srv/gb10-ai")
ENV_FILE = Path("/etc/gb10-ai/gb10.env")
HOME_ENV_FILE = Path("/etc/gb10-ai/home-assistant-model.env")
API_KEY_FILE = Path("/etc/gb10-ai/secrets/vllm_api_key")
MANAGER_ROOT = Path("/etc/gb10-ai/sparkrun")
RECIPES_DIR = MANAGER_ROOT / "recipes"
LOCK_FILE = Path("/var/lock/homecompute-sparkrun-model-manager.lock")
WAIT_SECONDS = 1200
SPARKRUN_CANDIDATES = (Path("/usr/local/bin/sparkrun"), Path("/root/.local/bin/sparkrun"))
CACHE_HELPER = ROOT / "scripts/model-cache-integrity.py"

# Only deployment IDs defined in config/model-catalog.json can cross this
# interface. These existing prepare commands implement the repository's
# authenticated or bounded artifact acquisition and accepted-cache manifests.
PREPARE_COMMANDS: dict[str, tuple[str, ...]] = {
    "automation-spark-primary": (
        "scripts/setup-compute-automation-moe.sh", "prepare", "--env", str(ENV_FILE)
    ),
    "home-spark-primary": (
        "scripts/setup-compute-home-assistant-model.sh", "prepare", "--env", str(HOME_ENV_FILE)
    ),
}
MANIFESTS = {
    "automation-spark-primary": GB10_ROOT / "manifests/accepted-automation-cache.json",
    "home-spark-primary": GB10_ROOT / "manifests/accepted-home-model-cache.json",
    "general-spark-qwen38": GB10_ROOT / "manifests/accepted-model-cache.json",
}


class RequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def deployment(catalog: dict[str, Any], deployment_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(deployment_id, str) or deployment_id not in catalog.get("deployments", {}):
        raise RequestError("unknown_deployment", "deployment must be an ID in the HomeCompute catalog")
    item = catalog["deployments"][deployment_id]
    artifact = catalog["artifacts"][item["artifact"]]
    runtime = catalog["runtime_profiles"][item["runtime_profile"]]
    return item, artifact, runtime


def eligible(item: dict[str, Any], artifact: dict[str, Any], runtime: dict[str, Any]) -> tuple[bool, str | None]:
    if runtime.get("family") != "vllm":
        return False, "only catalog vLLM profiles are supported"
    if item.get("host_role") != "compute-gb10" or item.get("network_scope") != "private_local":
        return False, "deployment is outside the private GB10 target"
    if item.get("availability") != "active" or item.get("lifecycle") == "disabled":
        return False, "deployment is disabled or not active in the catalog"
    if not any(state == "qualified" for state in artifact.get("qualification", {}).values()):
        return False, "artifact has not passed a qualification gate"
    if item.get("id") == "general-spark-qwen38":
        return False, "general Qwen3.8 remains intentionally stopped"
    return True, None


def deployment_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for deployment_id in sorted(catalog.get("deployments", {})):
        item, artifact, runtime = deployment(catalog, deployment_id)
        allowed, reason = eligible(item | {"id": deployment_id}, artifact, runtime)
        rows.append({
            "id": deployment_id,
            "name": item.get("served_model_name", deployment_id),
            "model": artifact.get("upstream_model_id"),
            "revision": artifact.get("revision"),
            "runtime": runtime.get("family"),
            "availability": item.get("availability"),
            "lifecycle": item.get("lifecycle"),
            "eligible": allowed,
            "reason": reason,
        })
    return rows


def run_checked(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, check=False, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as error:
        raise RequestError("dependency_missing", f"required executable is missing: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise RequestError("operation_timeout", f"command timed out: {argv[0]}") from error


def sparkrun_binary() -> str:
    for candidate in SPARKRUN_CANDIDATES:
        if candidate.is_file() and not candidate.is_symlink():
            metadata = candidate.stat()
            if metadata.st_uid == 0 and metadata.st_mode & 0o022 == 0 and os.access(candidate, os.X_OK):
                return str(candidate)
    raise RequestError("dependency_missing", "sparkrun must be installed in a fixed root-owned executable path")


def require_root() -> None:
    if os.geteuid() != 0:
        raise RequestError("privilege_required", "invoke this fixed helper through the approved root-owned SSH/sudo boundary")
    if os.uname().nodename.split(".", 1)[0] != "home-spark":
        raise RequestError("wrong_host", "sparkrun model management runs only on home-spark")


def assert_eligible(catalog: dict[str, Any], deployment_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    item, artifact, runtime = deployment(catalog, deployment_id)
    allowed, reason = eligible(item | {"id": deployment_id}, artifact, runtime)
    if not allowed:
        raise RequestError("deployment_not_eligible", reason or "deployment is not eligible")
    return item, artifact, runtime


def recipe_name(deployment_id: str) -> str:
    # This is also the sparkrun recipe filename stem. It is never supplied by
    # a caller, and its alphabet is intentionally narrower than recipe syntax.
    if not re.fullmatch(r"[a-z0-9-]+", deployment_id):
        raise RequestError("invalid_catalog", "catalog deployment ID is malformed")
    return f"homecompute-{deployment_id}"


def model_snapshot(artifact: dict[str, Any]) -> Path:
    repository = artifact["upstream_model_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RequestError("invalid_catalog", "catalog model ID has an unsupported shape")
    revision = artifact["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RequestError("invalid_catalog", "model revision must be a full immutable commit")
    return GB10_ROOT / "cache/huggingface/hub" / f"models--{repository.replace('/', '--')}" / "snapshots" / revision


def verify_accepted_cache(deployment_id: str, artifact: dict[str, Any]) -> None:
    manifest = MANIFESTS[deployment_id]
    if manifest.is_symlink() or not manifest.is_file():
        raise RequestError("cache_not_prepared", "accepted HomeCompute cache manifest is missing")
    if manifest.stat().st_uid != 0 or manifest.stat().st_mode & 0o022:
        raise RequestError("cache_not_prepared", "accepted cache manifest is not root-owned and protected")
    argv = [sys.executable, str(CACHE_HELPER), "verify", "--cache-root", str(GB10_ROOT / "cache/huggingface"),
            "--repo-id", artifact["upstream_model_id"]]
    for revision_key in ("revision", "tokenizer_revision", "code_revision"):
        revision = artifact.get(revision_key)
        if revision:
            argv.extend(["--revision", revision])
    argv.extend(["--manifest", str(manifest)])
    result = run_checked(argv, timeout=300)
    if result.returncode:
        raise RequestError("cache_verification_failed", "HomeCompute cache integrity verification failed")
    snapshot = model_snapshot(artifact)
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise RequestError("cache_not_prepared", "the catalog-pinned model snapshot is missing")


def generated_recipe(deployment_id: str, item: dict[str, Any], artifact: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    if runtime.get("family") != "vllm" or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", runtime.get("image", "")):
        raise RequestError("invalid_catalog", "runtime image must be pinned by digest")
    port = item.get("compute_host_port", item.get("runtime_config", {}).get("host_port"))
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RequestError("invalid_catalog", "catalog deployment has no valid fixed host port")
    rc = item.get("runtime_config", {})
    alias = item.get("served_model_name")
    if not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", alias):
        raise RequestError("invalid_catalog", "served model name is malformed")
    model_path = model_snapshot(artifact)
    # Production reaches vLLM through home-core's SSH loopback tunnel. Keep
    # the listener on loopback until a direct-link route is separately qualified.
    args = ["vllm", "serve", str(model_path), "--served-model-name", alias, "--host", "127.0.0.1",
            "--port", str(port), "--tensor-parallel-size", "1", "--trust-remote-code", "--generation-config", "vllm"]
    args.extend(["--max-model-len", str(rc["max_model_len"]), "--max-num-seqs", str(rc["max_num_seqs"]),
                 "--max-num-batched-tokens", str(rc["max_batched_tokens"]),
                 "--gpu-memory-utilization", str(rc["gpu_memory_utilization"])])
    args.extend(["--kv-cache-dtype", "fp8", "--enable-chunked-prefill", "--async-scheduling",
                 "--enable-prefix-caching", "--load-format", "fastsafetensors"])
    if rc.get("attention_backend"):
        args.extend(["--attention-backend", str(rc["attention_backend"])])
    if rc.get("moe_backend"):
        args.extend(["--moe-backend", str(rc["moe_backend"])])
    reasoning_parser = rc.get("reasoning_parser") or runtime.get("reasoning_parser")
    if reasoning_parser:
        args.extend(["--reasoning-parser", str(reasoning_parser)])
    elif deployment_id == "home-spark-primary":
        args.extend(["--reasoning-parser", "gemma4"])
    tool_parser = rc.get("tool_call_parser") or runtime.get("tool_call_parser")
    if tool_parser:
        tool_parser = "gemma4" if deployment_id == "home-spark-primary" else str(tool_parser)
        args.extend(["--tool-call-parser", tool_parser])
        args.append("--enable-auto-tool-choice")
    if deployment_id == "home-spark-primary":
        args.append("--language-model-only")
    if deployment_id == "automation-spark-primary":
        args.extend(["--allowed-media-domains", "invalid.homecompute.invalid"])
    if rc.get("default_chat_template_kwargs"):
        args.extend(["--default-chat-template-kwargs", json.dumps(rc["default_chat_template_kwargs"], separators=(",", ":"))])
    args.extend(["--no-enable-log-requests", "--disable-uvicorn-access-log"])

    cache_root = GB10_ROOT / "cache/huggingface"
    vllm_cache = GB10_ROOT / "cache/vllm"
    manifest = MANIFESTS[deployment_id]
    runtime_helper = GB10_ROOT / "runtime/model-cache-integrity.py"
    secret = API_KEY_FILE
    mounts = [f"{cache_root}:{cache_root}:ro", f"{vllm_cache}:{vllm_cache}",
              f"{manifest}:/run/homecompute-accepted-cache.json:ro",
              f"{runtime_helper}:/opt/homecompute/model-cache-integrity.py:ro",
              f"{secret}:/run/secrets/vllm_api_key:ro"]
    verify = ["python3", "/opt/homecompute/model-cache-integrity.py", "verify", "--cache-root", str(cache_root),
              "--repo-id", artifact["upstream_model_id"]]
    for revision_key in ("revision", "tokenizer_revision", "code_revision"):
        if artifact.get(revision_key):
            verify.extend(["--revision", artifact[revision_key]])
    verify.extend(["--manifest", "/run/homecompute-accepted-cache.json"])
    command = "/bin/sh -ec " + shlex.quote(
        shlex.join(verify) + "\nexport VLLM_API_KEY=\"$(cat /run/secrets/vllm_api_key)\"\nexec " + shlex.join(args)
    )
    environment = {
        "HOME": str(vllm_cache),
        "HF_HOME": str(cache_root),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "VLLM_NO_USAGE_STATS": "1",
        "DO_NOT_TRACK": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "VLLM_CACHE_ROOT": str(vllm_cache),
        "VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS": json.dumps(rc.get("default_chat_template_kwargs", {}), separators=(",", ":")),
        "VLLM_LOGGING_LEVEL": "INFO",
    }
    if deployment_id == "automation-spark-primary":
        environment["CUTE_DSL_ARCH"] = "sm_121a"
        environment["VLLM_FP8_MOE_BACKEND"] = str(rc.get("fp8_moe_backend", "triton"))
    try:
        runtime_account = pwd.getpwnam("gb10-ai")
    except KeyError as error:
        raise RequestError("runtime_account_missing", "the existing gb10-ai runtime account is required") from error
    return {
        "recipe_version": "2",
        "model": str(model_path),
        "runtime": "vllm",
        "container": runtime["image"],
        "min_nodes": 1,
        "max_nodes": 1,
        "defaults": {"port": port, "host": "127.0.0.1", "tensor_parallel": 1,
                     "served_model_name": alias, "max_model_len": rc["max_model_len"],
                     "gpu_memory_utilization": rc["gpu_memory_utilization"]},
        "env": environment,
        "command": command,
        "executor": "docker",
        "executor_config": {
            "auto_remove": False,
            "restart_policy": "unless-stopped",
            "privileged": False,
            "network": "host",
            "security_opt": ["no-new-privileges"],
            "user": f"{runtime_account.pw_uid}:{runtime_account.pw_gid}",
            "gpu_access_mode": "gpus",
            "shm_size": "8gb" if deployment_id == "home-spark-primary" else "16gb",
            "volumes": mounts,
        },
        "metadata": {"description": "HomeCompute catalog deployment; generated from pinned artifact/runtime tuple",
                     "homecompute_deployment": deployment_id,
                     "homecompute_revision": artifact["revision"]},
    }


def write_recipe(deployment_id: str, catalog: dict[str, Any]) -> Path:
    item, artifact, runtime = assert_eligible(catalog, deployment_id)
    verify_accepted_cache(deployment_id, artifact)
    if (not API_KEY_FILE.is_file() or API_KEY_FILE.is_symlink() or API_KEY_FILE.stat().st_uid != 0
            or API_KEY_FILE.stat().st_mode & 0o777 != 0o440 or API_KEY_FILE.stat().st_nlink != 1):
        raise RequestError("secret_unavailable", "the root-owned vLLM API-key file is missing or unsafe")
    for mount_source in (GB10_ROOT / "cache/huggingface", GB10_ROOT / "cache/vllm", MANIFESTS[deployment_id],
                         GB10_ROOT / "runtime/model-cache-integrity.py", API_KEY_FILE):
        if mount_source.is_symlink() or not mount_source.exists():
            raise RequestError("mount_source_unavailable", "a required fixed sparkrun mount source is missing or unsafe")
    recipe = generated_recipe(deployment_id, item, artifact, runtime)
    if MANAGER_ROOT.is_symlink() or RECIPES_DIR.is_symlink():
        raise RequestError("unsafe_recipe_directory", "sparkrun recipe path must not contain symlinks")
    MANAGER_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    RECIPES_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RECIPES_DIR.is_symlink() or RECIPES_DIR.stat().st_uid != 0 or RECIPES_DIR.stat().st_mode & 0o022:
        raise RequestError("unsafe_recipe_directory", "sparkrun recipe directory must be root-owned and protected")
    path = RECIPES_DIR / f"{recipe_name(deployment_id)}.yaml"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if path.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise RequestError("unsafe_recipe_path", "refusing a pre-existing symlink or temporary recipe")
    payload = (json.dumps(recipe, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chown(path, 0, 0)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    result = run_checked([sparkrun_binary(), "recipe", "validate", str(path)], timeout=120)
    if result.returncode:
        path.unlink(missing_ok=True)
        raise RequestError("recipe_validation_failed", "generated catalog recipe failed sparkrun validation")
    return path


def invoke_prepare(deployment_id: str) -> None:
    if deployment_id not in PREPARE_COMMANDS:
        raise RequestError("prepare_unavailable", "this deployment has no catalog-approved staging workflow")
    command = PREPARE_COMMANDS[deployment_id]
    script = ROOT / command[0]
    argv = ["bash", str(script), *command[1:]]
    result = run_checked(argv, timeout=6 * 60 * 60)
    if result.returncode:
        raise RequestError("prepare_failed", "HomeCompute artifact preparation or cache acceptance failed")


def recipe_path(deployment_id: str) -> Path:
    path = RECIPES_DIR / f"{recipe_name(deployment_id)}.yaml"
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise RequestError("recipe_not_prepared", "run prepare for this catalog deployment first")
    return path


def fixed_sparkrun(argv: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    result = run_checked([sparkrun_binary(), *argv], timeout=timeout)
    if result.returncode:
        # Never include a generated recipe or command body in caller-facing
        # errors; sparkrun output can include private host details.
        raise RequestError("sparkrun_failed", "sparkrun command failed; inspect sparkrun host diagnostics")
    return result


def check_job(deployment_id: str) -> str:
    result = run_checked([sparkrun_binary(), "cluster", "check-job", recipe_name(deployment_id),
                          "--cluster", CLUSTER, "--json"], timeout=45)
    if result.returncode == 0:
        return "running"
    if result.returncode == 1:
        return "stopped"
    return "unknown"


def status(catalog: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in deployment_rows(catalog):
        state = check_job(row["id"])
        item, _, _ = deployment(catalog, row["id"])
        port = item.get("compute_host_port", item.get("runtime_config", {}).get("host_port"))
        listener = run_checked(["ss", "-Hln", f"sport = :{port}"], timeout=5)
        port_available = None if listener.returncode else not listener.stdout.strip()
        rows.append({"id": row["id"], "name": row["name"], "eligible": row["eligible"],
                     "state": state, "port_available": port_available})
    return {"cluster": CLUSTER, "deployments": rows}


def assert_port_free(port: int) -> None:
    # Sparkrun's container publish uses a fixed catalog port. Refuse to replace
    # a Compose or other listener implicitly during the migration period.
    result = run_checked(["ss", "-Hln", f"sport = :{port}"], timeout=5)
    if result.returncode != 0:
        raise RequestError("preflight_failed", "could not inspect local listeners")
    if result.stdout.strip():
        raise RequestError("port_in_use", f"catalog port {port} is already in use; no workload was changed")


def wait_for_health(port: int) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
                connection.sendall(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
                response = connection.recv(256)
            if b" 200 " in response.split(b"\r\n", 1)[0]:
                return
        except OSError:
            pass
        time.sleep(5)
    raise RequestError("health_check_failed", "model did not pass the bounded HTTP health check")


def smoke(deployment_id: str, catalog: dict[str, Any]) -> None:
    item, _, _ = deployment(catalog, deployment_id)
    port = item.get("compute_host_port", item.get("runtime_config", {}).get("host_port"))
    if deployment_id == "automation-spark-primary":
        script = ROOT / "scripts/setup-compute-automation-moe.sh"
        argv = ["bash", str(script), "smoke", "--env", str(ENV_FILE)]
    elif deployment_id == "home-spark-primary":
        script = ROOT / "scripts/setup-compute-home-assistant-model.sh"
        argv = ["bash", str(script), "smoke", "--env", str(HOME_ENV_FILE)]
    else:
        raise RequestError("smoke_unavailable", "deployment has no catalog qualification smoke")
    # Existing smoke scripts validate health, auth behavior, the served alias,
    # language/protocol behavior, and required/automatic tool calls.
    result = run_checked(argv, timeout=900)
    if result.returncode:
        try:
            fixed_sparkrun(["stop", recipe_name(deployment_id), "--cluster", CLUSTER], timeout=180)
        finally:
            raise RequestError("qualification_smoke_failed", f"model failed HomeCompute smoke checks on port {port} and was stopped")


def load_model(deployment_id: str, catalog: dict[str, Any]) -> None:
    item, artifact, runtime = assert_eligible(catalog, deployment_id)
    path = recipe_path(deployment_id)
    verify_accepted_cache(deployment_id, artifact)
    port = item.get("compute_host_port", item.get("runtime_config", {}).get("host_port"))
    assert_port_free(port)
    fixed_sparkrun(["run", str(path), "--cluster", CLUSTER, "--solo", "--ensure", "--no-follow", "--no-sync-tuning"], timeout=6 * 60 * 60)
    try:
        wait_for_health(port)
        smoke(deployment_id, catalog)
    except RequestError:
        run_checked([sparkrun_binary(), "stop", recipe_name(deployment_id), "--cluster", CLUSTER], timeout=180)
        raise


def unload_model(deployment_id: str, catalog: dict[str, Any]) -> None:
    item, artifact, runtime = assert_eligible(catalog, deployment_id)
    recipe_path(deployment_id)
    if check_job(deployment_id) != "running":
        raise RequestError("not_running", "the selected catalog workload is not running under sparkrun")
    fixed_sparkrun(["stop", recipe_name(deployment_id), "--cluster", CLUSTER], timeout=180)
    state = check_job(deployment_id)
    if state != "stopped":
        raise RequestError("unload_unverified", "sparkrun did not confirm the workload stopped")


def replace_model(source_id: str, target_id: str, catalog: dict[str, Any]) -> None:
    if source_id == target_id:
        raise RequestError("invalid_replace", "source and target deployments must differ")
    source, _, _ = assert_eligible(catalog, source_id)
    target, _, _ = assert_eligible(catalog, target_id)
    source_recipe = recipe_path(source_id)
    target_recipe = recipe_path(target_id)
    if check_job(source_id) != "running":
        raise RequestError("source_not_running", "replace source is not a running sparkrun workload")
    if check_job(target_id) == "running":
        raise RequestError("target_already_running", "replace target is already running")
    target_port = target.get("compute_host_port", target.get("runtime_config", {}).get("host_port"))
    assert_port_free(target_port)
    fixed_sparkrun(["stop", recipe_name(source_id), "--cluster", CLUSTER], timeout=180)
    try:
        load_model(target_id, catalog)
    except RequestError as original_error:
        # Roll back the source from its previously generated, accepted recipe.
        source_port = source.get("compute_host_port", source.get("runtime_config", {}).get("host_port"))
        try:
            assert_port_free(source_port)
            fixed_sparkrun(["run", str(source_recipe), "--cluster", CLUSTER, "--solo", "--ensure", "--no-follow", "--no-sync-tuning"], timeout=6 * 60 * 60)
            wait_for_health(source_port)
            smoke(source_id, catalog)
        except RequestError as rollback_error:
            raise RequestError("replace_and_rollback_failed", f"target failed ({original_error.code}); source recovery also failed ({rollback_error.code})") from original_error
        raise RequestError("replace_rolled_back", f"target failed ({original_error.code}); source workload was restored") from original_error


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise RequestError("invalid_request", "request exceeds 4096 bytes")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RequestError("invalid_request", "stdin must contain one JSON object") from error
    if not isinstance(value, dict):
        raise RequestError("invalid_request", "request must be a JSON object")
    return value


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    schemas = {
        "list": {"action"}, "status": {"action"},
        "prepare": {"action", "deployment"}, "load": {"action", "deployment"},
        "unload": {"action", "deployment"}, "replace": {"action", "from", "to"},
    }
    if action not in schemas or set(request) != schemas[action]:
        raise RequestError("invalid_request", "action or request fields are invalid")
    catalog = load_catalog()
    if action == "list":
        return {"deployments": deployment_rows(catalog)}
    if action == "status":
        require_root()
        return status(catalog)
    if action == "prepare":
        require_root()
        deployment_id = request["deployment"]
        assert_eligible(catalog, deployment_id)
        invoke_prepare(deployment_id)
        path = write_recipe(deployment_id, catalog)
        return {"deployment": deployment_id, "prepared": True, "recipe": path.name}
    if action == "load":
        require_root()
        deployment_id = request["deployment"]
        load_model(deployment_id, catalog)
        return {"deployment": deployment_id, "state": "running", "qualified": True}
    if action == "unload":
        require_root()
        deployment_id = request["deployment"]
        unload_model(deployment_id, catalog)
        return {"deployment": deployment_id, "state": "stopped", "cache_retained": True}
    require_root()
    replace_model(request["from"], request["to"], catalog)
    return {"from": request["from"], "to": request["to"], "state": "running", "rollback": "enabled"}


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise RequestError("invalid_request", "this helper accepts JSON only on stdin and no command-line arguments")
        # Mutations serialize across dashboard and operator requests. Reads also
        # take the lock to avoid reporting a half-finished cold swap.
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_FILE.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            response = {"ok": True, "data": dispatch(read_request())}
        print(json.dumps(response, separators=(",", ":")))
        return 0
    except RequestError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}, separators=(",", ":")))
        return 1
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        # Keep machine output stable and do not leak command stderr, secrets, or
        # local filesystem details to the dashboard caller.
        print(json.dumps({"ok": False, "error": {"code": "internal_error", "message": type(error).__name__}}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
