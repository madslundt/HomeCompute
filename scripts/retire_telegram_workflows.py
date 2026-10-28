#!/usr/bin/env python3
"""Build the reviewed outbound-only Telegram migration from a fresh private snapshot.

Unpublish RETIRE_IDS after preserving a private backup.
Pass operations(workflow) to the n8n atomic update tool and leave family intake
unpublished. Mail is paused; outbound Telegram credentials and history are kept.
This module never executes a workflow or reads/writes a mailbox.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any
from datetime import datetime, timezone
from contextlib import closing

RETIRE_IDS = ('hcOpenClawTelegram20261009', 'p51AOjL4ggMQfsuA')
FAMILY_CALENDAR_ID = 'p51AOjL4ggMQfsuA'
INPUT_NODE_NAMES = (
    'Telegram Trigger (disabled - webhook)', 'Expand Telegram updates',
    'Telegram attachment?', 'Download Telegram attachment', 'Restore Telegram message',
)
NAME = 'Paused family mail calendar'
ORPHAN_PAIRS = {1257: 1258, 1889: 1890}
READ_RETRY_NODES = {
    'WxRovre260928abc': 'Get kitchen activity',
    'Bp4ePUN4mEDzs7g8': 'Read participation before native reconciliation',
}


def read_retry_operations(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Bound retries to the two reviewed read-only HA calls, never actions/writes."""
    name = READ_RETRY_NODES.get(workflow.get('id'))
    if not name:
        raise ValueError('Unreviewed read retry workflow')
    node = next((n for n in workflow['nodes'] if n['name'] == name), {})
    params = node.get('parameters', {})
    if node.get('type') != 'n8n-nodes-base.homeAssistant' or (params.get('resource'), params.get('operation')) != ('state', 'get'):
        raise ValueError('Reviewed HA state read changed; no retries applied')
    if node.get('retryOnFail') and node.get('maxTries', 3) <= 3:
        return []
    return [{'type': 'setNodeSettings', 'nodeName': name,
             'settings': {'retryOnFail': True, 'maxTries': 3, 'waitBetweenTries': 2000}}]


def child_id(data: str) -> str | None:
    values = json.loads(data)
    def deref(value):
        return values[int(value)] if isinstance(value, str) and value.isdigit() and int(value) < len(values) else value
    result = deref(values[0].get('resultData', {}))
    runs = deref(result.get('runData', {}))
    found = []
    for name, steps in runs.items():
        if 'collector' not in name.lower():
            continue
        for step in deref(steps):
            metadata = deref(deref(step).get('metadata', {}))
            sub = deref(metadata.get('subExecution', {}))
            if isinstance(sub, dict) and 'executionId' in sub:
                found.append(str(deref(sub['executionId'])))
    return found[0] if len(found) == 1 else None


def container_running() -> bool:
    result = subprocess.run(['docker', 'inspect', 'homecompute-automation-n8n-1',
                             '--format', '{{.State.Running}}'],
                            capture_output=True, text=True, check=True)
    if result.stdout.strip() not in {'true', 'false'}:
        raise ValueError('Cannot verify n8n is stopped')
    return result.stdout.strip() == 'true'


def cancel_orphaned_waits(database=Path('/srv/state/automation/n8n/database.sqlite'),
                          backups=Path('/var/lib/homecompute/n8n-telegram-retirement/orphan-waits')):
    """Cancel only the two reviewed orphan parents while the application is stopped."""
    if container_running():
        raise ValueError('Stop n8n before repairing orphaned waits')
    if database.is_symlink() or not database.is_file():
        raise ValueError('Expected existing regular n8n database')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=3)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        originals = []
        for parent, child in ORPHAN_PAIRS.items():
            row = db.execute('SELECT * FROM execution_entity WHERE id=?', (parent,)).fetchone()
            data = db.execute('SELECT * FROM execution_data WHERE executionId=?', (parent,)).fetchone()
            if (not row or not data or row['status'] != 'waiting' or row['waitTill'] != '3000-01-01 00:00:00.000'
                    or child_id(data['data']) != str(child)
                    or db.execute('SELECT 1 FROM execution_entity WHERE id=?', (child,)).fetchone()):
                raise ValueError('Orphan evidence changed; no execution was cancelled')
            originals.append({'execution': dict(row), 'execution_data': dict(data)})
        if container_running():
            raise ValueError('n8n restarted during repair; no execution was cancelled')
        backups.mkdir(parents=True, mode=0o700, exist_ok=True)
        if backups.is_symlink() or backups.stat().st_mode & 0o077:
            raise ValueError('Backup directory must be private')
        destination = backups / ('orphan-waits-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.json')
        with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
            json.dump(originals, stream)
            stream.flush()
            os.fsync(stream.fileno())
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        for item in originals:
            row = item['execution']
            changed = db.execute("UPDATE execution_entity SET status='canceled',waitTill=NULL,stoppedAt=? WHERE id=? AND status='waiting' AND waitTill=?",
                                 (now, row['id'], row['waitTill'])).rowcount
            if changed != 1:
                raise ValueError('Concurrent execution change; repair rolled back')
    return {'cancelled_orphan_parents': list(ORPHAN_PAIRS), 'backup': str(destination), 'history_deleted': 0}


