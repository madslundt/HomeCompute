"""Real broker HTTP/MCP authority boundaries using durable synthetic targets."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/codex-worker'))
from actions import ActionLedger
from broker import Ledger, serve

TOKENS = {role: 'synthetic-' + role + '-' * 32 for role in ('assistant', 'worker', 'operator')}
PROJECT = {'repository': 'example/synthetic', 'base_sha': 'a' * 40, 'base_branch': 'main',
           'classification': 'cloud_allowed', 'tests': ['python3', '-m', 'unittest'], 'write_prefixes': ['src/']}


class NoPublisher:
    def publish(self, *_):
        raise AssertionError('action routes cannot publish code')


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / 'tasks.sqlite3'
        self.now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()
        self.ledger = Ledger(path, {'demo': copy.deepcopy(PROJECT)})
        self.actions = ActionLedger(path, json.loads((ROOT / 'config/system-actions.json').read_text()),
                                    json.loads((ROOT / 'config/system-monitoring.json').read_text()), clock=lambda: self.now)
        self.actions.seed_synthetic('synthetic-core', healthy=False, version=1)
        self.server = serve(self.ledger, TOKENS, NoPublisher(), ('127.0.0.1', 0), self.actions)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        stamp = lambda offset: datetime.fromtimestamp(self.now + offset, timezone.utc).isoformat().replace('+00:00', 'Z')
        self.evidence = {'incident_key': 'synthetic:episode:1', 'gates': {}, 'observation': {
            'system_id': 'synthetic-core', 'check_id': 'synthetic.health', 'stable_key': 'synthetic-core:synthetic.health',
            'category': 'health', 'status': 'degraded', 'severity': 'warning', 'evidence': {'state': 'unhealthy'},
            'observed_at': stamp(0), 'expires_at': stamp(300), 'action': 'review_only', 'approval_required': True,
            'physical_device_actions': False}}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.actions.db.close(); self.ledger.db.close(); self.temp.cleanup()

    def request(self, path, body=None, *, role='operator', method=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {'Authorization': 'Bearer ' + TOKENS[role], 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        try:
            connection.request(method or ('GET' if body is None else 'POST'), path,
                               body=None if body is None else json.dumps(body), headers=headers)
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    def tool(self, name, arguments):
        return self.request('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                    'params': {'name': name, 'arguments': arguments}}, role='assistant')[1]

    def proposal(self):
        eid = self.request('/actions/evidence', self.evidence)[1]['id']
        return {'action_id': 'synthetic.recover', 'target': 'synthetic-core',
                'incident_key': 'synthetic:episode:1', 'evidence_id': eid}

    def test_assistant_proposes_but_only_operator_approves_and_executes(self):
        proposal = self.proposal()
        result = self.tool('homecompute_action_propose', proposal)['result']
        action = json.loads(result['content'][0]['text'])['actions'][0]
        self.assertEqual(action['state'], 'pending')
        prefix = '/actions/' + action['id']
        for role in ('assistant', 'worker'):
            self.assertNotEqual(self.request('/actions/evidence', self.evidence, role=role)[0], 200)
            self.assertNotEqual(self.request(prefix + '/review', role=role)[0], 200)
            for mutation in ('approve', 'execute'):
                self.assertEqual(self.request(prefix + '/' + mutation, {}, role=role)[0], 403)
        self.assertEqual(self.request(prefix + '/execute', {})[0], 400)
        digest = self.request(prefix + '/review')[1]['approval_sha256']
        self.assertEqual(self.request(prefix + '/approve', {'approval_sha256': '0' * 64})[0], 400)
        self.assertEqual(self.request(prefix + '/approve', {'approval_sha256': digest})[1]['state'], 'approved')
        self.assertEqual(self.request(prefix + '/execute', {})[1]['state'], 'completed')
        self.assertEqual(self.request(prefix + '/execute', {})[0], 400)
        self.assertTrue(self.actions.synthetic_state('synthetic-core')['healthy'])
        self.assertEqual(self.actions.audit(action['id'])[-1]['state'], 'completed')

    def test_mcp_inventory_and_no_operator_tools_or_argument_injection(self):
        inventory = self.request('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, role='assistant')[1]
        self.assertEqual(len(inventory['result']['tools']), 6)
        proposal = self.proposal()
        for key in ('command', 'url', 'token', 'gates', 'observed_at', 'approval'):
            self.assertIn('error', self.tool('homecompute_action_propose', {**proposal, key: 'untrusted'}))
        for name in ('homecompute_action_approve', 'homecompute_action_execute', 'homecompute_action_evidence'):
            self.assertIn('error', self.tool(name, {}))
        self.assertEqual(self.actions.list_actions(), [])

    def test_assistant_cancellation_and_status_do_not_disclose_approval_or_evidence(self):
        action = self.request('/actions', self.proposal(), role='assistant')[1]
        value = self.tool('homecompute_action_status', {'task_id': action['id']})['result']
        public = json.loads(value['content'][0]['text'])['actions'][0]
        self.assertFalse({'snapshot', 'approval_sha256', 'approved_at', 'evidence'} & set(public))
        value = self.tool('homecompute_action_cancel', {'task_id': action['id']})['result']
        self.assertEqual(json.loads(value['content'][0]['text'])['actions'][0]['state'], 'cancelled')

    def test_coding_task_policy_drift_is_rejected_before_execution_and_publication(self):
        body = {'project': 'demo', 'issue_key': 'policy:drift:1', 'summary': 'synthetic', 'context': ''}
        task = self.ledger.submit(body)
        self.ledger.projects['demo']['base_sha'] = 'b' * 40
        with self.assertRaises(ValueError): self.ledger.action(task['id'], 'approve', {})
        self.ledger.projects['demo']['base_sha'] = 'a' * 40
        self.ledger.action(task['id'], 'approve', {})
        self.ledger.projects['demo']['base_sha'] = 'b' * 40
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.get(task['id'])['state'], 'failed')

    def test_fresh_evidence_refresh_revokes_approval_over_http(self):
        action = self.request('/actions', self.proposal(), role='assistant')[1]
        prefix = '/actions/' + action['id']
        digest = self.request(prefix + '/review')[1]['approval_sha256']
        self.request(prefix + '/approve', {'approval_sha256': digest})
        self.now += 1
        fresh = copy.deepcopy(self.evidence)
        fresh['observation']['observed_at'] = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        eid = self.request('/actions/evidence', fresh)[1]['id']
        self.assertEqual(self.request(prefix + '/execute', {})[0], 400)
        self.assertEqual(self.request(prefix + '/refresh', {'evidence_id': eid}, role='assistant')[0], 403)
        self.assertEqual(self.request(prefix + '/refresh', {'evidence_id': eid})[1]['state'], 'pending')
        self.assertEqual(self.request(prefix + '/approve', {'approval_sha256': digest})[0], 400)
        digest = self.request(prefix + '/review')[1]['approval_sha256']
        self.request(prefix + '/approve', {'approval_sha256': digest})
        self.assertEqual(self.request(prefix + '/execute', {})[1]['state'], 'completed')

    def test_official_sdk_action_propose_status_cancel(self):
        sdk_root = os.environ.get('MCP_SDK_ROOT')
        if not sdk_root:
            self.skipTest('MCP_SDK_ROOT required for official SDK verification')
        sdk = Path(sdk_root).resolve() / 'dist/esm/client'
        proposal = self.proposal()
        script = '\n'.join([
            'import {Client} from ' + json.dumps((sdk / 'index.js').as_uri()) + ';',
            'import {StreamableHTTPClientTransport} from ' + json.dumps((sdk / 'streamableHttp.js').as_uri()) + ';',
            'const client = new Client({name:"synthetic-actions",version:"1"},{capabilities:{}});',
            'try {',
            'await client.connect(new StreamableHTTPClientTransport(new URL(process.env.MCP_TEST_URL),{requestInit:{headers:{Authorization:"Bearer "+process.env.MCP_TEST_TOKEN}}}));',
            'const tools = await client.listTools(); if (tools.tools.length !== 6) throw Error("inventory");',
            'const proposal = await client.callTool({name:"homecompute_action_propose",arguments:' + json.dumps(proposal) + '});',
            'const action = JSON.parse(proposal.content[0].text).actions[0]; if(action.state!=="pending") throw Error("approval escalation");',
            'const status = await client.callTool({name:"homecompute_action_status",arguments:{task_id:action.id}});',
            'if(JSON.parse(status.content[0].text).actions[0].state!=="pending") throw Error("status");',
            'const cancelled = await client.callTool({name:"homecompute_action_cancel",arguments:{task_id:action.id}});',
            'if(JSON.parse(cancelled.content[0].text).actions[0].state!=="cancelled") throw Error("cancel");',
            '} finally {await client.close();}',
        ])
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=15,
                                env={**os.environ, 'MCP_TEST_URL': f'http://127.0.0.1:{self.server.server_port}/mcp',
                                     'MCP_TEST_TOKEN': TOKENS['assistant']})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
