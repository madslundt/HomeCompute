#!/usr/bin/env python3
"""Operator-only, read-only metadata collector; never install or repair anything."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable

import homecompute
from maintenance_observations import SOURCES, TTL, project, semantic_evidence

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z0-9][a-z0-9_.@-]{0,120}$")
VERSION = re.compile(r"^v?[0-9]+(?:[.][0-9A-Za-z_-]+){1,5}$")
REASONS = {"collection_failed", "not_provisioned", "missing", "stale", "unsupported_schema",
           "health_not_reported", "update_metadata_unavailable", "cached_inventory_only", "never_started"}
SYSTEM_KEYS = {"id", "collector", "enabled", "target", "health_ttl_seconds",
               "update_ttl_seconds", "containers", "units", "updates"}


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("metadata file exceeds size limit")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("metadata must be an object")
    return data


def validate_registry(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {"schema_version", "mode", "automatic_actions", "physical_device_actions", "systems"}:
        raise ValueError("unsupported registry fields")
    if (data["schema_version"] != 1 or data["mode"] != "observe-only"
            or data["automatic_actions"] is not False or data["physical_device_actions"] is not False):
        raise ValueError("registry must prohibit automatic and physical actions")
    systems = data["systems"]
    if not isinstance(systems, list) or not 1 <= len(systems) <= 32:
        raise ValueError("registry requires 1-32 systems")
    seen = set()
    for system in systems:
        if not isinstance(system, dict) or set(system) != SYSTEM_KEYS:
            raise ValueError("unsupported system fields")
        if not isinstance(system["id"], str) or not ID.fullmatch(system["id"]) or system["id"] in seen:
            raise ValueError("invalid or duplicate system id")
        seen.add(system["id"])
        if type(system["enabled"]) is not bool:
            raise ValueError("enabled must be a boolean")
        collector = system["collector"]
        if collector == "homecompute-status":
            if system["target"] not in homecompute.HOSTS or system["id"] != system["target"]:
                raise ValueError("SSH target outside fixed collector allowlist")
        elif collector == "homeassistant-metadata":
            if system["target"] != "home-assistant" or system["id"] != "home-assistant":
                raise ValueError("HA target outside fixed collector allowlist")
        else:
            raise ValueError("unsupported collector")
        for key in ("health_ttl_seconds", "update_ttl_seconds"):
            if type(system[key]) is not int or not 60 <= system[key] <= 1209600:
                raise ValueError("invalid observation TTL")
        if not isinstance(system["containers"], list) or len(system["containers"]) > 64:
            raise ValueError("invalid container list")
        names = set()
        for service in system["containers"]:
            if (not isinstance(service, dict) or set(service) != {"name", "require_health"}
                    or not isinstance(service["name"], str) or not ID.fullmatch(service["name"])
                    or type(service["require_health"]) is not bool or service["name"] in names):
                raise ValueError("invalid container entry")
            names.add(service["name"])
        if (not isinstance(system["units"], list) or len(system["units"]) > 64
                or any(not isinstance(x, dict) or set(x) != {"name", "kind"}
                       or not isinstance(x["name"], str) or not ID.fullmatch(x["name"])
                       or not x["name"].endswith(".service") or x["kind"] not in ("oneshot", "persistent")
                       for x in system["units"])
                or len({x["name"] for x in system["units"]}) != len(system["units"])):
            raise ValueError("invalid unit list")
        allowed = {"model-update-report", "cached-apt-candidates"} | SOURCES if collector == "homecompute-status" else {"approved-update-metadata"}
        if not isinstance(system["updates"], list) or any(x not in allowed for x in system["updates"]):
            raise ValueError("unsupported update source")
    return data


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10000000 else None


def check(system: dict[str, Any], check_id: str, status: str, evidence: dict[str, Any],
          now: datetime, *, updates: bool = False) -> dict[str, Any]:
    ttl = system["update_ttl_seconds" if updates else "health_ttl_seconds"]
    if check_id.split(".")[0] in SOURCES:
        ttl = TTL
    return {"system_id": system["id"], "check_id": check_id,
            "stable_key": system["id"] + ":" + check_id,
            "category": "updates" if updates else "health", "status": status,
            "severity": "warning" if status == "degraded" else "info",
            "evidence": evidence, "observed_at": iso(now), "expires_at": iso(now + timedelta(seconds=ttl)),
            "action": "review_only", "approval_required": True, "physical_device_actions": False}


def normalize(system: dict[str, Any], raw: dict[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
    add = lambda key, status, evidence, **kw: check(system, key, status, evidence, now, **kw)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or raw.get("host") != system["target"]:
        return [add("collection", "unknown", {"reason": "collection_failed" if system["enabled"] else "not_provisioned"})] + [
            row for source in SOURCES.intersection(system["updates"]) for row in project(system, {}, source, now, check)]
    checks = [add("collection", "healthy", {"available": True})]
    if system["collector"] == "homeassistant-metadata":
        try:
            generated = timestamp(raw["generated_at"])
            fresh = -60 <= (now - generated).total_seconds() <= min(system["health_ttl_seconds"], system["update_ttl_seconds"])
        except (ValueError, KeyError, TypeError):
            fresh = False
        if not fresh:
            return [add("collection", "unknown", {"reason": "stale"})]
        # n8n owns the HA credential and returns only this finite projection.
        version = raw.get("installed_version")
        checks.append(add("installed-version", "healthy" if isinstance(version, str) and VERSION.fullmatch(version) else "unknown",
                          {"installed_version": version} if isinstance(version, str) and VERSION.fullmatch(version) else {"reason": "missing"}))
        for key in ("update_available_count", "unavailable_count"):
            value = count(raw.get(key))
            checks.append(add(key, "unknown" if value is None else ("degraded" if value else "healthy"),
                              {"count": value} if value is not None else {"reason": "missing"},
                              updates=key == "update_available_count"))
        for row in checks:
            row["observed_at"] = iso(generated)
            row["expires_at"] = iso(generated + timedelta(seconds=system["update_ttl_seconds"] if row["category"] == "updates" else system["health_ttl_seconds"]))
        return checks
    containers = raw.get("containers")
    for service in system["containers"]:
        key = "container." + service["name"]
        if not isinstance(containers, list):
            checks.append(add(key, "unknown", {"reason": "collection_failed"})); continue
        matches = [c for c in containers if isinstance(c, dict) and c.get("name") == service["name"]]
        if not matches:
            checks.append(add(key, "degraded", {"state": "missing"})); continue
        status = matches[0].get("status", "")
        if not isinstance(status, str):
            checks.append(add(key, "unknown", {"reason": "unsupported_schema"})); continue
        running = status.startswith("Up ")
        healthy = "(healthy)" in status
        bad = "(unhealthy)" in status or status.startswith(("Restarting ", "Exited ", "Dead"))
        state = "unhealthy" if bad else ("healthy" if running and healthy else "running" if running else "unknown")
        result = "degraded" if bad else "healthy" if running and (healthy or not service["require_health"]) else "unknown"
        checks.append(add(key, result, {"state": state}))
    unit_states = raw.get("unit_states", {})
    for unit in system["units"]:
        state = unit_states.get(unit["name"]) if isinstance(unit_states, dict) else None
        key = "unit." + unit["name"]
        if not isinstance(state, dict):
            checks.append(add(key, "unknown", {"reason": "collection_failed"})); continue
        if state.get("LoadState") != "loaded":
            checks.append(add(key, "degraded", {"state": "missing"})); continue
        if unit["kind"] == "persistent":
            running = state.get("ActiveState") == "active" and state.get("SubState") == "running"
            checks.append(add(key, "healthy" if running else "degraded", {"state": "running" if running else "unhealthy"}))
        else:
            try: started = int(state.get("ExecMainStartTimestampMonotonic", "0")) > 0
            except (ValueError, TypeError): started = False
            if not started:
                checks.append(add(key, "unknown", {"reason": "never_started"})); continue
            if state.get("ActiveState") in ("activating", "active"):
                checks.append(add(key, "unknown", {"state": "running"})); continue
            successful = state.get("Result") == "success" and str(state.get("ExecMainStatus")) == "0"
            checks.append(add(key, "healthy" if successful else "degraded", {"failed": not successful}))
    for source in system["updates"]:
        if source in SOURCES:
            checks.extend(project(system, raw, source, now, check))
        elif source == "cached-apt-candidates":
            packages = raw.get("platform_updates")
            valid = isinstance(packages, str) or packages is None
            candidates = sum("[upgradable from:" in x for x in packages.splitlines()) if isinstance(packages, str) else 0
            checks.append(add(source, "unknown" if not valid else "degraded" if candidates else "unknown",
                              {"candidate_count": candidates, "reason": "cached_inventory_only"} if valid else {"reason": "unsupported_schema"}, updates=True))
            # No apt refresh occurs; no candidates does not prove the OS is up to date.
        elif source == "model-update-report":
            report = raw.get("model_update_report")
            valid = isinstance(report, dict) and report.get("schema_version") == 1 and report.get("mode") == "review-only"
            try:
                generated = timestamp(report["generated_at"]) if valid else None
                fresh = generated is not None and -60 <= (now - generated).total_seconds() <= system["update_ttl_seconds"]
            except (ValueError, KeyError, TypeError):
                fresh = False
            if not valid or not fresh:
                checks.append(add(source, "unknown", {"reason": "stale" if valid else "update_metadata_unavailable"}, updates=True)); continue
            summary = report.get("summary", {})
            for name in ("changed", "pin_drift", "source_errors", "outperforms_active"):
                value = count(summary.get(name)) if isinstance(summary, dict) else None
                row = add(source + "." + name, "unknown" if value is None else "degraded" if value else "healthy",
                          {"count": value, "source_report_generated_at": iso(generated)} if value is not None else {"reason": "unsupported_schema"}, updates=True)
                row["expires_at"] = iso(generated + timedelta(seconds=system["update_ttl_seconds"]))
                checks.append(row)
    return checks


UNIT_PROJECTION = '''import json,subprocess
units=UNITS
result={}
fields=('LoadState','ActiveState','SubState','Result','ExecMainStatus','ExecMainStartTimestampMonotonic')
for unit in units:
 try:
  proc=subprocess.run(['systemctl','show',unit]+['-p'+k for k in fields],capture_output=True,text=True,timeout=5)
  if proc.returncode==0 and len(proc.stdout)<4096:
   values=dict(line.split('=',1) for line in proc.stdout.splitlines() if '=' in line)
   result[unit]={k:values.get(k) for k in fields}
 except (OSError,subprocess.TimeoutExpired,ValueError): pass
payload={'unit_states':result}
if MAINTENANCE:
 try:
  import os,stat
  fd=os.open('/var/lib/homecompute-maintenance/report.json',os.O_RDONLY|os.O_NOFOLLOW)
  try:
   info=os.fstat(fd)
   if not stat.S_ISREG(info.st_mode) or info.st_size>1048576: raise ValueError('report budget')
   payload['maintenance_report']=json.loads(os.read(fd,1048577))
  finally: os.close(fd)
 except (OSError,ValueError): pass
print(json.dumps(payload))
'''


HA_FIELDS = {"schema_version", "host", "generated_at", "installed_version", "update_available_count", "unavailable_count"}
HA_PROJECTION = '''import json,sys,urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
try:
 token=TOKEN
 request=urllib.request.Request('http://127.0.0.1:15678/webhook/homecompute-openclaw-ha-metadata',headers={'Authorization':'Bearer '+token},method='GET')
 with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=3) as response:
  if response.status!=200: raise ValueError('unavailable')
  raw=response.read(1025)
  if len(raw)>1024: raise ValueError('oversized')
  data=json.loads(raw)
 if not isinstance(data,dict) or set(data)!=FIELDS: raise ValueError('schema')
 print(json.dumps(data))
except Exception:
 sys.exit(2)
'''


def private_text(path: Path) -> str:
    if not path.is_absolute():
        raise ValueError("private metadata transport path must be absolute")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError("private metadata transport file must be owner-only and bounded")
        raw = os.read(fd, 4097)
        if len(raw) > 4096:
            raise ValueError("private metadata transport file exceeds limit")
        return raw.decode("utf-8")
    finally:
        os.close(fd)


def load_ha_transport(path: Path) -> dict[str, Any]:
    data = json.loads(private_text(path))
    if (not isinstance(data, dict) or set(data) != {"schema_version", "token_file"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1 or not isinstance(data["token_file"], str)
            or not Path(data["token_file"]).is_absolute()):
        raise ValueError("invalid private HA metadata transport")
    return data


def collect(system: dict[str, Any], runner: Callable[..., Any] = homecompute.default_runner,
            *, ha_transport: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not system["enabled"]:
        return None
    if system["collector"] == "homeassistant-metadata":
        if ha_transport is None:
            return None
        if (not isinstance(ha_transport, dict) or set(ha_transport) != {"schema_version", "token_file"}
                or type(ha_transport["schema_version"]) is not int or ha_transport["schema_version"] != 1
                or not isinstance(ha_transport["token_file"], str)):
            raise ValueError("invalid private HA metadata transport")
        token = private_text(Path(ha_transport["token_file"])).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("invalid HA metadata transport token")
        script = HA_PROJECTION.replace("FIELDS", repr(HA_FIELDS)).replace("TOKEN", json.dumps(token))
        result = runner(homecompute.ssh_argv("home-core", "python3 -"), input_text=script, timeout=4)
        if result.returncode != 0 or len(result.stdout) > 1024:
            return None
        try:
            raw = json.loads(result.stdout)
            if not isinstance(raw, dict) or set(raw) != HA_FIELDS:
                return None
            if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["host"] != "home-assistant":
                return None
            if not isinstance(raw["installed_version"], str) or not VERSION.fullmatch(raw["installed_version"]):
                return None
            if any(count(raw[key]) is None for key in ("update_available_count", "unavailable_count")):
                return None
            timestamp(raw["generated_at"])
            return raw
        except (ValueError, KeyError, TypeError):
            return None
    raw = homecompute.remote_status(system["target"], runner=runner)
    if system["units"] or SOURCES.intersection(system["updates"]):
        script = UNIT_PROJECTION.replace("UNITS", json.dumps([x["name"] for x in system["units"]])).replace(
            "MAINTENANCE", repr(bool(SOURCES.intersection(system["updates"]))))
        result = runner(homecompute.ssh_argv(system["target"], "python3 -"), input_text=script, timeout=25)
        if result.returncode == 0 and len(result.stdout) <= 1048576:
            try:
                payload = json.loads(result.stdout)
                if isinstance(payload, dict):
                    raw["unit_states"] = payload.get("unit_states")
                    raw["maintenance_report"] = payload.get("maintenance_report")
            except json.JSONDecodeError: pass
    return raw


def valid_previous(previous: dict[str, Any], keys: set[str]) -> dict[str, dict[str, Any]]:
    if previous.get("schema_version") != 1 or previous.get("document_type") != "system_observation_report":
        raise ValueError("unsupported previous report")
    rows = previous.get("findings")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("invalid previous findings")
    result = {}
    for row in rows:
        fields = {"system_id", "check_id", "stable_key", "category", "status", "severity", "evidence",
                  "observed_at", "expires_at", "episode_started_at", "episode_key"}
        if (not isinstance(row, dict) or set(row) != fields or row.get("stable_key") not in keys
                or row.get("status") not in ("unknown", "degraded")
                or row.get("stable_key") != str(row.get("system_id")) + ":" + str(row.get("check_id"))
                or row.get("category") not in ("health", "updates")
                or row.get("severity") != ("warning" if row.get("status") == "degraded" else "info")):
            raise ValueError("previous finding outside registry")
        for field in ("observed_at", "expires_at", "episode_started_at"):
            timestamp(row[field])
        expected_episode = hashlib.sha256((row["stable_key"] + ":" + row["episode_started_at"]).encode()).hexdigest()
        if row["episode_key"] != expected_episode:
            raise ValueError("invalid previous episode")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict): raise ValueError("invalid previous evidence")
        for key, value in evidence.items():
            valid = ((key in {"count", "candidate_count", "security_count"} and count(value) is not None)
                     or (key in {"available", "failed"} and type(value) is bool)
                     or (key == "state" and value in {"missing", "unhealthy", "healthy", "running", "unknown"})
                     or (key == "reason" and value in REASONS)
                     or (key == "source_report_generated_at" and isinstance(value, str) and iso(timestamp(value)) == value)
                     or (key == "source_digest" and isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value)))
                     or (key == "update_scope" and value in {"installed_packages", "package_catalog"})
                     or (key == "installed_version" and isinstance(value, str) and VERSION.fullmatch(value)))
            if not valid: raise ValueError("previous evidence is not normalized")
        result[row["stable_key"]] = row
    return result


def make_report(registry: dict[str, Any], snapshots: dict[str, Any], now: datetime,
                previous: dict[str, Any] | None = None) -> dict[str, Any]:
    observations = []
    allowed = set()
    for system in registry["systems"]:
        base = {"collection", "installed-version", "update_available_count", "unavailable_count"}
        base.update("container." + x["name"] for x in system["containers"])
        base.update("unit." + x["name"] for x in system["units"])
        base.update(system["updates"])
        base.update(x + ".coverage" for x in SOURCES.intersection(system["updates"]))
        if "package-update-report" in system["updates"]:
            base.add("package-update-report.reboot-required")
        base.update("model-update-report." + x for x in ("changed", "pin_drift", "source_errors", "outperforms_active"))
        allowed.update(system["id"] + ":" + x for x in base)
        observations.extend(normalize(system, snapshots.get(system["id"]), now))
    prior = valid_previous(previous, allowed) if previous else {}
    active = dict(prior)
    changes = []
    for row in observations:
        key = row["stable_key"]
        old = prior.get(key)
        if row["status"] == "healthy":
            if old: changes.append({"stable_key": key, "transition": "resolved", "episode_key": old.get("episode_key")})
            active.pop(key, None); continue
        if row["status"] == "unknown" and old and old["status"] == "degraded":
            # Collection gaps do not turn a known incident into a recovery.
            continue
        row = dict(row)
        row["episode_started_at"] = old["episode_started_at"] if old else iso(now)
        row["episode_key"] = hashlib.sha256((key + ":" + row["episode_started_at"]).encode()).hexdigest()
        if not old or old["status"] != row["status"] or semantic_evidence(old["evidence"]) != semantic_evidence(row["evidence"]):
            changes.append({"stable_key": key, "transition": "opened" if not old else "changed", "episode_key": row["episode_key"]})
        active[key] = row
    # Carried incidents retain their original evidence expiry, never pretend fresh collection.
    findings = [{k: row[k] for k in ("system_id", "check_id", "stable_key", "category", "status", "severity",
                "evidence", "observed_at", "expires_at", "episode_started_at", "episode_key")}
                for _, row in sorted(active.items())]
    return {"schema_version": 1, "document_type": "system_observation_report", "generated_at": iso(now),
            "mode": "observe-only", "automatic_actions": False, "physical_device_actions": False,
            "observations": observations, "findings": findings, "changes": changes,
            "notification_policy": "meaningful_changes_only", "approval_required_for_actions": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "config/system-monitoring.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="operator-only allowlisted SSH reads")
    mode.add_argument("--fixture", type=Path, help="synthetic metadata document with schema_version and systems")
    parser.add_argument("--previous", type=Path, help="previous normalized report, read only")
    parser.add_argument("--ha-transport", type=Path, help="owner-only metadata webhook transport config; HA must also be enabled in the registry")
    args = parser.parse_args(argv)
    try:
        registry = validate_registry(read_json(args.registry))
        ha_transport = load_ha_transport(args.ha_transport) if args.ha_transport else None
        if args.fixture:
            fixture = read_json(args.fixture)
            if fixture.get("schema_version") != 1 or not isinstance(fixture.get("systems"), dict):
                raise ValueError("unsupported fixture")
            snapshots = fixture["systems"]
        else:
            snapshots = {}
            for system in registry["systems"]:
                try: snapshots[system["id"]] = collect(system, ha_transport=ha_transport)
                except (homecompute.OperatorError, OSError, subprocess.TimeoutExpired, ValueError):
                    snapshots[system["id"]] = None
        report = make_report(registry, snapshots, datetime.now(timezone.utc), read_json(args.previous) if args.previous else None)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("Invalid or unavailable monitoring metadata; no action performed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
