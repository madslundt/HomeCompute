"""Household audience admission uses retained identities and closes public access first."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mattermost_household', ROOT / 'scripts/setup-mattermost-household.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
OWNER, PARTNER, TEAM, FAMILY, TOWN = (c * 26 for c in ('a', 'k', 'm', 'f', 't'))


class Server:
    def __init__(self):
        def room(identifier, name, kind='O'):
            return {'id': identifier * 26, 'name': name, 'type': kind, 'creator_id': OWNER,
                    'team_id': TEAM, 'delete_at': 0}
        self.rooms = {row['id']: row for row in (room('f', 'family', 'P'), room('t', 'town-square'),
                     room('i', 'inbox'), room('p', 'personal'), room('s', 'tasks', 'P'))}
        self.owner_memberships = set(self.rooms)
        self.partner_memberships = set()
        self.partner = None
        self.team_member = False
        self.events = []
        self.passwords = []
        self.lose_create_reply = False
        self.conversion_fail = False
        self.conversion_lies = False
        self.new_public_after_conversion = False
        self.auto_join = set()
        self.team_roles = 'team_user'
        self.channel_roles = {}

    def request(self, method, path, body=None, **kwargs):
        self.events.append((method, path))
        if method == 'GET' and path == '/channels/' + FAMILY:
            return deepcopy(self.rooms[FAMILY]), {}
        if method == 'GET' and path == f'/teams/{TEAM}/channels/name/town-square':
            return deepcopy(self.rooms[TOWN]), {}
        if method == 'GET' and path == f'/users/{OWNER}/teams/{TEAM}/channels':
            return [deepcopy(self.rooms[k]) for k in sorted(self.owner_memberships)], {}
        if method == 'GET' and path.startswith(f'/teams/{TEAM}/channels?page='):
            page = int(path.split('page=')[1].split('&')[0])
            selected = [deepcopy(v) for v in self.rooms.values() if v['type'] == 'O' and not v['delete_at']]
            return selected[page * 200:(page + 1) * 200], {}
        if method == 'GET' and path == '/users/username/kristina':
            return deepcopy(self.partner), {}
        if method == 'POST' and path == '/users':
            if self.partner is not None: raise AssertionError('duplicate user creation')
            self.passwords.append(body['password'])
            self.partner = {'id': PARTNER, 'username': body['username'], 'email': body['email'],
                            'first_name': body['first_name'], 'roles': 'system_user', 'is_bot': False, 'delete_at': 0}
            if self.lose_create_reply: raise setup.b.BootstrapError('uncertain creation')
            return deepcopy(self.partner), {}
        if method == 'GET' and path == '/users/' + PARTNER:
            return deepcopy(self.partner), {}
        if path == f'/teams/{TEAM}/members/{PARTNER}' and method == 'GET':
            return ({'user_id': PARTNER, 'roles': self.team_roles} if self.team_member else None), {}
        if path == f'/teams/{TEAM}/members' and method == 'POST':
            if any(v['type'] == 'O' and v['name'] != 'town-square' and not v['delete_at'] for v in self.rooms.values()):
                raise AssertionError('public owner content exposed before household admission')
            self.team_member = True
            self.partner_memberships = {TOWN} | self.auto_join
            return {'team_id': TEAM, 'user_id': PARTNER}, {}
        if path == f'/users/{PARTNER}/teams/{TEAM}/channels' and method == 'GET':
            return [deepcopy(self.rooms[k]) for k in sorted(self.partner_memberships)], {}
        if path.startswith('/channels/') and '/members' in path:
            channel = path.split('/')[2]
            if method == 'GET': return ({'user_id': PARTNER, 'roles': self.channel_roles.get(channel, 'channel_user')}
                                       if channel in self.partner_memberships else None), {}
            if method == 'POST': self.partner_memberships.add(channel)
            if method == 'DELETE': self.partner_memberships.remove(channel)
            return {}, {}
        if path.startswith('/channels/') and method == 'GET':
            return deepcopy(self.rooms[path.split('/')[2]]), {}
        raise AssertionError((method, path))

    def runner(self, command, **kwargs):
        identifier = command[-2]
        if command[-3] != 'modify' or command[-1] != '--private': raise AssertionError(command)
        self.events.append(('CONVERT', identifier))
        if not self.conversion_lies: self.rooms[identifier]['type'] = 'P'
        if self.new_public_after_conversion:
            self.rooms['z' * 26] = {'id': 'z' * 26, 'name': 'new-private-project', 'type': 'O',
                                  'creator_id': OWNER, 'team_id': TEAM, 'delete_at': 0}
        return subprocess.CompletedProcess(command, int(self.conversion_fail), '', '')


class HouseholdAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / 'private'
        self.directory.mkdir(mode=0o700)
        setup.b.save(self.directory / 'credentials.json', {'operator_id': OWNER, 'operator_session': 'synthetic'})
        setup.b.save(self.directory / 'n8n-channels.json', {'team_id': TEAM, 'channels': {'family': FAMILY}})
        self.server = Server()

    def invoke(self, apply=True, share_non_systems=False):
        with patch.object(setup.sync, 'operator', return_value=self.server):
            return setup.prepare(self.directory, apply=apply, share_non_systems=share_non_systems,
                                 runner=self.server.runner)

    def systems(self):
        mapping = {}
        for identifier, name in (('h', 'home-operations'), ('o', 'openclaw')):
            channel = {'id': identifier * 26, 'name': name, 'type': 'P', 'creator_id': OWNER,
                       'team_id': TEAM, 'delete_at': 0}
            self.server.rooms[channel['id']] = channel
            self.server.owner_memberships.add(channel['id'])
            mapping[name] = channel['id']
        setup.b.save(self.directory / 'channels.json', mapping)
        return set(mapping.values())

    def test_dry_run_inspects_privacy_without_users_or_channel_mutations(self):
        result = self.invoke(apply=False)
        self.assertFalse(result['applied'])
        self.assertEqual(set(result['owner_rooms_to_make_private']), {'inbox', 'personal'})
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))
        self.assertIsNone(self.server.partner)
        self.assertFalse((self.directory / 'household-account-pending.json').exists())

    def test_private_conversion_precedes_admission_and_partner_gets_only_shared_audience(self):
        result = self.invoke()
        creation = self.server.events.index(('POST', '/users'))
        admission = self.server.events.index(('POST', f'/teams/{TEAM}/members'))
        conversions = [i for i, event in enumerate(self.server.events) if event[0] == 'CONVERT']
        self.assertTrue(conversions)
        self.assertLess(max(conversions), creation)
        self.assertLess(creation, admission)
        self.assertEqual(self.server.partner_memberships, {FAMILY, TOWN})
        self.assertNotIn('s' * 26, self.server.partner_memberships)  # Owner Tasks stays private.
        self.assertFalse(result['private_mail_access'])
        self.assertFalse(result['private_assistant_enabled'])
        self.assertEqual(result['approved_family_users'], [OWNER, PARTNER])

    def test_initial_password_is_private_and_replay_never_rotates_it(self):
        result = self.invoke()
        onboarding = self.directory / 'kristina-onboarding.json'
        before = onboarding.read_bytes()
        value = json.loads(before)
        self.assertEqual(value['password'], self.server.passwords[0])
        self.assertEqual(onboarding.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(value['password'], json.dumps(result))
        self.assertFalse((self.directory / 'household-account-pending.json').exists())
        self.server.events.clear()
        self.invoke()
        self.assertEqual(onboarding.read_bytes(), before)
        self.assertEqual(len(self.server.passwords), 1)
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_occupied_username_and_pending_creation_are_never_adopted(self):
        self.server.partner = {'id': 'x' * 26, 'username': 'kristina'}
        with self.assertRaisesRegex(setup.b.BootstrapError, 'never adopt'):
            self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))
        self.server.partner = None
        setup.b.save(self.directory / 'household-account-pending.json', {'username': 'kristina'})
        with self.assertRaisesRegex(setup.b.BootstrapError, 'never adopt'):
            self.invoke()
        self.assertIsNone(self.server.partner)

    def test_lost_creation_reply_keeps_password_and_stops_automatic_recreation(self):
        self.server.lose_create_reply = True
        with self.assertRaisesRegex(setup.b.BootstrapError, 'uncertain'):
            self.invoke()
        pending = self.directory / 'household-account-pending.json'
        self.assertEqual(json.loads(pending.read_text())['password'], self.server.passwords[0])
        self.assertFalse(self.server.team_member)
        with self.assertRaisesRegex(setup.b.BootstrapError, 'never adopt'):
            self.invoke()
        self.assertEqual(len(self.server.passwords), 1)

    def test_conversion_failure_or_false_readback_stops_before_user_creation(self):
        for field in ('conversion_fail', 'conversion_lies'):
            self.server = Server()
            setattr(self.server, field, True)
            with self.subTest(field=field), self.assertRaises(setup.b.BootstrapError):
                self.invoke()
            self.assertIsNone(self.server.partner)
            self.assertFalse(self.server.team_member)

    def test_unjoined_public_room_cannot_leak_through_team_discovery(self):
        hidden = {'id': 'z' * 26, 'name': 'unjoined-owner-room', 'type': 'O', 'creator_id': OWNER,
                  'team_id': TEAM, 'delete_at': 0}
        self.server.rooms[hidden['id']] = hidden
        with self.assertRaisesRegex(setup.b.BootstrapError, 'unjoined'):
            self.invoke()
        self.assertIsNone(self.server.partner)
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_public_room_created_during_conversion_stops_admission(self):
        self.server.new_public_after_conversion = True
        with self.assertRaisesRegex(setup.b.BootstrapError, 'privacy changed'):
            self.invoke()
        self.assertIsNone(self.server.partner)
        self.assertFalse(self.server.team_member)

    def test_changed_retained_identity_is_refused_without_mutation(self):
        self.invoke()
        self.server.partner['roles'] = 'system_user system_admin'
        self.server.events.clear()
        with self.assertRaisesRegex(setup.b.BootstrapError, 'retained household'):
            self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_unexpected_owner_membership_is_reported_and_never_normalized_silently(self):
        self.invoke()
        self.server.partner_memberships.add('s' * 26)
        self.server.events.clear()
        with self.assertRaisesRegex(setup.b.BootstrapError, 'unexpected owner-room'):
            self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_automatic_off_topic_membership_is_removed_and_nothing_else_is_joined(self):
        identifier = 'o' * 26
        self.server.rooms[identifier] = {'id': identifier, 'name': 'off-topic', 'type': 'P', 'creator_id': OWNER,
                                         'team_id': TEAM, 'delete_at': 0}
        self.server.auto_join = {identifier}
        self.invoke()
        self.assertIn(('DELETE', f'/channels/{identifier}/members/{PARTNER}'), self.server.events)
        self.assertEqual(self.server.partner_memberships, {FAMILY, TOWN})

    def test_shared_family_tools_cannot_read_owner_task_or_mail_sources(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        from openclaw_team_policy import ROLE_SERVICE_TOOLS, HOUSEHOLD_TASK_TOOLS, MAIL_TOOLS
        self.assertFalse(ROLE_SERVICE_TOOLS['family'] & HOUSEHOLD_TASK_TOOLS)
        self.assertFalse(ROLE_SERVICE_TOOLS['family'] & MAIL_TOOLS)

    def test_reviewed_upgrade_joins_all_active_owner_channels_except_systems_and_replays(self):
        systems = self.systems()
        self.invoke()
        password = (self.directory / 'kristina-onboarding.json').read_bytes()
        self.server.events.clear()
        plan = self.invoke(apply=False, share_non_systems=True)
        self.assertEqual(set(plan['shared_channel_ids']), self.server.owner_memberships - systems)
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))
        result = self.invoke(share_non_systems=True)
        self.assertEqual(result['audience_policy'], 'all-except-systems')
        self.assertEqual(self.server.partner_memberships, self.server.owner_memberships - systems)
        access = setup.b.read_private(self.directory / 'household-access.json')
        self.assertEqual(set(access['shared_channel_ids']), self.server.partner_memberships)
        self.assertEqual(set(access['system_channel_ids']), systems)
        self.assertEqual((self.directory / 'kristina-onboarding.json').read_bytes(), password)
        self.server.events.clear()
        self.invoke()
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_shared_replay_adds_new_owner_room_and_removes_accidental_systems_membership(self):
        systems = self.systems()
        self.invoke(share_non_systems=True)
        identifier = 'z' * 26
        self.server.rooms[identifier] = {'id': identifier, 'name': 'new-project', 'type': 'P',
                                       'team_id': TEAM, 'creator_id': OWNER, 'delete_at': 0}
        self.server.owner_memberships.add(identifier)
        self.server.partner_memberships |= systems
        self.invoke()
        self.assertIn(identifier, self.server.partner_memberships)
        self.assertFalse(systems & self.server.partner_memberships)
        self.assertTrue(all(('DELETE', f'/channels/{value}/members/{PARTNER}') in self.server.events for value in systems))

    def test_system_identity_rename_cannot_change_its_private_exclusion(self):
        systems = self.systems()
        self.invoke(share_non_systems=True)
        for identifier in systems:
            self.server.rooms[identifier]['name'] = 'renamed-' + identifier[:1]
        self.invoke()
        self.assertFalse(systems & self.server.partner_memberships)

    def test_changed_system_identity_or_public_system_room_is_refused(self):
        systems = self.systems()
        self.invoke(share_non_systems=True)
        mapping = setup.b.read_private(self.directory / 'channels.json')
        mapping['openclaw'] = 's' * 26
        setup.b.save(self.directory / 'channels.json', mapping)
        with self.assertRaises(setup.b.BootstrapError):
            self.invoke()
        mapping['openclaw'] = 'o' * 26
        setup.b.save(self.directory / 'channels.json', mapping)
        self.server.rooms[next(iter(systems))]['type'] = 'O'
        with self.assertRaisesRegex(setup.b.BootstrapError, 'Systems channels'):
            self.invoke()

    def test_shared_channels_never_allow_team_or_channel_administrator_privileges(self):
        self.systems()
        self.invoke(share_non_systems=True)
        self.server.team_roles = 'team_user team_admin'
        with self.assertRaisesRegex(setup.b.BootstrapError, 'ordinary household team'):
            self.invoke()
        self.server.team_roles = 'team_user'
        self.server.channel_roles[FAMILY] = 'channel_user channel_admin'
        with self.assertRaisesRegex(setup.b.BootstrapError, 'ordinary household channel'):
            self.invoke()


if __name__ == '__main__':
    unittest.main()
