#!/usr/bin/env python3
"""Install the source-owned authenticated Android notification ingress on home-core."""
from __future__ import annotations

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
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'automations/android-notifications/n8n-workflow.json'
TEMPLATE_SHA256 = 'ef4a867bffdb6bdfc396f435150831611ab650cff3eef0db0af5d3c549c12920'
N8N = 'homecompute-automation-n8n-1'
DATABASE = Path('/srv/state/automation/n8n/database.sqlite')
WORKFLOW = 'hcAndroidNotifications20261110'
CREDENTIAL = 'hcAndroidNotification20261110'
CREDENTIAL_NAME = 'Android notification ingress'
SECRET = Path('/run/secrets/android/n8n-webhook-token')
BACKUPS = Path('/srv/backups/android-notifications')
WEBHOOK = 'http://127.0.0.1:15678/webhook/android-notifications'


def call(args: list[str], data: str | None = None) -> str:
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('Android notification provisioning failed; inspect private setup state')
    return result.stdout


def read_secret() -> str:
    info = SECRET.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 4096):
        raise ValueError('root-owned private Android webhook credential required')
    value = SECRET.read_text().strip()
    if not 32 <= len(value) <= 4096 or any(c.isspace() for c in value):
        raise ValueError('Android webhook credential is invalid')
    return value


def load_template() -> dict:
    raw = TEMPLATE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('exact reviewed Android notification workflow required')
    flow = json.loads(raw)
    if flow.get('active') is not False or len(flow.get('nodes', [])) != 2:
        raise ValueError('inactive two-node notification ingress required')
    nodes = {node.get('name'): node for node in flow['nodes']}
    webhook = nodes.get('Android notification ingress')
    normalizer = nodes.get('Validate and normalize')
    p = webhook.get('parameters', {}) if webhook else {}
    if (webhook is None or normalizer is None or webhook.get('type') != 'n8n-nodes-base.webhook'
            or p.get('httpMethod') != 'POST' or p.get('path') != 'android-notifications'
            or p.get('authentication') != 'headerAuth' or p.get('responseMode') != 'onReceived'
            or normalizer.get('type') != 'n8n-nodes-base.code'
            or 'com.facebook.orca' not in normalizer.get('parameters', {}).get('jsCode', '')
            or set(flow.get('connections', {})) != {'Android notification ingress'}
            or any(node.get('type', '').lower().endswith(('send', 'telegram', 'mattermost', 'email'))
                   for node in flow['nodes'])):
        raise ValueError('workflow does not match the fixed authenticated notification-only graph')
    return flow


def probe(token: str) -> None:
    event = {'event_type': 'NOTIFICATION_POSTED', 'device_id': 'android-tablet',
             'application_package': 'com.facebook.orca', 'sender': 'synthetic',
             'notification_text': 'synthetic activation check', 'timestamp': 1,
             'notification_key': 'homecompute-android-activation-probe', 'metadata': {}}
    request = urllib.request.Request(WEBHOOK, json.dumps(event).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status not in {200, 201, 202, 204}:
                raise ValueError('authenticated Android webhook did not accept a synthetic event')
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise ValueError('authenticated Android webhook did not accept a synthetic event') from error


def provision() -> dict:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('root on home-core required')
    os.umask(0o077)
    flow = load_template()
    token = read_secret()
    if not DATABASE.is_file() or DATABASE.is_symlink():
        raise ValueError('existing n8n database required')
    with sqlite3.connect('file:' + str(DATABASE) + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        projects = db.execute("SELECT p.id FROM project p JOIN project_relation r ON r.projectId=p.id "
            "JOIN user u ON u.id=r.userId WHERE p.type='personal' AND u.roleSlug='global:owner' "
            "AND r.role='project:personalOwner'").fetchall()
        if len(projects) != 1:
            raise ValueError('exact n8n owner project required')
        project = projects[0][0]
        if not isinstance(project, str) or not project:
            raise ValueError('n8n owner project identity is invalid')
        if db.execute('SELECT id,name FROM workflow_entity WHERE id=? OR name=?',
                      (WORKFLOW, flow['name'])).fetchall():
            raise ValueError('existing Android notification workflow requires reconciliation')
        if db.execute('SELECT id,name FROM credentials_entity WHERE id=? OR name=?',
                      (CREDENTIAL, CREDENTIAL_NAME)).fetchall():
            raise ValueError('existing Android notification credential requires reconciliation')
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        if BACKUPS.is_symlink() or BACKUPS.stat().st_uid != 0 or BACKUPS.stat().st_mode & 0o077:
            raise ValueError('root-owned private n8n backup directory required')
        backup = BACKUPS / ('n8n-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                            + '-' + secrets.token_hex(4))
        backup.mkdir(mode=0o700)
        with sqlite3.connect(backup / 'database.sqlite3') as target:
            os.chmod(backup / 'database.sqlite3', 0o600)
            db.backup(target)
    credential = [{'id': CREDENTIAL, 'name': CREDENTIAL_NAME, 'type': 'httpHeaderAuth',
                   'data': {'name': 'Authorization', 'value': 'Bearer ' + token}}]
    flow.update(id=WORKFLOW, active=False)
    flow['nodes'][0]['credentials']['httpHeaderAuth'] = {'id': CREDENTIAL, 'name': CREDENTIAL_NAME}
    temporary = ['/tmp/homecompute-android-workflow.json', '/tmp/homecompute-android-credential.json']
    try:
        for path, value in zip(temporary, ([flow], credential)):
            writer = 'const fs=require("fs");fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600,flag:"wx"})'
            call(['docker', 'exec', '-i', N8N, 'node', '-e', writer, path], json.dumps(value))
        call(['docker', 'exec', N8N, 'n8n', 'import:credentials', '--input=' + temporary[1],
              '--projectId=' + project])
        call(['docker', 'exec', N8N, 'n8n', 'import:workflow', '--input=' + temporary[0],
              '--projectId=' + project])
        call(['docker', 'exec', N8N, 'n8n', 'publish:workflow', '--id=' + WORKFLOW])
    finally:
        call(['docker', 'exec', N8N, 'node', '-e',
              'for(const p of process.argv.slice(1))require("fs").rmSync(p,{force:true})', *temporary])
    with sqlite3.connect('file:' + str(DATABASE) + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT active,versionId,activeVersionId FROM workflow_entity WHERE id=?',
                         (WORKFLOW,)).fetchone()
        credential_row = db.execute('SELECT id,name,type,data FROM credentials_entity WHERE id=?',
                                    (CREDENTIAL,)).fetchone()
        shared = db.execute('SELECT projectId,role FROM shared_credentials WHERE credentialsId=?',
                            (CREDENTIAL,)).fetchall()
        if (not row or not row[0] or row[1] != row[2] or not credential_row
                or credential_row[:3] != (CREDENTIAL, CREDENTIAL_NAME, 'httpHeaderAuth')
                or token in credential_row[3] or shared != [(project, 'credential:owner')]):
            raise ValueError('Android workflow activation or encrypted credential readback failed')
    probe(token)
    token = ''
    return {'workflow_active': True, 'authenticated_ingress_verified': True,
            'workflow_id': WORKFLOW, 'credential_id': CREDENTIAL, 'backup': str(backup),
            'outbound_actions': 0}


if __name__ == '__main__':
    try:
        print(json.dumps(provision(), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print('Android notification setup incomplete; preserve the private database backup for reconciliation.',
              file=sys.stderr)
        raise SystemExit(2)
