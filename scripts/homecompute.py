#!/usr/bin/env python3
"""Workstation CLI for observed HomeCompute state and explicit releases."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HOSTS = ("home-core", "home-spark")
REMOTE_STATUS = r'''#!/usr/bin/env bash
set -u
present() { command -v "$1" >/dev/null 2>&1 && printf true || printf false; }
host="$(hostname -s)"
# /etc/os-release is a trusted vendor/NixOS file; source it to preserve exact
# values without depending on Python being installed before the first rollout.
source /etc/os-release
os_name="${PRETTY_NAME:-unknown}"
revision_file=/var/lib/homecompute/deployed-revision
current=/srv/homecompute/current
if [[ "$host" == home-spark ]]; then revision_file=/var/lib/homecompute/home-spark-deployed-revision; fi
revision="$(cat "$revision_file" 2>/dev/null || sudo -n cat "$revision_file" 2>/dev/null || true)"
current_target="$(readlink "$current" 2>/dev/null || true)"
docker_access=false
docker_prefix=()
if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  docker_access=true
elif sudo -n docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  docker_access=true
  docker_prefix=(sudo -n)
fi
containers=null
if [[ "$docker_access" == true ]]; then
  containers="$("${docker_prefix[@]}" docker ps -a --format '{{json .}}' 2>/dev/null | jq -s '[.[] | {name:.Names,image:.Image,status:.Status,ports:.Ports}]' 2>/dev/null || printf '[]')"
fi
failed_raw="$(systemctl --failed --no-legend --plain 2>/dev/null || true)"
failed_units="$(printf '%s\n' "$failed_raw" | jq -Rn '[inputs | select(length > 0)]')"
gpu="$(nvidia-smi --query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null || true)"
platform_updates=''
if [[ "$host" == home-spark ]]; then
  platform_updates="$(apt list --upgradable 2>/dev/null | grep '\[upgradable from:' || true)"
fi
update_report=null
report_file=/var/lib/homecompute-model-update-check/report.json
# Keep upstream labels, URLs, error bodies and benchmark payloads on the host.
report_projection='{schema_version,document_type,generated_at,mode,status,summary:{changed:.summary.changed,pin_drift:.summary.pin_drift,source_errors:.summary.source_errors,outperforms_active:.summary.outperforms_active}}'
if [[ -r "$report_file" ]]; then update_report="$(jq -c "$report_projection" "$report_file" 2>/dev/null || printf null)"
elif sudo -n test -r "$report_file" 2>/dev/null; then update_report="$(sudo -n jq -c "$report_projection" "$report_file" 2>/dev/null || printf null)"; fi
tools="$(jq -cn \
  --argjson git "$(present git)" --argjson docker "$(present docker)" \
  --argjson jq "$(present jq)" --argjson curl "$(present curl)" \
  --argjson python3 "$(present python3)" --argjson nvidia_smi "$(present nvidia-smi)" \
  --argjson nvidia_ctk "$(present nvidia-ctk)" --argjson nvidia_container_cli "$(present nvidia-container-cli)" \
  --argjson nix "$(present nix)" --argjson nixos_rebuild "$(present nixos-rebuild)" \
  --argjson flock "$(present flock)" --argjson awk "$(present awk)" \
  --argjson sha256sum "$(present sha256sum)" --argjson realpath "$(present realpath)" \
  --argjson cmp "$(present cmp)" \
  --argjson compose "$(docker compose version >/dev/null 2>&1 && echo true || echo false)" \
  --argjson sudo "$(present sudo)" \
  --argjson docker_access "$docker_access" --argjson sudo_nopasswd "$(sudo -n true >/dev/null 2>&1 && echo true || echo false)" \
  '{git:$git,docker:$docker,jq:$jq,curl:$curl,python3:$python3,"nvidia-smi":$nvidia_smi,"nvidia-ctk":$nvidia_ctk,"nvidia-container-cli":$nvidia_container_cli,nix:$nix,"nixos-rebuild":$nixos_rebuild,flock:$flock,awk:$awk,sha256sum:$sha256sum,realpath:$realpath,cmp:$cmp,sudo:$sudo,"docker-compose-plugin":$compose,"docker-access":$docker_access,"sudo-nopasswd":$sudo_nopasswd}')"
jq -cn --arg host "$host" --arg os "$os_name" --arg kernel "$(uname -r)" \
  --arg revision "$revision" --arg current_target "$current_target" --arg gpu "$gpu" \
  --arg platform_updates "$platform_updates" --argjson containers "$containers" \
  --argjson failed_units "$failed_units" --argjson required_tools "$tools" \
  --argjson model_update_report "$update_report" \
  '{schema_version:1,host:$host,os:$os,kernel:$kernel,revision:(if $revision=="" then null else $revision end),current_target:(if $current_target=="" then null else $current_target end),containers:$containers,failed_units:$failed_units,gpu:(if $gpu=="" then null else $gpu end),platform_updates:(if $platform_updates=="" then null else $platform_updates end),required_tools:$required_tools,model_update_report:$model_update_report}'
'''


class OperatorError(Exception):
    pass


def valid_sha(value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("revision must be a lowercase 40-character Git commit SHA")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OperatorError(f"{path} must contain a JSON object")
    return value


def model_inventory(catalog: dict[str, Any], roster: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for deployment_id, deployment in catalog.get("deployments", {}).items():
        artifact = catalog.get("artifacts", {}).get(deployment.get("artifact"), {})
        rows.append({"id": deployment_id, "model": artifact.get("upstream_model_id"),
                     "revision": artifact.get("revision"), "host": "home-spark",
                     "role": deployment.get("served_model_name", deployment.get("host_role")),
                     "runtime": catalog.get("runtime_profiles", {}).get(deployment.get("runtime_profile"), {}).get("family"),
                     "quantization": artifact.get("quantization"), "desired_state": deployment.get("availability", "unknown"),
                     "qualification": artifact.get("qualification", {}), "actual_state": "unknown"})
    known = {str(row["model"]).lower() for row in rows}
    for key, model in roster.get("text_models", {}).items():
        name = model.get("model_id")
        if not name or name.lower() in known:
            continue
        rows.append({"id": key, "model": name, "revision": model.get("revision"), "host": "home-spark",
                     "role": ",".join(model.get("roles", [])),
                     "runtime": model.get("runtime_profile", {}).get("runtime", "unknown"),
                     "quantization": None, "desired_state": model.get("disposition", "unknown"),
                     "qualification": model.get("runtime_profile", {}).get("status", "unknown"), "actual_state": "unknown"})
    for key, service in roster.get("services", {}).items():
        rows.append({"id": key, "model": service.get("model_id"), "revision": service.get("revision"),
                     "host": "home-spark", "role": service.get("language", key),
                     "runtime": service.get("runtime_profile", {}).get("runtime", "unknown"),
                     "quantization": None, "desired_state": service.get("disposition", "unknown"),
                     "qualification": service.get("commercial_use_gate", service.get("voice_policy", "unknown")), "actual_state": "unknown"})
    return rows


def compare_drift(desired: str | None, observed: dict[str, Any] | None) -> str:
    if not observed or not observed.get("revision"):
        return "unknown"
    if not desired:
        return "unknown"
    return "current" if desired == observed["revision"] else "drifted"


def classify_updates(observed: dict[str, Any]) -> dict[str, list[str]]:
    """Keep application and vendor-owned platform signals in separate buckets."""
    application: list[str] = []
    platform: list[str] = []
    report = observed.get("model_update_report")
    if isinstance(report, dict):
        summary = report.get("summary", {})
        if summary.get("changed", 0): application.append(f"{summary['changed']} model upstream change(s)")
        if summary.get("pin_drift", 0): application.append(f"{summary['pin_drift']} model pin drift(s)")
        if summary.get("source_errors", 0): application.append(f"{summary['source_errors']} model update source error(s)")
    packages = observed.get("platform_updates")
    if isinstance(packages, str) and packages.strip():
        platform.append("DGX OS package candidates reported by apt")
    return {"application": application, "platform": platform}


def parse_remote_output(stdout: str, host: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{host}: malformed remote status output: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("host") != host:
        raise OperatorError(f"{host}: remote status output has an unsupported schema or host identity")
    for key, expected in (("containers", list), ("failed_units", list)):
        if not isinstance(data.get(key), expected) and not (key == "containers" and data.get(key) is None):
            raise OperatorError(f"{host}: remote status field {key} is malformed")
    if data.get("revision") is None and isinstance(data.get("current_target"), str):
        pointer_revision = data["current_target"].rstrip("/").rsplit("/", 1)[-1]
        if SHA_RE.fullmatch(pointer_revision):
            data["revision"] = pointer_revision
    return data


def default_runner(argv: list[str], *, input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)


def ssh_argv(host: str, remote_command: str, *, batch_mode: bool = True) -> list[str]:
    if host not in HOSTS:
        raise OperatorError(f"unsupported host: {host}")
    argv = ["ssh"]
    if batch_mode:
        argv.extend(["-o", "BatchMode=yes"])
    argv.extend(["-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2", host, remote_command]
    )
    return argv


def remote_status(host: str, runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> dict[str, Any]:
    try:
        result = runner(ssh_argv(host, "bash -s"), input_text=REMOTE_STATUS, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperatorError(f"{host}: SSH status failed: {exc}") from exc
    if result.returncode:
        raise OperatorError(f"{host}: unreachable or status command failed ({result.stderr.strip() or result.returncode})")
    return parse_remote_output(result.stdout, host)


def desired_revision(args: argparse.Namespace, runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> str:
    if args.revision:
        return args.revision
    result = runner(["git", "ls-remote", "origin", "refs/heads/master"], timeout=20)
    if result.returncode:
        raise OperatorError(f"cannot resolve origin/master: {result.stderr.strip()}")
    sha = result.stdout.split()[0] if result.stdout.split() else ""
    if not SHA_RE.fullmatch(sha):
        raise OperatorError("origin/master did not return a full commit SHA")
    return sha


def deploy_host(host: str, revision: str, *, sudo_nopasswd: bool = True,
                runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> str:
    valid_sha(revision)
    script = "deploy-home-core.sh" if host == "home-core" else "deploy-home-spark.sh"
    command = f"sudo -n bash -s -- {revision}"
    fetch = runner(["git", "-C", str(ROOT), "fetch", "--no-tags", "origin", revision], timeout=120)
    if fetch.returncode:
        raise OperatorError(f"cannot fetch deployment revision {revision}: {fetch.stderr.strip()}")
    content = runner(["git", "-C", str(ROOT), "show", f"{revision}:scripts/{script}"], timeout=20)
    if content.returncode:
        # The Spark application installer predates its repository release wrapper;
        # retain rollback support for revisions from before that wrapper existed.
        if host != "home-spark":
            raise OperatorError(f"{revision} has no {script} deployment entry point")
        content = subprocess.CompletedProcess([], 0, (ROOT / "scripts" / script).read_text(), "")
    payload = content.stdout
    try:
        if sudo_nopasswd or runner is not default_runner:
            result = runner(ssh_argv(host, command), input_text=payload, timeout=7200)
        else:
            if not sys.stdin.isatty():
                raise OperatorError(f"{host}: interactive sudo is required; run deployment from a terminal")
            encoded = base64.b64encode(payload.encode()).decode("ascii")
            interactive_command = f"sudo bash -c 'printf %s {encoded} | base64 -d | bash -s -- {revision}'"
            argv = ssh_argv(host, interactive_command, batch_mode=False)
            argv.insert(-2, "-tt")
            result = subprocess.run(argv, timeout=7200, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperatorError(f"{host}: deployment transport failed: {exc}") from exc
    if result.returncode:
        error = getattr(result, "stderr", None)
        raise OperatorError(f"{host}: deployment failed ({error.strip() if error else result.returncode})")
    return (result.stdout or "").strip()


def render_human(data: dict[str, Any]) -> str:
    lines = ["HomeCompute", "=" * 52, f"Desired revision: {data.get('desired_revision') or 'unknown'}"]
    for host in HOSTS:
        item = data.get("hosts", {}).get(host)
        if not item:
            lines.append(f"\n{host}: unreachable / unverified")
            continue
        state = compare_drift(data.get("desired_revision"), item)
        lines.append(f"\n{host} ({item.get('os') or 'OS unknown'})")
        lines.append(f"  deployment: {item.get('revision') or 'unknown'} ({state})")
        container_state = f"{len(item['containers'])} observed" if item["containers"] is not None else "unknown (no Docker access)"
        lines.append(f"  containers: {container_state}")
        lines.append(f"  failed units: {len(item['failed_units'])}")
        if item.get("gpu") is not None:
            lines.append(f"  GPU: {item['gpu'] or 'unavailable'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> int:
    parser = argparse.ArgumentParser(prog="homecompute")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor", "updates", "services", "drift"):
        p = sub.add_parser(name)
        if name == "status": p.add_argument("--json", action="store_true")
    models = sub.add_parser("models"); models.add_argument("--json", action="store_true")
    deploy = sub.add_parser("deploy"); deploy.add_argument("host", choices=(*HOSTS, "all"))
    deploy.add_argument("--revision", type=valid_sha); deploy.add_argument("--from", dest="from_remote", choices=("origin/master",))
    diff = sub.add_parser("diff"); diff.add_argument("host", choices=HOSTS); diff.add_argument("--revision", type=valid_sha)
    args = parser.parse_args(argv)
    try:
        if args.command == "deploy":
            revision = desired_revision(args, runner)
            targets = ["home-spark", "home-core"] if args.host == "all" else [args.host]
            print(f"Deploying {revision} to {', '.join(targets)}")
            preflight = {}
            preflight_errors: list[str] = []
            for host in targets:
                # Preflight every target before changing either host.
                try:
                    preflight[host] = remote_status(host, runner)
                except OperatorError as exc:
                    preflight_errors.append(str(exc))
            for host, item in preflight.items():
                required = ("docker", "docker-compose-plugin", "sudo")
                if host == "home-core": required += ("git", "nix", "nixos-rebuild", "flock", "jq")
                else: required += ("git", "python3", "flock", "jq", "curl", "awk", "sha256sum", "realpath", "cmp", "nvidia-smi", "nvidia-ctk", "nvidia-container-cli")
                missing = [tool for tool in required if not item.get("required_tools", {}).get(tool, False)]
                if missing:
                    preflight_errors.append(f"{host}: deployment preflight missing required tools/access: {', '.join(missing)}")
            if preflight_errors:
                raise OperatorError("; ".join(preflight_errors))
            for host, item in preflight.items():
                print(f"  {host}: preflight passed (deployed {item.get('revision') or 'revision unknown'})")
            for host in targets:
                result = deploy_host(host, revision,
                                     sudo_nopasswd=item.get("required_tools", {}).get("sudo-nopasswd", False),
                                     runner=runner)
                lines = [line for line in result.splitlines() if line.strip()]
                outcome = lines[-1] if lines else "command returned success"
                print(f"  {host}: {outcome}")
            if args.host == "all":
                print("Cross-host smoke check: not configured; verify gateway routes with homecompute doctor")
            return 0
        if args.command == "diff":
            rev = args.revision or desired_revision(argparse.Namespace(revision=None, from_remote=None), runner)
            observed = remote_status(args.host, runner)
            result = runner(["git", "-C", str(ROOT), "diff", "--stat", f"{observed.get('revision')}...{rev}"], timeout=30)
            if result.returncode: raise OperatorError(f"cannot compare {args.host} revision to {rev}: {result.stderr.strip()}")
            print(result.stdout or "No file changes."); return 0
        catalog = load_json(ROOT / "config/model-catalog.json")
        roster = load_json(ROOT / "config/gb10-model-roster.json")
        if args.command == "models":
            rows = model_inventory(catalog, roster)
            if args.json: print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                print("ID | model | revision | desired | qualification")
                for row in rows: print(f"{row['id']} | {row['model']} | {row['revision']} | {row['desired_state']} | actual unknown | {row['qualification']}")
            return 0
        observations: dict[str, Any] = {}; failures = {}
        for host in HOSTS:
            try: observations[host] = remote_status(host, runner)
            except OperatorError as exc: failures[host] = str(exc)
        desired = None
        try: desired = desired_revision(argparse.Namespace(revision=None, from_remote=None), runner)
        except OperatorError: pass
        payload = {"schema_version": 1, "desired_revision": desired, "hosts": observations, "unverified": failures}
        if args.command == "status":
            if getattr(args, "json", False): print(json.dumps(payload, indent=2, sort_keys=True))
            else: print(render_human(payload))
            return 0 if not failures else 1
        if args.command == "services":
            for host, item in observations.items():
                print(f"{host} (observed containers)")
                if item["containers"] is None: print("  unknown: Docker access unavailable")
                else:
                    for c in item["containers"]: print(f"  {c['name']}: {c['status']} [{c['image']}]")
            for host, error in failures.items(): print(f"{host}: unknown ({error})")
            return 0 if not failures else 1
        if args.command == "doctor":
            issues = bool(failures)
            for host in HOSTS:
                if host in failures: print(f"✗ {host}: {failures[host]}"); continue
                item=observations[host]
                print(f"✓ {host}: reachable; revision {item.get('revision') or 'unknown'}")
                print(f"{'✓' if not item['failed_units'] else '✗'} failed units: {len(item['failed_units'])}")
                issues = issues or bool(item["failed_units"])
                docker_count = len(item["containers"]) if item["containers"] is not None else None
                print(f"{'✓' if docker_count else '⚠'} Docker containers observed: {docker_count if docker_count is not None else 'unknown (no access)'}")
                for tool, present in item.get("required_tools", {}).items():
                    required = tool not in ("nix", "nixos-rebuild", "nvidia-smi", "nvidia-ctk", "nvidia-container-cli") or host == "home-core" and tool in ("nix", "nixos-rebuild") or host == "home-spark" and tool.startswith("nvidia-")
                    if required:
                        print(f"{'✓' if present else '✗'} {tool}: {'present' if present else 'missing'}")
                        issues = issues or not present
                if host == "home-spark":
                    print(f"{'✓' if item.get('gpu') else '✗'} NVIDIA GPU visibility")
                    issues = issues or not bool(item.get("gpu"))
            return 1 if issues else 0
        if args.command in ("drift", "updates"):
            for host in HOSTS:
                if host in failures: print(f"{host}: unknown ({failures[host]})"); continue
                item=observations[host]; state=compare_drift(desired,item)
                print(f"{host}: {state} (desired {desired or 'unknown'}, deployed {item.get('revision') or 'unknown'})")
                for service in item["containers"] or []:
                    print(f"  observed {service.get('name')}: {service.get('status')} ({service.get('image')})")
            if args.command == "updates":
                print("Application updates")
                classified = classify_updates(observations.get("home-core", {}))
                for update in classified["application"]: print(f"  {update}")
                if not classified["application"]: print("  no model update reported by home-core monitor")
            if "home-spark" in observations and args.command == "updates":
                classified=classify_updates(observations["home-spark"])
                print("Platform updates (vendor-managed; apt metadata may be stale)")
                for update in classified["platform"]: print(f"  {update}")
                if not classified["platform"]: print("  none detected or package metadata unavailable")
            return 0 if not failures else 1
    except (OperatorError, argparse.ArgumentTypeError, OSError) as exc:
        print(f"homecompute: error: {exc}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
