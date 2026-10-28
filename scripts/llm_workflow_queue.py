#!/usr/bin/env python3
"""Stage reviewed n8n local-LLM admission gates; apply only an exact staged plan.

Wait nodes persist executions, including manual and webhook inputs, in n8n's
existing database. Gates do not retry any started AI or side-effect node.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

READINESS_URL = 'https://ai.home.arpa/llm/status'
WAIT_SECONDS = 120  # Installed n8n sleeps in-process for waits below 65 seconds.
MARKER = 'HomeCompute durable local LLM queue v1'
PAUSE_FIELD = '__homecompute_llm_queue_started_ms'
# Each entry is (target, exact incoming source names, expected target type).
GATES = {
    'lFXikEZlVyAmmeoc': [('Claim due Lager batch', ('Daily at 07:00', 'Manual check', 'Webhook - sync storage categories'), 'httpRequest')],
    'xHrpkUT21u7Zs6UV': [('Configuration', ('Full sync mode', 'Category sync mode', 'Webhook - ingest HA snapshot'), 'code')],
    'p51AOjL4ggMQfsuA': [('Build multimodal extraction request', ('New unread Gmail', 'Telegram attachment?', 'Restore Telegram message'), 'code')],
    'O0JTIc9aqA7OkrQS': [
        ('Build structured Aula prompt', ('When Executed by Another Workflow',), 'code'),
        ('Aula Discovery', ('Prepare retry state', 'Wait before retry'), 'mcpClient'),
        ('Review Aula draft', ('Daily source-grounded?',), 'chainLlm'),
    ],
    'RjId1K2BPpv5lAVN': [('Read Battery and Price State', ('Manual Weekly Review', 'Sunday 08:00 Weekly Report', 'Sunday 09:00 Systems Review'), 'homeAssistant')],
    '86FmVCFIF86m8fLx': [('Select LLM Review', ('Validate Analysis Result',), 'code')],
    'FC7HqrZ9UGsMJKao': [('Get official HN ranking', ('Daily 10:00 Copenhagen',), 'httpRequest')],
    'reK3QQ0NYgxge1CB': [('Get a database page', ('Morning', 'Evening'), 'notion')],
    'Bp4ePUN4mEDzs7g8': [('Check family vacation', ('Daily Aula calendar sync',), 'executeWorkflow')],
}
PARENTS = {
    'U2kI7V9EGcMc14Su': ('Call Aula Collector — SELECT WORKFLOW', 'O0JTIc9aqA7OkrQS'),
    'jVOMdmaVWgIRFGfL': ('Call Aula collector', 'O0JTIc9aqA7OkrQS'),
}
WORKFLOW_IDS = tuple(GATES) + tuple(PARENTS)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def fingerprint(workflow: dict) -> str:
    return digest({k: workflow.get(k) for k in ('id', 'name', 'nodes', 'connections', 'settings', 'versionId', 'activeVersionId', 'active', 'nodeGroups')})


def incoming(workflow: dict, target: str) -> list[tuple[str, int, dict]]:
    return [(source, output, edge) for source, channels in workflow['connections'].items()
            for output, edges in enumerate(channels.get('main', [])) for edge in edges if edge['node'] == target]


def pause_capture_code() -> str:
    return f'''const started = Date.now();
return $input.all().map((item, index) => {{
  if (Object.prototype.hasOwnProperty.call(item.json, {json.dumps(PAUSE_FIELD)})) throw new Error('Reserved local LLM queue field already exists');
  return {{json: {{...item.json, [{json.dumps(PAUSE_FIELD)}]: started}}, ...(item.binary ? {{binary:item.binary}} : {{}}), pairedItem:{{item:index}}}};
}});'''


def pause_restore_code() -> str:
    # Only the collector's exact per-execution retry context is allowed to move.
    # The original deadline keeps its active-work budget; semantic retry delays
    # happen before capture and are never added back.
    return f'''const now = Date.now();
const state = $getWorkflowStaticData('global');
return $input.all().map((item, index) => {{
  const json = {{...item.json}};
  const started = json[{json.dumps(PAUSE_FIELD)}];
  delete json[{json.dumps(PAUSE_FIELD)}];
  if (!Number.isFinite(started)) throw new Error('Missing durable local LLM pause timestamp');
  const paused = Math.max(0, now - started);
  const deadline = Date.parse(json.processingDeadline || '');
  if (Number.isFinite(deadline)) {{
    const key = `aulaCollectorRetry:${{$execution.id}}`;
    if (json.__retryKey !== key) throw new Error('Aula retry context belongs to another execution');
    // Wait persists the input stream, but workflow static data is only saved
    // after successful executions. Its per-execution cache can be absent or
    // stale after a durable resume; the held item owns the current deadline.
    const previous = state[key] || {{}};
    if (typeof previous !== 'object' || Array.isArray(previous)) throw new Error('Invalid Aula retry context cache');
    if (paused >= {WAIT_SECONDS * 1000}) json.processingDeadline = new Date(deadline + paused).toISOString();
    state[key] = {{...previous, context:{{...previous.context, ...json}},
      attempt:previous.attempt ?? Number(json.retryAttempt || 0)}};
  }}
  return {{json, ...(item.binary ? {{binary:item.binary}} : {{}}), pairedItem:{{item:index}}}};
}});'''


def gate_nodes(workflow_id: str, target: dict, *, pause_budget: bool = False) -> list[dict]:
    suffix = hashlib.sha256(target['name'].encode()).hexdigest()[:7]
    names = {part: f'LLM queue · {suffix} · {part}' for part in ('Hold input', 'Check readiness', 'Ready?', 'Wait 2 minutes', 'Restore input', 'Resume budget')}
    def node(part: str, kind: str, version: float, parameters: dict, **extra) -> dict:
        x, y = target.get('position', [0, 0])
        return dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f'homecompute/llm-queue/v1/{workflow_id}/{suffix}/{part}')),
                    name=names[part], type='n8n-nodes-base.' + kind, typeVersion=version,
                    position=[x - 1200 + 220 * len(nodes), y + (160 if part == 'Wait 2 minutes' else 0)],
                    parameters=parameters, notes=MARKER + ': ' + target['name'], **extra)
    nodes: list[dict] = []
    nodes.append(node('Hold input', 'code' if pause_budget else 'noOp', 2 if pause_budget else 1,
                      {'mode': 'runOnceForAllItems', 'language': 'javaScript', 'jsCode': pause_capture_code()} if pause_budget else {}))
    nodes.append(node('Check readiness', 'httpRequest', 4.5, {
        'method': 'GET', 'url': READINESS_URL, 'authentication': 'none',
        'options': {'timeout': 5000, 'redirect': {'redirect': {'followRedirects': False}},
                    'response': {'response': {'responseFormat': 'json', 'neverError': True}}}},
        executeOnce=True, retryOnFail=False, onError='continueRegularOutput'))
    nodes.append(node('Ready?', 'if', 2.4, {'conditions': {
        'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
        'conditions': [{'id': 'local-llm-ready', 'leftValue': '={{ $json.available === true && $json.state === "ready" }}',
                        'rightValue': True, 'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}],
        'combinator': 'and'}, 'options': {}}))
    nodes.append(node('Wait 2 minutes', 'wait', 1.1, {'resume': 'timeInterval', 'amount': WAIT_SECONDS, 'unit': 'seconds'}))
    nodes.append(node('Restore input', 'merge', 3.2, {'mode': 'chooseBranch', 'numberInputs': 2,
                      'chooseBranchMode': 'waitForAll', 'output': 'specifiedInput'}))
    if pause_budget:
        nodes.append(node('Resume budget', 'code', 2, {'mode': 'runOnceForAllItems', 'language': 'javaScript', 'jsCode': pause_restore_code()}))
    return nodes


def bound_calls(workflow: dict) -> list[str]:
    changed = []
    for node in workflow['nodes']:
        kind = node['type'].rsplit('.', 1)[-1]
        if kind not in {'httpRequest', 'httpRequestTool', 'lmChatOpenAi', 'mcpClient', 'mcpClientTool'}:
            continue
        options = node.setdefault('parameters', {}).setdefault('options', {})
        timeout = options.get('timeout')
        if isinstance(timeout, str):
            if kind != 'lmChatOpenAi' or not all(s in timeout for s in ('Math.max(1000', 'Math.min(300000', 'processingDeadline')):
                raise ValueError('Unreviewed dynamic timeout on ' + node['name'])
        elif type(timeout) is not int or not 0 < timeout <= 600000:
            options['timeout'] = 300000 if kind == 'lmChatOpenAi' else 60000
            changed.append(node['name'])
    return changed


def patch_aula_deadline_reads(workflow: dict) -> list[str]:
    """Keep existing budget readers aligned with the paused per-execution state."""
    if workflow['id'] != 'O0JTIc9aqA7OkrQS':
        return []
    nodes = {node['name']: node for node in workflow['nodes']}
    changes = {
        'Attach Aula discovery context': (
            '  processingDeadline:resumed.processingDeadline || original.processingDeadline',
            "  processingDeadline:$getWorkflowStaticData('global')[`aulaCollectorRetry:${String($execution.id)}`]?.context?.processingDeadline || resumed.processingDeadline || original.processingDeadline"),
        'Evaluate Aula review': (
            "const base = $('Normalize structured facts').first(0, $runIndex).json;",
            "const originalBase = $('Normalize structured facts').first(0, $runIndex).json;\n"
            "const queuedDeadline = $getWorkflowStaticData('global')[`aulaCollectorRetry:${String($execution.id)}`]?.context?.processingDeadline;\n"
            "const base = {...originalBase, ...(Number.isFinite(Date.parse(queuedDeadline || '')) ? {processingDeadline:queuedDeadline} : {})};"),
    }
    for name, (old, new) in changes.items():
        code = nodes[name]['parameters']['jsCode']
        if code.count(old) != 1:
            raise ValueError('Aula deadline reader changed: ' + name)
        nodes[name]['parameters']['jsCode'] = code.replace(old, new)
    old_timeout = "={{ Math.max(1000, Math.min(300000, Date.parse($('Prepare retry state').first().json.processingDeadline) - Date.now() - 120000)) }}"
    new_timeout = "={{ Math.max(1000, Math.min(300000, (Date.parse($json.processingDeadline || '') || Date.now()) - Date.now() - 120000)) }}"
    for name in ('HomeCompute automation model', 'Local Aula summary reviewer'):
        options = nodes[name]['parameters']['options']
        if options.get('timeout') != old_timeout:
            raise ValueError('Aula model deadline reader changed: ' + name)
        options['timeout'] = new_timeout
    return [*changes, 'HomeCompute automation model', 'Local Aula summary reviewer']


def transform(workflow: dict) -> tuple[dict, dict]:
    identifier = workflow.get('id')
    if identifier not in WORKFLOW_IDS:
        raise ValueError('Workflow is outside the reviewed queue migration')
    result = deepcopy(workflow)
    by_name = {n['name']: n for n in result['nodes']}
    if len(by_name) != len(result['nodes']) or any(n.get('notes', '').startswith(MARKER) for n in result['nodes']):
        raise ValueError('Duplicate nodes or an already queued workflow requires review')
    report = {'workflow_id': identifier, 'gate_targets': [], 'anchors': {}, 'bounded_calls': [],
              'previous_execution_timeout': result.get('settings', {}).get('executionTimeout', 'instance default')}
    report['deadline_readers'] = patch_aula_deadline_reads(result)
    if identifier in PARENTS:
        name, child = PARENTS[identifier]
        node = by_name.get(name, {})
        if (node.get('type') != 'n8n-nodes-base.executeWorkflow' or
                node.get('parameters', {}).get('workflowId', {}).get('value') != child or
                node.get('parameters', {}).get('options', {}).get('waitForSubWorkflow') is not True):
            raise ValueError('Waiting parent shape changed')
        report['anchors'][name] = digest(node)
    for target_name, sources, expected_type in GATES.get(identifier, []):
        target = by_name.get(target_name)
        edges = incoming(result, target_name)
        if (not target or target['type'].rsplit('.', 1)[-1] != expected_type or
                set(source for source, _, _ in edges) != set(sources) or
                len(edges) != len(sources) or any(edge['index'] != 0 for _, _, edge in edges)):
            raise ValueError('Reviewed queue anchor changed: ' + target_name)
        report['anchors'][target_name] = digest(target)
        pause_budget = identifier == 'O0JTIc9aqA7OkrQS' and target_name != 'Build structured Aula prompt'
        added = gate_nodes(identifier, target, pause_budget=pause_budget)
        names = [n['name'] for n in added]
        if any(name in by_name for name in names):
            raise ValueError('Queue node identity collision')
        for _, _, edge in edges:
            edge['node'] = names[0]
        result['nodes'].extend(added)
        connections = result['connections']
        def connect(source, target, output=0, input_index=0):
            batches = connections.setdefault(source, {}).setdefault('main', [])
            while len(batches) <= output:
                batches.append([])
            batches[output].append({'node': target, 'type': 'main', 'index': input_index})
        connect(names[0], names[4], input_index=0)  # Native Merge retains all original items and binary links.
        connect(names[0], names[1])
        connect(names[1], names[2])
        connect(names[2], names[4], output=0, input_index=1)
        connect(names[2], names[3], output=1)
        connect(names[3], names[1])
        connect(names[4], names[5] if pause_budget else target_name)
        if pause_budget:
            connect(names[5], target_name)
        owner = next((g for g in result.get('nodeGroups', []) if target_name in g['nodeNames']), None)
        if owner:
            owner['nodeNames'].extend(names)  # Preserve the existing group's connected subgraph.
        else:
            result.setdefault('nodeGroups', []).append({
                'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'homecompute/llm-queue/group/' + identifier + '/' + target_name)),
                'name': 'Wait for local LLM · ' + target_name,
                'description': 'Retain original inputs, wait durably while the local LLM is unavailable, then continue this execution.',
                'nodeNames': names,
            })
        report['gate_targets'].append(target_name)
    report['bounded_calls'] = bound_calls(result)
    result.setdefault('settings', {})['executionTimeout'] = -1
    original_budget = report['previous_execution_timeout']
    result['nodes'].append({'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'homecompute/llm-queue/note/' + identifier)),
        'name': 'Local LLM availability queue', 'type': 'n8n-nodes-base.stickyNote', 'typeVersion': 1,
        'position': [-1200, -480], 'parameters': {'width': 680, 'height': 220, 'content':
            f'## {MARKER}\nOriginal whole-workflow timeout: {original_budget}. Disabled so database waits can survive an extended model outage. '
            'HTTP, model and MCP requests keep bounded timeouts. Checks run every 2 minutes and never call a model. '
            'Original trigger inputs survive; existing effects are never replayed. Independent non-LLM branches remain unchanged.'},
        'notes': MARKER})
    return result, report


def operations(original: dict, candidate: dict) -> list[dict]:
    old_nodes = {n['name']: n for n in original['nodes']}
    added = [n for n in candidate['nodes'] if n['name'] not in old_nodes]
    node_keys = {'credentials', 'disabled', 'id', 'name', 'notes', 'parameters', 'position', 'type', 'typeVersion'}
    behavior_keys = {'alwaysOutputData', 'executeOnce', 'maxTries', 'onError', 'retryOnFail', 'waitBetweenTries'}
    ops = [{'type': 'addNode', 'node': {key: deepcopy(value) for key, value in n.items() if key in node_keys}} for n in added]
    ops.extend({'type': 'setNodeSettings', 'nodeName': n['name'],
                'settings': {key: n[key] for key in behavior_keys if key in n}}
               for n in added if any(key in n for key in behavior_keys))
    for node in candidate['nodes']:
        old = old_nodes.get(node['name'])
        if old and node['parameters'] != old['parameters']:
            ops.append({'type': 'updateNodeParameters', 'nodeName': node['name'], 'parameters': deepcopy(node['parameters'])})
    def edges(workflow):
        return {(src, kind, oi, edge['node'], edge['index']) for src, channels in workflow['connections'].items()
                for kind, batches in channels.items() for oi, batch in enumerate(batches) for edge in batch}
    before, after = edges(original), edges(candidate)
    for action, values in [('removeConnection', before - after), ('addConnection', after - before)]:
        ops.extend({'type': action, 'source': src, 'target': dst, 'connectionType': kind, 'sourceIndex': oi, 'targetIndex': ii}
                   for src, kind, oi, dst, ii in sorted(values))
    ops.append({'type': 'setWorkflowSettings', 'settings': {'executionTimeout': -1}})
    old_groups = {g['name']: g for g in original.get('nodeGroups', [])}
    ops.extend({'type': 'addNodeGroup', 'name': g['name'], 'description': g.get('description', ''), 'nodeNames': g['nodeNames']}
               for g in candidate.get('nodeGroups', []) if g['name'] not in old_groups)
    ops.extend({'type': 'updateNodeGroup', 'groupName': g['name'], 'nodeNames': g['nodeNames']}
               for g in candidate.get('nodeGroups', []) if g['name'] in old_groups and g['nodeNames'] != old_groups[g['name']]['nodeNames'])
    return ops


def private_write(path: Path, value: Any) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as handle:
        if isinstance(value, str):
            handle.write(value)
        else:
            json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


class API:
    def __init__(self, base_url: str, key_file: Path, ca_file: Path | None = None):
        parsed = urlsplit(base_url)
        if (parsed.query or parsed.fragment or parsed.username or parsed.path not in {'', '/'} or
                not (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'})):
            raise ValueError('Use a verified HTTPS n8n origin or its loopback HTTP listener')
        info = key_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('API key file must be a private regular file')
        self.base, self.key = base_url.rstrip('/') + '/api/v1', key_file.read_text().strip()
        self.context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)

    def request(self, method: str, identifier: str, body: dict | None = None, *, activate=False):
        if identifier not in WORKFLOW_IDS:
            raise ValueError('Unreviewed workflow')
        path = '/workflows/' + identifier + ('/activate' if activate else '')
        if method == 'PUT':
            # n8n 2.x otherwise publishes active workflows as part of saving.
            # Publishing must be the separate, explicitly requested action.
            path += '?publishIfActive=false'
        if body is not None and 'nodeGroups' in body:
            body = deepcopy(body)
            ids = {node['name']: node['id'] for node in body['nodes']}
            body['nodeGroups'] = [{**{key: value for key, value in group.items() if key != 'nodeNames'},
                                   'nodeIds': [ids[name] for name in group['nodeNames']]}
                                  for group in body['nodeGroups']]
        request = Request(self.base + path, json.dumps(body).encode() if body is not None else None,
                          {'X-N8N-API-KEY': self.key, 'Content-Type': 'application/json'}, method=method)
        with urlopen(request, timeout=30, context=self.context) as response:
            result = json.load(response)
        if isinstance(result, dict) and 'nodeGroups' in result:
            names = {node['id']: node['name'] for node in result['nodes']}
            result['nodeGroups'] = [{**{key: value for key, value in group.items() if key != 'nodeIds'},
                                     'nodeNames': [names[node_id] for node_id in group['nodeIds']]}
                                    for group in result['nodeGroups']]
        return result


def stage(directory: Path, source, *, sdk=False) -> dict:
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {'schema_version': 1, 'readiness_url': READINESS_URL, 'wait_seconds': WAIT_SECONDS, 'workflows': []}
    for identifier in WORKFLOW_IDS:
        original = source(identifier)
        if original.get('activeVersion') and original['activeVersion'].get('sameAsDraft') is False:
            raise ValueError('Published graph differs from draft; review before queue migration')
        candidate, report = transform(original)
        private_write(directory / (identifier + '.original.json'), original)
        private_write(directory / (identifier + '.candidate.json'), candidate)
        private_write(directory / (identifier + '.operations.json'), operations(original, candidate))
        if sdk:
            from workflow_research_embeddings import preview_sdk
            private_write(directory / (identifier + '.sdk.ts'), preview_sdk(candidate, []))
        manifest['workflows'].append({**report, 'original_sha256': fingerprint(original), 'candidate_sha256': fingerprint(candidate)})
    private_write(directory / 'manifest.json', manifest)
    return manifest


def settings_match(actual: dict, expected: dict, default_execution_timeout: int | None = None) -> bool:
    actual = deepcopy(actual)
    if 'executionTimeout' not in actual and expected.get('executionTimeout') == -1 and default_execution_timeout == -1:
        # n8n removes a setting equal to the instance default when saving it.
        # Accept this only after independently confirming that default.
        actual['executionTimeout'] = -1
    return actual == expected


def apply(directory: Path, api: API, expected_manifest: str, *, publish=False, default_execution_timeout: int | None = None) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_text())
    if digest(manifest) != expected_manifest or [w['workflow_id'] for w in manifest['workflows']] != list(WORKFLOW_IDS):
        raise ValueError('Exact reviewed manifest required')
    # Validate the complete batch before the first mutation. Per-workflow fresh
    # checks catch later concurrent edits. Never restore an old staticData copy.
    staged = []
    for row in manifest['workflows']:
        identifier = row['workflow_id']
        original = json.loads((directory / (identifier + '.original.json')).read_text())
        candidate = json.loads((directory / (identifier + '.candidate.json')).read_text())
        if (fingerprint(original) != row['original_sha256'] or fingerprint(candidate) != row['candidate_sha256'] or
                fingerprint(transform(original)[0]) != row['candidate_sha256'] or
                fingerprint(api.request('GET', identifier)) != row['original_sha256']):
            raise ValueError('Workflow or reviewed candidate changed: ' + identifier)
        staged.append((identifier, row, original, candidate))
    updated = []
    for identifier, row, original, candidate in staged:
        if fingerprint(api.request('GET', identifier)) != row['original_sha256']:
            raise ValueError('Concurrent workflow edit; remaining batch stopped: ' + identifier)
        payload = {key: candidate[key] for key in ('name', 'nodes', 'connections', 'settings', 'nodeGroups') if key in candidate}
        api.request('PUT', identifier, payload)
        if publish and original.get('active'):
            api.request('POST', identifier, {}, activate=True)
        saved = api.request('GET', identifier)
        if any(saved.get(key) != candidate.get(key) for key in ('name', 'nodes', 'connections', 'nodeGroups')) or not settings_match(saved.get('settings', {}), candidate['settings'], default_execution_timeout):
            raise ValueError('Saved graph differs; reconcile before retry: ' + identifier)
        updated.append(identifier)
        private_write(directory / (identifier + '.applied.json'), {'workflow_id': identifier, 'versionId': saved.get('versionId'), 'published': bool(publish and original.get('active'))})
    return {'updated_workflow_ids': updated, 'published': publish}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['stage', 'apply'])
    parser.add_argument('--directory', type=Path, required=True, help='Private staged originals, candidates and manifest')
    parser.add_argument('--from-directory', type=Path, help='Read exact-ID JSON snapshots instead of contacting n8n')
    parser.add_argument('--base-url', default='http://127.0.0.1:15678')
    parser.add_argument('--api-key-file', type=Path)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--publish', action='store_true', help='Publish updated versions that were previously active')
    parser.add_argument('--confirmed-default-execution-timeout', type=int, choices=[-1],
                        help='Only pass after checking the actual instance default; allows n8n to omit its matching -1 timeout')
    parser.add_argument('--sdk', action='store_true', help='Write SDK previews for connector validation')
    args = parser.parse_args()
    api = API(args.base_url, args.api_key_file, args.ca_file) if args.api_key_file else None
    if args.command == 'stage':
        source = (lambda identifier: json.loads((args.from_directory / (identifier + '.json')).read_text())) if args.from_directory else (lambda identifier: api.request('GET', identifier))
        if not args.from_directory and api is None:
            parser.error('Stage requires --from-directory or --api-key-file')
        manifest = stage(args.directory, source, sdk=args.sdk)
        print(json.dumps({'staged_workflow_ids': list(WORKFLOW_IDS), 'manifest_sha256': digest(manifest)}))
    else:
        if api is None or not args.manifest_sha256 or args.from_directory:
            parser.error('Apply requires an API key, reviewed manifest digest, and no source directory')
        print(json.dumps(apply(args.directory, api, args.manifest_sha256, publish=args.publish,
                               default_execution_timeout=args.confirmed_default_execution_timeout)))


if __name__ == '__main__':
    main()
