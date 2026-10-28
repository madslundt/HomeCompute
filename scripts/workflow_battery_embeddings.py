"""Add advisory retrieval without changing battery thresholds, validators or delivery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from workflow_research_embeddings import DIRECTORY as COMMON_DIRECTORY, _add, chain, edge, http

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/semantic-battery'


def code(name: str, filename: str, prepare: str) -> dict[str, Any]:
    source = (DIRECTORY / filename).read_text().replace('PREPARE_NODE', repr(prepare))
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [0, 0],
            'parameters': {'mode': 'runOnceForEachItem', 'language': 'javaScript',
                'jsCode': (COMMON_DIRECTORY / 'retrieval-common.js').read_text() + '\n' + source}}


def patch(workflow: dict[str, Any], credential_id: str, *, weekly: bool = False) -> list[dict[str, Any]]:
    source = 'Prepare Review Snapshot' if weekly else 'Select LLM Review'
    model = 'Generate Battery Review (Local MoE)' if weekly else 'Analyze Plan and Battery Context'
    validated = 'Format Telegram Report' if weekly else 'Validate LLM Review'
    label = 'weekly' if weekly else 'hourly'
    prepare = 'Prepare ' + label + ' review history'
    existing = {node['name'] for node in workflow['nodes']}
    if prepare in existing:
        raise ValueError('Battery retrieval patch already present')
    if not {source, model, validated} <= existing:
        raise ValueError('Battery graph changed; inspect before patching')
    nodes = [code(prepare, 'prepare-search.js', prepare),
             http('Retrieve ' + label + ' review history', credential_id, profile='battery'),
             code('Apply ' + label + ' review history', 'apply-history.js', prepare),
             code('Prepare ' + label + ' review archive', 'remember-weekly.js' if weekly else 'remember-hourly.js', prepare),
             http('Archive ' + label + ' review history', credential_id, profile='battery')]
    operations = _add(nodes, 500)
    operations.append(edge(source, model, remove=True))
    operations.extend(chain([source, *[n['name'] for n in nodes[:3]], model]))
    operations.extend(chain([validated, *[n['name'] for n in nodes[3:]]]))
    for group in workflow.get('nodeGroups', []):
        additions = []
        if source in group['nodeNames']:
            additions.extend(n['name'] for n in nodes[:3])
        if validated in group['nodeNames']:
            additions.extend(n['name'] for n in nodes[3:])
        if additions:
            operations.append({'type': 'updateNodeGroup', 'groupName': group['name'], 'nodeNames': group['nodeNames'] + additions})
    return operations
