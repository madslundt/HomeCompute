#!/usr/bin/env python3
"""Provision private notification topics and one encrypted n8n delivery credential."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
from typing import Any

SPEC = importlib.util.spec_from_file_location('mattermost_bootstrap', Path(__file__).with_name('bootstrap-mattermost.py'))
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
N8N = 'homecompute-automation-n8n-1'
DATABASE = '/srv/state/automation/n8n/database.sqlite'
CREDENTIAL_NAME = 'HomeCompute Mattermost topic updates'
BOT_USERNAME = 'hermes'
BOT_DISPLAY_NAME = 'Hermes · Updates'
BOT_DESCRIPTION = 'Delivers automated workflow updates from n8n.'
TOPICS = {
    'personal': ('Planning', 'personal', 'Plans, calendars, household tasks, reminders and correspondence drafts with Luna. School updates belong in Aula; shared Gaia tasks in Family.'),
    'aula': ('Aula', 'personal', 'School messages, family briefings, and Aula updates.'),
    'news': ('News', 'researcher', 'News digests and research follow-up.'),
    'home-operations': ('Infrastructure', 'operator', 'HomeCompute servers, services, incidents, alerts and weekly systems reviews. Household devices belong in Home; batteries in Energy.'),
    'home': ('Home', 'operator', 'Household devices, Home Assistant automation, weather, and household reminders. Energy plans belong in Energy; server and service incidents in Infrastructure.'),
    'shopping': ('Food & household supplies', 'personal', 'Meals, recipes, groceries, everyday household supplies, lists, offers and stock. Hestia plans meals; Vega handles lists and offers; Ceres handles inventory. Other purchases belong in Shopping.'),
    'general-shopping': ('Shopping', 'researcher', 'Research and compare electronics, clothing, furniture, gifts and other purchases with Atlas and Nova. Meals, everyday supplies and household stock belong in Food & household supplies.'),
    'energy': ('Energy', 'operator', 'Battery plans, energy observations, and weekly reviews.'),
    'travel': ('Travel', 'personal', 'Family holidays, adventures, restaurants, and weekly holiday deals for two adults and two children.'),
    'household-admin': ('Household Admin', 'administrator', 'Household paperwork, warranties, subscriptions, renewals, and document deadlines. Themis works from supplied evidence and explicitly connected sources.'),
    'finances': ('Finances', 'finance', 'Private household budgeting, spending reviews, and subscription costs from supplied records. Plutus cannot access bank accounts or make payments.'),
    'family': ('Family', 'family', 'Shared planning, tasks, reminders, shopping and inventory with Gaia. School updates belong in Aula; calendar and correspondence work in Planning. Private adult sources stay separate.'),
}
ALIASES = {'calendar': 'personal', 'tasks': 'personal', 'correspondence': 'personal', 'storage': 'shopping', 'meals': 'shopping'}
ADDITIONAL_SPECIALISTS = {'shopping': 'shopper', 'travel': 'travel'}


def delivery_bot(api: Any, owner: str, state: dict[str, Any]) -> str:
    """Keep the delivery identity and credentials when migrating its public name."""
    selected, _ = api.request('GET', '/users/username/' + BOT_USERNAME, missing=True)
    if 'bot_id' in state:
        bot_id = bootstrap.identity({'id': state['bot_id']}, 'retained delivery bot')
        bot_user, _ = api.request('GET', '/users/' + bot_id)
    else:
        bot_user, _ = api.request('GET', '/users/username/n8n', missing=True)
        if selected is not None and (bot_user is None or selected['id'] != bot_user['id']):
            raise bootstrap.BootstrapError('requested delivery username belongs to another account')
        if bot_user is None:
            created, _ = api.request('POST', '/bots', {'username': BOT_USERNAME,
                'display_name': BOT_DISPLAY_NAME, 'description': BOT_DESCRIPTION})
            bot_id = bootstrap.identity({'id': created.get('user_id')}, 'delivery bot')
            bot_user, _ = api.request('GET', '/users/' + bot_id)
        else:
            bot_id = bootstrap.identity(bot_user, 'delivery bot')
    bot, _ = api.request('GET', '/bots/' + bot_id)
    if (bootstrap.identity(bot_user, 'delivery bot') != bot_id
            or bot.get('owner_id') != owner or bot.get('user_id') != bot_id
            or not bot_user.get('is_bot') or bot_user.get('delete_at', 0) != 0
            or bot.get('delete_at', 0) != 0
            or set(bot_user.get('roles', '').split()) != {'system_user'}):
        raise bootstrap.BootstrapError('delivery bot ownership or role differs')
    if selected is not None and bootstrap.identity(selected, 'selected delivery bot') != bot_id:
        raise bootstrap.BootstrapError('requested delivery username belongs to another account')
    if bot_user.get('username') not in {'n8n', BOT_USERNAME}:
        raise bootstrap.BootstrapError('delivery bot username requires reconciliation')
    if (bot_user['username'] != BOT_USERNAME or bot.get('display_name') != BOT_DISPLAY_NAME
            or bot.get('description') != BOT_DESCRIPTION):
        api.request('PUT', '/bots/' + bot_id, {'username': BOT_USERNAME,
            'display_name': BOT_DISPLAY_NAME, 'description': BOT_DESCRIPTION})
        renamed_user, _ = api.request('GET', '/users/' + bot_id)
        renamed_bot, _ = api.request('GET', '/bots/' + bot_id)
        if (renamed_user.get('id') != bot_id or renamed_user.get('username') != BOT_USERNAME
                or renamed_bot.get('user_id') != bot_id or renamed_bot.get('display_name') != BOT_DISPLAY_NAME
                or renamed_bot.get('description') != BOT_DESCRIPTION):
            raise bootstrap.BootstrapError('delivery bot rename readback requires reconciliation')
    return bot_id


def rename_delivery_bot(directory: Path = bootstrap.PRIVATE) -> dict[str, str]:
    with bootstrap.bootstrap_lock(directory):
        credentials = bootstrap.read_private(directory / 'credentials.json')
        state = bootstrap.read_private(directory / 'n8n-integration.json')
        owner = bootstrap.identity({'id': credentials['operator_id']}, 'operator')
        api = bootstrap.API(credentials['operator_session'])
        user, _ = api.request('GET', '/users/me')
        if bootstrap.identity(user, 'operator') != owner or 'system_admin' not in user.get('roles', '').split():
            raise bootstrap.BootstrapError('operator session identity differs')
        bot_id = bootstrap.identity({'id': state['bot_id']}, 'retained delivery bot')
        authenticated, _ = bootstrap.API(state['bot_token']).request('GET', '/users/me')
        if bootstrap.identity(authenticated, 'delivery token') != bot_id or not authenticated.get('is_bot'):
            raise bootstrap.BootstrapError('retained delivery token identity differs')
        delivery_bot(api, owner, state)
        return {'bot_id': bot_id, 'username': BOT_USERNAME, 'display_name': BOT_DISPLAY_NAME}


def provision(directory: Path = bootstrap.PRIVATE) -> dict[str, Any]:
    with bootstrap.bootstrap_lock(directory):
        credentials = bootstrap.read_private(directory / 'credentials.json')
        agents = bootstrap.read_private(directory / 'agent-accounts.json')
        owner = bootstrap.identity({'id': credentials['operator_id']}, 'operator')
        api = bootstrap.API(credentials['operator_session'])
        user, _ = api.request('GET', '/users/me')
        if bootstrap.identity(user, 'operator') != owner or 'system_admin' not in user.get('roles', '').split():
            raise bootstrap.BootstrapError('operator session identity differs')
        team, _ = api.request('GET', '/teams/name/homecompute')
        team_id = bootstrap.identity(team, 'team')
        path = directory / 'n8n-integration.json'
        state = bootstrap.read_private(path) if path.exists() else {}
        account_ids = {x['accountId']: bootstrap.identity({'id': x['bot_id']}, 'agent') for x in agents['accounts']}
        bot_id = delivery_bot(api, owner, state)
        state['bot_id'] = bot_id
        bootstrap.save(path, state)
        pending = directory / 'n8n-token-pending.json'
        if 'bot_token' not in state:
            if pending.exists():
                raise bootstrap.BootstrapError('uncertain prior delivery token creation requires reconciliation')
            bootstrap.save(pending, {'bot_id': bot_id})
            receipt, _ = api.request('POST', f'/users/{bot_id}/tokens', {'description': CREDENTIAL_NAME})
            token = receipt.get('token')
            token_id = bootstrap.identity(receipt, 'delivery token')
            if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{20,200}', token) or receipt.get('user_id') != bot_id:
                raise bootstrap.BootstrapError('delivery token receipt requires reconciliation')
            state.update(bot_id=bot_id, bot_token=token, token_id=token_id)
            bootstrap.save(path, state)
        if pending.exists():
            if bootstrap.read_private(pending).get('bot_id') != bot_id:
                raise bootstrap.BootstrapError('pending delivery token identity differs')
            pending.unlink()
        authenticated, _ = bootstrap.API(state['bot_token']).request('GET', '/users/me')
        if (bootstrap.identity(authenticated, 'delivery bot') != bot_id or not authenticated.get('is_bot')
                or set(authenticated.get('roles', '').split()) != {'system_user'}):
            raise bootstrap.BootstrapError('retained delivery token identity differs')
        member, _ = api.request('GET', f'/teams/{team_id}/members/{bot_id}', missing=True)
        if member is None:
            api.request('POST', f'/teams/{team_id}/members', {'team_id': team_id, 'user_id': bot_id})
        channels = {}
        for slug, (display, specialist, purpose) in TOPICS.items():
            channel, _ = api.request('GET', f'/teams/{team_id}/channels/name/{slug}', missing=True)
            if channel is None:
                channel, _ = api.request('POST', '/channels', {'team_id': team_id, 'name': slug, 'display_name': display,
                    'type': 'P', 'header': purpose + ' Existing notification delivery remains enabled.', 'purpose': purpose})
            channel_id = bootstrap.identity(channel, 'topic channel')
            if (channel.get('type') != 'P' or channel.get('team_id') != team_id or channel.get('delete_at', 0) != 0
                    or state.get('channels', {}).get(slug, channel_id) != channel_id):
                raise bootstrap.BootstrapError('topic channel privacy or identity differs')
            expected = {owner, bot_id, account_ids[specialist]}
            if slug != 'family':
                expected.add(account_ids['default'])
            if (directory / 'household-access.json').exists():
                access = bootstrap.read_private(directory / 'household-access.json')
                if (access.get('owner_id') != owner or access.get('team_id') != team_id
                        or slug == 'family' and access.get('family_channel_id') != channel_id):
                    raise bootstrap.BootstrapError('household audience identity differs')
                approved = access.get('shared_channel_ids', [])
                blocked = access.get('system_channel_ids', [])
                if (access.get('audience_policy') not in (None, 'all-except-systems')
                        or not isinstance(approved, list) or not isinstance(blocked, list)
                        or any(not isinstance(value, str) or not re.fullmatch('[a-z0-9]{26}', value)
                               for value in [*approved, *blocked])
                        or set(approved) & set(blocked)):
                    raise bootstrap.BootstrapError('household audience policy differs')
                if slug == 'family' or access.get('audience_policy') == 'all-except-systems' and channel_id in approved:
                    expected.add(bootstrap.identity({'id': access['partner_id']}, 'partner'))
            if slug == 'shopping':
                expected.update({account_ids['keeper'], account_ids['cook']})
            if slug in ADDITIONAL_SPECIALISTS:
                expected.add(account_ids[ADDITIONAL_SPECIALISTS[slug]])
            members, _ = api.request('GET', f'/channels/{channel_id}/members?page=0&per_page=200')
            actual = {x['user_id'] for x in members}
            # The shared assistant has its own workspace and source boundary.
            # Retire only Atlas's membership if its timer joined this new room
            # before the trusted Family mapping was saved.
            if slug == 'family' and account_ids['default'] in actual:
                api.request('DELETE', f'/channels/{channel_id}/members/' + account_ids['default'])
                actual.remove(account_ids['default'])
            if actual - expected:
                raise bootstrap.BootstrapError('topic channel audience requires explicit reconciliation')
            for user_id in sorted(expected - actual):
                api.request('POST', f'/channels/{channel_id}/members', {'user_id': user_id})
            channels[slug] = channel_id
            state['channels'] = {**state.get('channels', {}), slug: channel_id}
            bootstrap.save(path, state)
        channels.update({alias: channels[target] for alias, target in ALIASES.items()})
        state['channels'] = channels
        town, _ = api.request('GET', f'/teams/{team_id}/channels/name/town-square')
        off_topic, _ = api.request('GET', f'/teams/{team_id}/channels/name/off-topic', missing=True)
        if off_topic:
            member_path = f"/channels/{bootstrap.identity(off_topic, 'default channel')}/members/{bot_id}"
            member, _ = api.request('GET', member_path, missing=True)
            if member is not None:
                api.request('DELETE', member_path)
        joined, _ = api.request('GET', f'/users/{bot_id}/teams/{team_id}/channels')
        # Mattermost may create a system-bot welcome DM automatically. Limit
        # shared-channel membership without trying to delete private DMs.
        if {x['id'] for x in joined if x.get('type') in {'O', 'P'}} != set(channels.values()) | {bootstrap.identity(town, 'default channel')}:
            raise bootstrap.BootstrapError('delivery bot must belong only to topic channels and mandatory Town Square')
        state.setdefault('credential_id', secrets.token_hex(8))
        state.update(credential_name=CREDENTIAL_NAME, base_url=bootstrap.SITE, team_id=team_id)
        bootstrap.save(path, state)
        mapping = {key: state[key] for key in ['base_url', 'team_id', 'channels', 'bot_id', 'credential_id', 'credential_name']}
        bootstrap.save(directory / 'n8n-channels.json', mapping)
        return mapping


def command(argv: list[str], directory: Path, content: str | None = None) -> str:
    result = subprocess.run(argv, input=content, capture_output=True, text=True, timeout=120)
    if result.returncode:
        bootstrap.save(directory / 'n8n-last-failure.json', {'command': argv, 'returncode': result.returncode,
                       'stdout': result.stdout, 'stderr': result.stderr})
        raise bootstrap.BootstrapError('n8n operation failed; inspect protected diagnostics')
    return result.stdout


def import_credential(directory: Path = bootstrap.PRIVATE) -> dict[str, str]:
    with bootstrap.bootstrap_lock(directory):
        state = bootstrap.read_private(directory / 'n8n-integration.json')
        identifier = state['credential_id']
        if not re.fullmatch(r'[a-f0-9]{16}', identifier) or state['base_url'] != bootstrap.SITE:
            raise bootstrap.BootstrapError('fixed n8n credential identity differs')
        database = sqlite3.connect('file:' + DATABASE + '?mode=ro', uri=True)
        database.execute('PRAGMA query_only=ON')
        projects = database.execute("SELECT p.id FROM project p JOIN project_relation r ON r.projectId=p.id "
            "JOIN user u ON u.id=r.userId WHERE p.type='personal' AND u.roleSlug='global:owner' "
            "AND r.role='project:personalOwner'").fetchall()
        if len(projects) != 1 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', projects[0][0]):
            raise bootstrap.BootstrapError('exact n8n owner project required')
        project = projects[0][0]
        existing = database.execute('SELECT id,name,type,data FROM credentials_entity WHERE id=? OR name=?',
                                    (identifier, CREDENTIAL_NAME)).fetchall()
        if existing and (len(existing) != 1 or existing[0][:3] != (identifier, CREDENTIAL_NAME, 'mattermostApi')):
            raise bootstrap.BootstrapError('existing n8n credential identity differs')
        expected = {'accessToken': state['bot_token'], 'baseUrl': bootstrap.SITE, 'allowUnauthorizedCerts': False}
        prefix = '/tmp/homecompute-mattermost-' + secrets.token_hex(8)
        incoming, exported = prefix + '-import.json', prefix + '-export.json'
        node = ['docker', 'exec', '-i', N8N, 'node', '-e']
        create = "require('fs').writeFileSync(process.argv[1],require('fs').readFileSync(0),{mode:0o600,flag:'wx'})"
        try:
            if not existing:
                payload = [{'id': identifier, 'name': CREDENTIAL_NAME, 'type': 'mattermostApi', 'data': expected}]
                command(node + [create, incoming], directory, json.dumps(payload))
                command(['docker', 'exec', N8N, 'n8n', 'import:credentials', '--input=' + incoming, '--projectId=' + project], directory)
            command(node + [create, exported], directory, '')
            command(['docker', 'exec', N8N, 'n8n', 'export:credentials', '--id=' + identifier,
                     '--decrypted', '--output=' + exported], directory)
            raw = command(['docker', 'exec', N8N, 'node', '-e',
                           "process.stdout.write(require('fs').readFileSync(process.argv[1]))", exported], directory)
            saved = json.loads(raw)
            if len(saved) != 1 or saved[0]['id'] != identifier or saved[0]['data'] != expected:
                raise bootstrap.BootstrapError('encrypted n8n credential readback differs')
            row = database.execute('SELECT data FROM credentials_entity WHERE id=? AND type=?', (identifier, 'mattermostApi')).fetchone()
            shared = database.execute('SELECT projectId,role FROM shared_credentials WHERE credentialsId=?', (identifier,)).fetchall()
            if not row or state['bot_token'] in row[0] or shared != [(project, 'credential:owner')]:
                raise bootstrap.BootstrapError('n8n credential encryption or owner differs')
            probe = "const v=JSON.parse(require('fs').readFileSync(0,'utf8'));fetch('https://mattermost.home.arpa/api/v4/users/me',{headers:{Authorization:'Bearer '+v.token}}).then(async r=>{const b=await r.json();if(!r.ok||b.id!==v.id||!b.is_bot)process.exit(1);console.log(JSON.stringify({authenticated:true,tls_verified:true}))}).catch(()=>process.exit(1))"
            command(node + [probe], directory, json.dumps({'token': state['bot_token'], 'id': state['bot_id']}))
            result = {'id': identifier, 'name': CREDENTIAL_NAME, 'type': 'mattermostApi', 'project_id': project}
            bootstrap.save(directory / 'n8n-credential-receipt.json', {**result, 'encrypted_readback_verified': True,
                           'authenticated_tls_route_verified': True, 'posted_messages': 0})
            return result
        finally:
            database.close()
            command(['docker', 'exec', N8N, 'node', '-e',
                     "for(const p of process.argv.slice(1))try{require('fs').unlinkSync(p)}catch(e){if(e.code!=='ENOENT')throw e}", incoming, exported], directory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--import-credential', action='store_true')
    mode.add_argument('--rename-bot-only', action='store_true', help='Rename the retained delivery bot without provisioning channels or credentials.')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        result = rename_delivery_bot() if args.rename_bot_only else provision()
        if args.import_credential:
            result['credential'] = import_credential()
        print(json.dumps(result, indent=2))
    except (bootstrap.BootstrapError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise SystemExit('Mattermost n8n setup refused or failed; inspect protected state before retrying.') from None


if __name__ == '__main__':
    main()
