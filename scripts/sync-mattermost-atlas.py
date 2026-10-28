#!/usr/bin/env python3
"""Join existing Atlas to active HomeCompute rooms its owner creates or joins.

The root-local timer never creates bots/PATs, removes memberships, posts messages
or changes native agent configuration. A pinned private policy fences identity
drift. Interrupted joins are recovered from current membership on the next tick.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

LIMIT = 50


def load_bootstrap() -> Any:
    spec = importlib.util.spec_from_file_location('atlas_bootstrap', Path(__file__).with_name('bootstrap-mattermost.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operator(b: Any, directory: Path, credentials: dict[str, Any], owner: str) -> Any:
    api = b.API(credentials['operator_session'])
    try:
        user, _ = api.request('GET', '/users/me')
    except b.BootstrapError as error:
        if str(error) != 'API GET failed with HTTP 401; inspect privately': raise
        # Renew only an expired admin session; the existing Atlas PAT is never
        # regenerated. Login identity must still match the retained owner.
        if credentials.get('username') != 'mads' or credentials.get('email') != 'mads@home.arpa':
            raise b.BootstrapError('retained operator login identity differs')
        user, headers = b.API().request('POST', '/users/login',
            {'login_id': credentials['username'], 'password': credentials['password']})
        token = next((v for k, v in headers.items() if k.lower() == 'token'), '')
        if not isinstance(token, str) or len(token) < 20:
            raise b.BootstrapError('operator session renewal unavailable')
        api.token = token
    if (b.identity(user, 'operator') != owner or user.get('username') != 'mads'
            or user.get('is_bot') is True or user.get('delete_at', 0) != 0
            or 'system_admin' not in user.get('roles', '').split()):
        raise b.BootstrapError('operator session identity differs')
    if credentials['operator_session'] != api.token:
        b.save(directory / 'credentials.json', {**credentials, 'operator_session': api.token})
    return api


def sync(b: Any, directory: Path) -> dict[str, Any]:
    with b.bootstrap_lock(directory):
        credentials = b.read_private(directory / 'credentials.json')
        manifest = b.read_private(directory / 'agent-account-state.json')
        mapping = b.read_private(directory / 'n8n-channels.json')
        if manifest.get('provisioning_complete') is not True or not isinstance(manifest.get('accounts'), list):
            raise b.BootstrapError('complete retained account state required')
        rows = [row for row in manifest['accounts'] if row.get('accountId') == 'default']
        if len(rows) != 1 or rows[0].get('agentId') != 'main' or rows[0].get('username') != 'atlas':
            raise b.BootstrapError('retained coordinator identity differs')
        row = rows[0]
        owner = b.identity({'id': credentials['operator_id']}, 'operator')
        bot_id = b.identity({'id': row['bot_id']}, 'Atlas')
        token_id = b.identity({'id': row['token_id']}, 'Atlas token')
        team_id = b.identity({'id': mapping['team_id']}, 'HomeCompute team')
        if (credentials.get('bot_id') != bot_id or credentials.get('bot_token_id') != token_id
                or credentials.get('bot_token') != row['token'] or not isinstance(row['token'], str)):
            raise b.BootstrapError('coordinator credential receipts differ')
        policy = {'schema_version': 1, 'owner_id': owner, 'team_id': team_id, 'bot_id': bot_id,
                  'token_id': token_id, 'token_sha256': hashlib.sha256(row['token'].encode()).hexdigest()}
        policy_path = directory / 'atlas-membership-policy.json'
        if policy_path.exists() and b.read_private(policy_path) != policy:
            raise b.BootstrapError('pinned Atlas membership identity differs')
        api = operator(b, directory, credentials, owner)
        team, _ = api.request('GET', '/teams/name/homecompute')
        bot, _ = api.request('GET', '/bots/' + bot_id)
        authenticated, _ = b.API(row['token']).request('GET', '/users/me')
        if (b.identity(team, 'HomeCompute team') != team_id or team.get('delete_at', 0) != 0
                or bot.get('user_id') != bot_id or bot.get('owner_id') != owner or bot.get('delete_at', 0) != 0
                or b.identity(authenticated, 'authenticated Atlas') != bot_id or authenticated.get('username') != 'atlas'
                or authenticated.get('is_bot') is not True or authenticated.get('delete_at', 0) != 0
                or set(authenticated.get('roles', '').split()) != {'system_user'}):
            raise b.BootstrapError('Atlas membership authority differs')
        if not policy_path.exists(): b.save(policy_path, policy)
        spec = importlib.util.spec_from_file_location('atlas_accounts', Path(__file__).with_name('mattermost_agent_accounts.py'))
        accounts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(accounts)
        desired = set(accounts.owner_channels(b, api, owner, team_id))
        family = mapping.get('channels', {}).get('family')
        if family is not None:
            # This root-private map identifies the separate shared assistant.
            # A room name or message cannot exclude an arbitrary owner channel.
            desired.discard(b.identity({'id': family}, 'Family channel'))
        joined = set(accounts.owner_channels(b, api, bot_id, team_id))
        missing = sorted(desired - joined)
        for channel_id in missing[:LIMIT]:
            api.request('POST', f'/channels/{channel_id}/members', {'user_id': bot_id})
        if missing:
            joined = set(accounts.owner_channels(b, api, bot_id, team_id))
            if not set(missing[:LIMIT]) <= joined:
                raise b.BootstrapError('Atlas membership readback incomplete')
        receipt = {'checked_at': time.time(), 'owner_active_channels': len(desired),
                   'added': min(len(missing), LIMIT), 'pending': len(desired - joined), 'poll_interval_seconds': 30}
        b.save(directory / 'atlas-membership-status.json', receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', type=Path, default=Path('/srv/state/mattermost/private'))
    args = parser.parse_args()
    try:
        if os.geteuid() != 0: raise ValueError('root required')
        result = sync(load_bootstrap(), args.private_root)
        print(json.dumps(result))
        return 0
    except Exception:
        print('Atlas membership sync refused or unavailable; inspect private identity state and service status.', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
