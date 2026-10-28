"""Build a finite read-only Gmail/Outlook route without exporting provider OAuth."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

DIRECTORY = Path(__file__).resolve().parents[1] / 'automations/mail'
SOURCES = {'owner': {'alias': 'outlook_mads', 'provider': 'outlook', 'data_domain': 'personal'},
           'partner': {'alias': 'outlook_kristina', 'provider': 'outlook', 'data_domain': 'personal'},
           'work': {'alias': 'gmail_work', 'provider': 'gmail', 'data_domain': 'work:primary'}}


def route_credential(principal: str) -> dict[str, str]:
    if principal not in SOURCES:
        raise ValueError('Explicit mail principal required')
    return {'id': 'hcOpenClawMail' + principal.title() + '20261027',
            'name': 'HomeCompute read-only mail bridge ' + principal}


def build(directory: Path = DIRECTORY, *, principal: str = 'owner', credential: dict | None = None,
          configured: bool = False) -> dict[str, Any]:
    if (principal not in SOURCES or configured and credential is None
            or credential is not None and (not isinstance(credential, dict) or set(credential) != {'id', 'name'}
                or any(not isinstance(x, str) or not x for x in credential.values()))):
        raise ValueError('Explicit isolated mail credential reference required')
    source_identity = SOURCES[principal]
    provider = source_identity['provider']
    route = route_credential(principal)
    def node(name, kind, version, x, parameters, **options):
        return {'id': name.lower().replace(' ', '-'), 'name': name, 'type': 'n8n-nodes-base.' + kind,
                'typeVersion': version, 'position': [x, 0], 'parameters': parameters, **options}
    source = (directory / 'validate-mail-request.js').read_text()
    source = source.replace('__MAIL_SOURCE__', json.dumps(source_identity, sort_keys=True))
    if configured:
        source = source.replace('const configured = false;', 'const configured = true;')
    nodes = [node('Mail bridge webhook', 'webhook', 2.1, 0,
                  {'httpMethod': 'POST', 'path': 'homecompute-openclaw-mail-' + principal, 'authentication': 'headerAuth',
                   'responseMode': 'responseNode', 'options': {}}, webhookId='homecompute-openclaw-mail-' + principal,
                  credentials={'httpHeaderAuth': route}),
             node('Validate mail request', 'code', 2, 220, {'jsCode': source})]
    connections = {'Mail bridge webhook': {'main': [[{'node': 'Validate mail request', 'type': 'main', 'index': 0}]]}}
    def connect(a, b, output=0):
        ports = connections.setdefault(a, {'main': []})['main']
        while len(ports) <= output:
            ports.append([])
        ports[output].append({'node': b, 'type': 'main', 'index': 0})
    kind = 'gmailOAuth2' if provider == 'gmail' else 'microsoftOutlookOAuth2Api'
    name = 'Read ' + provider
    nodes.append(node(name, 'httpRequest', 4.5, 440, {
            'authentication': 'predefinedCredentialType', 'nodeCredentialType': kind, 'method': 'GET',
            'url': '={{ $json.request.url }}', 'sendHeaders': True, 'specifyHeaders': 'json',
            'jsonHeaders': '={{ JSON.stringify($json.request.headers) }}', 'sendBody': False,
            'options': {'timeout': 10000, 'redirect': {'redirect': {'followRedirects': False}},
                'response': {'response': {'fullResponse': True, 'neverError': True, 'responseFormat': 'json'}}}},
            **({'credentials': {kind: credential.copy()}} if credential else {}),
            retryOnFail=False, onError='continueRegularOutput'))
    connect('Validate mail request', name)
    connect(name, 'Project mail response')
    nodes.extend([node('Project mail response', 'code', 2, 660,
                       {'jsCode': (directory / 'project-mail-response.js').read_text()}),
                  node('Return mail result', 'respondToWebhook', 1.5, 880, {
                      'respondWith': 'json', 'responseBody': '={{ $json }}', 'options': {'responseCode': 200}})])
    connect('Project mail response', 'Return mail result')
    return {'name': 'HomeCompute read-only mail bridge ' + principal, 'active': False, 'nodes': nodes,
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
        encoded = json.dumps({'type': definition['type'], 'version': definition['typeVersion'], 'config': config,
            'output': [{'ok': True, 'account': 'gmail', 'operation': 'search',
                        'request': {'url': 'https://gmail.googleapis.com/gmail/v1/users/me/threads', 'headers': {}}}]}, separators=(',', ':'))
        encoded = re.sub(r'"(=(?:[^"\\]|\\.)*)"',
            lambda m: 'expr(' + json.dumps(json.loads('"' + m[1] + '"')[1:]) + ')', encoded)
        lines.append(f'const {handle} = {"trigger" if definition["type"].endswith(".webhook") else "node"}({encoded});')
    principal = workflow['name'].rsplit(' ', 1)[-1]
    lines.append("export default workflow(" + json.dumps('homecompute-mail-' + principal) + ', ' + json.dumps(workflow['name']) + ')')
    for source, ports in workflow['connections'].items():
        for index, edges in enumerate(ports['main']):
            for edge in edges:
                lines.append(f"  .add({handles[source]}.output({index}).to({handles[edge['node']]}))")
    return '\n'.join(lines) + ';\n'
