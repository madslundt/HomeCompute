#!/usr/bin/env python3
"""Native Mattermost admission, credential handling and config publication fences."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mattermost_setup', ROOT / 'scripts/setup-openclaw-mattermost.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MattermostSetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / 'openclaw.json'
        self.private = self.root / 'private'
        self.plugin = self.root / 'plugin'
        self.plugin.mkdir()
        (self.plugin / 'dist').mkdir()
        (self.plugin / 'dist/index.js').write_text('// native plugin')
        (self.plugin / 'package.json').write_text(json.dumps({'name': '@openclaw/mattermost', 'version': '2026.9.1'}))
        (self.plugin / 'openclaw.plugin.json').write_text(json.dumps({'id': 'mattermost'}))
        self.inspection = {'plugin': {'id': 'mattermost', 'origin': 'global', 'status': 'loaded',
                                     'rootDir': str(self.plugin), 'source': str(self.plugin / 'dist/index.js')},
                           'install': {'source': 'npm', 'spec': '@openclaw/mattermost@2026.9.1',
                                       'installPath': str(self.plugin), 'version': '2026.9.1',
                                       'resolvedName': '@openclaw/mattermost', 'resolvedVersion': '2026.9.1',
                                       'resolvedSpec': '@openclaw/mattermost@2026.9.1'}}
        self.owner = 'a' * 26
        self.token = 'SECRET_' + 'b' * 26
        self.token_file = self.root / 'token'
        self.accounts = {'base_url': setup.BASE_URL, 'accounts': [
            {'accountId': key, 'agentId': agent, 'username': username, 'bot_id': str(i).zfill(26),
             'token': self.token if key == 'default' else 'SECRET_' + str(i) * 26}
            for i, (key, (agent, username)) in enumerate(setup.ACCOUNTS.items(), 1)]}
        self.token_file.write_text(json.dumps(self.accounts))
        self.token_file.chmod(0o600)
        self.channels = {name: str(i) * 26 for i, name in enumerate(setup.ROUTES, 1)}
        self.channels_file = self.root / 'channels.json'
        self.channels_file.write_text(json.dumps(self.channels))
        self.before = {
            'agents': {'defaults': {'workspace': '/sandbox/.openclaw/workspace'},
                       'entries': {agent: {'tools': {'allow': ['memory_get']}} for agent, _ in setup.ACCOUNTS.values()}},
            'models': {'providers': {'inference': {'apiKey': 'RETAINED_PRIVATE_VALUE'}}},
            'tools': {'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False}},
            'gateway': {'reload': {'mode': 'off'}, 'auth': {'token': 'RETAINED_GATEWAY_TOKEN'}},
            'mcp': {'servers': {'homecompute': {'url': 'http://fixed-origin'}}},
            'plugins': {'allow': ['nemoclaw', 'homecompute-safety'], 'entries': {'nemoclaw': {'enabled': True}}},
            'channels': {'telegram': {'enabled': False}},
            'bindings': [{'agentId': 'main', 'match': {'channel': 'telegram', 'accountId': 'old'}}],
        }
        self.config.write_text(json.dumps(self.before))
        self.patches = [patch.object(setup, key, value) for key, value in
                        {'CONFIG': self.config, 'PRIVATE': self.private}.items()]
        for item in self.patches:
            item.start()
        self.calls = []

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def runner(self, arguments, **options):
        self.calls.append(arguments)
        self.assertNotIn(self.token, json.dumps(arguments))
        self.assertEqual(options, {'capture_output': True, 'timeout': 60})
        if arguments == ['openclaw', 'plugins', 'inspect', 'mattermost', '--json']:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.inspection).encode())
        path = Path(arguments[arguments.index('--batch-file') + 1])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        operations = json.loads(path.read_text())
        if '--dry-run' not in arguments:
            config = json.loads(self.config.read_text())
            for item in operations:
                parts = item['path'].split('.')
                target = config
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = item['value']
            self.config.write_text(json.dumps(config))
        return SimpleNamespace(returncode=0)

    def update(self, **options):
        return setup.update(self.owner, self.channels_file, self.token_file, runner=self.runner, **options)

    def test_candidate_validates_without_changing_runtime_or_exporting_credentials(self):
        original = self.config.read_bytes()
        result = self.update()
        self.assertFalse(result['applied'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(len(self.calls), 3)
        self.assertIn('--dry-run', self.calls[1])
        self.assertNotIn(self.token, json.dumps(result))
        self.assertEqual(list(self.private.iterdir()), [])

    def test_owner_dm_is_admitted_in_native_config(self):
        after, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(after['channels']['mattermost']['dmPolicy'], 'allowlist')
        self.assertEqual(after['channels']['mattermost']['allowFrom'], [self.owner])

    def test_account_routes_preserve_dms_and_keep_conservative_root_policy(self):
        after, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        channel = after['channels']['mattermost']
        self.assertEqual(set(channel['accounts']), set(setup.ACCOUNTS))
        self.assertEqual(channel['chatmode'], 'oncall')
        self.assertTrue(channel['requireMention'])
        self.assertEqual(channel['implicitMentions'], {'threadParticipation': False})
        self.assertEqual(channel['contextVisibility'], 'allowlist')
        self.assertEqual(channel['groupAllowFrom'], [self.owner])
        routes = after['bindings'][:-1]
        for route, (key, (agent, _)) in zip(routes, setup.ACCOUNTS.items()):
            self.assertEqual(route, {'agentId': agent, 'match': {'channel': 'mattermost', 'accountId': key},
                                    'session': {'dmScope': 'per-account-channel-peer', 'groupScope': 'per-group'}})

    def test_only_atlas_answers_owner_channels_while_private_specialists_remain_owner_only(self):
        after, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        root = after['channels']['mattermost']
        inherited = {key: value for key, value in root.items() if key != 'accounts'}
        for key, account in root['accounts'].items():
            effective = {**inherited, **account}
            if key == 'family':
                self.assertFalse(effective['enabled'])
                self.assertEqual(effective['dmPolicy'], 'disabled')
                self.assertEqual(effective['groupPolicy'], 'disabled')
                self.assertEqual(effective['allowFrom'], [])
                continue
            self.assertTrue(effective['enabled'])
            self.assertEqual(effective['dmPolicy'], 'allowlist')
            self.assertEqual(effective['allowFrom'], [self.owner])
            self.assertEqual(effective['contextVisibility'], 'allowlist')
            self.assertEqual(effective['implicitMentions'], {'threadParticipation': False})
            if key == 'default':
                self.assertEqual(effective['groupPolicy'], 'allowlist')
                self.assertEqual(effective['groupAllowFrom'], [self.owner])
                self.assertEqual(effective['chatmode'], 'onmessage')
                self.assertFalse(effective['requireMention'])
                self.assertEqual(effective['groups'], {'*': {'requireMention': False}})
            else:
                self.assertEqual(effective['groupPolicy'], 'disabled')
        self.assertEqual(after['agents'], self.before['agents'])
        self.assertEqual(after['tools'], self.before['tools'])

    def test_existing_mention_only_team_migrates_without_credential_or_conversation_rekey(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for account in before['channels']['mattermost']['accounts'].values():
            token = account['botToken']; account.clear(); account.update(enabled=True, botToken=token)
        after, _ = setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for key in setup.ACCOUNTS:
            self.assertEqual(after['channels']['mattermost']['accounts'][key]['botToken'],
                             before['channels']['mattermost']['accounts'][key]['botToken'])
        left = copy.deepcopy(before); right = copy.deepcopy(after)
        left['channels']['mattermost'].pop('accounts'); right['channels']['mattermost'].pop('accounts')
        self.assertEqual(left, right)
        again, _ = setup.candidate(after, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(again, after)

    def test_explicit_room_isolation_migrates_existing_routes_without_changing_dm_scopes(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for route in before['bindings']:
            if route['match']['channel'] == 'mattermost':
                route['session'].pop('groupScope')
        after, _ = setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for old, new in zip(before['bindings'], after['bindings']):
            if old['match']['channel'] == 'mattermost':
                self.assertEqual(new, {**old, 'session': {**old['session'], 'groupScope': 'per-group'}})
            else:
                self.assertEqual(new, old)
        channel = after['channels']['mattermost']
        self.assertEqual(channel['replyToMode'], 'all')
        self.assertEqual(channel['replyToModeByChatType'], {'direct': 'off', 'group': 'all', 'channel': 'all'})
        self.assertFalse(channel['implicitMentions']['threadParticipation'])
        self.assertEqual(after['agents'], before['agents'])
        self.assertEqual(after['tools'], before['tools'])
        self.assertEqual(after['channels'], before['channels'])

    def test_exact_legacy_channel_routes_retired_without_modifying_agent_state(self):
        before = copy.deepcopy(self.before)
        before['channels']['mattermost'] = {'botToken': self.token, 'dmPolicy': 'disabled'}
        before['bindings'] += [{'agentId': agent, 'match': {'channel': 'mattermost', 'accountId': 'default',
                                                          'peer': {'kind': 'channel', 'id': self.channels[name]}}}
                               for name, agent in setup.ROUTES.items()]
        after, _ = setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(after['agents'], before['agents'])
        self.assertEqual(len(after['bindings']), len(setup.ACCOUNTS) + 1)
        self.assertTrue(all('peer' not in item['match'] for item in after['bindings'][:-1]))

    def test_named_eight_account_expansion_preserves_existing_credentials_and_dm_routes(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for key in ('shopper', 'keeper', 'travel'):
            before['channels']['mattermost']['accounts'].pop(key)
        before['bindings'] = [route for route in before['bindings']
                              if route.get('match', {}).get('accountId') not in {'shopper', 'keeper', 'travel'}]
        previous_routes = copy.deepcopy(before['bindings'])
        previous_tokens = copy.deepcopy(before['channels']['mattermost']['accounts'])
        after, _ = setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(len(after['channels']['mattermost']['accounts']), len(setup.ACCOUNTS))
        self.assertTrue(all(route in after['bindings'] for route in previous_routes))
        for key, account in previous_tokens.items():
            self.assertEqual(after['channels']['mattermost']['accounts'][key], account)
        self.assertEqual(after['agents'], before['agents'])
        self.assertEqual(after['tools'], before['tools'])
        self.assertEqual(len(setup.ROUTES), 5)
        self.assertEqual([row['username'] for row in self.accounts['accounts']],
                         ['atlas', 'luna', 'nova', 'apollo', 'orion', 'vega', 'ceres', 'lyra',
                          'hestia', 'themis', 'plutus', 'gaia'])

    def test_unreviewed_aliases_and_unconfigured_specialists_are_refused(self):
        accounts = copy.deepcopy(self.accounts); accounts['accounts'][0]['username'] = 'openclaw'
        with self.assertRaises(ValueError):
            setup.candidate(self.before, self.owner, self.channels, accounts, setup.BASE_URL)
        for role in ('shopper', 'keeper', 'travel'):
            before = copy.deepcopy(self.before); before['agents']['entries'].pop(role)
            with self.assertRaises(ValueError):
                setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)

    def test_travel_expansion_preserves_seven_native_dm_routes_and_credentials(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        before['channels']['mattermost']['accounts'].pop('travel')
        before['bindings'] = [route for route in before['bindings']
                              if route.get('match', {}).get('accountId') != 'travel']
        retained = copy.deepcopy(before)
        after, _ = setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(before, retained)
        for key, account in before['channels']['mattermost']['accounts'].items():
            self.assertEqual(after['channels']['mattermost']['accounts'][key], account)
        self.assertTrue(all(route in after['bindings'] for route in before['bindings']))
        lyra = after['channels']['mattermost']['accounts']['travel']
        self.assertTrue(lyra['enabled'])
        self.assertEqual(lyra['groupPolicy'], 'disabled')
        self.assertEqual(after['agents'], before['agents'])
        self.assertEqual(after['mcp'], before['mcp'])

    def test_duplicate_identities_tokens_and_existing_token_changes_refused(self):
        for field in ('bot_id', 'token'):
            accounts = copy.deepcopy(self.accounts)
            accounts['accounts'][1][field] = accounts['accounts'][0][field]
            with self.assertRaises(ValueError):
                setup.candidate(self.before, self.owner, self.channels, accounts, setup.BASE_URL)
        before = copy.deepcopy(self.before)
        before['channels']['mattermost'] = {'botToken': 'CHANGED_' + 'z' * 26}
        with self.assertRaises(ValueError):
            setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)

    def test_apply_preserves_managed_sections_and_unrelated_binding_with_private_backup(self):
        original = self.config.read_bytes()
        result = self.update()
        result = self.update(apply=True, expected_config_sha256=result['source_config_sha256'],
                             helper_sha256=result['helper_sha256'])
        self.assertTrue(result['applied'])
        after = json.loads(self.config.read_text())
        for field in ('agents', 'models', 'tools', 'gateway', 'mcp'):
            self.assertEqual(after[field], self.before[field])
        self.assertEqual(after['channels']['telegram'], self.before['channels']['telegram'])
        self.assertEqual(after['bindings'][-1], self.before['bindings'][0])
        self.assertEqual(len(after['bindings']), len(setup.ACCOUNTS) + 1)
        channel = after['channels']['mattermost']
        self.assertEqual(channel['dmPolicy'], 'allowlist')
        self.assertEqual(channel['groupAllowFrom'], [self.owner])
        self.assertEqual(channel['groups'], {'*': {'requireMention': True}})
        self.assertNotIn('botToken', channel)
        self.assertEqual(channel['accounts']['default']['botToken'], self.token)
        self.assertEqual(channel['replyToMode'], 'all')
        backups = list(self.private.glob('backup-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_apply_requires_both_reviewed_digests(self):
        for options in ({}, {'expected_config_sha256': '0' * 64, 'helper_sha256': '0' * 64}):
            with self.assertRaises(ValueError):
                self.update(apply=True, **options)
        self.assertEqual(self.calls, [])

    def test_config_change_during_native_validation_fails_without_overwriting(self):
        def changing_runner(arguments, **options):
            if arguments[1:3] == ['plugins', 'inspect']:
                return self.runner(arguments, **options)
            self.config.write_text(json.dumps({**self.before, 'newOwnerSetting': True}))
            return SimpleNamespace(returncode=0)
        with self.assertRaises(ValueError):
            setup.update(self.owner, self.channels_file, self.token_file, runner=changing_runner)
        self.assertTrue(json.loads(self.config.read_text())['newOwnerSetting'])
        self.assertFalse(list(self.private.glob('backup-*')))
        self.assertFalse(list(self.private.glob('candidate-*')))

    def test_unknown_or_missing_plugin_refused_before_native_validation(self):
        (self.plugin / 'package.json').write_text(json.dumps({'name': '@openclaw/mattermost', 'version': '2026.10.6'}))
        with self.assertRaises(ValueError):
            self.update()
        self.assertEqual(len(self.calls), 1)

    def test_token_permissions_symlink_and_workspace_location_refused(self):
        self.token_file.chmod(0o644)
        with self.assertRaises(ValueError):
            self.update()
        self.token_file.chmod(0o600)
        linked = self.root / 'linked-token'
        linked.symlink_to(self.token_file)
        with self.assertRaises(OSError):
            setup.update(self.owner, self.channels_file, linked, runner=self.runner)
        before = copy.deepcopy(self.before)
        before['agents']['entries']['personal']['workspace'] = str(self.root)
        self.config.write_text(json.dumps(before))
        with self.assertRaises(ValueError):
            self.update()
        self.assertEqual(self.calls, [])

    def test_untrusted_destinations_duplicate_channels_and_missing_agents_refused(self):
        for channels, base in ((self.channels, 'https://example.com:9443'),
                               ({name: 'a' * 26 for name in setup.ROUTES}, setup.BASE_URL)):
            with self.assertRaises(ValueError):
                setup.candidate(self.before, self.owner, channels, self.accounts, base)
        before = copy.deepcopy(self.before)
        del before['agents']['entries']['engineer']
        with self.assertRaises(ValueError):
            setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)

    def test_conflicting_channel_binding_and_named_accounts_refused(self):
        before = copy.deepcopy(self.before)
        before['bindings'].append({'agentId': 'operator', 'match': {'channel': 'mattermost',
                                  'peer': {'kind': 'channel', 'id': self.channels['inbox']}}})
        with self.assertRaises(ValueError):
            setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        before = copy.deepcopy(self.before)
        before['channels']['mattermost'] = {'accounts': {'unreviewed': {'enabled': True}}}
        with self.assertRaises(ValueError):
            setup.candidate(before, self.owner, self.channels, self.accounts, setup.BASE_URL)

    def test_reapplying_is_idempotent_for_native_routes(self):
        after, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        again, _ = setup.candidate(after, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(again, after)

    def test_family_requires_explicit_member_ids_and_never_falls_back_to_private_atlas(self):
        family_id, partner = 'f' * 26, 'p' * 26
        after, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL,
                                   family_channel_id=family_id, family_user_ids=[self.owner, partner])
        accounts = after['channels']['mattermost']['accounts']
        family = accounts['family']
        self.assertTrue(family['enabled'])
        self.assertEqual(family['dmPolicy'], 'disabled')
        self.assertEqual(family['groupPolicy'], 'allowlist')
        self.assertEqual(family['groupAllowFrom'], sorted([self.owner, partner]))
        self.assertEqual(family['groups'], {'*': {'requireMention': True}, family_id: {'requireMention': False}})
        family_routes = [r for r in after['bindings'] if r['match'].get('accountId') == 'family']
        self.assertEqual(len(family_routes), 2)
        self.assertTrue(all(r['agentId'] == 'family' for r in family_routes))
        self.assertEqual(family_routes[0]['match']['peer'], {'kind': 'channel', 'id': family_id})
        self.assertNotIn('peer', family_routes[1]['match'])
        for account, value in accounts.items():
            if account == 'family': continue
            self.assertEqual(value['allowFrom'], [self.owner])
            self.assertNotIn(partner, value.get('groupAllowFrom', []))
        again, _ = setup.candidate(after, self.owner, self.channels, self.accounts, setup.BASE_URL,
                                   family_channel_id=family_id, family_user_ids=[self.owner, partner])
        self.assertEqual(again, after)

    def test_family_rejects_forged_channels_duplicate_members_and_unreviewed_roster(self):
        for args in ({'family_channel_id': 'invalid'},
                     {'family_channel_id': self.channels['inbox']},
                     {'family_channel_id': 'f' * 26, 'family_user_ids': ['p' * 26]},
                     {'family_channel_id': 'f' * 26, 'family_user_ids': [self.owner, self.owner]},
                     {'family_user_ids': [self.owner, 'p' * 26]}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL, **args)

    def test_native_failure_does_not_publish_and_removes_secret_candidate(self):
        original = self.config.read_bytes()
        def failing_validation(arguments, **options):
            if arguments[1:3] == ['plugins', 'inspect']:
                return self.runner(arguments, **options)
            return SimpleNamespace(returncode=1, stderr=self.token)
        with self.assertRaises(ValueError):
            setup.update(self.owner, self.channels_file, self.token_file,
                         runner=failing_validation)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.private.iterdir()), [])

    def test_matching_local_tarball_cannot_claim_official_ingress_trust(self):
        for fields in ({'source': 'path'}, {'artifactKind': 'npm-pack'},
                       {'sourcePath': '/staged/mattermost.tgz'},
                       {'spec': 'npm-pack:/staged/mattermost.tgz'},
                       {'resolvedName': '@someone/mattermost'},
                       {'resolvedSpec': '@openclaw/mattermost@2026.10.6'}):
            with self.subTest(fields=fields):
                original = copy.deepcopy(self.inspection)
                self.inspection['install'].update(fields)
                with self.assertRaises(ValueError):
                    self.update()
                self.inspection = original
        self.assertFalse(self.private.exists())
        self.assertTrue(all(call[1:3] == ['plugins', 'inspect'] for call in self.calls))

    def test_absent_provenance_and_shadowed_or_foreign_active_paths_fail_closed(self):
        reports = []
        absent = copy.deepcopy(self.inspection)
        del absent['install']
        reports.append(absent)
        shadowed = copy.deepcopy(self.inspection)
        shadowed['plugin']['rootDir'] = str(self.root / 'shadow')
        reports.append(shadowed)
        foreign = copy.deepcopy(self.inspection)
        foreign['plugin']['source'] = str(self.root / 'other/index.js')
        reports.append(foreign)
        for report in reports:
            with self.subTest(report=report):
                self.inspection = report
                with self.assertRaises((ValueError, KeyError)):
                    self.update()
        self.assertFalse(self.private.exists())

    def test_shared_channels_change_only_two_sender_lists_and_survive_roster_reapply(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        after, batch = setup.household_channel_access(before, self.owner, 'p' * 26, ['s' * 26], ['x' * 26, 'y' * 26])
        expected = copy.deepcopy(before)
        expected['channels']['mattermost']['groupAllowFrom'] = [self.owner, 'p' * 26]
        expected['channels']['mattermost']['accounts']['default']['groupAllowFrom'] = [self.owner, 'p' * 26]
        self.assertEqual(after, expected)
        self.assertEqual([b['path'] for b in batch], ['channels.mattermost.groupAllowFrom', 'channels.mattermost.accounts.default.groupAllowFrom'])
        replay, _ = setup.household_channel_access(after, self.owner, 'p' * 26, ['s' * 26], ['x' * 26, 'y' * 26])
        self.assertEqual(replay, after)
        refreshed, _ = setup.candidate(after, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.assertEqual(refreshed, after)

    def test_channel_sharing_rejects_overlapping_rooms_unknown_senders_and_dm_expansion(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        for allowed, systems in ((['x' * 26], ['x' * 26, 'y' * 26]), ([], ['x' * 26, 'y' * 26]),
                                 (['s' * 26], ['x' * 26]), (['*'], ['x' * 26, 'y' * 26])):
            with self.subTest(allowed=allowed, systems=systems), self.assertRaises(ValueError):
                setup.household_channel_access(before, self.owner, 'p' * 26, allowed, systems)
        for path in ('root', 'default', 'personal'):
            changed = copy.deepcopy(before)
            boundary = changed['channels']['mattermost']
            if path != 'root': boundary = boundary['accounts'][path]
            boundary['allowFrom'].append('p' * 26)
            with self.assertRaises(ValueError):
                setup.household_channel_access(changed, self.owner, 'p' * 26, ['s' * 26], ['x' * 26, 'y' * 26])
        before['channels']['mattermost']['groupAllowFrom'] = [self.owner, 'z' * 26]
        with self.assertRaises(ValueError):
            setup.household_channel_access(before, self.owner, 'p' * 26, ['s' * 26], ['x' * 26, 'y' * 26])

    def test_shared_channel_apply_needs_no_account_manifest_and_preserves_private_state(self):
        before, _ = setup.candidate(self.before, self.owner, self.channels, self.accounts, setup.BASE_URL)
        self.config.write_text(json.dumps(before)); self.token_file.unlink()
        arguments = (self.owner, 'p' * 26, ['s' * 26], ['x' * 26, 'y' * 26])
        with self.assertRaises(ValueError): setup.share_channels(*arguments, apply=True, runner=self.runner)
        review = setup.share_channels(*arguments, runner=self.runner)
        result = setup.share_channels(*arguments, apply=True, runner=self.runner,
            expected_config_sha256=review['source_config_sha256'], helper_sha256=review['helper_sha256'])
        self.assertTrue(result['applied']); self.assertFalse(result['private_dm_access_changed'])
        self.assertTrue(result['restart_required']); self.assertTrue(result['channel_reload_required'])
        self.assertEqual(result['systems_exclusion'], 'Mattermost-membership')
        expected, _ = setup.household_channel_access(before, *arguments)
        self.assertEqual(json.loads(self.config.read_text()), expected)
        self.assertNotIn(self.token, json.dumps(result))

    def test_plugin_install_change_during_validation_fails_before_publication(self):
        def changing_runner(arguments, **options):
            result = self.runner(arguments, **options)
            if '--dry-run' in arguments:
                self.inspection['install']['installedAt'] = 'changed'
            return result
        with self.assertRaises(ValueError):
            setup.update(self.owner, self.channels_file, self.token_file, runner=changing_runner)
        self.assertFalse(list(self.private.glob('backup-*')))
        self.assertEqual(list(self.private.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
