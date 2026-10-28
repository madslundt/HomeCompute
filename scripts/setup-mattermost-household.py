#!/usr/bin/env python3
"""Reconcile Kristina's retained login and explicitly approved household channels."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load('household_bootstrap', 'bootstrap-mattermost.py')
sync = load('household_sync', 'sync-mattermost-atlas.py')
USERNAME = 'kristina'
EMAIL = 'kristina@home.arpa'
SYSTEM_CHANNELS = frozenset({'home-operations', 'openclaw'})


def audience(rows: list[dict], directory: Path, access: dict | None, family_id: str,
             town_id: str, team_id: str, *, share_non_systems: bool) -> tuple[set[str], set[str], bool]:
    """Bind Systems to operator-owned IDs; message text and room labels grant nothing."""
    shared = share_non_systems or (access or {}).get('audience_policy') == 'all-except-systems'
    if (access or {}).get('audience_policy') not in (None, 'all-except-systems'):
        raise b.BootstrapError('unknown retained household audience policy')
    if not shared:
        return {family_id, town_id}, set(), False
    mapping = b.read_private(directory / 'channels.json')
    if not isinstance(mapping, dict) or not SYSTEM_CHANNELS <= mapping.keys():
        raise b.BootstrapError('trusted Systems channel identities required')
    systems = {b.identity({'id': mapping[name]}, 'Systems channel') for name in SYSTEM_CHANNELS}
    if len(systems) != len(SYSTEM_CHANNELS) or systems & {family_id, town_id}:
        raise b.BootstrapError('distinct Systems channel identities required')
    active = [row for row in rows if row.get('type') in {'O', 'P'} and not row.get('delete_at', 0)]
    identifiers = {b.identity(row, 'household channel') for row in active}
    if (len(identifiers) != len(active) or any(row.get('team_id') != team_id for row in active)
            or not systems | {family_id, town_id} <= identifiers):
        raise b.BootstrapError('complete active household channel inventory required')
    if any(row.get('id') in systems and row.get('type') != 'P' for row in active):
        raise b.BootstrapError('Systems channels must remain private')
    if (access or {}).get('audience_policy') == 'all-except-systems':
        retained = access.get('system_channel_ids')
        approved = access.get('shared_channel_ids')
        if (not isinstance(retained, list) or set(retained) != systems
                or not isinstance(approved, list) or len(set(approved)) != len(approved)
                or any(b.identity({'id': value}, 'shared channel') in systems for value in approved)):
            raise b.BootstrapError('retained household audience identities differ')
    return identifiers - systems, systems, True


def public_rooms(api, team_id: str) -> list[dict]:
    """Inspect the whole team's discoverable audience, including unjoined rooms."""
    selected = []
    for page in range(11):
        rows, _ = api.request('GET', f'/teams/{team_id}/channels?page={page}&per_page=200')
        if not isinstance(rows, list) or len(rows) > 200 or any(not isinstance(row, dict) for row in rows):
            raise b.BootstrapError('bounded team public-room inventory required')
        for row in rows:
            b.identity(row, 'public room')
            if row.get('type') != 'O' or row.get('team_id') != team_id or row.get('delete_at', 0):
                raise b.BootstrapError('team public-room inventory differs')
            selected.append(row)
        if len(selected) > 2000 or len({row['id'] for row in selected}) != len(selected):
            raise b.BootstrapError('bounded unique team public-room inventory required')
        if len(rows) < 200:
            return selected
    raise b.BootstrapError('complete team public-room inventory required')


