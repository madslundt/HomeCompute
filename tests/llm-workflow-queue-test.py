#!/usr/bin/env python3
"""Queue graph invariants and synthetic item/deadline behavior; no live effects."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('llm_queue', ROOT / 'scripts/llm_workflow_queue.py')
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)


def fixture(identifier):
    nodes, connections = {}, {}
    def add(name, kind='noOp'):
        nodes.setdefault(name, {'id': name, 'name': name, 'type': 'n8n-nodes-base.' + kind,
                              'typeVersion': 1, 'position': [1200, 600], 'parameters': {}})
    def connect(source, target):
        connections.setdefault(source, {'main': [[]]})['main'][0].append({'node': target, 'type': 'main', 'index': 0})
    for target, sources, kind in q.GATES.get(identifier, []):
        add(target, kind)
        for source in sources:
            add(source)
            connect(source, target)
    if identifier in q.PARENTS:
        name, child = q.PARENTS[identifier]
        add(name, 'executeWorkflow')
        nodes[name]['parameters'] = {'workflowId': {'value': child}, 'options': {'waitForSubWorkflow': True}}
    if identifier == 'O0JTIc9aqA7OkrQS':
        add('Attach Aula discovery context', 'code')
        nodes['Attach Aula discovery context']['parameters'] = {'jsCode': "const original=$('Prepare retry state').first().json;const resumed=original;const base={\n  processingDeadline:resumed.processingDeadline || original.processingDeadline\n};return [{json:base}];"}
        add('Evaluate Aula review', 'code')
        nodes['Evaluate Aula review']['parameters'] = {'jsCode': "const base = $('Normalize structured facts').first(0, $runIndex).json;\nreturn [{json:base}];"}
        for name in ('HomeCompute automation model', 'Local Aula summary reviewer'):
            add(name, 'lmChatOpenAi')
            nodes[name]['parameters'] = {'options': {'timeout': "={{ Math.max(1000, Math.min(300000, Date.parse($('Prepare retry state').first().json.processingDeadline) - Date.now() - 120000)) }}"}}
    add('Independent deterministic action')
    return {'id': identifier, 'name': 'Synthetic queue fixture', 'active': True, 'versionId': 'v1',
            'activeVersionId': 'v1', 'nodes': list(nodes.values()), 'connections': connections,
            'settings': {'executionOrder': 'v1', 'executionTimeout': 300}, 'nodeGroups': []}


class QueueGraphTest(unittest.TestCase):
    def test_config_is_exact_activity_contract(self):
        config = json.loads((ROOT / 'config/llm-workflow-queue.json').read_text())
        self.assertEqual(config, {'schema_version': 1, 'workflow_ids': list(q.WORKFLOW_IDS),
                                 'readiness_url': q.READINESS_URL, 'wait_seconds': 120})

    def test_missing_timeout_only_means_unlimited_with_confirmed_global_default(self):
        expected = {'executionOrder': 'v1', 'executionTimeout': -1}
        actual = {'executionOrder': 'v1'}
        self.assertFalse(q.settings_match(actual, expected))
        self.assertFalse(q.settings_match(actual, expected, 3600))
        self.assertTrue(q.settings_match(actual, expected, -1))
        self.assertFalse(q.settings_match({'executionOrder': 'v0'}, expected, -1))

    def test_each_reviewed_workflow_transforms_without_touching_effects(self):
        for identifier in q.WORKFLOW_IDS:
            with self.subTest(identifier=identifier):
                original = fixture(identifier)
                saved = deepcopy(original)
                candidate, report = q.transform(original)
                self.assertEqual(original, saved)
                self.assertEqual(candidate['settings']['executionTimeout'], -1)
                self.assertEqual(report['previous_execution_timeout'], 300)
                self.assertEqual(next(n for n in candidate['nodes'] if n['name'] == 'Independent deterministic action'),
                                 next(n for n in original['nodes'] if n['name'] == 'Independent deterministic action'))
                for target, sources, _ in q.GATES.get(identifier, []):
                    edge = q.incoming(candidate, target)
                    self.assertEqual(len(edge), 1)
                    self.assertIn(edge[0][0].split(' · ')[-1], {'Restore input', 'Resume budget'})
                    for source in sources:
                        self.assertNotIn(target, [e['node'] for batch in candidate['connections'][source]['main'] for e in batch])
                self.assertEqual(q.fingerprint(q.transform(original)[0]), q.fingerprint(candidate))

    def test_wait_is_database_durable_and_both_if_outputs_are_wired(self):
        candidate, _ = q.transform(fixture(next(iter(q.GATES))))
        gate = [n for n in candidate['nodes'] if n.get('notes', '').startswith(q.MARKER) and n['type'] != 'n8n-nodes-base.stickyNote']
        check = next(n for n in gate if n['type'] == 'n8n-nodes-base.httpRequest')
        wait = next(n for n in gate if n['type'] == 'n8n-nodes-base.wait')
        condition = next(n for n in gate if n['type'] == 'n8n-nodes-base.if')
        merge = next(n for n in gate if n['type'] == 'n8n-nodes-base.merge')
        self.assertGreater(wait['parameters']['amount'] * 1000, 65000)
        self.assertEqual(wait['parameters']['unit'], 'seconds')
        self.assertEqual(check['parameters']['url'], q.READINESS_URL)
        self.assertEqual(check['parameters']['options']['timeout'], 5000)
        self.assertTrue(check['executeOnce'])
        self.assertEqual(check['onError'], 'continueRegularOutput')
        self.assertEqual(merge['parameters'], {'mode': 'chooseBranch', 'numberInputs': 2,
                         'chooseBranchMode': 'waitForAll', 'output': 'specifiedInput'})
        self.assertEqual(candidate['connections'][condition['name']]['main'][0][0]['node'], merge['name'])
        self.assertEqual(candidate['connections'][condition['name']]['main'][1][0]['node'], wait['name'])
        self.assertEqual(candidate['connections'][wait['name']]['main'][0][0]['node'], check['name'])
        self.assertEqual({edge['index'] for _, _, edge in q.incoming(candidate, merge['name'])}, {0, 1})

    def test_gating_inside_existing_group_preserves_group_connectivity(self):
        original = fixture(next(iter(q.GATES)))
        target = q.GATES[original['id']][0][0]
        original['nodeGroups'] = [{'id': 'existing', 'name': 'Existing stage', 'nodeNames': [target]}]
        candidate, _ = q.transform(original)
        self.assertEqual(len(candidate['nodeGroups']), 1)
        group = candidate['nodeGroups'][0]
        self.assertIn(target, group['nodeNames'])
        self.assertEqual(len(group['nodeNames']), 6)
        self.assertTrue(any(op['type'] == 'updateNodeGroup' for op in q.operations(original, candidate)))

    def test_connector_operations_explicitly_preserve_http_failure_behavior(self):
        original = fixture(next(iter(q.GATES)))
        candidate, _ = q.transform(original)
        ops = q.operations(original, candidate)
        check = next(n for n in candidate['nodes'] if n['name'].endswith('Check readiness'))
        added = next(op['node'] for op in ops if op['type'] == 'addNode' and op['node']['name'] == check['name'])
        self.assertNotIn('onError', added)
        self.assertNotIn('executeOnce', added)
        settings = next(op['settings'] for op in ops if op['type'] == 'setNodeSettings' and op['nodeName'] == check['name'])
        self.assertEqual(settings, {'executeOnce': True, 'retryOnFail': False, 'onError': 'continueRegularOutput'})

    def test_input_checkpoint_survives_multiple_offline_checks_then_resumes_once(self):
        # A native Merge's input 1 is the original stream; input 2 is readiness.
        # Persist/reload its held input like n8n does on the durable Wait path.
        original = [{'json': {'request_id': 'manual-42', 'text': 'æøå'}, 'binary': {'photo': {'id': 'filesystem-v2:abc'}},
                     'pairedItem': {'item': 0}}, {'json': {'request_id': 'webhook-43', 'value': 17}, 'pairedItem': {'item': 1}}]
        held = deepcopy(original)
        effects, waits = 0, 0
        for response in ({'available': False, 'state': 'offline'}, {'error': 'ECONNREFUSED'},
                         {'available': 'true', 'state': 'ready'}, {'available': True, 'state': 'draining'},
                         {'available': True, 'state': 'ready'}):
            available = response.get('available') is True and response.get('state') == 'ready'
            if not available:
                waits += 1
                held = json.loads(json.dumps(held))
                self.assertEqual(effects, 0)
                continue
            effects += 1
            result = held
        self.assertEqual(waits, 4)
        self.assertEqual(effects, 1)
        self.assertEqual(result, original)

    def test_unknown_changed_and_already_patched_graphs_fail_closed(self):
        original = fixture(next(iter(q.GATES)))
        changed = deepcopy(original)
        changed['connections']['Unknown source'] = {'main': [[{'node': q.GATES[original['id']][0][0], 'type': 'main', 'index': 0}]]}
        for bad in (changed, {**original, 'id': 'unreviewed'}, q.transform(original)[0]):
            with self.assertRaises(ValueError):
                q.transform(bad)

    def test_bounded_calls_preserve_existing_limits_and_limit_missing_calls(self):
        original = fixture(next(iter(q.GATES)))
        original['nodes'] += [
            {'name': 'Existing model', 'type': '@n8n/n8n-nodes-langchain.lmChatOpenAi', 'parameters': {'options': {'timeout': 180000}}},
            {'name': 'Unbounded HTTP', 'type': 'n8n-nodes-base.httpRequest', 'parameters': {}},
        ]
        candidate, report = q.transform(original)
        nodes = {n['name']: n for n in candidate['nodes']}
        self.assertEqual(nodes['Existing model']['parameters']['options']['timeout'], 180000)
        self.assertEqual(nodes['Unbounded HTTP']['parameters']['options']['timeout'], 60000)
        self.assertIn('Unbounded HTTP', report['bounded_calls'])

    def test_parent_propagation_only_updates_waiting_collector_parents(self):
        for identifier in q.PARENTS:
            candidate, report = q.transform(fixture(identifier))
            self.assertEqual(report['gate_targets'], [])
            self.assertFalse(any(n['type'] == 'n8n-nodes-base.wait' for n in candidate['nodes']))
            self.assertEqual(candidate['settings']['executionTimeout'], -1)

    def test_staged_plan_is_private_and_contains_exact_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue'
            manifest = q.stage(path, fixture)
            self.assertEqual(len(manifest['workflows']), 11)
            for row in manifest['workflows']:
                original_path = path / (row['workflow_id'] + '.original.json')
                self.assertEqual(original_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(q.fingerprint(json.loads(original_path.read_text())), row['original_sha256'])

    def test_drift_is_detected_before_any_remote_write(self):
        class API:
            def __init__(self):
                self.writes = 0
            def request(self, method, identifier, *args, **kwargs):
                if method != 'GET':
                    self.writes += 1
                return {**fixture(identifier), 'versionId': 'changed'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue'
            manifest = q.stage(path, fixture)
            api = API()
            with self.assertRaises(ValueError):
                q.apply(path, api, q.digest(manifest))
            self.assertEqual(api.writes, 0)

    def test_rest_save_stays_draft_and_maps_group_node_ids(self):
        original = fixture(next(iter(q.GATES)))
        candidate, _ = q.transform(original)
        payload = {key: candidate[key] for key in ('name', 'nodes', 'connections', 'settings', 'nodeGroups')}
        observed = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return observed[-1].data
        def request(req, **kwargs):
            observed.append(req)
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('synthetic-test-key')
            key.chmod(0o600)
            api = q.API('https://n8n.example.test', key)
            with patch.object(q, 'urlopen', request):
                saved = api.request('PUT', original['id'], payload)
        self.assertTrue(observed[0].full_url.endswith('?publishIfActive=false'))
        wire = json.loads(observed[0].data)
        self.assertTrue(all('nodeIds' in group and 'nodeNames' not in group for group in wire['nodeGroups']))
        self.assertEqual(saved, payload)
        self.assertEqual(payload['nodeGroups'], candidate['nodeGroups'])


class AulaPauseCodeTest(unittest.TestCase):
    def run_code(self, code, items, *, now, state=None, historic=None):
        source = '''const vm=require('vm');const data=JSON.parse(process.argv[1]);
const context={$input:{all:()=>data.items},$execution:{id:'123'},$json:data.items?.[0]?.json,$runIndex:0,$:()=>({first:()=>({json:data.historic})}),$getWorkflowStaticData:()=>data.state||{},Date:class extends Date{static now(){return data.now}}};
const result=vm.runInNewContext('(function(){'+data.code+'})()',context);
console.log(JSON.stringify({items:result,state:context.$getWorkflowStaticData()}));'''
        result = subprocess.run(['node', '-e', source, json.dumps({'code': code, 'items': items, 'now': now, 'state': state, 'historic': historic})],
                                check=True, text=True, capture_output=True)
        return json.loads(result.stdout)

    def test_current_budget_survives_historical_context_readers_and_model_timeouts(self):
        workflow = fixture('O0JTIc9aqA7OkrQS')
        q.patch_aula_deadline_reads(workflow)
        nodes = {node['name']: node for node in workflow['nodes']}
        historic = {'processingDeadline': '2026-10-26T09:00:00.000Z'}
        current = {'processingDeadline': '2026-10-27T09:00:00.000Z'}
        state = {'aulaCollectorRetry:123': {'context': current}}
        for name in ('Attach Aula discovery context', 'Evaluate Aula review'):
            result = self.run_code(nodes[name]['parameters']['jsCode'], [], now=1793001600000, state=state, historic=historic)
            self.assertEqual(result['items'][0]['json']['processingDeadline'], current['processingDeadline'])
        # Outage extends the current budget; model calls use that input, not
        # yesterday's immutable output from Prepare retry state.
        for name in ('HomeCompute automation model', 'Local Aula summary reviewer'):
            expression = nodes[name]['parameters']['options']['timeout'][3:-3]
            result = self.run_code('return [{json:{timeout:' + expression + '}}];', [{'json': current}],
                                   now=1793088000000, historic=historic)
            self.assertEqual(result['items'][0]['json']['timeout'], 300000)
            invalid = self.run_code('return [{json:{timeout:' + expression + '}}];', [{'json': {}}], now=1793088000000)
            self.assertEqual(invalid['items'][0]['json']['timeout'], 1000)

    def test_only_unavailable_pause_extends_exact_aula_deadline(self):
        item = {'json': {'__retryKey': 'aulaCollectorRetry:123', 'processingStartedAt': '2026-10-26T08:00:00.000Z',
                        'processingDeadline': '2026-10-26T09:00:00.000Z', 'request_id': 'original'},
                'binary': {'photo': {'id': 'filesystem-v2:photo'}}}
        started = 1793001600000
        captured = self.run_code(q.pause_capture_code(), [item], now=started)['items']
        state = {'aulaCollectorRetry:123': {'context': deepcopy(item['json']), 'attempt': 1}}
        fast = self.run_code(q.pause_restore_code(), captured, now=started + 5000, state=state)
        self.assertEqual(fast['items'][0]['json'], item['json'])
        delayed = self.run_code(q.pause_restore_code(), captured, now=started + 3600000, state=state)
        restored = delayed['items'][0]
        self.assertEqual(restored['json']['processingDeadline'], '2026-10-26T10:00:00.000Z')
        self.assertEqual(restored['json']['processingStartedAt'], item['json']['processingStartedAt'])
        self.assertEqual(restored['binary'], item['binary'])
        self.assertNotIn(q.PAUSE_FIELD, restored['json'])
        self.assertEqual(delayed['state']['aulaCollectorRetry:123']['context']['processingDeadline'], restored['json']['processingDeadline'])
        self.assertEqual(delayed['state']['aulaCollectorRetry:123']['attempt'], 1)

    def test_retry_semantic_wait_is_not_added_to_active_budget(self):
        # Capture starts after the existing semantic Wait; only availability
        # time after this new capture advances the original deadline.
        item = {'json': {'__retryKey': 'aulaCollectorRetry:123', 'processingDeadline': '2026-10-26T09:00:00.000Z'}}
        capture_at = 1793001600000 + 300000
        captured = self.run_code(q.pause_capture_code(), [item], now=capture_at)['items']
        state = {'aulaCollectorRetry:123': {'context': deepcopy(item['json'])}}
        result = self.run_code(q.pause_restore_code(), captured, now=capture_at + 120000, state=state)
        self.assertEqual(result['items'][0]['json']['processingDeadline'], '2026-10-26T09:02:00.000Z')

    def test_durable_wait_resume_rehydrates_missing_execution_cache_from_held_item(self):
        item = {'json': {'__retryKey': 'aulaCollectorRetry:123', 'processingDeadline': '2026-10-26T09:00:00.000Z',
                         'retryAttempt': 2, 'request_id': 'original', 'sourceEvidence': {'stable': 'retained'}},
                'binary': {'photo': {'id': 'filesystem-v2:photo'}}}
        started = 1793001600000
        held = self.run_code(q.pause_capture_code(), [item], now=started)['items']
        held = json.loads(json.dumps(held))  # Database persistence before another process resumes it.
        result = self.run_code(q.pause_restore_code(), held, now=started + 3600000,
                               state={'aulaCollectorRetry:other': {'context': {'untouched': True}}})
        output = result['items'][0]
        self.assertEqual(output['json']['processingDeadline'], '2026-10-26T10:00:00.000Z')
        self.assertEqual(output['json']['sourceEvidence'], item['json']['sourceEvidence'])
        self.assertEqual(output['binary'], item['binary'])
        self.assertEqual(output['pairedItem'], {'item': 0})
        self.assertEqual(result['state']['aulaCollectorRetry:123']['context'], output['json'])
        self.assertEqual(result['state']['aulaCollectorRetry:123']['attempt'], 2)
        self.assertEqual(result['state']['aulaCollectorRetry:other']['context'], {'untouched': True})

    def test_repeated_waits_use_durable_current_deadline_instead_of_stale_cache(self):
        started = 1793001600000
        item = {'json': {'__retryKey': 'aulaCollectorRetry:123', 'processingDeadline': '2026-10-26T09:00:00.000Z'}}
        held = self.run_code(q.pause_capture_code(), [item], now=started)['items']
        first = self.run_code(q.pause_restore_code(), held, now=started + 120000, state={})['items']
        next_held = self.run_code(q.pause_capture_code(), first, now=started + 180000)['items']
        stale = {'aulaCollectorRetry:123': {'context': deepcopy(item['json']), 'attempt': 4,
                                          'weeklyMessageOutput': 'existing retained field'}}
        second = self.run_code(q.pause_restore_code(), next_held, now=started + 300000, state=stale)
        self.assertEqual(second['items'][0]['json']['processingDeadline'], '2026-10-26T09:04:00.000Z')
        self.assertEqual(second['state']['aulaCollectorRetry:123']['weeklyMessageOutput'], 'existing retained field')
        self.assertEqual(second['state']['aulaCollectorRetry:123']['attempt'], 4)
        self.assertNotIn(q.PAUSE_FIELD, second['items'][0]['json'])

    def test_foreign_execution_context_is_rejected_even_with_matching_cache(self):
        item = {'json': {'__retryKey': 'aulaCollectorRetry:foreign', 'processingDeadline': '2026-10-26T09:00:00.000Z'}}
        held = self.run_code(q.pause_capture_code(), [item], now=1793001600000)['items']
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_code(q.pause_restore_code(), held, now=1793001720000,
                          state={'aulaCollectorRetry:foreign': {'context': item['json']}})


if __name__ == '__main__':
    unittest.main()
