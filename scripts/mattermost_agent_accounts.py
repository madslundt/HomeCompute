"""Reconcile named Mattermost bots by retained identity, never by display name.

Bootstrap supplies its private state/API primitives so the installer and tests
use one credential boundary. New bot and PAT creation have separate durable
fences; interrupted renames can be read back and resumed using the same ID.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

LEGACY = {'default': 'openclaw', 'personal': 'openclaw-personal', 'researcher': 'openclaw-researcher',
          'engineer': 'openclaw-engineer', 'operator': 'openclaw-operator'}
KEYS = ['accountId', 'agentId', 'username', 'bot_id', 'token']


def owner_channels(b: Any, api: Any, owner: str, team_id: str) -> list[str]:
    """Select joined active team rooms; direct conversations stay independent."""
    rows, _ = api.request('GET', f'/users/{owner}/teams/{team_id}/channels')
    if not isinstance(rows, list) or len(rows) > 2000 or any(not isinstance(x, dict) for x in rows):
        raise b.BootstrapError('owner channel inventory requires reconciliation')
    selected = []
    for row in rows:
        if row.get('type') not in {'O', 'P'} or row.get('team_id') != team_id or row.get('delete_at', 0) != 0:
            continue
        channel_id = b.identity(row, 'owner channel')
        if channel_id in selected:
            raise b.BootstrapError('duplicate owner channel identity')
        selected.append(channel_id)
    return selected


def verify_bot(b: Any, api: Any, bot_id: str, owner: str) -> tuple[dict[str, Any], dict[str, Any]]:
    user, _ = api.request('GET', '/users/' + bot_id)
    bot, _ = api.request('GET', '/bots/' + bot_id)
    if (b.identity(user, 'bot') != bot_id or user.get('is_bot') is not True or user.get('delete_at', 0) != 0
            or set(user.get('roles', '').split()) != {'system_user'}
            or bot.get('owner_id') != owner or bot.get('user_id') != bot_id or bot.get('delete_at', 0) != 0):
        raise b.BootstrapError('agent bot ownership or role differs')
    return user, bot


def prove_token(b: Any, token: str, bot_id: str) -> None:
    if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{20,200}', token):
        raise b.BootstrapError('retained agent token format differs')
    user, _ = b.API(token).request('GET', '/users/me')
    if (b.identity(user, 'authenticated bot') != bot_id or user.get('is_bot') is not True
            or user.get('delete_at', 0) != 0 or set(user.get('roles', '').split()) != {'system_user'}):
        raise b.BootstrapError('agent token does not prove its bot identity')


def provision(b: Any, directory: Path, *, activate: bool = False) -> dict[str, Any]:
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        channels = b.read_private(directory / 'channels.json')
        owner = b.identity({'id': credentials['operator_id']}, 'operator')
        api = b.API(credentials['operator_session'])
        user, _ = api.request('GET', '/users/me')
        if b.identity(user, 'operator') != owner or 'system_admin' not in user.get('roles', '').split():
            raise b.BootstrapError('retained operator session does not prove admin identity')
        team, _ = api.request('GET', '/teams/name/homecompute')
        team_id = b.identity(team, 'team')
        coordinator_channels = owner_channels(b, api, owner, team_id)
        path, export = directory / 'agent-account-state.json', directory / 'agent-accounts.json'
        if path.exists():
            manifest = b.read_private(path)
        elif export.exists():
            manifest = b.read_private(export)
            if (manifest.get('provisioning_complete') is not True or not isinstance(manifest.get('accounts'), list)
                    or any(not isinstance(x, dict) or 'token_id' not in x for x in manifest['accounts'])):
                raise b.BootstrapError('retained export requires full account state reconciliation; refusing replacement')
        else:
            manifest = {'base_url': b.TRANSPORT_SITE, 'accounts': []}
        if (manifest.get('base_url') != b.TRANSPORT_SITE or not isinstance(manifest.get('accounts'), list)
                or any(not isinstance(x, dict) or x.get('accountId') not in b.AGENT_ACCOUNTS for x in manifest['accounts'])):
            raise b.BootstrapError('retained agent account manifest differs')
        accounts = {x['accountId']: x for x in manifest['accounts']}
        if len(accounts) != len(manifest['accounts']):
            raise b.BootstrapError('duplicate retained agent account')
        manifest.pop('excluded_channel_ids', None)

        def persist() -> None:
            manifest.update(accounts=list(accounts.values()), provisioning_complete=False)
            b.save(path, manifest)

        # The coordinator always has an independent credential receipt, even
        # before the first multi-account export. Its ID is authoritative.
        if 'default' not in accounts:
            accounts['default'] = {'accountId': 'default', 'agentId': 'main', 'username': LEGACY['default'],
                                   'bot_id': credentials['bot_id'], 'token': credentials['bot_token'],
                                   'token_id': credentials['bot_token_id']}
        if (accounts['default'].get('bot_id') != credentials['bot_id']
                or accounts['default'].get('token') != credentials['bot_token']
                or accounts['default'].get('token_id') != credentials['bot_token_id']):
            raise b.BootstrapError('coordinator token identity differs')

        # Check every retained ID and token before the first rename. A desired
        # username occupied by any other account is never adopted or replaced.
        verified = {}
        for account_id, (agent_id, username, _) in b.AGENT_ACCOUNTS.items():
            retained = accounts.get(account_id)
            if retained:
                bot_id = b.identity({'id': retained.get('bot_id')}, 'retained bot')
                if retained.get('agentId') != agent_id:
                    raise b.BootstrapError('retained agent account identity differs')
                bot_user, bot = verify_bot(b, api, bot_id, owner)
                allowed = {username, retained.get('username'), LEGACY.get(account_id)}
                if bot_user.get('username') not in allowed:
                    raise b.BootstrapError('retained bot username requires explicit reconciliation')
                if 'token' in retained:
                    b.identity({'id': retained.get('token_id')}, 'agent token')
                    prove_token(b, retained['token'], bot_id)
                verified[account_id] = (bot_user, bot)
            selected, _ = api.request('GET', '/users/username/' + username, missing=True)
            if selected is not None and (not retained or b.identity(selected, 'selected username') != retained['bot_id']):
                raise b.BootstrapError('requested agent username belongs to another account')

        for account_id, (agent_id, username, channel) in b.AGENT_ACCOUNTS.items():
            display = b.AGENT_DISPLAY_NAMES[account_id]
            retained = accounts.get(account_id)
            bot_pending = directory / ('agent-bot-' + account_id + '-pending.json')
            if retained:
                bot_id = retained['bot_id']
                bot_user, bot = verified[account_id]
            else:
                if bot_pending.exists():
                    raise b.BootstrapError('prior agent bot creation needs reconciliation; refusing duplicate')
                b.save(bot_pending, {'accountId': account_id, 'agentId': agent_id, 'username': username, 'owner_id': owner})
                created, _ = api.request('POST', '/bots', {'username': username, 'display_name': display,
                    'description': 'Focused HomeCompute ' + agent_id + ' agent.'})
                bot_id = b.identity({'id': created.get('user_id')}, 'created bot')
                bot_user, bot = verify_bot(b, api, bot_id, owner)
                if bot_user.get('username') != username:
                    raise b.BootstrapError('new bot receipt differs from requested identity')
                retained = {'accountId': account_id, 'agentId': agent_id, 'username': username, 'bot_id': bot_id}
                accounts[account_id] = retained
                persist()  # Save the proven creation ID before requesting a PAT.
            if bot_pending.exists():
                pending = b.read_private(bot_pending)
                if pending.get('accountId') != account_id or pending.get('username') != username or pending.get('owner_id') != owner:
                    raise b.BootstrapError('pending bot identity differs')
                bot_pending.unlink()

            token_pending = directory / ('agent-token-' + account_id + '-pending.json')
            if 'token' not in retained:
                if token_pending.exists():
                    raise b.BootstrapError('prior agent token creation needs reconciliation')
                b.save(token_pending, {'bot_id': bot_id})
                created, _ = api.request('POST', f'/users/{bot_id}/tokens', {'description': 'HomeCompute ' + agent_id})
                token_id = b.identity(created, 'agent token')
                token = created.get('token')
                if created.get('user_id') != bot_id or not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{20,200}', token):
                    raise b.BootstrapError('agent token receipt invalid; reconcile pending creation')
                retained.update(token=token, token_id=token_id)
                persist()  # Retain the only returned plaintext PAT immediately.
            prove_token(b, retained['token'], bot_id)
            if token_pending.exists():
                if b.read_private(token_pending).get('bot_id') != bot_id:
                    raise b.BootstrapError('pending agent token identity differs')
                token_pending.unlink()

            rename_pending = directory / ('agent-name-' + account_id + '-pending.json')
            if rename_pending.exists():
                pending = b.read_private(rename_pending)
                if pending.get('bot_id') != bot_id or pending.get('username') != username or pending.get('display_name') != display:
                    raise b.BootstrapError('pending bot rename identity differs')
            if bot_user.get('username') != username or bot.get('display_name') != display:
                if not rename_pending.exists():
                    b.save(rename_pending, {'bot_id': bot_id, 'username': username, 'display_name': display,
                        'previous_username': bot_user.get('username'), 'previous_display_name': bot.get('display_name')})
                api.request('PUT', '/bots/' + bot_id, {'username': username, 'display_name': display})
                bot_user, bot = verify_bot(b, api, bot_id, owner)
                if bot_user.get('username') != username or bot.get('display_name') != display:
                    raise b.BootstrapError('bot rename readback requires reconciliation')
            retained['username'] = username
            accounts[account_id] = retained
            persist()
            if rename_pending.exists(): rename_pending.unlink()

            # Atlas is the owner's contact in every joined active team room.
            # Existing DMs, project invitations and specialist rooms survive.
            member, _ = api.request('GET', f'/teams/{team_id}/members/{bot_id}', missing=True)
            if member is None:
                api.request('POST', f'/teams/{team_id}/members', {'team_id': team_id, 'user_id': bot_id})
            channel_id = channels.get(channel)
            if channel_id is None:
                topic, _ = api.request('GET', f'/teams/{team_id}/channels/name/{channel}', missing=True)
                if topic is not None:
                    if topic.get('type') != 'P': raise b.BootstrapError('specialist topic must remain private')
                    channel_id = b.identity(topic, 'specialist topic')
            if channel_id:
                member, _ = api.request('GET', f'/channels/{channel_id}/members/{bot_id}', missing=True)
                if account_id == 'family':
                    if member is not None: api.request('DELETE', f'/channels/{channel_id}/members/{bot_id}')
                elif member is None: api.request('POST', f'/channels/{channel_id}/members', {'user_id': bot_id})
            if account_id == 'default':
                for channel_id in coordinator_channels:
                    member_path = f'/channels/{channel_id}/members/{bot_id}'
                    member, _ = api.request('GET', member_path, missing=True)
                    if member is None: api.request('POST', f'/channels/{channel_id}/members', {'user_id': bot_id})
            else:
                off, _ = api.request('GET', f'/teams/{team_id}/channels/name/off-topic', missing=True)
                if off:
                    member_path = f"/channels/{b.identity(off, 'default channel')}/members/{bot_id}"
                    member, _ = api.request('GET', member_path, missing=True)
                    if member is not None: api.request('DELETE', member_path)
        manifest.update(provisioning_complete=True)
        b.save(path, manifest)
        b.save(export, {'base_url': b.TRANSPORT_SITE, 'accounts': [{key: x[key] for key in KEYS} for x in accounts.values()]})
        return {'accounts': len(accounts), 'manifest': str(export), 'legacy_memberships_removed': False,
                'coordinator_owner_channels': len(coordinator_channels)}