def prepare(directory: Path = b.PRIVATE, *, apply: bool = False, share_non_systems: bool = False,
            runner=subprocess.run) -> dict:
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        owner = b.identity({'id': credentials['operator_id']}, 'operator')
        api = sync.operator(b, directory, credentials, owner)
        mapping = b.read_private(directory / 'n8n-channels.json')
        team_id = b.identity({'id': mapping['team_id']}, 'team')
        family_id = b.identity({'id': mapping['channels']['family']}, 'Family')
        family, _ = api.request('GET', '/channels/' + family_id)
        if (family.get('id') != family_id or family.get('type') != 'P'
                or family.get('team_id') != team_id or family.get('delete_at', 0)):
            raise b.BootstrapError('private Family room required')
        rows, _ = api.request('GET', f'/users/{owner}/teams/{team_id}/channels')
        if not isinstance(rows, list) or len(rows) > 2000 or any(not isinstance(row, dict) for row in rows):
            raise b.BootstrapError('bounded owner room inventory required')
        town, _ = api.request('GET', f'/teams/{team_id}/channels/name/town-square')
        town_id = b.identity(town, 'Town Square')
        if town.get('type') != 'O' or town.get('team_id') != team_id or town.get('delete_at', 0):
            raise b.BootstrapError('active public Town Square required')
        public = [row for row in rows if row.get('type') == 'O' and not row.get('delete_at', 0)
                  and row.get('name') != 'town-square']
        if any(row.get('creator_id') != owner or row.get('team_id') != team_id for row in public):
            raise b.BootstrapError('public owner room privacy needs reconciliation')
        visible = public_rooms(api, team_id)
        if {row['id'] for row in visible} != {town_id, *(b.identity(row, 'owner room') for row in public)}:
            raise b.BootstrapError('unjoined public room privacy needs reconciliation before household admission')
        path = directory / 'household-access.json'
        access = b.read_private(path) if path.exists() else None
        pending = directory / 'household-account-pending.json'
        selected, _ = api.request('GET', '/users/username/' + USERNAME, missing=True)
        if access:
            b.identity({'id': access.get('partner_id')}, 'retained partner')
            if (access.get('schema_version') != 1 or access.get('owner_id') != owner
                    or access.get('team_id') != team_id or access.get('family_channel_id') != family_id
                    or access.get('partner_username') != USERNAME
                    or selected is None or selected.get('id') != access.get('partner_id')
                    or selected.get('email') != EMAIL or selected.get('is_bot')
                    or selected.get('delete_at', 0) or selected.get('roles') != 'system_user'):
                raise b.BootstrapError('retained household login differs')
        elif selected is not None or pending.exists():
            raise b.BootstrapError('unreconciled household account; never adopt an existing username')
        allowed, systems, shared = audience(rows, directory, access, family_id, town_id, team_id,
                                            share_non_systems=share_non_systems)
        result = {'partner_username': USERNAME, 'family_channel_id': family_id,
                  'owner_rooms_to_make_private': [row['name'] for row in public],
                  'private_mail_access': False, 'private_assistant_enabled': False, 'applied': False,
                  'audience_policy': 'all-except-systems' if shared else 'family-only',
                  'shared_channel_ids': sorted(allowed), 'system_channel_ids': sorted(systems)}
        if not apply:
            return result
        if public:
            b.save(directory / ('household-public-rooms-before-' + secrets.token_hex(8) + '.json'), public)
        for row in public:
            identifier = b.identity(row, 'owner room')
            command = ['docker', 'exec', '--user', '2000:2000', b.CONTAINER,
                       '/mattermost/bin/mmctl', '--local', '--quiet', 'channel', 'modify', identifier, '--private']
            completed = runner(command, capture_output=True, text=True, timeout=30)
            if completed.returncode:
                raise b.BootstrapError('owner room conversion unconfirmed; reconcile before household admission')
            after, _ = api.request('GET', '/channels/' + identifier)
            if after.get('type') != 'P' or after.get('id') != identifier:
                raise b.BootstrapError('owner room privacy readback differs')
        if {row['id'] for row in public_rooms(api, team_id)} != {town_id}:
            raise b.BootstrapError('team public-room privacy changed; reconcile before household admission')
        if access is None:
            password = secrets.token_urlsafe(36)
            # Keep the only initial password private and fence uncertain creation.
            b.save(pending, {'username': USERNAME, 'email': EMAIL, 'password': password})
            created, _ = api.request('POST', '/users', {'username': USERNAME, 'email': EMAIL,
                                    'password': password, 'first_name': 'Kristina'})
            partner = b.identity(created, 'partner')
            access = {'schema_version': 1, 'owner_id': owner, 'partner_id': partner,
                      'partner_username': USERNAME, 'team_id': team_id, 'family_channel_id': family_id}
            b.save(directory / 'kristina-onboarding.json', {'site_url': b.SITE, 'username': USERNAME,
                   'password': password, 'email': EMAIL, 'user_id': partner,
                   'note': 'Local login identity; no email is sent. Keep this initial password private.'})
            b.save(path, access)
            pending.unlink()
        partner = access['partner_id']
        # Confirm the account before admitting it to the household team.
        person, _ = api.request('GET', '/users/' + partner)
        if (person.get('username') != USERNAME or person.get('email') != EMAIL
                or person.get('roles') != 'system_user' or person.get('is_bot') or person.get('delete_at', 0)):
            raise b.BootstrapError('household account verification required')
        member, _ = api.request('GET', f'/teams/{team_id}/members/{partner}', missing=True)
        if member is None:
            api.request('POST', f'/teams/{team_id}/members', {'team_id': team_id, 'user_id': partner})
            member, _ = api.request('GET', f'/teams/{team_id}/members/{partner}')
        if member.get('roles') != 'team_user':
            raise b.BootstrapError('ordinary household team membership required')
        joined, _ = api.request('GET', f'/users/{partner}/teams/{team_id}/channels')
        extra = [row for row in joined if row.get('type') in {'O', 'P'} and not row.get('delete_at', 0)
                 and row.get('id') not in allowed]
        if any(row.get('name') != 'off-topic' and row.get('id') not in systems for row in extra):
            raise b.BootstrapError('partner has unexpected owner-room access; reconcile memberships')
        if shared:
            proposed = {**access, 'audience_policy': 'all-except-systems',
                        'shared_channel_ids': sorted(allowed), 'system_channel_ids': sorted(systems)}
            if proposed != access:
                b.save(path, proposed)
                access = proposed
        for row in extra:
            api.request('DELETE', f'/channels/{row["id"]}/members/{partner}')
        for channel_id in sorted(allowed):
            member, _ = api.request('GET', f'/channels/{channel_id}/members/{partner}', missing=True)
            if member is None:
                api.request('POST', f'/channels/{channel_id}/members', {'user_id': partner})
                member, _ = api.request('GET', f'/channels/{channel_id}/members/{partner}')
            if member.get('roles') != 'channel_user':
                raise b.BootstrapError('ordinary household channel membership required')
        joined, _ = api.request('GET', f'/users/{partner}/teams/{team_id}/channels')
        actual = {row['id'] for row in joined if row.get('type') in {'O', 'P'} and not row.get('delete_at', 0)}
        if actual != allowed:
            raise b.BootstrapError('household membership readback differs')
        result.update(applied=True, partner_id=partner, approved_family_users=[owner, partner],
                      onboarding_file=str(directory / 'kristina-onboarding.json'))
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', type=Path, default=b.PRIVATE)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--share-non-systems', action='store_true',
                        help='reviewed additive channel grant excluding the fixed Systems identities')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError('root required')
        print(json.dumps(prepare(args.private_root, apply=args.apply, share_non_systems=args.share_non_systems)))
        return 0
    except (b.BootstrapError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Household preparation refused; preserve private receipts and reconcile identities and room privacy.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
