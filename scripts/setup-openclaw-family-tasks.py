#!/usr/bin/env python3
"""Provision shared task delivery using the admitted private Family room."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
from typing import Any

CONFIG = Path('/var/lib/homecompute-openclaw/config')
PRIVATE = Path('/srv/state/mattermost/private')


def bootstrap():
    spec = importlib.util.spec_from_file_location('family_bootstrap', Path(__file__).with_name('bootstrap-mattermost.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(*, apply: bool = False) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise ValueError('root required')
    b = bootstrap()
    with b.bootstrap_lock(PRIVATE):
        access = b.read_private(PRIVATE / 'household-access.json')
        mapping = b.read_private(PRIVATE / 'n8n-channels.json')
        integration = b.read_private(PRIVATE / 'n8n-integration.json')
        family = b.identity({'id': access['family_channel_id']}, 'Family')
        if family != mapping['channels']['family'] or access['team_id'] != mapping['team_id']:
            raise ValueError('Retained private Family identity differs')
        api = b.API(integration['bot_token'])
        bot, _ = api.request('GET', '/users/me')
        if b.identity(bot, 'delivery bot') != integration['bot_id'] or not bot.get('is_bot') or bot.get('delete_at', 0):
            raise ValueError('Retained delivery bot differs')
        room, _ = api.request('GET', '/channels/' + family)
        if room.get('type') != 'P' or room.get('team_id') != access['team_id'] or room.get('delete_at', 0):
            raise ValueError('Private admitted Family room required')
        members, _ = api.request('GET', '/channels/' + family + '/members?page=0&per_page=200')
        if not isinstance(members, list) or len(members) >= 200:
            raise ValueError('Bounded Family membership required')
        human_ids = set()
        for row in members:
            person, _ = api.request('GET', '/users/' + b.identity({'id': row['user_id']}, 'member'))
            if not person.get('is_bot'):
                human_ids.add(person['id'])
        if human_ids != {access['owner_id'], access['partner_id']}:
            raise ValueError('Family membership differs from admitted adults')
        if not any(row['user_id'] == bot['id'] for row in members):
            raise ValueError('Existing delivery bot must already belong to Family')
        value = {'schema_version': 1, 'channel_id': family, 'bot_id': bot['id'],
                 'token_file': str(CONFIG / 'family-task-mattermost-token')}
        expected = {'family-tasks.json': (json.dumps(value, indent=2) + '\n').encode(),
                    'family-task-mattermost-token': (integration['bot_token'] + '\n').encode()}
        uid = pwd.getpwnam('homecompute-openclaw').pw_uid
        for name, content in expected.items():
            path = CONFIG / name
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if (not path.is_file() or path.is_symlink() or info.st_uid != uid or info.st_mode & 0o077
                        or path.read_bytes() != content):
                    raise ValueError('Existing family configuration requires reconciliation')
        if apply:
            for name, content in expected.items():
                path = CONFIG / name
                if path.exists():
                    continue
                temporary = CONFIG / ('.family-task-' + secrets.token_hex(12))
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chown(temporary, uid, pwd.getpwnam('homecompute-openclaw').pw_gid)
                os.rename(temporary, path)
        return {'applied': apply, 'private_family_verified': True, 'approved_adults': len(human_ids),
                'new_accounts': 0, 'messages_sent': 0, 'owner_tasks_shared': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(install(apply=args.apply)))
    except Exception:
        raise SystemExit('Family task provisioning refused; reconcile the private identities and configuration.') from None
