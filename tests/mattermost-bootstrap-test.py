#!/usr/bin/env python3
"""Exercise provisioning retries without resetting users or duplicating bot tokens."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mattermost_bootstrap', ROOT / 'scripts/bootstrap-mattermost.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
OWNER, TEAM, BOT, TOKEN_ID = 'o' * 26, 't' * 26, 'b' * 26, 'k' * 26


class Server:
    def __init__(self):
        self.user = None
        self.password = None
        self.team = None
        self.bot = None
        self.channels = {}
        self.members = set()
        self.commands = []
        self.requests = []
        self.token_creations = 0
        self.uncertain_token = False

    def mmctl(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[-3:] == ['user', 'list', '--all']:
            users = [self.user] if self.user else None
            return subprocess.CompletedProcess(argv, 0, json.dumps(users), 'There are 1 users on local instance\n')
        if 'create' in argv:
            self.password = argv[argv.index('--password') + 1]
            self.user = {'id': OWNER, 'username': 'mads', 'email': 'mads@home.arpa',
                         'roles': 'system_user system_admin', 'is_bot': False, 'delete_at': 0}
            return subprocess.CompletedProcess(argv, 0, 'created', '')
        raise AssertionError('Unexpected mmctl command')

    def request(self, method, path, body=None, *, missing=False):
        self.requests.append((method, path, body))
        if path == '/users/login':
            if not self.user or body['password'] != self.password:
                raise module.BootstrapError('operator login refused')
            return self.user, {'Token': 's' * 26}
        if path == '/teams/name/homecompute':
            return self.team, {}
        if path == '/teams' and method == 'POST':
            self.team = {'id': TEAM, **body}
            return self.team, {}
        if '/members' in path:
            if method == 'GET':
                return ({'user_id': path.rsplit('/', 1)[-1]} if path in self.members else None), {}
            member_path = path + '/' + body['user_id']
            self.members.add(member_path)
            return body, {}
        if '/channels/name/' in path:
            return self.channels.get(path.rsplit('/', 1)[-1]), {}
        if path == '/channels' and method == 'POST':
            channel = {'id': str(len(self.channels) + 1).zfill(26), **body}
            self.channels[body['name']] = channel
            return channel, {}
        if path in {'/users/username/' + module.AGENT_ACCOUNTS['default'][1], '/users/' + BOT}:
            return ({'id': BOT, 'is_bot': True} if self.bot else None), {}
        if path == '/bots' and method == 'POST':
            self.bot = {'user_id': BOT, 'owner_id': OWNER}
            return self.bot, {}
        if path == '/bots/' + BOT:
            return self.bot, {}
        if path == '/users/' + BOT + '/tokens':
            self.token_creations += 1
            if self.uncertain_token:
                raise module.BootstrapError('API outcome unavailable; inspect before retrying mutation')
            return {'id': TOKEN_ID, 'token': 'x' * 26, 'user_id': BOT}, {}
        raise AssertionError((method, path))


class FakeAPI:
    def __init__(self, server):
        self.server = server
        self.token = ''

    def request(self, *args, **kwargs):
        return self.server.request(*args, **kwargs)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'private'
        self.server = Server()

    def invoke(self):
        with patch.object(module.subprocess, 'run', side_effect=self.server.mmctl), \
                patch.object(module, 'API', side_effect=lambda: FakeAPI(self.server)):
            return module.bootstrap(self.directory)

    def test_repeated_bootstrap_preserves_password_and_single_token(self):
        first = self.invoke()
        credentials = json.loads((self.directory / 'credentials.json').read_text())
        second = self.invoke()
        retained = json.loads((self.directory / 'credentials.json').read_text())
        self.assertEqual(first['team_id'], second['team_id'])
        self.assertEqual(credentials['password'], retained['password'])
        self.assertEqual(credentials['bot_token'], retained['bot_token'])
        self.assertEqual(self.server.token_creations, 1)
        self.assertEqual(sum('create' in command for command in self.server.commands), 1)
        for command in self.server.commands:
            self.assertEqual(command[:5], ['docker', 'exec', '--user', '2000:2000', module.CONTAINER])
            self.assertIn('--local', command)
            self.assertNotIn('search', command)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(credentials['password'], json.dumps(second))
        self.assertNotIn(credentials['bot_token'], json.dumps(second))

    def test_uncertain_token_creation_is_fenced_against_retries(self):
        self.server.uncertain_token = True
        with self.assertRaises(module.BootstrapError):
            self.invoke()
        self.assertTrue((self.directory / 'bot-token-pending.json').is_file())
        self.assertNotIn('bot_token', json.loads((self.directory / 'credentials.json').read_text()))
        self.server.uncertain_token = False
        with self.assertRaisesRegex(module.BootstrapError, 'reconciliation'):
            self.invoke()
        self.assertEqual(self.server.token_creations, 1)

    def test_bootstrap_does_not_recreate_consolidated_channels(self):
        self.invoke()
        self.invoke()
        retired = {'home-assistant', 'alerts'}
        self.assertTrue(retired.isdisjoint(self.server.channels))
        created = {body['name'] for method, path, body in self.server.requests
                   if method == 'POST' and path == '/channels'}
        self.assertTrue(retired.isdisjoint(created))
        for filename in ('channels.json', 'openclaw-channel.json'):
            exported = json.loads((self.directory / filename).read_text())
            self.assertTrue(retired.isdisjoint(exported.get('channels', exported)))

    def test_management_channel_is_private_and_retained_public_collision_is_refused(self):
        self.invoke()
        channel = self.server.channels['openclaw']
        self.assertEqual(channel['type'], 'P')
        self.assertIn('routing', channel['header']); self.assertIn('skills', channel['header'])
        self.assertTrue(all(value['type'] == 'P' for value in self.server.channels.values()))
        channel['type'] = 'O'
        count = self.server.token_creations
        with self.assertRaisesRegex(module.BootstrapError, 'privacy'):
            self.invoke()
        self.assertEqual(self.server.token_creations, count)

    def test_household_admission_refuses_retained_public_owner_room(self):
        self.invoke()
        module.save(self.directory / 'household-access.json', {'schema_version': 1})
        self.server.channels['family']['type'] = 'O'
        count = self.server.token_creations
        with self.assertRaisesRegex(module.BootstrapError, 'owner channel privacy'):
            self.invoke()
        self.assertEqual(self.server.token_creations, count)
        self.assertEqual(self.server.channels['family']['type'], 'O')

    def test_missing_owner_room_is_recreated_private_after_household_admission(self):
        self.invoke()
        module.save(self.directory / 'household-access.json', {'schema_version': 1})
        del self.server.channels['family']
        self.invoke()
        self.assertEqual(self.server.channels['family']['type'], 'P')

    def test_legacy_public_owner_room_remains_reconcilable_before_household_admission(self):
        self.invoke()
        self.server.channels['family']['type'] = 'O'
        self.invoke()
        self.assertEqual(self.server.channels['family']['type'], 'O')

    def test_retained_legacy_login_url_migrates_without_replacing_credentials_or_bot_transport(self):
        self.invoke()
        path = self.directory / 'credentials.json'
        before = json.loads(path.read_text())
        module.save(path, {**before, 'site_url': module.TRANSPORT_SITE})
        self.invoke()
        after = json.loads(path.read_text())
        self.assertEqual(after, {**before, 'site_url': module.SITE})
        login = json.loads((self.directory / 'operator-login.json').read_text())
        self.assertEqual(login['site_url'], module.SITE)
        self.assertEqual(login['password'], before['password'])
        self.assertEqual(json.loads((self.directory / 'openclaw-channel.json').read_text())['base_url'],
                         module.TRANSPORT_SITE)
        self.assertEqual(self.server.token_creations, 1)

    def test_existing_operator_is_not_reset_or_adopted(self):
        self.server.user = {'id': OWNER, 'username': 'mads', 'email': 'other@example.com'}
        with self.assertRaisesRegex(module.BootstrapError, 'existing mads'):
            self.invoke()
        self.assertEqual(len(self.server.commands), 1)
        self.assertFalse(self.server.requests)
        self.assertIsNone(self.server.password)

    def test_changed_retained_operator_id_is_refused(self):
        self.invoke()
        path = self.directory / 'credentials.json'
        value = json.loads(path.read_text())
        value['operator_id'] = 'z' * 26
        module.save(path, value)
        count = len(self.server.requests)
        with self.assertRaisesRegex(module.BootstrapError, 'identity differs'):
            self.invoke()
        self.assertEqual(self.server.requests[count:][0][1], '/users/login')
        self.assertEqual(len(self.server.requests[count:]), 1)

    def test_foreign_bot_owner_is_not_adopted(self):
        self.invoke()
        self.server.bot['owner_id'] = 'z' * 26
        with self.assertRaisesRegex(module.BootstrapError, 'bot ownership'):
            self.invoke()
        self.assertEqual(self.server.token_creations, 1)

    def test_world_readable_bot_token_is_replaced_privately(self):
        self.invoke()
        path = self.directory / 'openclaw-bot-token'
        path.chmod(0o644)
        self.invoke()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_text(), 'x' * 26 + '\n')

    def test_symlink_parent_is_refused(self):
        link = self.directory.parent / 'alias'
        link.symlink_to(self.directory.parent, target_is_directory=True)
        with self.assertRaisesRegex(module.BootstrapError, 'without symlinks'):
            module.bootstrap(link / 'private')
        self.assertFalse(self.server.requests)

    def test_concurrent_bootstrap_is_refused(self):
        with module.bootstrap_lock(self.directory):
            with self.assertRaisesRegex(module.BootstrapError, 'in progress'):
                self.invoke()
        self.assertFalse(self.server.commands)

    def test_http_error_does_not_expose_body_or_authentication(self):
        error = urllib.error.HTTPError('http://127.0.0.1', 401, 'private secret', {},
                                       io.BytesIO(b'private token body'))
        opener = unittest.mock.Mock()
        opener.open.side_effect = error
        with patch.object(module.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(module.BootstrapError) as caught:
                module.API('sensitive-bearer-token').request('GET', '/users/me')
        self.assertNotIn('private', str(caught.exception).replace('privately', ''))
        self.assertNotIn('sensitive', str(caught.exception))
        self.assertNotIn('body', str(caught.exception))

    def test_invalid_token_receipt_keeps_pending_fence(self):
        original = self.server.request

        def request(method, path, body=None, **kwargs):
            value, headers = original(method, path, body, **kwargs)
            if path.endswith('/tokens'):
                value['user_id'] = 'z' * 26
            return value, headers

        self.server.request = request
        with self.assertRaisesRegex(module.BootstrapError, 'token receipt'):
            self.invoke()
        self.assertTrue((self.directory / 'bot-token-pending.json').is_file())
        self.assertNotIn('bot_token', json.loads((self.directory / 'credentials.json').read_text()))


class RoleServer:
    def __init__(self):
        self.bots = {'openclaw': {'user_id': BOT, 'owner_id': OWNER, 'display_name': 'Old coordinator'}}
        self.tokens = {'x' * 26: BOT}
        self.members = set()
        self.token_creations = 0
        self.uncertain_token = False
        self.bot_creations = 0
        self.renames = 0
        self.uncertain_bot = False
        self.uncertain_rename = False
        self.owner_channels = []

    def request(self, token, method, path, body=None, **kwargs):
        if path == '/users/me':
            if token == 's' * 26:
                return {'id': OWNER, 'roles': 'system_admin'}, {}
            return {'id': self.tokens.get(token, 'z' * 26), 'is_bot': True, 'roles': 'system_user'}, {}
        if path == '/teams/name/homecompute':
            return {'id': TEAM}, {}
        if path == f'/users/{OWNER}/teams/{TEAM}/channels':
            return self.owner_channels, {}
        if path.startswith('/users/username/'):
            bot = self.bots.get(path.rsplit('/', 1)[-1])
            return ({'id': bot['user_id'], 'username': path.rsplit('/', 1)[-1], 'is_bot': True, 'roles': 'system_user'} if bot else None), {}
        if path == '/bots' and method == 'POST':
            self.bot_creations += 1
            bot = {'user_id': chr(ord('c') + len(self.bots)) * 26, 'owner_id': OWNER, 'display_name': body['display_name']}
            self.bots[body['username']] = bot
            if self.uncertain_bot: raise module.BootstrapError('uncertain bot creation')
            return bot, {}
        if path.startswith('/bots/'):
            name, bot = next((n, b) for n, b in self.bots.items() if b['user_id'] == path.rsplit('/', 1)[-1])
            if method == 'PUT':
                self.renames += 1
                self.bots.pop(name); self.bots[body['username']] = bot
                bot['display_name'] = body['display_name']
                if self.uncertain_rename: raise module.BootstrapError('uncertain rename')
            return bot, {}
        if path.startswith('/users/') and path.endswith('/tokens'):
            self.token_creations += 1
            if self.uncertain_token:
                raise module.BootstrapError('uncertain creation')
            bot_id = path.split('/')[2]
            token = 'token-' + bot_id
            self.tokens[token] = bot_id
            return {'id': str(self.token_creations).zfill(26), 'token': token, 'user_id': bot_id}, {}
        if path.startswith('/users/'):
            bid = path.rsplit('/', 1)[-1]
            name = next(n for n, b in self.bots.items() if b['user_id'] == bid)
            return {'id': bid, 'username': name, 'is_bot': True, 'roles': 'system_user'}, {}
        if '/channels/name/' in path:
            slug = path.rsplit('/', 1)[-1]
            return ({'id': ('a' if slug == 'town-square' else 'f') * 26} if slug in {'town-square', 'off-topic'} else None), {}
        if '/members' in path:
            if method == 'GET':
                return ({} if path in self.members else None), {}
            if method == 'DELETE':
                self.members.remove(path)
                return None, {}
            self.members.add(path + '/' + body['user_id'])
            if path.startswith('/teams/'):
                self.members.update('/channels/' + c * 26 + '/members/' + body['user_id'] for c in ['a', 'f'])
            return body, {}
        raise AssertionError((method, path))


class RoleAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'private'
        self.directory.mkdir(mode=0o700)
        self.channels = {name: str(i + 1).zfill(26) for i, name in enumerate(module.CHANNELS)}
        module.save(self.directory / 'channels.json', self.channels)
        module.save(self.directory / 'credentials.json', {'operator_id': OWNER, 'operator_session': 's' * 26,
                    'bot_id': BOT, 'bot_token': 'x' * 26, 'bot_token_id': TOKEN_ID})
        self.server = RoleServer()
        self.server.owner_channels = [{'id': value, 'name': name, 'type': 'O', 'team_id': TEAM, 'delete_at': 0}
                                      for name, value in self.channels.items()]
        self.server.owner_channels.extend({'id': identifier * 26, 'name': name, 'type': 'O', 'team_id': TEAM, 'delete_at': 0}
                                         for name, identifier in [('town-square', 'a'), ('off-topic', 'f')])
        self.server.members.update('/channels/' + channel + '/members/' + BOT
                                   for name, channel in self.channels.items() if name not in ['home-assistant', 'alerts'])

    def invoke(self, activate=False):
        def api(token=''):
            client = unittest.mock.Mock()
            client.request.side_effect = lambda *args, **kwargs: self.server.request(token, *args, **kwargs)
            return client
        with patch.object(module, 'API', side_effect=api):
            return module.provision_accounts(self.directory, activate=activate)

    def test_role_accounts_are_idempotent_private_and_preserve_coordinator_token(self):
        first = self.invoke()
        self.invoke()
        manifest = json.loads((self.directory / 'agent-accounts.json').read_text())
        self.assertEqual(set(manifest), {'base_url', 'accounts'})
        self.assertTrue(json.loads((self.directory / 'agent-account-state.json').read_text())['provisioning_complete'])
        self.assertEqual(self.server.token_creations, len(module.AGENT_ACCOUNTS) - 1)
        self.assertEqual(len({x['token'] for x in manifest['accounts']}), len(module.AGENT_ACCOUNTS))
        self.assertEqual(manifest['accounts'][0]['token'], 'x' * 26)
        self.assertNotIn('token-', json.dumps(first))
        self.assertEqual(stat.S_IMODE((self.directory / 'agent-accounts.json').stat().st_mode), 0o600)
        self.assertEqual(set(manifest['accounts'][0]), {'accountId', 'agentId', 'username', 'bot_id', 'token'})
        for account in manifest['accounts']:
            member = '/channels/' + 'f' * 26 + '/members/' + account['bot_id']
            if account['accountId'] == 'default': self.assertIn(member, self.server.members)
            else: self.assertNotIn(member, self.server.members)

    def test_uncertain_role_token_creation_is_fenced(self):
        self.server.uncertain_token = True
        with self.assertRaises(module.BootstrapError):
            self.invoke()
        self.server.uncertain_token = False
        with self.assertRaisesRegex(module.BootstrapError, 'reconciliation'):
            self.invoke()
        self.assertEqual(self.server.token_creations, 1)
        self.assertTrue((self.directory / 'agent-token-personal-pending.json').is_file())

    def test_activation_retains_owner_rooms_and_preserves_project_invites(self):
        project = '/channels/' + 'p' * 26 + '/members/' + BOT
        self.server.members.add(project)
        self.invoke(activate=True)
        for name in self.channels:
            self.assertIn('/channels/' + self.channels[name] + '/members/' + BOT, self.server.members)
        self.assertIn('/channels/' + self.channels['inbox'] + '/members/' + BOT, self.server.members)
        self.assertIn(project, self.server.members)

    def test_foreign_role_bot_owner_is_never_adopted(self):
        self.server.bots[module.AGENT_ACCOUNTS['personal'][1]] = {'user_id': 'd' * 26, 'owner_id': 'z' * 26}
        with self.assertRaisesRegex(module.BootstrapError, 'ownership|another account'):
            self.invoke()
        self.assertEqual(self.server.token_creations, 0)

    def test_wrong_retained_role_token_is_refused_without_rotation(self):
        self.invoke()
        path = self.directory / 'agent-account-state.json'
        manifest = json.loads(path.read_text())
        manifest['accounts'][1]['token'] = 'wrong-token-' + 'z' * 26
        module.save(path, manifest)
        with self.assertRaisesRegex(module.BootstrapError, 'bot identity'):
            self.invoke()
        self.assertEqual(self.server.token_creations, len(module.AGENT_ACCOUNTS) - 1)


if __name__ == '__main__':
    unittest.main()
