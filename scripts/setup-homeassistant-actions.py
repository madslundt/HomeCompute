#!/usr/bin/env python3
"""Root-only provisioner for the independent bounded HA approval controller.

Does not approve proposals or call HA services. Imports one separately
authenticated n8n workflow using the existing HA credential; preserves existing
workflows, credentials, and the read-only HA adapter. Agent adapter restart to
pick up supplementary socket group membership remains an operator step.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys

TEMPLATE_SHA256 = '05c3d9ce159101109d33befe1854a08299920e4c462407b55ab12079d97ecc18'
CONTAINER = 'homecompute-automation-n8n-1'
DATABASE = Path('/srv/state/automation/n8n/database.sqlite')
WORKFLOW, CREDENTIAL = 'hcApprovedHA20261012', 'hcApprovedHAExecutor20261012'
HA_CREDENTIAL = 'Xk0jp9yhKbeuvcVH'
STATE = Path('/var/lib/homecompute-ha-actions')
INSTALL = Path('/opt/homecompute/ha-actions')
UNIT = Path('/etc/systemd/system.attached/homecompute-ha-actions.service')
USER, ADAPTER = 'homecompute-ha-actions', 'homecompute-openclaw'


def call(args: list[str], data: str | None = None) -> None:
    result = subprocess.run(args, input=data, text=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=120)
    if result.returncode:
        raise ValueError('trusted setup operation failed; inspect private state')


def metadata(db: sqlite3.Connection) -> dict[str, list]:
    return {r[0]: list(r[1:]) for r in db.execute('SELECT id,active,versionId,activeVersionId,updatedAt FROM workflow_entity')}


def validate_template(path: Path) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('exact reviewed workflow template required')
    flow = json.loads(raw)
    if flow['active'] is not False or len(flow['nodes']) != 11:
        raise ValueError('inert finite workflow required')
    gate = flow['nodes'][1]
    webhook = flow['nodes'][0]['parameters']
    if (webhook.get('httpMethod') != 'POST' or webhook.get('path') != 'homecompute-approved-ha-actions'
            or webhook.get('authentication') != 'headerAuth' or 'configured=false' not in gate['parameters']['jsCode']):
        raise ValueError('fixed authenticated gated route required')
    return flow


def write_private(path: Path, contents: str, uid: int, gid: int) -> None:
    with path.open('x') as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), 0o600)
        os.fchown(stream.fileno(), uid, gid)


def boot_link(unit: Path) -> None:
    directory = unit.parent / 'multi-user.target.wants'
    link = directory / unit.name
    if directory.is_symlink():
        raise ValueError('unsafe attached boot directory')
    directory.mkdir(mode=0o755, exist_ok=True)
    mode = directory.lstat()
    if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.geteuid() or mode.st_mode & 0o022:
        raise ValueError('unsafe attached boot directory ownership')
    if link.is_symlink():
        if os.readlink(link) != str(unit):
            raise ValueError('unmanaged boot dependency')
    elif link.exists():
        raise ValueError('boot dependency must be a managed symlink')
    else:
        link.symlink_to(unit)


def provision(template: Path, controller: Path) -> dict:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('root on home-core required')
    flow = validate_template(template)
    if not controller.is_file() or controller.is_symlink():
        raise ValueError('reviewed controller source required')
    os.umask(0o077)
    for path in (STATE, INSTALL, UNIT):
        if path.is_symlink() or path.exists():
            raise ValueError('existing controller installation requires reconciliation')
    if UNIT.parent.is_symlink():
        raise ValueError('attached unit directory symlink refused')
    UNIT.parent.mkdir(mode=0o755, exist_ok=True)
    attached = UNIT.parent.stat()
    if attached.st_uid != 0 or attached.st_mode & 0o022:
        raise ValueError('private root-owned attached unit directory required')
    pwd.getpwnam(ADAPTER)
    with sqlite3.connect('file:' + str(DATABASE) + '?mode=ro', uri=True) as db:
        if db.execute('SELECT id FROM workflow_entity WHERE id=?', (WORKFLOW,)).fetchone():
            raise ValueError('existing action workflow requires reconciliation')
        if db.execute('SELECT id FROM credentials_entity WHERE id=?', (CREDENTIAL,)).fetchone():
            raise ValueError('existing executor credential requires reconciliation')
        if db.execute('SELECT type FROM credentials_entity WHERE id=?', (HA_CREDENTIAL,)).fetchone() != ('homeAssistantApi',):
            raise ValueError('existing HA credential unavailable')
        owner = db.execute('SELECT projectId FROM shared_workflow WHERE workflowId=?', ('hcOpenClawHA20261010',)).fetchone()
        if not owner:
            raise ValueError('existing read-only HA project unavailable')
        project = owner[0]
        before = metadata(db)
        credentials_before = list(db.execute('SELECT id,name,type,updatedAt FROM credentials_entity ORDER BY id'))
        backup = Path('/srv/backups/ha-actions-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        backup.mkdir(mode=0o700, parents=True)
        with sqlite3.connect(backup / 'n8n-before.sqlite3') as target:
            db.backup(target)
        write_private(backup / 'workflow-metadata.json', json.dumps(before), 0, 0)
    try:
        user = pwd.getpwnam(USER)
    except KeyError:
        call(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', str(STATE),
              '--shell', '/run/current-system/sw/bin/nologin', USER])
        user = pwd.getpwnam(USER)
    if user.pw_uid == 0 or user.pw_gid != grp.getgrnam(USER).gr_gid:
        raise ValueError('dedicated unprivileged controller user/group required')
    call(['usermod', '-a', '-G', USER, ADAPTER])
    STATE.mkdir(mode=0o700)
    os.chown(STATE, user.pw_uid, user.pw_gid)
    INSTALL.mkdir(mode=0o755, parents=True)
    os.chmod(INSTALL, 0o755)
    shutil.copyfile(controller, INSTALL / 'homeassistant_actions.py')
    os.chmod(INSTALL / 'homeassistant_actions.py', 0o644)
    token = secrets.token_urlsafe(48)
    write_private(STATE / 'executor-token', token + '\n', user.pw_uid, user.pw_gid)
    credential = [{'id': CREDENTIAL, 'name': 'HA controller executor only', 'type': 'httpHeaderAuth',
                   'data': {'name': 'Authorization', 'value': 'Bearer ' + token}}]
    flow.update(id=WORKFLOW, active=False)
    flow['nodes'][1]['parameters']['jsCode'] = flow['nodes'][1]['parameters']['jsCode'].replace('configured=false', 'configured=true', 1)
    temporary = ['/tmp/hc-approved-ha-workflow.json', '/tmp/hc-approved-ha-credential.json']
    try:
        for path, value in zip(temporary, ([flow], credential)):
            code = 'const fs=require("fs");fs.writeFileSync(' + json.dumps(path) + ',fs.readFileSync(0),{mode:0o600});'
            call(['docker', 'exec', '-i', CONTAINER, 'node', '-e', code], json.dumps(value))
        call(['docker', 'exec', CONTAINER, 'n8n', 'import:credentials', '--input=' + temporary[1], '--projectId=' + project])
        call(['docker', 'exec', CONTAINER, 'n8n', 'import:workflow', '--input=' + temporary[0], '--projectId=' + project])
        call(['docker', 'exec', CONTAINER, 'n8n', 'publish:workflow', '--id=' + WORKFLOW])
    finally:
        call(['docker', 'exec', CONTAINER, 'node', '-e',
              'for(const p of ' + json.dumps(temporary) + ')require("fs").rmSync(p,{force:true});'])
    with sqlite3.connect('file:' + str(DATABASE) + '?mode=ro', uri=True) as db:
        after = metadata(db)
        if after != before | {WORKFLOW: after.get(WORKFLOW)}:
            raise ValueError('existing workflows changed; preserve private backup')
        credentials_after = list(db.execute('SELECT id,name,type,updatedAt FROM credentials_entity WHERE id!=? ORDER BY id', (CREDENTIAL,)))
        if credentials_after != credentials_before:
            raise ValueError('existing credentials changed; preserve private backup')
        active = db.execute('SELECT active,versionId,activeVersionId FROM workflow_entity WHERE id=?', (WORKFLOW,)).fetchone()
        if not active or not active[0] or active[1] != active[2]:
            raise ValueError('approved workflow publication unavailable')
    python = shutil.which('python3')
    if not python or any(ch in python for ch in ' \n\r'):
        raise ValueError('fixed Python executable required')
    unit = f'''[Unit]
Description=HomeCompute bounded Home Assistant action approvals
After=network-online.target docker.service
Requires=docker.service

[Service]
User={USER}
Group={USER}
RuntimeDirectory=homecompute-ha-actions
RuntimeDirectoryMode=0750
UMask=0077
ExecStart={python} {INSTALL}/homeassistant_actions.py serve
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ReadWritePaths={STATE} /run/homecompute-ha-actions
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressDeny=any
IPAddressAllow=127.0.0.1/32
CapabilityBoundingSet=
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
'''
    UNIT.write_text(unit)
    os.chmod(UNIT, 0o644)
    boot_link(UNIT)
    call(['systemd-analyze', 'verify', str(UNIT)])
    call(['docker', 'restart', CONTAINER])
    call(['systemctl', 'daemon-reload'])
    call(['systemctl', 'start', UNIT.name])
    return {'provisioned': True, 'workflow': WORKFLOW, 'controller': UNIT.name,
            'existing_workflows_preserved': len(before), 'existing_credentials_preserved': len(credentials_before),
            'backup': str(backup), 'ha_service_calls': 0,
            'adapter_restart_required_for_socket_group': True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--controller', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.template, args.controller)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print('HA action setup incomplete; preserve private credentials and backup for reconciliation.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
