"""Space-name migrations preserve durable bot IDs, PATs and conversations."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('mattermost_bootstrap_fixtures', Path(__file__).with_name('mattermost-bootstrap-test.py'))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
b = fixtures.module
LEGACY = {'default': 'openclaw', 'personal': 'openclaw-personal', 'researcher': 'openclaw-researcher',
          'engineer': 'openclaw-engineer', 'operator': 'openclaw-operator'}


class MigrationTests(fixtures.RoleAccountTests):
    def seed_legacy(self):
        old = {key: (agent, LEGACY[key], channel) for key, (agent, _, channel) in b.AGENT_ACCOUNTS.items() if key in LEGACY}
        with patch.object(b, 'AGENT_ACCOUNTS', old):
            self.invoke()
        return b.read_private(self.directory / 'agent-account-state.json')

    def test_existing_five_migrate_by_id_and_create_only_the_new_bots(self):
        before = self.seed_legacy(); old = {x['accountId']: x for x in before['accounts']}
        initial_bots = self.server.bot_creations; initial_tokens = self.server.token_creations
        project = '/channels/' + 'p' * 26 + '/members/' + old['personal']['bot_id']
        self.server.members.add(project)
        self.invoke(); self.invoke()
        after = b.read_private(self.directory / 'agent-account-state.json')
        accounts = {x['accountId']: x for x in after['accounts']}
        self.assertEqual(set(accounts), set(b.AGENT_ACCOUNTS))
        self.assertEqual(self.server.bot_creations - initial_bots, len(b.AGENT_ACCOUNTS) - len(LEGACY))
        self.assertEqual(self.server.token_creations - initial_tokens, len(b.AGENT_ACCOUNTS) - len(LEGACY))
        for key, row in old.items():
            for field in ('bot_id', 'token', 'token_id', 'agentId'):
                self.assertEqual(accounts[key][field], row[field])
            self.assertEqual(accounts[key]['username'], b.AGENT_ACCOUNTS[key][1])
        self.assertIn(project, self.server.members)
        self.assertTrue(all(name not in self.server.bots for name in LEGACY.values()))
        self.assertEqual(set(self.server.bots), {v[1] for v in b.AGENT_ACCOUNTS.values()})
        self.assertEqual(b.read_private(self.directory / 'credentials.json')['bot_token'], old['default']['token'])
        export = b.read_private(self.directory / 'agent-accounts.json')
        self.assertEqual(set(export), {'base_url', 'accounts'})
        self.assertTrue(all(set(row) == {'accountId', 'agentId', 'username', 'bot_id', 'token'} for row in export['accounts']))

    def test_seventh_username_collision_is_detected_before_any_rename_or_creation(self):
        self.seed_legacy(); initial = (self.server.renames, self.server.bot_creations, self.server.token_creations)
        self.server.bots[b.AGENT_ACCOUNTS['keeper'][1]] = {'user_id': 'z' * 26, 'owner_id': fixtures.OWNER}
        with self.assertRaisesRegex(b.BootstrapError, 'another account'): self.invoke()
        self.assertEqual((self.server.renames, self.server.bot_creations, self.server.token_creations), initial)

    def test_existing_seven_add_only_travel_and_preserve_every_retained_identity(self):
        previous = {key: value for key, value in b.AGENT_ACCOUNTS.items() if key != 'travel'}
        with patch.object(b, 'AGENT_ACCOUNTS', previous):
            self.invoke()
        before = b.read_private(self.directory / 'agent-account-state.json')
        counts = (self.server.bot_creations, self.server.token_creations)
        self.invoke(); self.invoke()
        after = {x['accountId']: x for x in b.read_private(self.directory / 'agent-account-state.json')['accounts']}
        for row in before['accounts']:
            self.assertEqual(after[row['accountId']], row)
        self.assertEqual(after['travel']['username'], 'lyra')
        self.assertEqual((self.server.bot_creations - counts[0], self.server.token_creations - counts[1]), (1, 1))

    def test_uncertain_bot_creation_retains_fence_and_never_creates_another(self):
        self.seed_legacy(); self.server.uncertain_bot = True
        with self.assertRaises(b.BootstrapError): self.invoke()
        count = self.server.bot_creations
        self.assertTrue((self.directory / 'agent-bot-shopper-pending.json').exists())
        self.server.uncertain_bot = False
        with self.assertRaises(b.BootstrapError): self.invoke()
        self.assertEqual(self.server.bot_creations, count)

    def test_uncertain_rename_is_read_back_and_resumed_without_replacement(self):
        before = self.seed_legacy(); initial = (self.server.bot_creations, self.server.token_creations)
        self.server.uncertain_rename = True
        with self.assertRaises(b.BootstrapError): self.invoke()
        self.assertTrue((self.directory / 'agent-name-default-pending.json').exists())
        self.server.uncertain_rename = False
        self.invoke()
        after = {x['accountId']: x for x in b.read_private(self.directory / 'agent-account-state.json')['accounts']}
        self.assertEqual(after['default']['bot_id'], before['accounts'][0]['bot_id'])
        self.assertFalse((self.directory / 'agent-name-default-pending.json').exists())
        created = len(b.AGENT_ACCOUNTS) - len(LEGACY)
        self.assertEqual((self.server.bot_creations - initial[0], self.server.token_creations - initial[1]), (created, created))

    def test_wrong_existing_token_blocks_every_name_change(self):
        self.seed_legacy(); path = self.directory / 'agent-account-state.json'; data = b.read_private(path)
        data['accounts'][-1]['token'] = 'wrong-retained-token-' + 'z' * 26; b.save(path, data)
        before = self.server.renames
        with self.assertRaisesRegex(b.BootstrapError, 'bot identity'): self.invoke()
        self.assertEqual(self.server.renames, before)

    def test_bare_export_without_full_state_is_never_treated_as_fresh(self):
        self.seed_legacy()
        (self.directory / 'agent-account-state.json').unlink()
        before = (self.server.renames, self.server.bot_creations, self.server.token_creations)
        with self.assertRaisesRegex(b.BootstrapError, 'reconciliation'):
            self.invoke()
        self.assertEqual((self.server.renames, self.server.bot_creations, self.server.token_creations), before)
        self.assertTrue(all(name in self.server.bots for name in LEGACY.values()))

    def test_bot_display_names_use_role_labels_and_idempotent_second_run(self):
        self.invoke(); renames = self.server.renames; self.invoke()
        self.assertEqual(self.server.renames, renames)
        for account_id, (_, username, _) in b.AGENT_ACCOUNTS.items():
            self.assertEqual(self.server.bots[username]['display_name'], b.AGENT_DISPLAY_NAMES[account_id])

    def test_private_specialist_topics_join_without_creating_public_channels(self):
        original = self.server.request
        topic = {'shopping': 's' * 26, 'storage': 'v' * 26, 'travel': 'w' * 26}
        def request(token, method, path, body=None, **kwargs):
            if '/channels/name/' in path and path.rsplit('/', 1)[-1] in topic:
                return {'id': topic[path.rsplit('/', 1)[-1]], 'type': 'P'}, {}
            self.assertFalse(path == '/channels' and method == 'POST')
            return original(token, method, path, body, **kwargs)
        self.server.request = request
        self.invoke()
        rows = {x['accountId']: x for x in b.read_private(self.directory / 'agent-account-state.json')['accounts']}
        for role, slug in [('shopper', 'shopping'), ('keeper', 'shopping'), ('travel', 'travel')]:
            self.assertIn('/channels/' + topic[slug] + '/members/' + rows[role]['bot_id'], self.server.members)

    def test_coordinator_joins_all_active_owner_rooms_and_preserves_specialists(self):
        self.invoke()
        before = b.read_private(self.directory / 'agent-account-state.json')
        rows = {x['accountId']: x for x in before['accounts']}
        specialist = '/channels/' + 'x' * 26 + '/members/' + rows['engineer']['bot_id']
        self.server.members.add(specialist)
        self.server.owner_channels.extend([
            {'id': 'p' * 26, 'type': 'P', 'team_id': fixtures.TEAM, 'delete_at': 0, 'name': 'owner-private-project'},
            {'id': 'q' * 26, 'type': 'O', 'team_id': fixtures.TEAM, 'delete_at': 0, 'name': 'owner-public-room'},
            {'id': 'r' * 26, 'type': 'P', 'team_id': fixtures.TEAM, 'delete_at': 5, 'name': 'archived'},
            {'id': 's' * 26, 'type': 'P', 'team_id': 'z' * 26, 'delete_at': 0, 'name': 'foreign-team'},
            {'id': 't' * 26, 'type': 'D', 'team_id': '', 'delete_at': 0, 'name': 'private-dm'},
            {'id': 'u' * 26, 'type': 'G', 'team_id': '', 'delete_at': 0, 'name': 'private-group-dm'},
        ])
        value = self.invoke(activate=True)
        after = b.read_private(self.directory / 'agent-account-state.json')
        self.assertEqual(before, after)
        self.assertFalse(value['legacy_memberships_removed'])
        self.assertEqual(value['coordinator_owner_channels'], len(self.channels) + 4)
        for identifier in ['p', 'q', 'a', 'f']:
            self.assertIn('/channels/' + identifier * 26 + '/members/' + fixtures.BOT, self.server.members)
        for identifier in ['r', 's', 't', 'u']:
            self.assertNotIn('/channels/' + identifier * 26 + '/members/' + fixtures.BOT, self.server.members)
        self.assertIn(specialist, self.server.members)
        membership = self.server.members.copy()
        self.invoke(activate=True)
        self.assertEqual(self.server.members, membership)

    def test_malformed_owner_inventory_fails_before_account_mutation(self):
        self.seed_legacy(); initial = (self.server.renames, self.server.bot_creations, self.server.token_creations)
        self.server.owner_channels = [{'id': 'broken', 'type': 'P', 'team_id': fixtures.TEAM}]
        with self.assertRaisesRegex(b.BootstrapError, 'identity'):
            self.invoke()
        self.assertEqual((self.server.renames, self.server.bot_creations, self.server.token_creations), initial)

    def test_shared_family_contact_does_not_admit_atlas_to_the_family_room(self):
        family_id = 'q' * 26
        b.save(self.directory / 'n8n-channels.json', {'channels': {'family': family_id}})
        self.server.owner_channels.append({'id': family_id, 'type': 'P', 'team_id': fixtures.TEAM})
        self.invoke(activate=True)
        self.assertNotIn('/channels/' + family_id + '/members/' + fixtures.BOT, self.server.members)


if __name__ == '__main__': unittest.main()
