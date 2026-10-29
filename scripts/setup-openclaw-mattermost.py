#!/usr/bin/env python3
"""Validate or apply the private, native Mattermost channel in the managed sandbox.

Run the candidate first. Application requires its source-config digest and this
helper's digest. Native batch files and backups contain secrets and remain private.
This helper does not install plugins, restart services, or change agent policies.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

CONFIG = Path('/sandbox/.openclaw/openclaw.json')
PRIVATE = Path('/sandbox/.openclaw/mattermost-setup')
BASE_URL = 'https://home-core.tail479ad.ts.net:9443'
ROUTES = {'inbox': 'main', 'personal': 'personal', 'research': 'researcher',
          'engineering': 'engineer', 'home-operations': 'operator'}
ACCOUNTS = {'default': ('main', 'atlas'), 'personal': ('personal', 'luna'),
            'researcher': ('researcher', 'nova'), 'engineer': ('engineer', 'apollo'),
            'operator': ('operator', 'orion'), 'shopper': ('shopper', 'vega'), 'keeper': ('keeper', 'ceres'),
            'travel': ('travel', 'lyra'), 'cook': ('cook', 'hestia'),
            'administrator': ('administrator', 'themis'), 'finance': ('finance', 'plutus'),
            'family': ('family', 'gaia')}
IDENTIFIER = re.compile(r'[a-z0-9]{26}')
DIGEST = re.compile(r'[0-9a-f]{64}')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path, limit: int, *, private: bool = False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('regular input required')
        if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('owner-private input required')
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError('bounded input required')
        return data
    finally:
        os.close(fd)


def outside_workspaces(path: Path, config: dict) -> None:
    resolved = path.resolve()
    defaults = config.get('agents', {}).get('defaults', {})
    roots = [defaults.get('workspace', '/sandbox/.openclaw/workspace')]
    roots += [entry['workspace'] for entry in config.get('agents', {}).get('entries', {}).values()
              if entry.get('workspace')]
    for root in roots:
        if resolved.is_relative_to(Path(root).expanduser().resolve()):
            raise ValueError('secret material must remain outside agent workspaces')


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError('private directory required')
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('private directory ownership required')


def write_private(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def validate_plugin(before: dict, runner) -> str:
    # This static native inspection resolves the real package-owner install
    # record from OpenClaw's SQLite index. plugins.installs in the config is
    # legacy metadata and cannot establish current official provenance.
    result = runner(['openclaw', 'plugins', 'inspect', 'mattermost', '--json'],
                    capture_output=True, timeout=60)
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError('native Mattermost provenance inspection failed')
    report = json.loads(result.stdout)
    plugin, record = report['plugin'], report['install']
    exact_spec = '@openclaw/mattermost@2026.9.1'
    if (plugin.get('id') != 'mattermost' or plugin.get('status') == 'error'
            or record.get('source') != 'npm' or record.get('spec') != exact_spec
            or 'artifactKind' in record or 'sourcePath' in record
            or record.get('version', record.get('resolvedVersion')) != '2026.9.1'
            or record.get('resolvedVersion', '2026.9.1') != '2026.9.1'
            or record.get('resolvedName', '@openclaw/mattermost') != '@openclaw/mattermost'
            or record.get('resolvedSpec', exact_spec) != exact_spec
            or record.get('clawhubPackage', '@openclaw/mattermost') != '@openclaw/mattermost'):
        raise ValueError('official registry-installed pinned Mattermost plugin required')
    root = Path(record['installPath'])
    if (not root.is_absolute() or not root.resolve().is_relative_to(CONFIG.parent.resolve())
            or root.resolve() != Path(plugin['rootDir']).resolve()
            or not Path(plugin['source']).resolve().is_relative_to(root.resolve())):
        raise ValueError('active Mattermost source must match its official install record')
    outside_workspaces(root, before)
    package_bytes = read_file(root / 'package.json', 65536)
    manifest_bytes = read_file(root / 'openclaw.plugin.json', 65536)
    package, manifest = json.loads(package_bytes), json.loads(manifest_bytes)
    if (package.get('name') != '@openclaw/mattermost' or package.get('version') != '2026.9.1'
            or manifest.get('id') != 'mattermost' or not (root / 'dist/index.js').is_file()):
        raise ValueError('matching installed native Mattermost plugin required')
    evidence = {'install': record, 'root': str(root.resolve()), 'source': plugin['source'],
                'package_sha256': digest(package_bytes), 'manifest_sha256': digest(manifest_bytes)}
    return digest(json.dumps(evidence, sort_keys=True).encode())


def account_tokens(manifest: dict, base_url: str) -> dict[str, str]:
    if (not isinstance(manifest, dict) or set(manifest) != {'base_url', 'accounts'}
            or manifest['base_url'] != base_url or not isinstance(manifest['accounts'], list)
            or len(manifest['accounts']) != len(ACCOUNTS)):
        raise ValueError('reviewed account manifest required')
    tokens, identities = {}, set()
    for row in manifest['accounts']:
        if (not isinstance(row, dict)
                or set(row) != {'accountId', 'agentId', 'username', 'bot_id', 'token'}
                or row['accountId'] not in ACCOUNTS or row['accountId'] in tokens
                or (row['agentId'], row['username']) != ACCOUNTS[row['accountId']]
                or not isinstance(row['bot_id'], str) or not IDENTIFIER.fullmatch(row['bot_id'])
                or row['bot_id'] in identities or not isinstance(row['token'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{26,256}', row['token'])):
            raise ValueError('fixed bot identities required')
        tokens[row['accountId']] = row['token']
        identities.add(row['bot_id'])
    if set(tokens) != set(ACCOUNTS) or len(set(tokens.values())) != len(tokens):
        raise ValueError('distinct role accounts required')
    return tokens


def candidate(before: dict, owner: str, channels: dict, accounts: dict, base_url: str, *,
              family_channel_id: str | None = None, family_user_ids: list[str] | None = None) -> tuple[dict, list]:
    if (not isinstance(owner, str) or not IDENTIFIER.fullmatch(owner)
            or not isinstance(channels, dict) or set(channels) != set(ROUTES)
            or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in channels.values())
            or len(set(channels.values())) != len(ROUTES) or base_url != BASE_URL):
        raise ValueError('fixed channel identity and destination required')
    family_users = [owner] if family_user_ids is None else family_user_ids
    if (not isinstance(family_users, list) or not 1 <= len(family_users) <= 8 or owner not in family_users
            or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in family_users)
            or len(set(family_users)) != len(family_users)
            or family_channel_id is not None and (not isinstance(family_channel_id, str)
                                                 or not IDENTIFIER.fullmatch(family_channel_id)
                                                 or family_channel_id in {value for name,value in channels.items() if name != 'personal'})
            or family_channel_id is None and family_users != [owner]):
        raise ValueError('explicit Family channel and authenticated member identities required')
    tokens = account_tokens(accounts, base_url)
    if (before['agents']['defaults']['workspace'] != '/sandbox/.openclaw/workspace'
            or not {agent for agent, _ in ACCOUNTS.values()} <= set(before['agents']['entries'])
            or before['tools']['fs']['workspaceOnly'] is not True
            or before['tools']['elevated']['enabled'] is not False):
        raise ValueError('managed agent boundary required')
    after = copy.deepcopy(before)
    old_channel = after.setdefault('channels', {}).get('mattermost', {})
    existing_accounts = old_channel.get('accounts', {})
    if (not isinstance(existing_accounts, dict) or set(existing_accounts) - set(ACCOUNTS)
            or any(not isinstance(value, dict) or value.get('botToken') != tokens[key]
                   for key, value in existing_accounts.items())
            or old_channel.get('botToken', tokens['default']) != tokens['default']):
        raise ValueError('existing Mattermost credential ownership requires reconciliation')
    shared = old_channel.get('groupAllowFrom', [owner])
    if shared != [owner]:
        if (not isinstance(shared, list) or len(shared) != 2 or owner not in shared
                or len(set(shared)) != 2
                or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in shared)
                or existing_accounts.get('default', {}).get('groupAllowFrom') != shared):
            raise ValueError('existing shared channel audience requires reconciliation')
    channel = copy.deepcopy(old_channel)
    channel.pop('botToken', None)
    # Membership bounds channel access. Atlas answers the exact owner in every
    # joined channel; specialists remain DM/delegation-only to prevent fanout.
    channel.update({'enabled': True, 'baseUrl': base_url,
                    'dmPolicy': 'allowlist', 'allowFrom': [owner],
                    'groupPolicy': 'allowlist', 'groupAllowFrom': [owner],
                    'groups': {'*': {'requireMention': True}},
                    'chatmode': 'oncall', 'requireMention': True,
                    'implicitMentions': {'threadParticipation': False},
                    'contextVisibility': 'allowlist',
                    'dangerouslyAllowNameMatching': False, 'replyToMode': 'all',
                    'replyToModeByChatType': {'direct': 'off', 'group': 'all', 'channel': 'all'},
                    'commands': {'native': False, 'nativeSkills': False},
                    'network': {'dangerouslyAllowPrivateNetwork': True}})
    channel['accounts'] = {key: {'enabled': key != 'family', 'botToken': tokens[key],
                                'dmPolicy': 'allowlist', 'allowFrom': [owner]} for key in ACCOUNTS}
    channel['accounts']['default'].update({
        'groupPolicy': 'allowlist', 'groupAllowFrom': [owner],
        'groups': {'*': {'requireMention': False}}, 'chatmode': 'onmessage', 'requireMention': False,
    })
    # A subsequent roster refresh must retain a separately reviewed shared-room
    # sender grant; Family membership alone never creates this grant.
    channel['groupAllowFrom'] = copy.deepcopy(shared)
    channel['accounts']['default']['groupAllowFrom'] = copy.deepcopy(shared)
    for key in ACCOUNTS.keys() - {'default'}:
        channel['accounts'][key]['groupPolicy'] = 'disabled'
    # The pinned plugin has no channel-admission allowlist. Gaia therefore has
    # a shared-only role on every family-account route, including its fallback.
    # No message on this account may fall through to the private system agent.
    family = channel['accounts']['family']
    family.update({'enabled': False, 'dmPolicy': 'disabled', 'allowFrom': [],
                   'groupPolicy': 'disabled',
                   'groupAllowFrom': sorted(family_users),
                   'groups': {'*': {'requireMention': True}}, 'chatmode': 'onmessage', 'requireMention': True})
    if family_channel_id:
        family['groups'][family_channel_id] = {'requireMention': False}
    after['channels']['mattermost'] = channel
    plugins = after.setdefault('plugins', {})
    plugins['allow'] = list(dict.fromkeys(plugins.get('allow', []) + ['mattermost']))
    plugins.setdefault('entries', {}).setdefault('mattermost', {})['enabled'] = True
    previous = after.get('bindings', [])
    if not isinstance(previous, list):
        raise ValueError('binding list required')
    routes = [{'agentId': agent, 'match': {'channel': 'mattermost', 'accountId': key},
               'session': {'dmScope': 'per-account-channel-peer', 'groupScope': 'per-group'}}
              for key, (agent, _) in ACCOUNTS.items()]
    if family_channel_id:
        routes.insert(0, {'agentId': 'family', 'match': {'channel': 'mattermost', 'accountId': 'family',
                                                     'peer': {'kind': 'channel', 'id': family_channel_id}},
                          'session': {'dmScope': 'per-account-channel-peer', 'groupScope': 'per-group'}})
    prior_routes = copy.deepcopy(routes)
    for item in prior_routes:
        item['session'].pop('groupScope')
    legacy = [{'agentId': agent, 'match': {'channel': 'mattermost', 'accountId': 'default',
                                          'peer': {'kind': 'channel', 'id': channels[name]}}}
              for name, agent in ROUTES.items()]
    unrelated = []
    for item in previous:
        if item.get('match', {}).get('channel') != 'mattermost':
            unrelated.append(item)
        elif item not in legacy and item not in routes and item not in prior_routes:
            raise ValueError('conflicting Mattermost conversation binding')
    # Retire only our exact old peer routes. Agent databases and existing thread
    # records remain untouched; the coordinator must retain its role everywhere.
    after['bindings'] = routes + unrelated
    batch = [{'path': path, 'value': value} for path, value in (
        ('channels.mattermost', channel), ('plugins.allow', plugins['allow']),
        ('plugins.entries.mattermost', plugins['entries']['mattermost']),
        ('bindings', after['bindings']))]
    return after, batch


def household_channel_access(before: dict, owner: str, partner: str,
                             allowed_ids: list[str], system_ids: list[str]) -> tuple[dict, list]:
    """Admit a reviewed adult to Atlas channels; Mattermost membership fences Systems.

    The pinned plugin cannot express per-room sender exclusions. The operator
    must first reconcile the supplied room IDs against real memberships; these
    IDs are review evidence, never a claim of native per-room enforcement.
    """
    identities = [owner, partner]
    if (any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in identities)
            or owner == partner or not isinstance(allowed_ids, list) or not 1 <= len(allowed_ids) <= 64
            or not isinstance(system_ids, list) or len(system_ids) != 2
            or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in allowed_ids + system_ids)
            or len(set(allowed_ids + system_ids)) != len(allowed_ids + system_ids)):
        raise ValueError('explicit distinct adult and non-Systems room identities required')
    channel = before['channels']['mattermost']
    accounts = channel['accounts']
    audience = sorted(identities)
    if (channel.get('enabled') is not True or channel.get('baseUrl') != BASE_URL
            or channel.get('contextVisibility') != 'allowlist'
            or channel.get('dangerouslyAllowNameMatching') is not False
            or set(accounts) != set(ACCOUNTS)):
        raise ValueError('reviewed native channel boundary required')
    for boundary in (channel, accounts['default']):
        if (boundary.get('dmPolicy') != 'allowlist' or boundary.get('allowFrom') != [owner]
                or boundary.get('groupPolicy') != 'allowlist'
                or sorted(boundary.get('groupAllowFrom', [])) not in ([owner], audience)):
            raise ValueError('existing owner DM and bounded channel audience required')
    for key, account in accounts.items():
        if key in {'default', 'family'}:
            continue
        if (account.get('dmPolicy') != 'allowlist' or account.get('allowFrom') != [owner]
                or account.get('groupPolicy') != 'disabled'):
            raise ValueError('private specialist admission differs')
    after = copy.deepcopy(before)
    after['channels']['mattermost']['groupAllowFrom'] = audience
    after['channels']['mattermost']['accounts']['default']['groupAllowFrom'] = audience
    return after, [{'path': path, 'value': audience} for path in (
        'channels.mattermost.groupAllowFrom', 'channels.mattermost.accounts.default.groupAllowFrom')]


def update(owner: str, channels_file: Path, accounts_file: Path, base_url: str = BASE_URL, *,
           apply: bool = False, expected_config_sha256: str | None = None,
           helper_sha256: str | None = None, family_channel_id: str | None = None,
           family_user_ids: list[str] | None = None, runner=subprocess.run) -> dict:
    raw = read_file(CONFIG, 2 * 1024 * 1024)
    before = json.loads(raw)
    outside_workspaces(accounts_file, before)
    outside_workspaces(PRIVATE, before)
    accounts = json.loads(read_file(accounts_file, 16384, private=True))
    channels = json.loads(read_file(channels_file, 16384))
    after, batch = candidate(before, owner, channels, accounts, base_url,
                             family_channel_id=family_channel_id, family_user_ids=family_user_ids)
    return publish(raw, before, after, batch, apply=apply, expected_config_sha256=expected_config_sha256,
                   helper_sha256=helper_sha256, runner=runner)


def share_channels(owner: str, partner: str, allowed_ids: list[str], system_ids: list[str], *,
                   apply: bool = False, expected_config_sha256: str | None = None,
                   helper_sha256: str | None = None, runner=subprocess.run) -> dict:
    raw = read_file(CONFIG, 2 * 1024 * 1024)
    before = json.loads(raw)
    outside_workspaces(PRIVATE, before)
    after, batch = household_channel_access(before, owner, partner, allowed_ids, system_ids)
    return publish(raw, before, after, batch, apply=apply, expected_config_sha256=expected_config_sha256,
                   helper_sha256=helper_sha256, runner=runner, summary={
                       'mode': 'shared-channel-access', 'shared_channel_ids': sorted(allowed_ids),
                       'system_channel_ids': sorted(system_ids), 'partner_id': partner,
                       'systems_exclusion': 'Mattermost-membership', 'private_dm_access_changed': False,
                       'channel_reload_required': apply, 'restart_required': apply})


def family_coordinator_candidate(before: dict, owner: str, partner: str, family_id: str) -> tuple[dict, list]:
    """Retire Gaia ingress while retaining Atlas's already approved adult audience."""
    if (any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in (owner, partner, family_id))
            or owner == partner):
        raise ValueError('Exact existing household identities required')
    channel = before['channels']['mattermost']
    atlas, gaia = channel['accounts']['default'], channel['accounts']['family']
    audience = sorted([owner, partner])
    if (sorted(channel['groupAllowFrom']) != audience or sorted(atlas['groupAllowFrom']) != audience
            or atlas.get('allowFrom') != [owner] or atlas.get('dmPolicy') != 'allowlist'
            or gaia.get('dmPolicy') != 'disabled' or gaia.get('allowFrom') != []
            or sorted(gaia.get('groupAllowFrom', [])) != audience
            or family_id not in gaia.get('groups', {})):
        raise ValueError('Existing approved household boundary differs')
    after = copy.deepcopy(before)
    target = after['channels']['mattermost']['accounts']['family']
    target.update(enabled=False, groupPolicy='disabled')
    batch = [{'path': 'channels.mattermost.accounts.family.' + key, 'value': target[key]}
             for key in ('enabled', 'groupPolicy') if gaia.get(key) != target[key]]
    return after, batch


