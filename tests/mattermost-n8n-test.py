#!/usr/bin/env python3
"""Private topic audience, delivery identity, and retry safety checks."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mattermost_n8n', ROOT / 'scripts/setup-mattermost-n8n.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
OWNER, TEAM, BOT = 'o' * 26, 't' * 26, 'n' * 26
TOKEN = 'delivery-private-token-' + 'x' * 30


class Server:
    def __init__(self):
        self.bot = None
        self.username = 'n8n'
        self.occupied = None
        self.channels = {'town-square': {'id': 'a' * 26, 'type': 'O'}, 'off-topic': {'id': 'b' * 26, 'type': 'O'}}
        self.members = {'a' * 26: set(), 'b' * 26: set()}
        self.team_member = False
        self.tokens = 0
        self.uncertain = False
        self.methods = []

    def request(self, token, method, path, body=None, **kwargs):
        self.methods.append((method, path))
        if path == '/users/me':
            return ({'id': BOT, 'is_bot': True, 'roles': 'system_user'} if token == TOKEN
                    else {'id': OWNER, 'roles': 'system_user system_admin'}), {}
        if path == '/teams/name/homecompute':
            return {'id': TEAM}, {}
        if path.startswith('/users/username/'):
            name = path.rsplit('/', 1)[-1]
            if name == setup.BOT_USERNAME and self.occupied:
                return self.occupied, {}
            return (self.user() if self.bot and name == self.username else None), {}
        if path == '/bots' and method == 'POST':
            self.bot = {'user_id': BOT, 'owner_id': OWNER, **body}
            self.username = body['username']
            return self.bot, {}
        if path == '/bots/' + BOT:
            if method == 'PUT':
                self.bot.update(body)
                self.username = body['username']
            return self.bot, {}
        if path == '/users/' + BOT:
            return self.user(), {}
        if path == '/users/' + BOT + '/tokens':
            self.tokens += 1
            if self.uncertain:
                raise setup.bootstrap.BootstrapError('uncertain token response')
            return {'id': 'k' * 26, 'token': TOKEN, 'user_id': BOT}, {}
        if '/channels/name/' in path:
            return self.channels.get(path.rsplit('/', 1)[-1]), {}
        if path == '/channels' and method == 'POST':
            value = {'id': str(len(self.channels) + 1).zfill(26), **body}
            self.channels[body['name']] = value
            self.members[value['id']] = {OWNER}
            return value, {}
        if path.startswith('/teams/') and '/members' in path:
            if method == 'GET':
                return ({} if self.team_member else None), {}
            self.team_member = True
            self.members['a' * 26].add(BOT)
            self.members['b' * 26].add(BOT)
            return body, {}
        if '/members' in path:
            channel = path.split('/')[2]
            if '?' in path:
                return [{'user_id': user} for user in self.members[channel]], {}
            if method == 'GET':
                return ({} if path.rsplit('/', 1)[-1] in self.members[channel] else None), {}
            if method == 'DELETE':
                self.members[channel].remove(path.rsplit('/', 1)[-1])
            else:
                self.members[channel].add(body['user_id'])
            return None, {}
        if path == f'/users/{BOT}/teams/{TEAM}/channels':
            return [{'id': v['id'], 'type': v['type']} for v in self.channels.values() if BOT in self.members[v['id']]], {}
        raise AssertionError((method, path))

    def user(self):
        return {'id': BOT, 'username': self.username, 'is_bot': True, 'roles': 'system_user'}


class MattermostN8nTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'private'
        self.directory.mkdir(mode=0o700)
        setup.bootstrap.save(self.directory / 'credentials.json', {'operator_id': OWNER, 'operator_session': 'operator'})
        self.agents = {'default': 'c' * 26, 'personal': 'p' * 26, 'researcher': 'r' * 26,
                       'engineer': 'e' * 26, 'operator': 'q' * 26, 'shopper': 's' * 26, 'keeper': 'k' * 26,
                       'travel': 'v' * 26, 'cook': 'h' * 26, 'administrator': 'j' * 26, 'finance': 'f' * 26,
                       'family': 'g' * 26}
        setup.bootstrap.save(self.directory / 'agent-accounts.json', {'accounts': [
            {'accountId': name, 'bot_id': identifier} for name, identifier in self.agents.items()]})
        self.server = Server()

    def invoke(self, rename_only=False):
        def api(token):
            client = unittest.mock.Mock()
            client.request.side_effect = lambda *args, **kwargs: self.server.request(token, *args, **kwargs)
            return client
        with patch.object(setup.bootstrap, 'API', side_effect=api):
            return setup.rename_delivery_bot(self.directory) if rename_only else setup.provision(self.directory)

    def test_legacy_delivery_bot_is_renamed_without_changing_credentials_or_channels(self):
        self.invoke()
        before = {p.name: p.read_bytes() for p in self.directory.glob('*.json')}
        self.server.username = 'n8n'
        self.server.bot.update(username='n8n', display_name='n8n updates', description='Private workflow update delivery.')
        self.server.methods.clear()
        result = self.invoke(rename_only=True)
        self.assertEqual(result, {'bot_id': BOT, 'username': 'hermes', 'display_name': 'Hermes · Updates'})
        self.assertEqual(self.server.username, 'hermes')
        self.assertEqual(self.server.bot['display_name'], 'Hermes · Updates')
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.glob('*.json')}, before)
        self.assertEqual([(m, p) for m, p in self.server.methods if m != 'GET'], [('PUT', '/bots/' + BOT)])
        self.server.methods.clear()
        self.invoke(rename_only=True)
        self.assertTrue(all(m == 'GET' for m, _ in self.server.methods))
        self.invoke()
        self.assertEqual(self.server.tokens, 1)

    def test_occupied_hermes_name_is_refused_without_mutation(self):
        self.invoke()
        self.server.username = 'n8n'
        self.server.occupied = {'id': 'z' * 26}
        self.server.methods.clear()
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'another account'):
            self.invoke(rename_only=True)
        self.assertTrue(all(m == 'GET' for m, _ in self.server.methods))

    def test_existing_hermes_without_retained_identity_is_not_adopted(self):
        self.server.occupied = {'id': 'z' * 26}
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'another account'):
            self.invoke()
        self.assertTrue(all(m == 'GET' for m, _ in self.server.methods))

    def test_private_audiences_and_single_delivery_token_survive_repeated_setup(self):
        first = self.invoke()
        second = self.invoke()
        self.assertEqual(first, second)
        self.assertEqual(self.server.tokens, 1)
        for slug, (_, specialist, _) in setup.TOPICS.items():
            channel = self.server.channels[slug]
            self.assertEqual(channel['type'], 'P')
            expected = {OWNER, BOT, self.agents[specialist]}
            expected.add(self.agents['default'])
            if slug == 'shopping':
                expected.update({self.agents['keeper'], self.agents['cook']})
            if slug in setup.ADDITIONAL_SPECIALISTS:
                expected.add(self.agents[setup.ADDITIONAL_SPECIALISTS[slug]])
            self.assertEqual(self.server.members[channel['id']], expected)
        self.assertNotIn(BOT, self.server.members['b' * 26])
        self.assertIn(BOT, self.server.members['a' * 26])
        self.assertNotIn(TOKEN, json.dumps(first))
        for name in ['n8n-integration.json', 'n8n-channels.json']:
            self.assertEqual(stat.S_IMODE((self.directory / name).stat().st_mode), 0o600)
        self.assertFalse(any(path.startswith('/posts') for _, path in self.server.methods))

    def test_consolidated_topics_share_delivery_identity_without_recreating_retired_rooms(self):
        mapping = self.invoke()['channels']
        for source, target in setup.ALIASES.items():
            self.assertEqual(mapping[source], mapping[target])
            self.assertNotIn(source, self.server.channels)
        self.assertNotEqual(mapping['travel'], mapping['shopping'])
        self.assertIn(self.agents['keeper'], self.server.members[mapping['shopping']])

    def test_travel_specialist_joins_existing_private_topic_without_removing_luna(self):
        before = {key: value for key, value in setup.ADDITIONAL_SPECIALISTS.items() if key != 'travel'}
        with patch.object(setup, 'ADDITIONAL_SPECIALISTS', before):
            first = self.invoke()
        channel = self.server.channels['travel']['id']
        audience = self.server.members[channel].copy()
        self.assertIn(self.agents['personal'], audience)
        self.assertNotIn(self.agents['travel'], audience)
        second = self.invoke()
        self.assertEqual(first, second)
        self.assertEqual(self.server.members[channel], audience | {self.agents['travel']})
        self.assertEqual(self.server.tokens, 1)

    def test_retained_shared_adult_receipt_adds_only_explicitly_approved_topics_and_replays(self):
        mapping = self.invoke()
        partner = 'u' * 26
        approved = {mapping['channels'][name] for name in ('family', 'shopping', 'tasks', 'finances')}
        receipt = {'schema_version': 1, 'owner_id': OWNER, 'partner_id': partner,
                   'partner_username': 'kristina', 'team_id': TEAM,
                   'family_channel_id': mapping['channels']['family'],
                   'audience_policy': 'all-except-systems', 'shared_channel_ids': sorted(approved),
                   'system_channel_ids': ['x' * 26, 'y' * 26]}
        setup.bootstrap.save(self.directory / 'household-access.json', receipt)
        before = {name: values.copy() for name, values in self.server.members.items()}
        self.invoke()
        for identifier, members in self.server.members.items():
            self.assertEqual(members, before[identifier] | ({partner} if identifier in approved else set()))
        self.server.methods.clear()
        self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.methods))
        self.assertEqual(self.server.tokens, 1)
        self.assertEqual(setup.bootstrap.read_private(self.directory / 'household-access.json'), receipt)

    def test_shared_receipt_with_system_channel_overlap_is_rejected(self):
        mapping = self.invoke()
        identifier = mapping['channels']['shopping']
        setup.bootstrap.save(self.directory / 'household-access.json', {
            'schema_version': 1, 'owner_id': OWNER, 'partner_id': 'u' * 26, 'team_id': TEAM,
            'family_channel_id': mapping['channels']['family'], 'audience_policy': 'all-except-systems',
            'shared_channel_ids': [identifier], 'system_channel_ids': [identifier]})
        self.server.methods.clear()
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'audience policy'):
            self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.methods))

    def test_legacy_adult_receipt_remains_family_only(self):
        mapping = self.invoke()
        partner = 'u' * 26
        setup.bootstrap.save(self.directory / 'household-access.json', {
            'schema_version': 1, 'owner_id': OWNER, 'partner_id': partner, 'team_id': TEAM,
            'family_channel_id': mapping['channels']['family']})
        self.invoke()
        for slug, channel in mapping['channels'].items():
            self.assertEqual(partner in self.server.members[channel], channel == mapping['channels']['family'])

    def test_uncertain_delivery_token_is_not_created_twice(self):
        self.server.uncertain = True
        with self.assertRaises(setup.bootstrap.BootstrapError): self.invoke()
        self.server.uncertain = False
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'reconciliation'): self.invoke()
        self.assertEqual(self.server.tokens, 1)
        self.assertTrue((self.directory / 'n8n-token-pending.json').is_file())

    def test_existing_public_topic_or_additional_human_is_refused(self):
        self.server.channels['family'] = {'id': 'j' * 26, 'team_id': TEAM, 'type': 'O'}
        self.server.members['j' * 26] = {OWNER}
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'privacy'): self.invoke()
        self.server.channels['family']['type'] = 'P'
        self.server.members['j' * 26].add('z' * 26)
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'audience'): self.invoke()

    def test_foreign_bot_is_not_adopted(self):
        self.server.bot = {'user_id': BOT, 'owner_id': 'z' * 26}
        with self.assertRaisesRegex(setup.bootstrap.BootstrapError, 'ownership'): self.invoke()
        self.assertEqual(self.server.tokens, 0)

    def test_credential_identity_guard_precedes_n8n_command(self):
        self.invoke()
        path = self.directory / 'n8n-integration.json'
        value = setup.bootstrap.read_private(path)
        value['credential_id'] = '../../other'
        setup.bootstrap.save(path, value)
        with patch.object(setup, 'command') as command:
            with self.assertRaises(setup.bootstrap.BootstrapError): setup.import_credential(self.directory)
        command.assert_not_called()

    def test_failed_cli_output_is_private_and_never_in_public_error(self):
        completed = subprocess.CompletedProcess(['n8n'], 1, TOKEN, 'private credential error')
        with patch.object(setup.subprocess, 'run', return_value=completed):
            with self.assertRaises(setup.bootstrap.BootstrapError) as caught:
                setup.command(['n8n'], self.directory)
        self.assertNotIn(TOKEN, str(caught.exception))
        path = self.directory / 'n8n-last-failure.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())['stdout'], TOKEN)


if __name__ == '__main__':
    unittest.main()
