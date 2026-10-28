#!/usr/bin/env python3
"""Back up and repair only our budget Code nodes in a paused Aula snapshot."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3

from llm_workflow_queue import MARKER, pause_restore_code, private_write

DATABASE = Path('/srv/state/automation/n8n/database.sqlite')
WORKFLOW = 'O0JTIc9aqA7OkrQS'
LEGACY_SHA = '0bf6dd0fcab333a2a0f0a4238b32a365b537498245f190d99bf9dac27252b823'
NAMES = {'LLM queue · d042b06 · Resume budget', 'LLM queue · 6213a1c · Resume budget'}


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def patch_graph(graph: dict) -> tuple[dict, list[dict]]:
    result = deepcopy(graph)
    found = [node for node in result['nodes'] if node['name'] in NAMES]
    if len(found) != 2 or {node['name'] for node in found} != NAMES:
        raise ValueError('Exact two reviewed budget nodes required')
    operations = []
    for node in found:
        if (node['type'] != 'n8n-nodes-base.code' or not node.get('notes', '').startswith(MARKER) or
                sha(node['parameters']['jsCode']) != LEGACY_SHA):
            raise ValueError('Budget node source changed')
        node['parameters']['jsCode'] = pause_restore_code()
        operations.append({'type': 'updateNodeParameters', 'nodeName': node['name'],
                           'parameters': {'jsCode': pause_restore_code()}})
    return result, operations


def stage(database: Path, directory: Path, execution_id: int) -> dict:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('''SELECT e.status,e.workflowId,d.workflowData,d.data FROM execution_entity e
            JOIN execution_data d ON d.executionId=e.id WHERE e.id=?''', (execution_id,)).fetchone()
        if row is None or row['status'] != 'waiting' or row['workflowId'] != WORKFLOW:
            raise ValueError('Only the reviewed workflow in durable waiting state can be repaired')
        candidate, operations = patch_graph(json.loads(row['workflowData']))
        before = row['workflowData']
        after = json.dumps(candidate, separators=(',', ':'), ensure_ascii=False)
        nodes, version = db.execute('SELECT nodes,versionId FROM workflow_entity WHERE id=?', (WORKFLOW,)).fetchone()
        live_nodes = [node for node in json.loads(nodes) if node['name'] in NAMES]
        if (len(live_nodes) != 2 or {node['name'] for node in live_nodes} != NAMES or
                any(node['type'] != 'n8n-nodes-base.code' or not node.get('notes', '').startswith(MARKER) or
                    sha(node['parameters']['jsCode']) not in {LEGACY_SHA, sha(pause_restore_code())}
                    for node in live_nodes)):
            raise ValueError('Live graph differs from reviewed budget repair')
        manifest = {'schema_version': 1, 'workflow_id': WORKFLOW, 'execution_id': execution_id,
                    'workflow_version': version, 'before_sha256': sha(before), 'after_sha256': sha(after),
                    'node_code_sha256': sha(pause_restore_code())}
        private_write(directory / 'execution.workflow.before.json', before)
        private_write(directory / 'execution.workflow.after.json', after)
        private_write(directory / 'operations.json', operations)
        private_write(directory / 'manifest.json', manifest)
    return manifest


def apply(database: Path, directory: Path, expected: str, clock=lambda: datetime.now(timezone.utc)) -> dict:
    manifest_text = (directory / 'manifest.json').read_text()
    if sha(manifest_text) != expected:
        raise ValueError('Reviewed repair manifest required')
    plan = json.loads(manifest_text)
    if plan['schema_version'] != 1 or plan['workflow_id'] != WORKFLOW:
        raise ValueError('Unreviewed repair plan')
    before = (directory / 'execution.workflow.before.json').read_text()
    after = (directory / 'execution.workflow.after.json').read_text()
    if sha(before) != plan['before_sha256'] or sha(after) != plan['after_sha256']:
        raise ValueError('Repair backup or candidate changed')
    patched, _ = patch_graph(json.loads(before))
    if patched != json.loads(after) or sha(pause_restore_code()) != plan['node_code_sha256']:
        raise ValueError('Repair source changed')
    db = sqlite3.connect(database, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('''SELECT e.status,e.workflowId,e.waitTill,d.workflowData,d.data FROM execution_entity e
            JOIN execution_data d ON d.executionId=e.id WHERE e.id=?''', (plan['execution_id'],)).fetchone()
        if (row is None or row['status'] != 'waiting' or row['workflowId'] != WORKFLOW or
                row['workflowData'] != before or row['waitTill'] is None):
            raise ValueError('Execution is no longer the reviewed paused snapshot')
        wake = datetime.fromisoformat(row['waitTill'].replace('Z', '+00:00'))
        if wake.tzinfo is None:
            wake = wake.replace(tzinfo=timezone.utc)
        if (wake - clock()).total_seconds() < 30:
            raise ValueError('Resume is too close; wait for the next durable pause')
        data_digest = sha(row['data'])
        changed = db.execute('UPDATE execution_data SET workflowData=? WHERE executionId=? AND workflowData=?',
                             (after, plan['execution_id'], before)).rowcount
        if changed != 1:
            raise ValueError('Paused snapshot changed concurrently')
        observed = db.execute('SELECT workflowData,data FROM execution_data WHERE executionId=?',
                              (plan['execution_id'],)).fetchone()
        if observed['workflowData'] != after or sha(observed['data']) != data_digest:
            raise ValueError('Execution input changed during repair')
        db.commit()
        result = {'execution_id': plan['execution_id'], 'changed_node_count': 2,
                  'execution_data_unchanged': True, 'status_unchanged': True}
        private_write(directory / 'applied.json', result)
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['stage', 'apply'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--execution-id', type=int, default=152939)
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise ValueError('Run as root on home-core')
    if args.command == 'stage':
        manifest = stage(DATABASE, args.directory, args.execution_id)
        print(json.dumps({'execution_id': manifest['execution_id'], 'operations': 2,
                          'manifest_sha256': sha((args.directory / 'manifest.json').read_text())}))
    else:
        print(json.dumps(apply(DATABASE, args.directory, args.manifest_sha256)))


if __name__ == '__main__':
    main()
