#!/usr/bin/env python3
"""Organize the owner's Mattermost sidebar and retire redundant rooms, retaining history."""
from __future__ import annotations

import argparse
import copy
import hashlib
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
TASK_CONFIG = Path('/var/lib/homecompute-openclaw/config/household-tasks.json')
GROUPS = {
    'Family & plans': ('family', 'travel'),
    'Household': ('home', 'shopping', 'general-shopping', 'finances'),
    'Research & projects': ('research', 'engineering'),
    'Systems': ('home-operations', 'openclaw'),
}
CONSOLIDATED = b.CHANNEL_ALIASES
RETIRED = {**CONSOLIDATED, 'home-assistant': 'home', 'alerts': 'home-operations',
           'off-topic': 'town-square', 'agent-playground': 'openclaw'}
DETAILS = {
    'inbox': ('Inbox', 'Start here when you are unsure where a task belongs. Use a topic channel for ongoing work; keep follow-ups in its thread.'),
    'family': ('Family', 'Shared family planning, school updates, calendars, tasks and reminders with Atlas and Luna. Use explicit shared tasks for family reminders. Private DMs and adult memories stay separate. Meals and household supplies have their own channel.'),
    'travel': ('Travel', 'Holidays, local adventures, restaurants, date nights, and travel deals. Calendar event changes belong in Family.'),
    'finances': ('Money & paperwork', 'Budgets, expenses and subscription costs with Plutus; paperwork, warranties, insurance, renewals and document deadlines with Themis. Use supplied records. Private documents stay in their originating thread; reminders belong in Family.'),
    'home': ('Home', 'Home Assistant, devices, house maintenance, electricity, batteries, consumption and charging plans with Orion. Servers and service incidents belong in Infrastructure; shared planning and reminders in Family.'),
    'shopping': ('Food & household supplies', 'Meals, recipes, groceries, cleaning products, toiletries and household stock. Hestia plans meals; Vega handles lists and offers; Ceres handles stock. Electronics, clothing, gifts and furniture belong in Shopping. Keep each task in its thread.'),
    'general-shopping': ('Shopping', 'Research and compare electronics, clothing, furniture, gifts and other purchases with Atlas and Nova. Food, everyday household supplies, shopping lists and stock belong in Food & household supplies; holidays and outings in Travel.'),
    'research': ('Research & news', 'Research, comparisons, source-backed investigations, delivered news digests and follow-up with Nova. Keep each question or report discussion in its thread. Product comparisons belong in Shopping.'),
    'engineering': ('Engineering', 'Software implementation, bugs, and coding projects. Running services and incidents belong in Infrastructure; agent configuration in OpenClaw.'),
    'home-operations': ('Infrastructure', 'HomeCompute servers, services, incidents, alerts, and maintenance. Household devices belong in Home; energy plans in Home; agent configuration in OpenClaw.'),
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
    desired = {'Favorites': [active['inbox']] if 'inbox' in active else [],
               'Channels': [active['town-square']] if 'town-square' in active else []}
    desired.update({name: [active[n] for n in slugs if n in active] for name, slugs in GROUPS.items()})
    managed = {identifier for ids in desired.values() for identifier in ids}
    old = {x['id'] for x in channels if x.get('delete_at') or x.get('name') in RETIRED} | excluded
    for category in result:
        category['channel_ids'] = [x for x in category['channel_ids'] if x not in managed | old]
        key = {'favorites': 'Favorites', 'channels': 'Channels'}.get(category['type'], category['display_name'])
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
        if not set(DETAILS) - {'general-shopping'} <= set(by_name):
            raise b.BootstrapError('complete workspace channel inventory required')
        if 'general-shopping' in by_name and (by_name['general-shopping'].get('type') != 'P'
                or by_name['general-shopping'].get('delete_at')):
            raise b.BootstrapError('active private general Shopping channel required')
        access_path = directory / 'household-access.json'
        admission = b.read_private(access_path) if access_path.exists() else {}
        approved_humans = {owner}
        if admission.get('partner_id'):
            approved_humans.add(b.identity({'id': admission['partner_id']}, 'approved partner'))
        for source, target in RETIRED.items():
            if source not in by_name or by_name[source].get('delete_at'):
                continue
            if target not in by_name or by_name[target].get('delete_at'):
                raise b.BootstrapError('active consolidation destination required')
            if source in {'alerts', 'off-topic'} and by_name[source].get('total_msg_count', 0):
                raise b.BootstrapError('expected empty redundant channel now contains messages')
            if source in {'personal', 'aula', 'energy', 'household-admin', 'news'}:
                if (by_name[source].get('type') != 'P' or by_name[target].get('type') != 'P'
                        or human_audience(api, by_name[source]['id']) != approved_humans
                        or human_audience(api, by_name[target]['id']) != approved_humans):
                    raise b.BootstrapError('consolidation requires the same existing private adult audience')
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
        access = exports.get('household-access.json')
        if access and (access.get('owner_id') != owner or access.get('team_id') != team_id
                or access.get('audience_policy') != 'all-except-systems'):
            raise b.BootstrapError('reviewed household audience required before adding Shopping')
        b.save(backup, {'channels': channels, 'categories': categories, 'exports': exports})
        if 'general-shopping' not in by_name:
            display, purpose = DETAILS['general-shopping']
            row, _ = api.request('POST', '/channels', {'team_id': team_id, 'name': 'general-shopping',
                'display_name': display, 'type': 'P', 'header': purpose, 'purpose': purpose})
            b.identity(row, 'general Shopping channel')
            if row.get('team_id') != team_id or row.get('type') != 'P' or row.get('name') != 'general-shopping':
                raise b.BootstrapError('new Shopping channel readback differs; retain organization backup')
            by_name['general-shopping'] = row
            channels.append(row)
        general_id = by_name['general-shopping']['id']
        # Persist delivery aliases before archiving a destination used by producers.
        retired_ids = {by_name[name]['id']: by_name[target]['id']
                       for name, target in CONSOLIDATED.items() if name in by_name}
        for name, export in exports.items():
            cleaned = copy.deepcopy(export)
            if name == 'household-access.json':
                blocked = set(cleaned['system_channel_ids'])
                removed = {by_name[name]['id'] for name in RETIRED if name in by_name}
                cleaned['shared_channel_ids'] = sorted(({retired_ids.get(value, value)
                    for value in cleaned['shared_channel_ids'] if value not in removed or value in retired_ids}
                    | {general_id}) - blocked)
            elif name.startswith('n8n-'):
                mapping = cleaned['channels']
                mapping['general-shopping'] = general_id
                for source, target in CONSOLIDATED.items():
                    if source in mapping:
                        mapping[target] = by_name[target]['id']
                        mapping[source] = by_name[target]['id']
            else:
                mapping = cleaned if name == 'channels.json' else cleaned['channels']
                mapping['general-shopping'] = general_id
                for source in RETIRED:
                    mapping.pop(source, None)
                for target in set(CONSOLIDATED.values()):
                    mapping[target] = by_name[target]['id']
            if cleaned != export:
                b.save(directory / name, cleaned)
        for slug, (display, purpose) in DETAILS.items():
            row = by_name[slug]
            header = purpose
            earlier = [source for source, target in RETIRED.items() if target == slug and source in by_name]
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


def human_audience(api: Any, channel_id: str) -> set[str]:
    members, _ = api.request('GET', f'/channels/{channel_id}/members?page=0&per_page=200')
    if not isinstance(members, list) or len(members) >= 200:
        raise b.BootstrapError('bounded channel audience required')
    humans = set()
    for member in members:
        person, _ = api.request('GET', '/users/' + b.identity({'id': member['user_id']}, 'channel member'))
        if not person.get('is_bot'):
            humans.add(b.identity(person, 'human member'))
    return humans


def reconcile_task_destination(*, apply: bool = False) -> dict:
    """Retarget the existing reminder configuration, preserving its identity and token path."""
    import pwd
    import stat
    import tempfile
    path = TASK_CONFIG
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise b.BootstrapError('canonical reminder configuration required')
    info = path.stat()
    if info.st_uid != pwd.getpwnam('homecompute-openclaw').pw_uid or stat.S_IMODE(info.st_mode) != 0o600:
        raise b.BootstrapError('private service-owned reminder configuration required')
    raw = path.read_bytes()
    current = json.loads(raw)
    expected = {'schema_version', 'channel_id', 'bot_id', 'token_file'}
    if set(current) != expected or current['channel_id'] not in {'hrnwimmho7b4fkaa5xcm9d9i8h', 'fth7yi7ea7yutyscphhbjgehce'}:
        raise b.BootstrapError('retained reminder destination differs')
    after = {**current, 'channel_id': 'fth7yi7ea7yutyscphhbjgehce'}
    result = {'changed': current != after, 'applied': apply}
    if apply and current != after:
        backup = b.PRIVATE / ('task-destination-' + uuid.uuid4().hex + '.json')
        b.save(backup, current)
        if path.read_bytes() != raw:
            raise b.BootstrapError('reminder configuration changed during validation')
        fd, temporary = tempfile.mkstemp(prefix='.channel-', dir=path.parent)
        try:
            os.fchown(fd, info.st_uid, info.st_gid)
            with os.fdopen(fd, 'w') as stream:
                json.dump(after, stream, indent=2)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        if json.loads(path.read_bytes()) != after:
            raise b.BootstrapError('reminder destination readback differs')
        result['backup_file'] = str(backup)
    return result


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


def refresh_directory(directory: Path = b.PRIVATE, *, apply: bool = False,
                      source: Path | None = None) -> dict:
    """Update the retained directory post, refusing edits made outside its receipt."""
    source = source or Path(__file__).resolve().parents[1] / 'deploy/mattermost/agent-directory.md'
    desired = source.read_text()
    if not desired.startswith('# Your agent team') or not 1 <= len(desired) <= 16000:
        raise b.BootstrapError('reviewed agent directory source required')
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        receipt = b.read_private(directory / 'agent-directory.json')
        channels = b.read_private(directory / 'channels.json')
        api = b.API(credentials['bot_token'])
        user, _ = api.request('GET', '/users/me')
        if user.get('id') != credentials['bot_id'] or user.get('is_bot') is not True:
            raise b.BootstrapError('retained directory author required')
        post_id = b.identity({'id': receipt['post_id']}, 'directory post')
        post, _ = api.request('GET', '/posts/' + post_id)
        current = post['message']
        if (post.get('user_id') != user['id'] or post.get('channel_id') != channels['inbox']
                or receipt.get('channel_id') != channels['inbox'] or post.get('delete_at')
                or hashlib.sha256(current.encode()).hexdigest() != receipt['content_sha256']):
            raise b.BootstrapError('directory post drift requires review')
        result = {'changed': current != desired, 'applied': apply, 'post_id': post_id}
        if apply and current != desired:
            backup = directory / ('organization-directory-' + uuid.uuid4().hex + '.json')
            b.save(backup, {'post': post, 'receipt': receipt})
            api.request('PUT', '/posts/' + post_id, {'id': post_id, 'message': desired})
            actual, _ = api.request('GET', '/posts/' + post_id)
            if actual.get('message') != desired:
                raise b.BootstrapError('directory update readback differs; retain backup')
            b.save(directory / 'agent-directory.json',
                   {**receipt, 'content_sha256': hashlib.sha256(desired.encode()).hexdigest()})
            result['backup_file'] = str(backup)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--household-sidebar', action='store_true', help="organize Kristina's approved non-Systems rooms")
    parser.add_argument('--refresh-directory', action='store_true', help='update only the retained agent directory post from repository source')
    parser.add_argument('--task-destination', action='store_true', help='retarget existing household reminders to the retained Family channel')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        if sum((args.household_sidebar, args.refresh_directory, args.task_destination)) > 1:
            parser.error('choose household sidebar or directory refresh')
        operation = reconcile_task_destination if args.task_destination else (
            refresh_directory if args.refresh_directory else (organize_partner if args.household_sidebar else organize))
        print(json.dumps(operation(apply=args.apply), indent=2))
    except (b.BootstrapError, OSError, ValueError, KeyError, TypeError):
        raise SystemExit('Workspace organization failed; inspect the protected backup before retrying.') from None


if __name__ == '__main__':
    main()
