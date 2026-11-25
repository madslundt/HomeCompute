#!/usr/bin/env python3
"""Check OpenBot's gateway TLS, notify the owner, and back up Caddy's CA."""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import NamedTemporaryFile
from urllib import request
from uuid import uuid4

STATE = Path("/var/lib/homecompute/certificate-maintenance")
CA_DIRECTORY = Path("/srv/state/control-plane/caddy-data/caddy/pki/authorities/local")
CA_NAMES = ("root.crt", "root.key", "intermediate.crt", "intermediate.key")
INBOX_SECRET = Path("/run/secrets/automation/inbox-webhook-env")
INBOX_ORIGIN = "http://192.168.30.30"
INBOX_OWNER = "madslundt"
CONTAINER = "homecompute-openbot"
HOSTS = ("mcp.home.arpa", "llm.home.arpa")
GUEST_RESULTS = {"container-unavailable", "gateway-mapping-missing", "agent-runtime-unavailable",
                 "agent-trust-bundle-missing", "gateway-https-failed"}
BUN_PROBE = r'''
try {
  if (process.env.SSL_CERT_FILE !== "/run/homecompute/agent-ca-bundle.pem"
      || !(await Bun.file(process.env.SSL_CERT_FILE).exists())) {
    console.log("agent-trust-bundle-missing");
    process.exit(1);
  }
  const response = await fetch("https://" + process.argv.at(-1) + "/v1/models", {
    signal: AbortSignal.timeout(9000), redirect: "error",
  });
  if (response.status !== 401 && response.status !== 200) throw Error();
  console.log("https-ok");
} catch {
  console.log("gateway-https-failed");
  process.exit(1);
}
'''
RUNTIME_PROBE = r'''
set -eu
runtime=''
for path in /data/.config/OpenBot/provider-runtimes/bun/linux-*/[0-9]*/bin/bun; do
  if [ -x "$path" ]; then printf '%s\n' "$path"; fi
done | sort -V | tail -n 1
'''


def command(args, timeout):
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def guest_probe() -> str:
    inspected = command(["docker", "inspect", "--format", "{{json .HostConfig.ExtraHosts}}", CONTAINER], 4)
    if inspected.returncode:
        return "container-unavailable"
    entries = json.loads(inspected.stdout)
    host = next((host for host in HOSTS if any(
        item in {f"{host}:10.77.20.1", f"{host}=10.77.20.1"} for item in entries or [])), None)
    if host is None:
        return "gateway-mapping-missing"
    runtime = command(["docker", "exec", CONTAINER, "sh", "-c", RUNTIME_PROBE], 5).stdout.strip()
    if not re.fullmatch(r"/data/\.config/OpenBot/provider-runtimes/bun/linux-[a-z0-9]+/[0-9]+\.[0-9]+\.[0-9]+/bin/bun", runtime):
        return "agent-runtime-unavailable"
    result = command(["docker", "exec", CONTAINER, "env", "-u", "NODE_EXTRA_CA_CERTS",
                      "-u", "NODE_TLS_REJECT_UNAUTHORIZED", runtime, "-e", BUN_PROBE, host], 13)
    output = result.stdout.strip()
    if result.returncode == 0 and output == "https-ok":
        return output
    return output if output in GUEST_RESULTS else "gateway-https-failed"


