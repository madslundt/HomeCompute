#!/usr/bin/env python3
"""Workstation CLI for observed HomeCompute state and explicit releases."""

from __future__ import annotations

import argparse
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
REMOTE_STATUS = r'''import json, os, pathlib, platform, shutil, subprocess
def run(args):
    try:
        p=subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError): return None
def read(path):
    try: return pathlib.Path(path).read_text().strip()
    except OSError: return None
os_release={}
for line in (read('/etc/os-release') or '').splitlines():
    if '=' in line:
        k,v=line.split('=',1); os_release[k]=v.strip('"')
host=run(['hostname','-s']) or platform.node()
revision_file='/var/lib/homecompute/deployed-revision' if host == 'home-core' else '/var/lib/homecompute/home-spark-deployed-revision'
revision=read(revision_file)
current='/srv/homecompute/current'
containers=[]
raw=run(['docker','ps','-a','--format','{{json .}}'])
if raw:
    for line in raw.splitlines():
        try:
            item=json.loads(line)
            containers.append({'name':item.get('Names'), 'image':item.get('Image'), 'status':item.get('Status'), 'ports':item.get('Ports')})
        except json.JSONDecodeError: pass
failed=run(['systemctl','--failed','--no-legend','--plain'])
gpu=run(['nvidia-smi','--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'])
platform_updates=None
if host == 'home-spark':
    output=run(['apt','list','--upgradable']) or ''
    platform_updates='\n'.join(line for line in output.splitlines() if '[upgradable from:' in line)
tools={name:bool(shutil.which(name)) for name in ['git','docker','jq','curl','python3','nvidia-smi','nvidia-ctk','nvidia-container-cli','nix','nixos-rebuild','flock','awk','sha256sum','realpath','cmp']}
tools['docker-compose-plugin']=bool(run(['docker','compose','version']))
tools['docker-access']=bool(run(['docker','info','--format','{{.ServerVersion}}']))
tools['sudo-nopasswd']=bool(run(['sudo','-n','true']))
update_report=None
try: update_report=json.loads(read('/var/lib/homecompute/model-update-check/report.json') or 'null')
except (json.JSONDecodeError, TypeError): pass
print(json.dumps({'schema_version':1,'host':host,'os':os_release.get('PRETTY_NAME'),'kernel':platform.release(),'revision':revision,'current_target':os.readlink(current) if os.path.islink(current) else None,'containers':containers,'failed_units':failed.splitlines() if failed else [],'gpu':gpu,'platform_updates':platform_updates,'required_tools':tools,'model_update_report':update_report}, separators=(',',':')))
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
        if not isinstance(data.get(key), expected):
            raise OperatorError(f"{host}: remote status field {key} is malformed")
    return data


def default_runner(argv: list[str], *, input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)


def ssh_argv(host: str, remote_command: str) -> list[str]:
    if host not in HOSTS:
        raise OperatorError(f"unsupported host: {host}")
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2", host, remote_command]


def remote_status(host: str, runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> dict[str, Any]:
    try:
        result = runner(ssh_argv(host, "python3 -"), input_text=REMOTE_STATUS, timeout=25)
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


def deploy_host(host: str, revision: str, runner: Callable[..., subprocess.CompletedProcess[str]] = default_runner) -> None:
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
        result = runner(ssh_argv(host, command), input_text=payload, timeout=7200)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperatorError(f"{host}: deployment transport failed: {exc}") from exc
    if result.returncode:
        raise OperatorError(f"{host}: deployment failed ({result.stderr.strip() or result.returncode})")


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
        lines.append(f"  containers: {len(item['containers'])} observed")
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
            for host in targets:
                # Preflight every target before changing either host.
                preflight[host] = remote_status(host, runner)
                required = ("docker", "docker-compose-plugin", "docker-access", "sudo-nopasswd")
                if host == "home-core": required += ("git", "nix", "nixos-rebuild", "flock", "jq", "python3")
                else: required += ("git", "python3", "flock", "jq", "curl", "awk", "sha256sum", "realpath", "cmp", "nvidia-smi", "nvidia-ctk", "nvidia-container-cli")
                missing = [tool for tool in required if not preflight[host].get("required_tools", {}).get(tool, False)]
                if missing:
                    raise OperatorError(f"{host}: deployment preflight missing required tools/access: {', '.join(missing)}")
                print(f"  {host}: preflight passed (deployed {preflight[host].get('revision') or 'revision unknown'})")
            for host in targets:
                deploy_host(host, revision, runner)
                print(f"  {host}: deployment command succeeded; see host checks above")
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
                print(f"{'✓' if item['containers'] else '⚠'} Docker containers observed: {len(item['containers'])}")
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
                for service in item["containers"]:
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