def unpublish_retired() -> dict[str, Any]:
    """Run on home-core; caller coordinates the single n8n restart afterward."""
    for identifier in RETIRE_IDS:
        subprocess.run(['docker', 'exec', 'homecompute-automation-n8n-1', 'n8n',
                        'unpublish:workflow', '--id=' + identifier],
                       check=True, capture_output=True, text=True)
    return {'unpublished': list(RETIRE_IDS), 'restart_required': True,
            'credentials_deleted': 0, 'history_deleted': 0}


def operations(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if workflow.get('id') != FAMILY_CALENDAR_ID:
        raise ValueError('Unreviewed workflow: outbound notification workflows must be retained')
    if (workflow.get('activeVersion') or {}).get('sameAsDraft') is False:
        raise ValueError('Published graph differs from draft; reconcile before retirement')
    nodes = {node['name']: node for node in workflow['nodes']}
    gmail = nodes.get('New unread Gmail', {})
    if gmail.get('type') != 'n8n-nodes-base.gmailTrigger':
        raise ValueError('Expected Gmail intake graph changed')
    if not set(INPUT_NODE_NAMES) <= nodes.keys():
        raise ValueError('Telegram intake graph changed; inspect before retirement')
    result = [{'type': 'setNodeDisabled', 'nodeName': 'New unread Gmail', 'disabled': True}] if not gmail.get('disabled', False) else []
    result += [{'type': 'setNodeDisabled', 'nodeName': name, 'disabled': True}
              for name in INPUT_NODE_NAMES if not nodes[name].get('disabled', False)]
    # Disabled nodes pass input through. Cut every exit from this retired branch,
    # retaining its disconnected internal graph and existing canvas groups.
    for source, channels in workflow.get('connections', {}).items():
        if source not in (*INPUT_NODE_NAMES, 'New unread Gmail'):
            continue
        for kind, batches in channels.items():
            for output, edges in enumerate(batches):
                for edge in edges:
                    if edge['node'] in INPUT_NODE_NAMES:
                        continue
                    result.append({'type': 'removeConnection', 'source': source,
                                   'target': edge['node'], 'connectionType': kind,
                                   'sourceIndex': output, 'targetIndex': edge['index']})
    if workflow.get('name') != NAME:
        result.append({'type': 'setWorkflowMetadata', 'name': NAME,
                       'description': 'Family mail intake is paused. Telegram is outbound-only; historical nodes and credentials are retained.'})
    groups = {group['name']: group for group in workflow.get('nodeGroups', [])}
    if 'Download attachment' in groups:
        result.append({'type': 'removeNodeGroup', 'groupName': 'Download attachment'})
    if 'Retired Telegram intake' not in groups:
        result.append({'type': 'addNodeGroup', 'name': 'Retired Telegram intake',
                       'description': 'Disabled historical input branch. Mail is paused; Telegram only sends notifications.',
                       'nodeNames': list(INPUT_NODE_NAMES[1:])})
    return result


if __name__ == '__main__':
    if sys.argv[1:] == ['--cancel-orphaned-waits']:
        print(json.dumps(cancel_orphaned_waits()))
        raise SystemExit(0)
    if sys.argv[1:] == ['--unpublish-retired']:
        print(json.dumps(unpublish_retired()))
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit('Usage: retire_telegram_workflows.py PRIVATE_WORKFLOW_JSON')
    workflow = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps({'unpublish_workflow_ids': RETIRE_IDS,
                      'workflow_id': FAMILY_CALENDAR_ID,
                      'operations': operations(workflow)}, indent=2))
