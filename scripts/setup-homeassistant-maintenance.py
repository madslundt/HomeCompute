#!/usr/bin/env python3
"""Install the finite root HA maintenance route without approving or installing updates.

Imports one separately authenticated workflow using the retained HA credential.
Preserves existing workflow/credential records, takes an online backup, and
restarts n8n to register the new route. Existing installs require reconciliation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys

TEMPLATE_SHA256 = "0432be11c768b3118d1182e4bd0998efe2685c2da8f2bb06cfddf2ce7559c52b"
CONTAINER = "homecompute-automation-n8n-1"
DATABASE = Path("/srv/state/automation/n8n/database.sqlite")
WORKFLOW, CREDENTIAL = "hcHAMaintenance20261013", "hcHAMaintenanceExecutor20261013"
HA_CREDENTIAL = "Xk0jp9yhKbeuvcVH"
STATE = Path("/var/lib/homecompute-ha-maintenance")
HELPER = Path("/usr/local/libexec/homecompute-home-assistant-maintenance")


def call(args: list[str], data: str | None = None) -> None:
    result = subprocess.run(args, input=data, text=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=120)
    if result.returncode:
        raise ValueError("trusted HA maintenance setup failed; inspect private server state")


def metadata(db: sqlite3.Connection) -> dict:
    return {r[0]: list(r[1:]) for r in db.execute("SELECT id,active,versionId,activeVersionId,updatedAt FROM workflow_entity")}


def validate_template(path: Path) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("exact reviewed maintenance template required")
    flow = json.loads(raw)
    if flow.get("active") is not False or len(flow.get("nodes", [])) != 9:
        raise ValueError("inert finite workflow required")
    return flow


def trusted_parent(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("root-owned fixed installation directories required")
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unsafe installation directory")


def write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), mode)
        os.fchown(handle.fileno(), 0, 0)


def provision(template: Path, wrapper: Path) -> dict:
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "home-core":
        raise ValueError("root on home-core required")
    flow = validate_template(template)
    if wrapper.is_symlink() or not wrapper.is_file():
        raise ValueError("trusted maintenance wrapper source required")
    code = wrapper.read_bytes()
    if not code.startswith(b"#!/run/current-system/sw/bin/python3 -I\n"):
        raise ValueError("fixed isolated Python interpreter required")
    compile(code, str(wrapper), "exec")
    os.umask(0o077)
    if STATE.exists() or STATE.is_symlink() or HELPER.exists() or HELPER.is_symlink():
        raise ValueError("existing maintenance installation requires reconciliation")
    trusted_parent(STATE.parent)
    HELPER.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    trusted_parent(HELPER.parent)
    with sqlite3.connect("file:" + str(DATABASE) + "?mode=ro", uri=True) as db:
        if (db.execute("SELECT id FROM workflow_entity WHERE id=?", (WORKFLOW,)).fetchone()
                or db.execute("SELECT id FROM credentials_entity WHERE id=?", (CREDENTIAL,)).fetchone()):
            raise ValueError("existing maintenance route requires reconciliation")
        if db.execute("SELECT type FROM credentials_entity WHERE id=?", (HA_CREDENTIAL,)).fetchone() != ("homeAssistantApi",):
            raise ValueError("retained HA credential unavailable")
        owner = db.execute("SELECT projectId FROM shared_workflow WHERE workflowId=?", ("hcOpenClawHA20261010",)).fetchone()
        if not owner:
            raise ValueError("retained HA project unavailable")
        project = owner[0]
        before = metadata(db)
        credentials_before = list(db.execute("SELECT id,name,type,updatedAt FROM credentials_entity ORDER BY id"))
        backup = Path("/srv/backups/ha-maintenance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        backup.mkdir(mode=0o700, parents=True)
        with sqlite3.connect(backup / "n8n-before.sqlite3") as destination:
            db.backup(destination)
        write_new(backup / "workflow-metadata.json", json.dumps(before).encode())
    STATE.mkdir(mode=0o700)
    token = secrets.token_urlsafe(48)
    write_new(STATE / "executor-token", (token + "\n").encode())
    write_new(HELPER, code, 0o755)
    flow.update(id=WORKFLOW, active=False)
    flow["nodes"][1]["parameters"]["jsCode"] = flow["nodes"][1]["parameters"]["jsCode"].replace("configured=false", "configured=true", 1)
    credential = [{"id": CREDENTIAL, "name": "HA maintenance root executor only", "type": "httpHeaderAuth",
                   "data": {"name": "Authorization", "value": "Bearer " + token}}]
    paths = ["/tmp/hc-ha-maintenance-workflow.json", "/tmp/hc-ha-maintenance-credential.json"]
    try:
        for path, value in zip(paths, ([flow], credential)):
            js = "const fs=require('fs');fs.writeFileSync(" + json.dumps(path) + ",fs.readFileSync(0),{mode:0o600});"
            call(["docker", "exec", "-i", CONTAINER, "node", "-e", js], json.dumps(value))
        call(["docker", "exec", CONTAINER, "n8n", "import:credentials", "--input=" + paths[1], "--projectId=" + project])
        call(["docker", "exec", CONTAINER, "n8n", "import:workflow", "--input=" + paths[0], "--projectId=" + project])
        call(["docker", "exec", CONTAINER, "n8n", "publish:workflow", "--id=" + WORKFLOW])
    finally:
        call(["docker", "exec", CONTAINER, "node", "-e", "for(const p of " + json.dumps(paths) + ")require('fs').rmSync(p,{force:true});"])
    with sqlite3.connect("file:" + str(DATABASE) + "?mode=ro", uri=True) as db:
        after = metadata(db)
        remaining = list(db.execute("SELECT id,name,type,updatedAt FROM credentials_entity WHERE id!=? ORDER BY id", (CREDENTIAL,)))
        if after != before | {WORKFLOW: after.get(WORKFLOW)} or remaining != credentials_before:
            raise ValueError("existing records changed; preserve private backup")
        active = db.execute("SELECT active,versionId,activeVersionId FROM workflow_entity WHERE id=?", (WORKFLOW,)).fetchone()
        if not active or not active[0] or active[1] != active[2]:
            raise ValueError("new maintenance publication unavailable")
    call(["docker", "restart", CONTAINER])
    return {"provisioned": True, "workflow": WORKFLOW, "wrapper": str(HELPER),
            "existing_workflows_preserved": len(before), "existing_credentials_preserved": len(credentials_before),
            "backup": str(backup), "ha_updates_executed": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.template, args.wrapper)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print("HA maintenance setup incomplete; retain private state and backup for reconciliation.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