def probe() -> str:
    try:
        result = command([
            "ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={STATE / 'probe_known_hosts'}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "ConnectTimeout=5", "-i", str(STATE / "probe-key"),
            "hermes-operator@10.77.20.2", "probe",
        ], 30)
    except (OSError, subprocess.TimeoutExpired):
        return "agent-probe-unavailable"
    output = result.stdout.strip()
    if result.returncode == 0 and output == "https-ok":
        return output
    return output if output in GUEST_RESULTS else "agent-probe-unavailable"


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def inbox(payload: dict) -> dict:
    try:
        keys = re.findall(r"^INBOX_WEBHOOK_ID=([A-Za-z0-9_-]{32,256})$",
                          INBOX_SECRET.read_text(), re.MULTILINE)
        if len(keys) != 1:
            raise ValueError("invalid Inbox secret")
        opener = request.build_opener(request.ProxyHandler({}), NoRedirects())
        message = request.Request(f"{INBOX_ORIGIN}/api/webhook/{keys[0]}",
                                  data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"})
        with opener.open(message, timeout=8) as response:
            body = response.read(65537)
            if response.status not in (200, 201) or len(body) > 65536:
                raise ValueError("invalid Inbox response")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("invalid Inbox response")
        return result
    except (OSError, ValueError):
        raise RuntimeError("Home Assistant Inbox request failed") from None


def recipient() -> str:
    users = inbox({"action": "recipients"}).get("users", [])
    if not isinstance(users, list):
        raise RuntimeError("Home Assistant Inbox directory is unavailable")
    owners = [user.get("id") for user in users if isinstance(user, dict)
              and user.get("name") == INBOX_OWNER]
    if len(owners) != 1 or not isinstance(owners[0], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owners[0]):
        raise RuntimeError("Home Assistant Inbox owner is unavailable or ambiguous")
    return owners[0]


def save_incident(path: Path, value: dict) -> None:
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    os.replace(temporary, path)


def monitor() -> bool:
    result = probe()
    incident_path = STATE / "openbot-incident.json"
    incident = json.loads(incident_path.read_text()) if incident_path.exists() else None
    if incident is None and result != "https-ok":
        incident = {"id": f"openbot-tls-{uuid4().hex}", "reason": result, "notified": False}
        save_incident(incident_path, incident)
    if incident and not incident["notified"]:
        reason = incident["reason"].replace("-", " ")
        receipt = inbox({
            "action": "ingest", "title": "OpenBot HTTPS check failed",
            "body": f"The OpenBot agent could not verify its LiteLLM gateway. Check result: {reason}.",
            "category": "agents", "source": "homecompute-openbot-tls",
            "recipients": {"type": "users", "user_ids": [recipient()]},
            "priority": "normal", "send_push": True, "external_id": incident["id"],
        })
        if not receipt.get("id"):
            raise RuntimeError("Home Assistant Inbox did not confirm the alert")
        incident["notified"] = True
        save_incident(incident_path, incident)
        print("OpenBot HTTPS failure delivered to Home Assistant Inbox")
    if result == "https-ok":
        incident_path.unlink(missing_ok=True)
    print(f"OpenBot HTTPS check: {result}", flush=True)
    return result == "https-ok"


def ca_hashes() -> dict[Path, bytes]:
    if CA_DIRECTORY.resolve() != CA_DIRECTORY:
        raise ValueError("Caddy CA directory must be a real path")
    digests = {}
    for name in CA_NAMES:
        path = CA_DIRECTORY / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
                or info.st_nlink != 1 or not 0 < info.st_size <= 16384):
            raise ValueError("Caddy CA files must be bounded root-only regular files")
        digests[path] = hashlib.sha256(path.read_bytes()).digest()
    return digests


def backup_ca() -> None:
    spec = importlib.util.spec_from_file_location("nas_backup", Path(__file__).with_name("nas-recovery-backup.py"))
    assert spec and spec.loader
    nas = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nas)
    nas.private_file(nas.STATE / "backup-authorized")
    nas.require_mount(nas.load_configuration(nas.CONFIG, mount_only=True))
    digests = ca_hashes()
    restic = partial(nas.restic, repository=nas.MOUNT / "restic-homecompute-selected")
    output = restic(["--retry-lock", "5m", "backup", "--json", "--tag", "homecompute-caddy-ca",
                     *map(str, digests)])
    summaries = [item["snapshot_id"] for line in output.splitlines()
                 if (item := json.loads(line)).get("message_type") == "summary"]
    if len(summaries) != 1 or not re.fullmatch(r"[0-9a-f]{64}", summaries[0]):
        raise RuntimeError("Restic did not confirm a completed Caddy CA snapshot")
    snapshot = summaries[0]
    for path, expected in digests.items():
        restored = restic(["--retry-lock", "5m", "dump", snapshot, str(path)])
        if hashlib.sha256(restored.encode()).digest() != expected:
            raise RuntimeError("Caddy CA snapshot did not match the live files; retry the backup")
    if ca_hashes() != digests:
        raise RuntimeError("Caddy CA files changed during the snapshot; retry the backup")
    nas.record("last-caddy-ca-backup.json", {"snapshot": snapshot, "files_verified": len(digests)})
    print("Encrypted Caddy CA backup verified by reading all four files from Restic")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "check-probe", "monitor", "check-recipient", "backup-ca"))
    action = parser.parse_args().action
    os.umask(0o077)
    if action == "probe":
        try:
            result = guest_probe()
        except subprocess.TimeoutExpired:
            result = "agent-runtime-unavailable"
        print(result)
        return 0 if result == "https-ok" else 1
    if action == "monitor":
        return 0 if monitor() else 1
    if action == "check-probe":
        result = probe()
        print(f"OpenBot HTTPS check: {result}")
        return 0 if result == "https-ok" else 1
    if action == "check-recipient":
        recipient()
        print("Home Assistant Inbox owner verified")
    else:
        backup_ca()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