def retire_family_contact(owner: str, partner: str, family_id: str, *, apply=False,
                          expected_config_sha256=None, helper_sha256=None, runner=subprocess.run) -> dict:
    raw = read_file(CONFIG, 2 * 1024 * 1024)
    before = json.loads(raw)
    after, changes = family_coordinator_candidate(before, owner, partner, family_id)
    return publish(raw, before, after, changes, apply=apply, expected_config_sha256=expected_config_sha256,
                   helper_sha256=helper_sha256, runner=runner,
                   summary={'mode': 'family-coordinator', 'new_tool_grants': False,
                            'private_dm_access_changed': False})


def publish(raw: bytes, before: dict, after: dict, batch: list, *, apply: bool,
            expected_config_sha256: str | None, helper_sha256: str | None,
            runner, summary: dict | None = None) -> dict:
    source_digest = digest(raw)
    if apply and (not expected_config_sha256 or not DIGEST.fullmatch(expected_config_sha256)
                  or expected_config_sha256 != source_digest
                  or not helper_sha256 or not DIGEST.fullmatch(helper_sha256)
                  or helper_sha256 != digest(Path(__file__).read_bytes())):
        raise ValueError('reviewed source and helper digest required')
    provenance_digest = validate_plugin(before, runner)
    private_directory(PRIVATE)
    batch_path = PRIVATE / ('candidate-' + uuid.uuid4().hex + '.json')
    write_private(batch_path, json.dumps(batch).encode())
    command = ['openclaw', 'config', 'set', '--batch-file', str(batch_path)]
    try:
        validation = runner(command + ['--dry-run'], capture_output=True, timeout=60)
        if validation.returncode:
            raise ValueError('native Mattermost candidate refused')
        if read_file(CONFIG, 2 * 1024 * 1024) != raw:
            raise ValueError('source configuration changed during validation')
        if validate_plugin(before, runner) != provenance_digest:
            raise ValueError('native Mattermost install changed during validation')
        result = {'validated': True, 'applied': False, 'source_config_sha256': source_digest,
                  'helper_sha256': digest(Path(__file__).read_bytes()), 'channel_count': len(ROUTES),
                  'plugin_provenance_sha256': provenance_digest,
                  'dm_policy': 'allowlist', 'account_count': len(ACCOUNTS),
                  'channel_contact_account': 'default', 'specialist_channel_policy': 'disabled',
                  'family_ingress_enabled': after['channels']['mattermost']['accounts']['family']['enabled'],
                  'family_route_prepared': after['channels']['mattermost']['accounts']['family']['enabled'],
                  'base_url': BASE_URL, 'restart_required': apply}
        result.update(summary or {})
        if not apply:
            return result
        write_private(PRIVATE / ('backup-' + uuid.uuid4().hex + '.json'), raw)
        # Native config set performs the atomic batch publication. No raw file
        # replacement, provider rewrite, gateway restart, or credential export.
        applied = runner(command, capture_output=True, timeout=60)
        if applied.returncode:
            raise ValueError('native Mattermost application requires reconciliation')
        persisted = json.loads(read_file(CONFIG, 2 * 1024 * 1024))
        # Native bookkeeping may update meta; all operator-owned sections must
        # exactly match the candidate, including models, agents, MCP and tools.
        if {k: v for k, v in persisted.items() if k != 'meta'} != {k: v for k, v in after.items() if k != 'meta'}:
            raise ValueError('native publication differs from reviewed candidate; reconcile backup')
        result['applied'] = True
        return result
    finally:
        batch_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', required=True)
    parser.add_argument('--channels-json', type=Path)
    parser.add_argument('--accounts-json', type=Path)
    parser.add_argument('--base-url', default=BASE_URL)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-config-sha256')
    parser.add_argument('--helper-sha256')
    parser.add_argument('--family-channel-id', help='trusted private Family room; retains the disabled historical Gaia account')
    parser.add_argument('--family-user-id', action='append', help='explicit approved Family member IDs; include owner')
    parser.add_argument('--share-channel-access', action='store_true', help='reviewed additive Atlas channel sender grant; private DMs remain owner-only')
    parser.add_argument('--retire-family-contact', action='store_true', help='disable Gaia ingress before consolidating the existing adult rooms under Atlas')
    parser.add_argument('--partner-id')
    parser.add_argument('--shared-channel-id', action='append')
    parser.add_argument('--system-channel-id', action='append', help='exact restricted Infrastructure/OpenClaw room IDs; membership is enforced separately')
    args = parser.parse_args()
    try:
        if args.retire_family_contact:
            if args.share_channel_access or args.channels_json or args.accounts_json or args.shared_channel_id or args.system_channel_id:
                raise ValueError('Family retirement cannot change other channel policy')
            result = retire_family_contact(args.owner_id, args.partner_id, args.family_channel_id,
                apply=args.apply, expected_config_sha256=args.expected_config_sha256, helper_sha256=args.helper_sha256)
        elif args.share_channel_access:
            if (args.channels_json or args.accounts_json or args.family_channel_id or args.family_user_id
                    or args.base_url != BASE_URL):
                raise ValueError('channel-sharing review cannot regenerate account configuration')
            result = share_channels(args.owner_id, args.partner_id, args.shared_channel_id,
                                    args.system_channel_id, apply=args.apply,
                                    expected_config_sha256=args.expected_config_sha256,
                                    helper_sha256=args.helper_sha256)
        else:
            if (not args.channels_json or not args.accounts_json or args.partner_id
                    or args.shared_channel_id or args.system_channel_id):
                raise ValueError('explicit native setup inputs required')
            result = update(args.owner_id, args.channels_json, args.accounts_json, args.base_url,
                            apply=args.apply, expected_config_sha256=args.expected_config_sha256,
                            helper_sha256=args.helper_sha256, family_channel_id=args.family_channel_id,
                            family_user_ids=args.family_user_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        print('Native Mattermost setup refused; preserve private inputs and reconcile configuration.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
