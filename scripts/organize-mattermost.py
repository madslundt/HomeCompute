#!/usr/bin/env python3
"""Organize the owner's Mattermost sidebar and retire redundant rooms, retaining history."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
from typing import Any
import uuid


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load('mattermost_bootstrap', 'bootstrap-mattermost.py')
GROUPS = {
    'Family & plans': ('family', 'personal', 'aula', 'travel'),
    'Household': ('home', 'energy', 'meals', 'shopping', 'household-admin', 'finances'),
    'Research & projects': ('research', 'news', 'engineering', 'agent-playground'),
    'Systems': ('home-operations', 'openclaw'),
}
CONSOLIDATED = {'calendar': 'personal', 'tasks': 'personal', 'correspondence': 'personal', 'storage': 'shopping'}
RETIRED = {**CONSOLIDATED, 'home-assistant': 'home', 'alerts': 'home-operations', 'off-topic': 'town-square'}
DETAILS = {
    'inbox': ('Inbox', 'Start here when you are unsure where a task belongs. Use a topic channel for ongoing work; keep follow-ups in its thread.'),
    'family': ('Family', 'Shared planning, tasks, reminders, shopping and inventory with Gaia. School updates belong in Aula; calendar and correspondence work in Planning. Gaia has no access to private adult sources.'),
    'personal': ('Planning', 'Plans, calendar events, household tasks, reminders, and correspondence drafts with Luna. School updates belong in Aula; shared Gaia tasks in Family; outings and holidays in Travel.'),
    'aula': ('Aula', 'School and childcare messages, notices, and Aula briefings. Calendar changes and combined household preparation belong in Planning.'),
    'travel': ('Travel', 'Holidays, local adventures, restaurants, date nights, and travel deals. Calendar event changes belong in Planning.'),
    'meals': ('Meals', 'Meal plans, recipes, ingredients to use before expiry, and shopping proposals with Hestia. Vega handles shopping changes; Ceres handles stock changes in Shopping & inventory.'),
    'household-admin': ('Household Admin', 'Paperwork, warranties, subscriptions, renewals, and document deadlines with Themis. Analysis uses supplied evidence and explicitly connected sources.'),
    'finances': ('Finances', 'Private budgets, spending reviews, and subscription costs with Plutus, using supplied records. Bank access and payments are not enabled.'),
    'home': ('Home', 'Devices, Home Assistant, weather, and house maintenance with Orion. Chores and reminders belong in Planning; electricity in Energy; servers in Infrastructure. Earlier threads: ' + b.SITE + '/homecompute/channels/home-assistant'),
    'energy': ('Energy', 'Electricity, batteries, consumption, charging plans, and energy reviews. Other household devices belong in Home.'),
    'shopping': ('Shopping & inventory', 'Shopping lists, product offers, discount watches, and household stock, locations, quantities and expiry dates. Vega handles purchases and lists; Ceres handles inventory. Holidays, restaurants and experience deals belong in Travel.'),
    'research': ('Research', 'Questions, comparisons, and investigations with sources. Automated news digests and their follow-up belong in News.'),
    'news': ('News', 'Delivered news digests and discussion of those reports. New research questions belong in Research.'),
    'engineering': ('Engineering', 'Software implementation, bugs, and coding projects. Running services and incidents belong in Infrastructure; agent configuration in OpenClaw.'),
    'agent-playground': ('Agent playground', 'Experiments and tests with the agent team. Atlas coordinates ordinary messages. Production agent configuration belongs in OpenClaw.'),
    'home-operations': ('Infrastructure', 'HomeCompute servers, services, incidents, alerts, and maintenance. Household devices belong in Home; energy plans in Energy; agent configuration in OpenClaw.'),
    'openclaw': ('OpenClaw', 'Agent setup, specialists, routing, tools, skills, and new project channels. Server and service incidents belong in Infrastructure; application coding in Engineering.'),
    'town-square': ('Town Square', 'Workspace announcements and general conversation. Start tasks in Inbox or the relevant topic channel.'),
}


def category_plan(categories: list[dict], channels: list[dict], *, excluded_channels: set[str] | None = None) -> list[dict]:
    """Assign every active channel once; preserve DMs and unfamiliar custom groups."""
    result = copy.deepcopy(categories)
    excluded = excluded_channels or set()
    active = {x['name']: x['id'] for x in channels
              if x.get('type') in {'O', 'P'} and not x.get('delete_at') and x['name'] not in RETIRED
              and x['id'] not in excluded}
    desired = {'Favorites': [active[n] for n in ('inbox', 'town-square') if n in active]}
    desired.update({name: [active[n] for n in slugs if n in active] for name, slugs in GROUPS.items()})
    managed = {identifier for ids in desired.values() for identifier in ids}
    old = {x['id'] for x in channels if x.get('delete_at') or x.get('name') in RETIRED} | excluded
    for category in result:
        category['channel_ids'] = [x for x in category['channel_ids'] if x not in managed | old]
        key = 'Favorites' if category['type'] == 'favorites' else category['display_name']
        if key in desired:
            category['channel_ids'] = desired[key] + category['channel_ids']
            category['sorting'] = 'manual'
            category['collapsed'] = False
    return result


def organize(directory: Path = b.PRIVATE, *, apply: bool = False) -> dict:
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        owner = b.identity({'id': credentials['operator_id']}, 'operator')
        sync = load('mattermost_sync', 'sync-mattermost-atlas.py')
        api = sync.operator(b, directory, credentials, owner)
        team, _ = api.request('GET', '/teams/name/homecompute')
        team_id = b.identity(team, 'team')
        prefix = f'/users/{owner}/teams/{team_id}/channels/categories'
        channels, _ = api.request('GET', f'/users/{owner}/teams/{team_id}/channels?include_deleted=true')
        categories, _ = api.request('GET', prefix)
        by_name = {x['name']: x for x in channels if x.get('type') in {'O', 'P'}}
        if not set(DETAILS) <= set(by_name):
            raise b.BootstrapError('complete workspace channel inventory required')
        for source, target in RETIRED.items():
            if source not in by_name or by_name[source].get('delete_at'):
                continue
            if target not in by_name or by_name[target].get('delete_at'):
                raise b.BootstrapError('active consolidation destination required')
            if source in {'alerts', 'off-topic'} and by_name[source].get('total_msg_count', 0):
                raise b.BootstrapError('expected empty redundant channel now contains messages')
        if by_name['home'].get('type') != 'P':
            raise b.BootstrapError('household channel must remain private')
        receipt = {'groups': {name: list(slugs) for name, slugs in GROUPS.items()},
                   'retired': RETIRED, 'applied': apply}
        if not apply:
            return receipt
        backup = directory / ('organization-' + uuid.uuid4().hex + '.json')
        exports = {}
        for name in ('channels.json', 'openclaw-channel.json', 'n8n-channels.json', 'n8n-integration.json', 'household-access.json'):
            if (directory / name).exists():
                exports[name] = b.read_private(directory / name)
        b.save(backup, {'channels': channels, 'categories': categories, 'exports': exports})
        # Persist delivery aliases before archiving a destination used by producers.
        retired_ids = {by_name[name]['id']: by_name[target]['id']
                       for name, target in CONSOLIDATED.items() if name in by_name}
        for name, export in exports.items():
            cleaned = copy.deepcopy(export)
            if name == 'household-access.json':
                cleaned['shared_channel_ids'] = sorted({retired_ids.get(value, value)
                                                       for value in cleaned['shared_channel_ids']})
            elif name.startswith('n8n-'):
                mapping = cleaned['channels']
                for source, target in CONSOLIDATED.items():
                    if source in mapping:
                        mapping[target] = by_name[target]['id']
                        mapping[source] = by_name[target]['id']
            else:
                mapping = cleaned if name == 'channels.json' else cleaned['channels']
                for source in RETIRED:
                    mapping.pop(source, None)
            if cleaned != export:
                b.save(directory / name, cleaned)
        for slug, (display, purpose) in DETAILS.items():
            row = by_name[slug]
            header = purpose
            earlier = [source for source, target in CONSOLIDATED.items() if target == slug and source in by_name]
            if earlier:
                header += ' Earlier threads: ' + ' · '.join('[' + by_name[source].get('display_name', source.title()) + '](' + b.SITE + '/homecompute/channels/' + source + ')' for source in earlier)
            if slug == 'inbox' and '[Agent directory]' in row.get('header', ''):
                header += ' ' + row['header'][row['header'].index('[Agent directory]'):]
            changes = {'display_name': display, 'header': header, 'purpose': purpose}
            if any(row.get(key) != value for key, value in changes.items()):
                api.request('PUT', f'/channels/{row["id"]}/patch', changes)
        for source, target in RETIRED.items():
            row = by_name.get(source)
            if row and not row.get('delete_at'):
                purpose = 'Consolidated into ' + b.SITE + '/homecompute/channels/' + target + '. Earlier threads remain here as read-only history.'
                api.request('PUT', f'/channels/{row["id"]}/patch', {'header': purpose, 'purpose': purpose})
                api.request('DELETE', '/channels/' + row['id'])
        # Reuse the earlier broad Home category rather than leaving an empty duplicate.
        for category in categories['categories']:
            if category['type'] == 'custom' and category['display_name'] == 'Home':
                category['display_name'] = 'Household'
                api.request('PUT', prefix + '/' + category['id'], category)
        for name in GROUPS:
            if not any(c['type'] == 'custom' and c['display_name'] == name for c in categories['categories']):
                category, _ = api.request('POST', prefix, {'display_name': name, 'type': 'custom', 'sorting': 'manual',
                                                         'user_id': owner, 'team_id': team_id, 'channel_ids': []})
                categories['categories'].append(category)
        current, _ = api.request('GET', prefix)
        planned = category_plan(current['categories'], channels)
        if planned != current['categories']:
            api.request('PUT', prefix, planned)
        ranks = {'favorites': 0, 'channels': 5, 'direct_messages': 6}
        names = {name: i + 1 for i, name in enumerate(GROUPS)}
        ordered = sorted(enumerate(planned), key=lambda item: (
            names.get(item[1]['display_name'], ranks.get(item[1]['type'], 5)), item[0]))
        order = [c['id'] for _, c in ordered]
        api.request('PUT', prefix + '/order', order)
        after, _ = api.request('GET', prefix)
        expected = {c['id']: c['channel_ids'] for c in planned}
        if after['order'] != order or {c['id']: c['channel_ids'] for c in after['categories']} != expected:
            raise b.BootstrapError('sidebar readback differs; retain organization backup')
        for source in RETIRED:
            if source in by_name:
                row, _ = api.request('GET', '/channels/' + by_name[source]['id'])
                if not row.get('delete_at'):
                    raise b.BootstrapError('redundant channel archive readback differs')
        receipt['backup_file'] = str(backup)
        return receipt


def organize_partner(directory: Path = b.PRIVATE, *, apply: bool = False) -> dict:
    """Arrange only the partner's approved rooms; never copy the owner's DMs."""
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        access = b.read_private(directory / 'household-access.json')
        owner = b.identity({'id': credentials['operator_id']}, 'operator')
        partner = b.identity({'id': access['partner_id']}, 'partner')
        team = b.identity({'id': access['team_id']}, 'team')
        if access.get('owner_id') != owner or access.get('audience_policy') != 'all-except-systems':
            raise b.BootstrapError('reviewed shared household audience required')
        allowed = {b.identity({'id': value}, 'shared room') for value in access['shared_channel_ids']}
        blocked = {b.identity({'id': value}, 'Systems room') for value in access['system_channel_ids']}
        if len(blocked) != 2 or allowed & blocked:
            raise b.BootstrapError('distinct shared and Systems rooms required')
        sync = load('partner_sidebar_sync', 'sync-mattermost-atlas.py')
        api = sync.operator(b, directory, credentials, owner)
        channels, _ = api.request('GET', f'/users/{partner}/teams/{team}/channels?include_deleted=true')
        actual = {row['id'] for row in channels if row.get('type') in {'O', 'P'} and not row.get('delete_at')}
        if actual != allowed:
            raise b.BootstrapError('reconcile partner memberships before sidebar organization')
        prefix = f'/users/{partner}/teams/{team}/channels/categories'
        current, _ = api.request('GET', prefix)
        groups = {name: slugs for name, slugs in GROUPS.items() if name != 'Systems'}
        result = {'partner_id': partner, 'groups': list(groups), 'channel_count': len(actual), 'applied': False}
        if not apply:
            return result
        backup = directory / ('partner-sidebar-' + uuid.uuid4().hex + '.json')
        b.save(backup, current)
        for name in groups:
            if not any(c['type'] == 'custom' and c['display_name'] == name for c in current['categories']):
                api.request('POST', prefix, {'display_name': name, 'type': 'custom', 'sorting': 'manual',
                                            'user_id': partner, 'team_id': team, 'channel_ids': []})
        current, _ = api.request('GET', prefix)
        planned = category_plan(current['categories'], channels, excluded_channels=blocked)
        if planned != current['categories']:
            api.request('PUT', prefix, planned)
        names = {name: index + 1 for index, name in enumerate(groups)}
        ranks = {'favorites': 0, 'channels': 5, 'direct_messages': 6}
        ordered = sorted(enumerate(planned), key=lambda item: (
            names.get(item[1]['display_name'], ranks.get(item[1]['type'], 5)), item[0]))
        order = [row['id'] for _, row in ordered]
        api.request('PUT', prefix + '/order', order)
        after, _ = api.request('GET', prefix)
        if (after['order'] != order or {c['id']: c['channel_ids'] for c in after['categories']}
                != {c['id']: c['channel_ids'] for c in planned}):
            raise b.BootstrapError('partner sidebar readback differs')
        return {**result, 'applied': True, 'backup_file': str(backup)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--household-sidebar', action='store_true', help="organize Kristina's approved non-Systems rooms")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        operation = organize_partner if args.household_sidebar else organize
        print(json.dumps(operation(apply=args.apply), indent=2))
    except (b.BootstrapError, OSError, ValueError, KeyError, TypeError):
        raise SystemExit('Workspace organization failed; inspect the protected backup before retrying.') from None


if __name__ == '__main__':
    main()
