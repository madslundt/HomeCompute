"""Consolidation keeps audiences, private boundaries and notification behavior bounded."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mattermost_channel_destinations as delivery
spec = importlib.util.spec_from_file_location('native', ROOT / 'scripts/setup-openclaw-mattermost.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
spec = importlib.util.spec_from_file_location('organization', ROOT / 'scripts/organize-mattermost.py')
organization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(organization)


class ConsolidationTests(unittest.TestCase):
    def test_reminder_retarget_preserves_credentials_mode_owner_and_retries_without_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'household-tasks.json'
            current = {'schema_version': 1, 'channel_id': 'hrnwimmho7b4fkaa5xcm9d9i8h',
                       'bot_id': 'b' * 26, 'token_file': '/private/unchanged-token'}
            path.write_text(json.dumps(current)); path.chmod(0o600)
            with patch.object(organization, 'TASK_CONFIG', path), patch.object(organization.b, 'PRIVATE', root), \
                    patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())):
                organization.reconcile_task_destination()
                self.assertEqual(json.loads(path.read_text()), current)
                organization.reconcile_task_destination(apply=True)
                self.assertEqual(json.loads(path.read_text()),
                                 {**current, 'channel_id': 'fth7yi7ea7yutyscphhbjgehce'})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertFalse(organization.reconcile_task_destination(apply=True)['changed'])
                self.assertEqual(len(list(root.glob('task-destination-*.json'))), 1)
                current['channel_id'] = 'x' * 26
                path.write_text(json.dumps(current))
                with self.assertRaises(organization.b.BootstrapError):
                    organization.reconcile_task_destination(apply=True)

    def test_family_retirement_only_revokes_gaia_ingress_and_retains_private_settings(self):
        owner, partner, family = 'o' * 26, 'p' * 26, 'f' * 26
        before = {'models': {'retained': True}, 'tools': {'retained': True}, 'channels': {'mattermost': {
            'groupAllowFrom': [owner, partner], 'accounts': {
                'default': {'groupAllowFrom': [owner, partner], 'allowFrom': [owner], 'dmPolicy': 'allowlist'},
                'family': {'enabled': True, 'groupPolicy': 'allowlist', 'dmPolicy': 'disabled', 'allowFrom': [],
                           'groupAllowFrom': [owner, partner], 'groups': {family: {'requireMention': False}}}}}}}
        expected = copy.deepcopy(before)
        expected['channels']['mattermost']['accounts']['family'].update(enabled=False, groupPolicy='disabled')
        after, batch = native.family_coordinator_candidate(before, owner, partner, family)
        self.assertEqual(after, expected)
        self.assertEqual(len(batch), 2)
        self.assertEqual(native.family_coordinator_candidate(after, owner, partner, family)[1], [])
        bad = copy.deepcopy(before)
        bad['channels']['mattermost']['accounts']['default']['allowFrom'].append(partner)
        with self.assertRaises(ValueError):
            native.family_coordinator_candidate(bad, owner, partner, family)

    def test_notification_patch_changes_only_channel_literal_and_keeps_system_review_private(self):
        workflow = {'id': 'RjId1K2BPpv5lAVN', 'nodes': [{
            'name': delivery.NODES['RjId1K2BPpv5lAVN'][0], 'type': 'n8n-nodes-base.httpRequest',
            'parameters': {'method': 'POST', 'url': 'https://mattermost.home.arpa/api/v4/posts',
                'nodeCredentialType': 'mattermostApi', 'bodyParameters': {'parameters': [
                    {'name': 'channel_id', 'value': "={{ weekly:'pc5q5ko9e3d7zpixr74j49u7uh',system:'pzrmq1a583yc3jbm839b3fxmfr' }}"},
                    {'name': 'message', 'value': 'Unchanged message expression'}]}}}]}
        original = copy.deepcopy(workflow)
        operations = delivery.patch(workflow)
        self.assertEqual(workflow, original)
        parameters = operations[0]['parameters']
        self.assertIn('foxof7h57jrwmcqacmafjw713c', parameters['bodyParameters']['parameters'][0]['value'])
        self.assertIn('pzrmq1a583yc3jbm839b3fxmfr', parameters['bodyParameters']['parameters'][0]['value'])
        self.assertEqual(parameters['bodyParameters']['parameters'][1], original['nodes'][0]['parameters']['bodyParameters']['parameters'][1])
        workflow['nodes'][0]['parameters'] = parameters
        self.assertEqual(delivery.patch(workflow), [])
        workflow['nodes'][0]['parameters']['url'] = 'https://wrong.invalid/api/v4/posts'
        with self.assertRaises(ValueError):
            delivery.patch(workflow)


if __name__ == '__main__':
    unittest.main()
