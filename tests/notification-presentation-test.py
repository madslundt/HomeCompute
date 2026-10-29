"""Presentation patches only replace existing Mattermost message parameters."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workflow_notification_presentation import patch, TARGETS

class NotificationPresentationTests(unittest.TestCase):
    def fixture(self, kind):
        return {'nodes': [{'id': 'stable-node', 'name': TARGETS[kind][0],
                'type': 'n8n-nodes-base.httpRequest', 'credentials': {'mattermostApi': {'id': 'credential'}},
                'parameters': {'method': 'POST', 'url': 'https://mattermost.home.arpa/api/v4/posts',
                    'nodeCredentialType': 'mattermostApi', 'authentication': 'predefinedCredentialType',
                    'bodyParameters': {'parameters': [{'name': 'channel_id', 'value': 'existing-audience'},
                                                      {'name': 'message', 'value': 'existing-message'}]},
                    'options': {'timeout': 8000}}}, {'id': 'stable-formatter', 'name': TARGETS[kind][2],
                    'type': 'n8n-nodes-base.code', 'parameters': {'mode': 'runOnceForAllItems',
                    'jsCode': 'return [{json:{message:"existing",report:"existing",visibleCharacters:8}}];'}}],
                'connections': {'unchanged': {}}, 'active': True}

    def test_only_message_changes_and_input_is_not_mutated(self):
        for kind in TARGETS:
            before = self.fixture(kind); original = deepcopy(before)
            operations = patch(before, kind)
            self.assertEqual(before, original)
            self.assertEqual(len(operations), 2)
            self.assertTrue(all(op['type'] == 'updateNodeParameters' for op in operations))
            after = operations[1]['parameters']
            self.assertEqual(after['options'], original['nodes'][0]['parameters']['options'])
            self.assertEqual(after['bodyParameters']['parameters'][0], {'name': 'channel_id', 'value': 'existing-audience'})
            self.assertIn('mattermostMessage', after['bodyParameters']['parameters'][1]['value'])
            self.assertIn(original['nodes'][1]['parameters']['jsCode'], operations[0]['parameters']['jsCode'])
            self.assertIn('catch (_)', operations[0]['parameters']['jsCode'])

    def test_reapplying_source_updates_replaces_one_wrapper_without_nesting(self):
        fixture = self.fixture('weekly')
        for _ in range(2):
            for operation in patch(fixture, 'weekly'):
                next(node for node in fixture['nodes'] if node['name'] == operation['nodeName'])['parameters'] = operation['parameters']
        code = fixture['nodes'][1]['parameters']['jsCode']
        self.assertEqual(code.count('const validatedItems'), 1)

    def test_formatter_executes_in_code_and_keeps_original_outputs_on_presentation_failure(self):
        for kind in TARGETS:
            operation = patch(self.fixture(kind), kind)[0]
            code = operation['parameters']['jsCode']
            harness = "const fs=require('fs');const code=fs.readFileSync(0,'utf8');const result=new Function('$',code)(()=>({item:{json:{reportType:'weekly'}}}));process.stdout.write(JSON.stringify(result));"
            result = subprocess.run(['node', '-e', harness], input=code, capture_output=True,
                                    text=True, check=True, timeout=10)
            item = json.loads(result.stdout)[0]['json']
            self.assertEqual(item['report'], 'existing')
            self.assertEqual(item['message'], 'existing')
            self.assertEqual(item['visibleCharacters'], 8)
            if kind == 'news':
                self.assertEqual(item['mattermostMessage'], '')
                self.assertIn('mattermostPresentationError', item)
            else:
                self.assertTrue(item['mattermostMessage'].startswith('Battery review: existing'))

    def test_changed_transport_or_audience_schema_requires_inspection(self):
        for kind in TARGETS:
            for field, value in [('url', 'https://elsewhere.invalid'), ('method', 'GET'), ('nodeCredentialType', 'other')]:
                fixture = self.fixture(kind); fixture['nodes'][0]['parameters'][field] = value
                with self.assertRaises(ValueError): patch(fixture, kind)
            fixture = self.fixture(kind); fixture['nodes'][0]['parameters']['bodyParameters']['parameters'].pop(0)
            with self.assertRaises(ValueError): patch(fixture, kind)

if __name__ == '__main__': unittest.main()
