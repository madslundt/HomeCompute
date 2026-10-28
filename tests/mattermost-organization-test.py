#!/usr/bin/env python3
"""Check sidebar consolidation without changing channel or conversation identities."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mattermost_organization', ROOT / 'scripts/organize-mattermost.py')
organization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(organization)


class CategoryPlanTests(unittest.TestCase):
    def test_channel_descriptions_fit_mattermost_purpose_limit(self):
        for slug, (_, purpose) in organization.DETAILS.items():
            with self.subTest(channel=slug):
                self.assertLessEqual(len(purpose), 250)

    def setUp(self):
        names = ['inbox', 'town-square', *[slug for slugs in organization.GROUPS.values() for slug in slugs]]
        self.channels = [{'id': f'{index:026d}', 'name': name, 'type': 'P', 'delete_at': 0}
                         for index, name in enumerate(names, 1)]
        self.ids = {row['name']: row['id'] for row in self.channels}
        self.retired = ['r' * 25 + str(index) for index, _ in enumerate(organization.RETIRED)]
        self.channels += [{'id': identifier, 'name': name, 'type': 'O', 'delete_at': 0}
                          for identifier, name in zip(self.retired, organization.RETIRED)]
        self.channels += [{'id': 'a' * 26, 'name': 'old-project', 'type': 'P', 'delete_at': 123},
                          {'id': 'u' * 26, 'name': 'other-project', 'type': 'P', 'delete_at': 0},
                          {'id': 'd' * 26, 'name': 'owner__atlas', 'type': 'D', 'delete_at': 0}]
        self.categories = [self.category('favorites', 'Favorites', ['d' * 26, self.ids['home']]),
                           self.category('channels', 'Channels', list(self.ids.values()) + self.retired + ['a' * 26]),
                           self.category('direct_messages', 'Direct Messages', ['d' * 26]),
                           self.category('custom', 'Unrelated project', ['u' * 26])]
        self.categories += [self.category('custom', name, [self.ids['inbox'], *self.retired])
                            for name in organization.GROUPS]

    def category(self, kind, name, channels):
        return {'id': name, 'type': kind, 'display_name': name, 'channel_ids': channels,
                'sorting': 'alphabetical', 'collapsed': True}

    def test_each_managed_channel_has_one_home_and_retired_channels_disappear(self):
        planned = organization.category_plan(self.categories, self.channels)
        all_ids = [identifier for category in planned for identifier in category['channel_ids']]
        for identifier in self.ids.values():
            self.assertEqual(all_ids.count(identifier), 1)
        self.assertTrue(set(self.retired + ['a' * 26]).isdisjoint(all_ids))
        by_name = {category['display_name']: category for category in planned}
        self.assertEqual(by_name['Favorites']['channel_ids'],
                         [self.ids['inbox'], self.ids['town-square'], 'd' * 26])
        for name, slugs in organization.GROUPS.items():
            category = by_name[name]
            self.assertEqual(category['channel_ids'], [self.ids[slug] for slug in slugs])
            self.assertEqual(category['sorting'], 'manual')
            self.assertFalse(category['collapsed'])

    def test_direct_conversations_and_unrelated_custom_groups_survive(self):
        planned = organization.category_plan(self.categories, self.channels)
        for name in ('Direct Messages', 'Unrelated project'):
            before = next(category for category in self.categories if category['display_name'] == name)
            after = next(category for category in planned if category['display_name'] == name)
            self.assertEqual(after, before)
        self.assertIn('d' * 26, planned[0]['channel_ids'])

    def test_planning_does_not_mutate_input(self):
        before_categories = copy.deepcopy(self.categories)
        before_channels = copy.deepcopy(self.channels)
        organization.category_plan(self.categories, self.channels)
        self.assertEqual(self.categories, before_categories)
        self.assertEqual(self.channels, before_channels)

    def test_second_plan_is_idempotent(self):
        planned = organization.category_plan(self.categories, self.channels)
        self.assertEqual(organization.category_plan(planned, self.channels), planned)


class OrganizationAPI:
    """Model the Mattermost boundaries exercised by the apply operation."""
    owner, team = 'o' * 26, 't' * 26

    def __init__(self, case, channels, categories):
        self.case = case
        self.channels = {row['id']: copy.deepcopy(row) for row in channels}
        self.categories = copy.deepcopy(categories)
        self.order = [row['id'] for row in categories]
        self.prefix = f'/users/{self.owner}/teams/{self.team}/channels/categories'
        self.calls = []
        self.history = {row['id']: [{'id': 'historical-post', 'message': 'Retain this thread'}]
                        for row in channels if row['name'] == 'home-assistant'}
        for row in self.channels.values():
            row['total_msg_count'] = len(self.history.get(row['id'], []))

    def category_body(self, body):
        self.case.assertEqual(body['user_id'], self.owner)
        self.case.assertEqual(body['team_id'], self.team)
        self.case.assertIsInstance(body['channel_ids'], list)
        self.case.assertTrue(set(body['channel_ids']) <= self.channels.keys())

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        result = None
        if method == 'GET' and path == '/teams/name/homecompute':
            result = {'id': self.team}
        elif method == 'GET' and path == f'/users/{self.owner}/teams/{self.team}/channels?include_deleted=true':
            result = list(self.channels.values())
        elif path == self.prefix and method == 'GET':
            result = {'categories': self.categories, 'order': self.order}
        elif path == self.prefix and method == 'POST':
            self.category_body(body)
            self.case.assertEqual(body['channel_ids'], [])
            self.case.assertEqual(body['type'], 'custom')
            result = {**body, 'id': f'{100 + len(self.categories):026d}', 'collapsed': False}
            self.categories.append(copy.deepcopy(result))
            self.order.append(result['id'])
        elif path == self.prefix and method == 'PUT':
            self.case.assertEqual({row['id'] for row in body}, {row['id'] for row in self.categories})
            for row in body:
                self.category_body(row)
            self.categories = copy.deepcopy(body)
            result = body
        elif path == self.prefix + '/order' and method == 'PUT':
            self.case.assertEqual(set(body), {row['id'] for row in self.categories})
            self.case.assertEqual(len(body), len(self.categories))
            self.order = copy.deepcopy(body)
            result = body
        elif path.startswith(self.prefix + '/') and method == 'PUT':
            self.category_body(body)
            self.case.assertEqual(path.rsplit('/', 1)[-1], body['id'])
            selected = next(index for index, row in enumerate(self.categories) if row['id'] == body['id'])
            self.categories[selected] = copy.deepcopy(body)
            result = body
        elif path.startswith('/channels/'):
            identifier = path.split('/')[2]
            row = self.channels[identifier]
            if method == 'PUT' and path.endswith('/patch'):
                self.case.assertLessEqual(len(body.get('purpose', '')), 250)
                row.update(copy.deepcopy(body))
            elif method == 'DELETE' and path == '/channels/' + identifier:
                self.case.assertEqual(row['delete_at'], 0)
                row['delete_at'] = 1234
            else:
                self.case.assertEqual((method, path), ('GET', '/channels/' + identifier))
            result = row
        else:
            self.case.fail(f'Unexpected Mattermost operation: {method} {path}')
        return copy.deepcopy(result), {}


class OrganizationApplyTests(unittest.TestCase):
    def test_apply_preserves_history_and_delivery_mapping_and_retries_without_reorganizing(self):
        fixture = CategoryPlanTests()
        fixture.setUp()
        categories = copy.deepcopy(fixture.categories[:4])
        categories.append(fixture.category('custom', 'Home', [fixture.ids['home'], *fixture.retired]))
        for index, category in enumerate(categories, 1):
            category.update(id=f'{index + 200:026d}', user_id=OrganizationAPI.owner, team_id=OrganizationAPI.team)
        api = OrganizationAPI(self, fixture.channels, categories)
        history = copy.deepcopy(api.history)
        original_map = {row['name']: row['id'] for row in fixture.channels if row['type'] in {'O', 'P'}}
        sync = organization.load('organization_test_sync', 'sync-mattermost-atlas.py')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / 'private'
            directory.mkdir(mode=0o700)
            organization.b.save(directory / 'credentials.json', {'operator_id': api.owner})
            organization.b.save(directory / 'channels.json', original_map)
            channel_export = {'owner_id': api.owner, 'channels': original_map, 'base_url': organization.b.TRANSPORT_SITE}
            organization.b.save(directory / 'openclaw-channel.json', channel_export)
            delivery_map = {'team_id': api.team, 'channels': {'home': fixture.ids['home'], 'energy': fixture.ids['energy']}}
            organization.b.save(directory / 'n8n-channels.json', delivery_map)
            delivery_bytes = (directory / 'n8n-channels.json').read_bytes()
            with patch.object(organization, 'load', return_value=sync), patch.object(sync, 'operator', return_value=api):
                receipt = organization.organize(directory, apply=True)
                self.assertTrue(receipt['applied'])
                backup = json.loads(Path(receipt['backup_file']).read_text())
                self.assertEqual(backup['exports']['channels.json'], original_map)
                self.assertEqual(backup['exports']['openclaw-channel.json'], channel_export)
                self.assertEqual(len([row for row in api.categories if row['display_name'] == 'Household']), 1)
                self.assertFalse(any(row['display_name'] == 'Home' for row in api.categories))
                self.assertEqual([next(row['display_name'] for row in api.categories if row['id'] == identifier)
                                  for identifier in api.order[:5]], ['Favorites', *organization.GROUPS])
                expected_by_name = {'Favorites': [fixture.ids['inbox'], fixture.ids['town-square'], 'd' * 26],
                                    'Channels': [], 'Direct Messages': ['d' * 26], 'Unrelated project': ['u' * 26]}
                expected_by_name.update({name: [fixture.ids[slug] for slug in slugs]
                                         for name, slugs in organization.GROUPS.items()})
                for row in api.categories:
                    self.assertEqual(row['channel_ids'], expected_by_name[row['display_name']])
                for source in organization.RETIRED:
                    self.assertGreater(api.channels[original_map[source]]['delete_at'], 0)
                self.assertEqual(api.history, history)
                expected_map = {name: identifier for name, identifier in original_map.items() if name not in organization.RETIRED}
                self.assertEqual(organization.b.read_private(directory / 'channels.json'), expected_map)
                self.assertEqual(organization.b.read_private(directory / 'openclaw-channel.json'),
                                 {**channel_export, 'channels': expected_map})
                self.assertEqual((directory / 'n8n-channels.json').read_bytes(), delivery_bytes)
                before_retry = copy.deepcopy((api.channels, api.categories, api.order, api.history))
                first_calls = len(api.calls)
                organization.organize(directory, apply=True)
                self.assertEqual((api.channels, api.categories, api.order, api.history), before_retry)
                self.assertEqual([(method, path) for method, path, _ in api.calls[first_calls:] if method != 'GET'],
                                 [('PUT', api.prefix + '/order')])
                self.assertEqual((directory / 'n8n-channels.json').read_bytes(), delivery_bytes)
                self.assertTrue(all(not (method == 'POST' and path in {'/posts', '/channels'})
                                    for method, path, _ in api.calls))


if __name__ == '__main__':
    unittest.main()
