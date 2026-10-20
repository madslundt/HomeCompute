#!/usr/bin/env python3
"""Provision only the finite HA GET route and private transport on home-core.

Reuses the existing n8n Home Assistant credential. Creates one input-free reader
credential and workflow; no HA service/state writes or Telegram sends. Existing
workflow records and credentials are untouched. Run before core communication
activation, then preserve all receiver/outbox state during its normal restart.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import sqlite3
import subprocess
import sys
from typing import Any

CONTAINER = 'homecompute-automation-n8n-1'
DATABASE = Path('/srv/state/automation/n8n/database.sqlite')
CONFIG = Path('/var/lib/homecompute-openclaw/config')
WORKFLOW = 'hcOpenClawHA20261010'
CREDENTIAL = 'hcOpenClawHAReader20261010'


def call(args: list[str], *, data: str | None = None) -> None:
    result = subprocess.run(args, input=data, text=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=90)
    if result.returncode:
        raise ValueError('trusted n8n operation failed; inspect private server state')


def metadata(db: sqlite3.Connection) -> dict[str, list[Any]]:
    return {row[0]: list(row[1:]) for row in db.execute('SELECT id,active,versionId,activeVersionId,updatedAt FROM workflow_entity')}


def container_file(name: str, value: Any) -> str:
    path = '/tmp/' + name
    code = 'const fs=require("fs");fs.writeFileSync(' + json.dumps(path) + ',fs.readFileSync(0),{mode:0o600});'
    call(['docker', 'exec', '-i', CONTAINER, 'node', '-e', code], data=json.dumps(value))
    return path


def validate_template(flow: dict[str, Any]) -> None:
    types = [node['type'] for node in flow['nodes']]
    expected = ['n8n-nodes-base.webhook', 'n8n-nodes-base.code', 'n8n-nodes-base.homeAssistant',
                'n8n-nodes-base.homeAssistant', 'n8n-nodes-base.code', 'n8n-nodes-base.respondToWebhook']
    if types != expected or flow.get('active') is not False:
        raise ValueError('finite reviewed HA node graph required')
    names = [node['name'] for node in flow['nodes']]
    expected_connections = {names[i]: {'main': [[{'node': names[i + 1], 'type': 'main', 'index': 0}]]} for i in range(5)}
    if flow['connections'] != expected_connections:
        raise ValueError('input gate and finite projection must precede response')
    webhook = flow['nodes'][0]['parameters']
    if (webhook.get('httpMethod') != 'GET' or webhook.get('path') != 'homecompute-openclaw-ha-metadata'
            or webhook.get('authentication') != 'headerAuth' or webhook.get('responseMode') != 'responseNode'):
        raise ValueError('fixed authenticated input-free HA GET required')
    for node, resource, operation in zip(flow['nodes'][2:4], ('config', 'state'), ('get', 'getAll')):
        if (node['parameters'].get('resource') != resource or node['parameters'].get('operation') != operation
                or node['credentials']['homeAssistantApi']['id'] != 'Xk0jp9yhKbeuvcVH'
                or node.get('retryOnFail') is not False):
            raise ValueError('only existing credential config/states GETs allowed')
    settings = flow['settings']
    if (settings.get('saveDataSuccessExecution') != 'none' or settings.get('saveDataErrorExecution') != 'none'
            or any(settings.get(k) is not False for k in ('saveManualExecutions', 'saveExecutionProgress', 'availableInMCP'))
            or settings.get('executionTimeout') != 12
            or 'configured=false' not in flow['nodes'][1]['parameters']['jsCode']):
        raise ValueError('closed source gate and no payload retention required')


def provision(template: Path) -> dict[str, Any]:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('root on home-core required')
    os.umask(0o077)
    info = pwd.getpwnam('homecompute-openclaw')
    for path in (CONFIG, CONFIG / 'home-assistant.json', CONFIG / 'ha-metadata-reader-token', CONFIG / 'system-monitoring.json'):
        if path.is_symlink():
            raise ValueError('fixed private paths required')
    if (CONFIG / 'home-assistant.json').exists() or (CONFIG / 'ha-metadata-reader-token').exists():
        raise ValueError('existing HA transport must be reconciled; refusing to replace credentials')
    db = sqlite3.connect('file:' + str(DATABASE) + '?mode=ro', uri=True)
    if db.execute('SELECT id FROM workflow_entity WHERE id=?', (WORKFLOW,)).fetchone():
        raise ValueError('existing HA route must be reconciled')
    if db.execute('SELECT type FROM credentials_entity WHERE id=?', ('Xk0jp9yhKbeuvcVH',)).fetchone() != ('homeAssistantApi',):
        raise ValueError('existing HA credential unavailable')
    if db.execute('SELECT id FROM credentials_entity WHERE id=?', (CREDENTIAL,)).fetchone():
        raise ValueError('existing reader credential must be reconciled')
    owner = db.execute('SELECT projectId FROM shared_workflow WHERE workflowId=?', ('hcOpenClawTelegram20261009',)).fetchone()
    if not owner:
        raise ValueError('existing sender project unavailable')
    project = owner[0]
    flow = json.loads(template.read_text())
    validate_template(flow)
    before = metadata(db)
    backup = Path('/srv/backups/openclaw-ha-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700, parents=True)
    with sqlite3.connect(backup / 'n8n-before.sqlite3') as target:
        db.backup(target)
    (backup / 'workflow-metadata.json').write_text(json.dumps(before))
    flow.update(id=WORKFLOW, name='HomeCompute OpenClaw HA read-only observations', active=False)
    for node in flow['nodes']:
        if node['name'] == 'Approved input-free metadata request':
            node['parameters']['jsCode'] = node['parameters']['jsCode'].replace('configured=false', 'configured=true')
        if node['type'] == 'n8n-nodes-base.webhook':
            node['credentials'] = {'httpHeaderAuth': {'id': CREDENTIAL, 'name': 'OpenClaw HA finite read-only route'}}
    policy = json.loads(Path('/opt/homecompute/openclaw-communication/config/system-monitoring.json').read_text())
    for system in policy['systems']:
        if system['id'] == 'home-assistant':
            system['enabled'] = True
            system['health_ttl_seconds'] = system['update_ttl_seconds'] = 900
    if (CONFIG / 'system-monitoring.json').exists():
        raise ValueError('private registry already exists; reconcile policy before provisioning')
    token = secrets.token_urlsafe(48)
    credential = [{'id': CREDENTIAL, 'name': 'OpenClaw HA finite read-only route', 'type': 'httpHeaderAuth',
                   'data': {'name': 'Authorization', 'value': 'Bearer ' + token}}]
    for path, content in ((CONFIG / 'system-monitoring.json', json.dumps(policy)),
                          (CONFIG / 'ha-metadata-reader-token', token + '\n'),
                          (CONFIG / 'home-assistant.json', json.dumps({'schema_version': 1,
                           'token_file': str(CONFIG / 'ha-metadata-reader-token')}))):
        with path.open('x') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), info.pw_uid, info.pw_gid)
    workflow_file, credential_file = '/tmp/hc-openclaw-ha-workflow.json', '/tmp/hc-openclaw-ha-credential.json'
    try:
        container_file('hc-openclaw-ha-workflow.json', [flow])
        container_file('hc-openclaw-ha-credential.json', credential)
        call(['docker', 'exec', CONTAINER, 'n8n', 'import:credentials', '--input=' + credential_file, '--projectId=' + project])
        call(['docker', 'exec', CONTAINER, 'n8n', 'import:workflow', '--input=' + workflow_file, '--projectId=' + project])
        call(['docker', 'exec', CONTAINER, 'n8n', 'publish:workflow', '--id=' + WORKFLOW])
    finally:
        call(['docker', 'exec', CONTAINER, 'node', '-e',
              'for(const p of ' + json.dumps([workflow_file, credential_file]) + ')require("fs").rmSync(p,{force:true});'])
    if metadata(db) != before | {WORKFLOW: metadata(db).get(WORKFLOW)}:
        raise ValueError('existing workflow metadata changed; reconcile before restarting n8n')
    active = db.execute('SELECT active,versionId,activeVersionId FROM workflow_entity WHERE id=?', (WORKFLOW,)).fetchone()
    if not active or not active[0] or active[1] != active[2]:
        raise ValueError('HA route publication unavailable; preserve backup')
    call(['docker', 'restart', CONTAINER])
    return {'provisioned': True, 'workflow': WORKFLOW, 'existing_workflows_preserved': len(before),
            'backup': str(backup), 'template_sha256': hashlib.sha256(template.read_bytes()).hexdigest(),
            'ha_writes': 0, 'telegram_messages': 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.template)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print('HA route setup incomplete; preserve credentials, workflow state and backup for reconciliation.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
