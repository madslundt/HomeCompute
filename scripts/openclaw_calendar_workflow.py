"""Build the inspectable finite calendar bridge without contacting n8n or Google."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/calendar'
GOOGLE_CREDENTIAL = {'id': 'reijOto5INx70Y7u', 'name': 'Home family Lundt'}
ROUTE_CREDENTIAL = {'id': 'hcOpenClawCalendarRoute20261024', 'name': 'HomeCompute calendar bridge only'}


def build(directory: Path = DIRECTORY) -> dict[str, Any]:
    def node(name: str, kind: str, version: float, x: int, parameters: dict[str, Any], **options: Any) -> dict[str, Any]:
        return {'id': name.lower().replace(' ', '-'), 'name': name, 'type': 'n8n-nodes-base.' + kind,
                'typeVersion': version, 'position': [x, 0], 'parameters': parameters, **options}
    def code(name: str, file: str, x: int) -> dict[str, Any]:
        return node(name, 'code', 2, x, {'jsCode': (directory / file).read_text()})
    def http(name: str, x: int, write: bool = False, availability: bool = False) -> dict[str, Any]:
        return node(name, 'httpRequest', 4.5, x, {
            'authentication': 'predefinedCredentialType', 'nodeCredentialType': 'googleCalendarOAuth2Api',
            'method': '={{ $json.request.method }}' if write else ('POST' if availability else 'GET'),
            'url': '={{ $json.request.url }}',
            'sendHeaders': True, 'specifyHeaders': 'json', 'jsonHeaders': '={{ JSON.stringify($json.request.headers || {}) }}',
            'sendBody': write or availability,
            **({'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.request.body) }}'} if write or availability else {}),
            'options': {'timeout': 8000, 'redirect': {'redirect': {'followRedirects': False}},
                        'response': {'response': {'fullResponse': True, 'neverError': True, 'responseFormat': 'json'}}}},
            credentials={'googleCalendarOAuth2Api': GOOGLE_CREDENTIAL.copy()}, retryOnFail=False,
            onError='continueRegularOutput')
    nodes = [
        node('Calendar bridge webhook', 'webhook', 2.1, 0,
             {'httpMethod': 'POST', 'path': 'homecompute-openclaw-calendar', 'authentication': 'headerAuth',
              'responseMode': 'responseNode', 'options': {}},
             webhookId='homecompute-openclaw-calendar', credentials={'httpHeaderAuth': ROUTE_CREDENTIAL.copy()}),
        code('Validate calendar request', 'validate-calendar-request.js', 220),
        node('Availability request', 'if', 2.3, 330,
             {'conditions': {'options': {'caseSensitive': True, 'typeValidation': 'strict', 'version': 2},
                             'conditions': [{'id': 'availability', 'leftValue': '={{ $json.operation }}',
                                             'rightValue': 'availability', 'operator': {'type': 'string', 'operation': 'equals'}}],
                             'combinator': 'and'}, 'options': {}}),
        http('Read calendar preflight', 440),
        http('Read calendar availability', 440, availability=True),
        code('Check calendar preflight', 'check-calendar-preflight.js', 660),
        node('Calendar mutation permitted', 'if', 2.3, 880,
             {'conditions': {'options': {'caseSensitive': True, 'typeValidation': 'strict', 'version': 2},
                             'conditions': [{'id': 'write-permitted', 'leftValue': '={{ $json.write }}',
                                             'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
                             'combinator': 'and'}, 'options': {}}),
        http('Write one calendar event', 1100, True),
        code('Project calendar write receipt', 'project-calendar-write.js', 1320),
        node('Return calendar read', 'respondToWebhook', 1.5, 1100,
             {'respondWith': 'json', 'responseBody': '={{ $json.result }}', 'options': {'responseCode': 200}}),
        node('Return calendar write', 'respondToWebhook', 1.5, 1540,
             {'respondWith': 'json', 'responseBody': '={{ $json }}', 'options': {'responseCode': 200}}),
    ]
    connections: dict[str, Any] = {}
    def connect(source: str, target: str, output: int = 0) -> None:
        outputs = connections.setdefault(source, {'main': []})['main']
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({'node': target, 'type': 'main', 'index': 0})
    for a, b in [('Calendar bridge webhook', 'Validate calendar request'),
                 ('Validate calendar request', 'Availability request'),
                 ('Read calendar preflight', 'Check calendar preflight'),
                 ('Read calendar availability', 'Check calendar preflight'),
                 ('Check calendar preflight', 'Calendar mutation permitted')]:
        connect(a, b)
    connect('Availability request', 'Read calendar availability')
    connect('Availability request', 'Read calendar preflight', 1)
    connect('Calendar mutation permitted', 'Write one calendar event')
    connect('Calendar mutation permitted', 'Return calendar read', 1)
    connect('Write one calendar event', 'Project calendar write receipt')
    connect('Project calendar write receipt', 'Return calendar write')
    return {'name': 'HomeCompute finite household calendar bridge', 'active': False, 'nodes': nodes,
            'connections': connections, 'settings': {'executionOrder': 'v1', 'executionTimeout': 25,
                'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'none',
                'saveManualExecutions': False, 'saveExecutionProgress': False, 'availableInMCP': False}}


def sdk(workflow: dict[str, Any]) -> str:
    lines = ["import { workflow, node, trigger, expr } from '@n8n/workflow-sdk';"]
    handles = {}
    for index, definition in enumerate(workflow['nodes']):
        handle = 'step' + str(index)
        handles[definition['name']] = handle
        config = {k: v for k, v in definition.items() if k not in {'id', 'type', 'typeVersion'}}
        sample = {'ok': True, 'operation': 'events', 'write': False, 'result': {'ok': True, 'events': []},
                  'request': {'method': 'GET', 'url': 'https://www.googleapis.com/calendar/v3', 'headers': {}, 'body': {}}}
        encoded = json.dumps({'type': definition['type'], 'version': definition['typeVersion'],
                              'config': config, 'output': [sample]}, separators=(',', ':'))
        # SDK expression values must be expr(), including nested parameter expressions.
        import re
        encoded = re.sub(r'"(=(?:[^"\\]|\\.)*)"', lambda m: 'expr(' + json.dumps(json.loads('"'+m[1]+'"')[1:]) + ')', encoded)
        factory = 'trigger' if definition['type'].endswith('.webhook') else 'node'
        lines.append(f'const {handle} = {factory}({encoded});')
    lines.append("export default workflow('homecompute-calendar', " + json.dumps(workflow['name']) + ')')
    for source, ports in workflow['connections'].items():
        for index, edges in enumerate(ports['main']):
            for edge in edges:
                lines.append(f"  .add({handles[source]}.output({index}).to({handles[edge['node']]}))")
    lines.append("  .group('Calendar preflight', [step1,step2,step3,step4,step5], {description:'Validate fixed household targets and read fresh Google state before any change.'})")
    lines.append("  .group('Single checked write', [step7,step8,step10], {description:'One revision-checked guest-free event change; return only status, then core reads back.'});")
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    artifact = build()
    (DIRECTORY / 'n8n-workflow.json').write_text(json.dumps(artifact, indent=2) + '\n')
    (DIRECTORY / 'n8n-workflow.ts').write_text(sdk(artifact))
