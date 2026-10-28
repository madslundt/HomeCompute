#!/usr/bin/env python3
"""Operator acceptance probes using the private Mattermost account and real threads."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import uuid

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/srv/state/mattermost/private')
SPEC = importlib.util.spec_from_file_location('mattermost_bootstrap', HERE / 'bootstrap-mattermost.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)

ROLE_ACCOUNTS = {'main': 'default', **{role: role for role in
                 ('personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel')}}
ROLE_CHANNELS = {'coordinator': 'inbox', 'personal': 'personal', 'researcher': 'research',
                 'engineer': 'engineering', 'operator': 'home-operations', 'shopper': 'shopping', 'keeper': 'storage',
                 'travel': 'travel'}
TOOL_PROBES = {
    'research-tool': ('researcher', 'research', 'Use the protected browser to navigate to https://example.com and read its page title.'),
    'engineering-tool': ('engineer', 'engineering', 'Use homecompute__homecompute_task_status with empty arguments to read bounded task status.'),
    'operations-tool': ('operator', 'home-operations', 'Use homecompute__homecompute_systems with empty arguments to list bounded observed systems.'),
    'shopping-tool': ('shopper', 'shopping', 'Read skills/household-shopping/SKILL.md, then use homecompute__homecompute_shopping_list with empty arguments to read the current shopping list.'),
    'storage-tool': ('keeper', 'storage', 'Read skills/household-storage/SKILL.md, then use homecompute__homecompute_storage_list with empty arguments to read physical household inventory.'),
    'travel-tool': ('travel', 'travel', 'Read skills/travel-adventures/SKILL.md, then use homecompute__homecompute_web_search with query "Visit Copenhagen official family activities" to find one official public source and its URL. Do not include private calendar or household details in the search.'),
}
SCENARIOS = [*ROLE_CHANNELS, 'channel-coordinator', 'channel-context', 'project-membership', 'project-resume',
             *('dm-' + role for role in ROLE_ACCOUNTS), *('dm-resume-' + role for role in ROLE_ACCOUNTS),
             *TOOL_PROBES, 'thread-isolation', 'delegation', 'delegation-concurrency', 'restart-resume']


def probe_path(scenario: str, label: str | None = None) -> Path:
    selected = scenario if label is None else label
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', selected):
        raise ValueError('Bounded acceptance label required.')
    return PRIVATE / ('acceptance-' + selected + '.json')


def account_rows() -> dict[str, dict]:
    accounts = json.loads((PRIVATE / 'agent-accounts.json').read_text())['accounts']
    rows = {}
    bot_ids, usernames = set(), set()
    if not isinstance(accounts, list):
        raise ValueError('Retained agent roster required.')
    for row in accounts:
        if not isinstance(row, dict):
            raise ValueError('Distinct retained agent identities required.')
        account = row.get('accountId')
        username = row.get('username')
        if (account not in ROLE_ACCOUNTS.values() or account in rows
                or row.get('agentId') != ('main' if account == 'default' else account)
                or not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9._-]{0,63}', username)
                or username in usernames or row.get('bot_id') in bot_ids
                or not re.fullmatch(r'[a-z0-9]{26}', row.get('bot_id', ''))):
            raise ValueError('Distinct retained agent identities required.')
        rows[account] = row
        bot_ids.add(row['bot_id']); usernames.add(username)
    if not {'default', 'personal', 'researcher', 'engineer', 'operator'} <= rows.keys():
        raise ValueError('Retained agent roster incomplete.')
    return rows


def mention(rows: dict[str, dict], account: str) -> str:
    return '@' + rows[account]['username']


def topic_channels(channels: dict, required: str, api: object, owner: str) -> dict:
    if required in {'shopping', 'storage', 'home', 'travel'}:
        topics = json.loads((PRIVATE / 'n8n-channels.json').read_text())
        value = topics.get('channels', {}).get(required)
        if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9]{26}', value):
            raise ValueError('Retained private topic channel required.')
        channel, _ = api.request('GET', '/channels/' + value)
        if channel.get('type') != 'P' or channel.get('creator_id') != owner:
            raise ValueError('Owner-created private topic channel required.')
        return {**channels, required: value}
    return channels


def resolve_context_channels(api: object, channels: dict, owner: str, coordinator: str, selected: str) -> dict:
    names = selected.split(',')
    if len(names) != 2 or len(set(names)) != 2 or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name) for name in names):
        raise ValueError('Two distinct bounded context channel names required.')
    inbox, _ = api.request('GET', '/channels/' + channels['inbox'])
    result = {}
    for name in names:
        path = '/channels/' + channels[name] if name in channels else '/teams/' + inbox['team_id'] + '/channels/name/' + name
        channel, _ = api.request('GET', path)
        selected_id = bootstrap.identity(channel, 'context channel')
        if (channel.get('type') not in {'O', 'P'} or channel.get('creator_id') != owner
                or channel.get('team_id') != inbox['team_id']):
            raise ValueError('Owner-created channels in the retained team required.')
        for user in (owner, coordinator):
            member, _ = api.request('GET', '/channels/' + selected_id + '/members/' + user, missing=True)
            if not isinstance(member, dict) or member.get('user_id') != user:
                raise ValueError('Owner and coordinator must already belong to both context channels.')
        result[name] = selected_id
    if len(set(result.values())) != 2:
        raise ValueError('Context channels must resolve to distinct identities.')
    return result


def project_probe(resume: bool = False, label: str | None = None, previous_label: str | None = None,
                  project_channel: str = 'agent-playground') -> dict:
    raise ValueError('Legacy project probes expect specialist channel replies and are retired under '
                     'the single-coordinator policy. Use channel-coordinator or specialist DM probes.')


def run(scenario: str, label: str | None = None, *, previous_label: str | None = None,
        project_channel: str = 'agent-playground', context_channels: str = 'agent-playground,inbox') -> dict:
    if scenario in {'project-membership', 'project-resume'}:
        return project_probe(scenario == 'project-resume', label, previous_label, project_channel)
    if scenario not in SCENARIOS:
        raise ValueError('Unknown probe scenario.')
    probe_path(scenario, label)
    if previous_label is not None:
        probe_path(scenario, previous_label)
    report = probe_path(scenario, label)
    if report.exists():
        raise ValueError('This probe already has retained state; inspect it before starting another.')
    credentials = json.loads((PRIVATE / 'credentials.json').read_text())
    channels = json.loads((PRIVATE / 'channels.json').read_text())
    api = bootstrap.API(credentials['operator_session'])
    accounts = account_rows()
    account_id = 'default'  # Channels have one visible responder; specialists keep their own DMs.
    direct = scenario.startswith('dm-')
    resume_dm = scenario.startswith('dm-resume-')
    if direct:
        role = scenario.removeprefix('dm-resume-') if resume_dm else scenario.removeprefix('dm-')
        account_id = ROLE_ACCOUNTS[role]
    account = accounts[account_id]
    if direct:
        value, _ = api.request('POST', '/channels/direct', [credentials['operator_id'], account['bot_id']])
        channels['direct'] = bootstrap.identity(value, 'direct conversation')
    state = {'scenario': scenario, 'account_id': account_id, 'started_at': time.time(),
             'posts': [], 'post_times': {}, 'replies': [], 'complete': False,
             'policy': 'single-channel-coordinator-v1', 'checks': []}
    bootstrap.save(report, state)
    marker = uuid.uuid4().hex[:10].upper()

    def post(channel: str, text: str, root: str = '', *, address: bool = True, sender: str = 'owner') -> str:
        # Record admission before the request. A lost acknowledgement cannot
        # automatically send a duplicate task with possible external effects.
        state['pending_post'] = {'channel': channel, 'root': root, 'sender': sender}
        bootstrap.save(report, state)
        message = text if direct or not address else '@' + account['username'] + ' ' + text
        client = api if sender == 'owner' else bootstrap.API(accounts[sender]['token'])
        value, _ = client.request('POST', '/posts', {'channel_id': channels[channel],
                                                'root_id': root, 'message': message})
        selected = bootstrap.identity(value, 'acceptance post')
        state['posts'].append(selected)
        state['post_times'][selected] = value['create_at']
        state.pop('pending_post')
        bootstrap.save(report, state)
        return selected

    bot_ids = {row['bot_id'] for row in accounts.values()}

    def bot_replies(root: str, after: int = 0) -> list[dict]:
        value, _ = api.request('GET', '/posts/' + root + '/thread')
        return [row for row in value.get('posts', {}).values()
                if row.get('user_id') in bot_ids and row['id'] not in state['posts']
                and row.get('create_at', 0) >= after]

    def coordinator_only(root: str) -> None:
        if any(row['user_id'] != accounts['default']['bot_id'] for row in bot_replies(root)):
            raise ValueError('A specialist replied visibly in a coordinator-only channel.')

    def silent(root: str, after: int) -> None:
        deadline = time.monotonic() + 15
        while True:
            if bot_replies(root, after):
                raise ValueError('A bot-authored message triggered a visible agent reply.')
            if time.monotonic() >= deadline:
                return
            time.sleep(1)

    def wait(root: str, expected: str, *, after: int = 0, timeout: int = 180) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            path = '/channels/' + channels['direct'] + '/posts?per_page=50' if direct else f'/posts/{root}/thread'
            value, _ = api.request('GET', path)
            if not direct:
                coordinator_only(root)
            for item in value.get('posts', {}).values():
                if (item.get('user_id') == account['bot_id'] and (direct or item.get('root_id') == root)
                        and item.get('create_at', 0) >= max(after, state['post_times'].get(root, 0))
                        and expected in item.get('message', '')):
                    state['replies'].append({'id': item['id'], 'root_id': root, 'text': item['message']})
                    bootstrap.save(report, state)
                    return item
            time.sleep(3)
        raise ValueError('Expected reply did not arrive in its original thread; retained probe was not replayed.')

    if scenario == 'channel-context':
        resolved = resolve_context_channels(api, channels, credentials['operator_id'], accounts['default']['bot_id'], context_channels)
        channels = {**channels, **resolved}
        first, second = list(resolved)
        cases = [(first, 'A1'), (second, 'B1'), (first, 'A2')]
        codes = {name: 'CANARY_' + name + '_' + uuid.uuid4().hex.upper() for _, name in cases}
        roots = {}
        state['context_channels'] = resolved
        state['context_roots'] = {}
        bootstrap.save(report, state)

        def exact(root: str, expected: str, after: int = 0) -> None:
            answer = wait(root, expected, after=after)
            if answer['message'].strip() != expected:
                raise ValueError('Context probe requires a code-only reply with no other content.')
            forbidden = [code for code in codes.values() if code != expected]
            if any(code in row.get('message', '') for row in bot_replies(root) for code in forbidden):
                raise ValueError('A reference leaked between channels or task threads.')

        for channel, name in cases:
            code = codes[name]
            root = post(channel, 'Setup context check. The reference assigned to THIS thread is ' + code +
                        '. Keep it only in this conversation. Reply with exactly ' + code + '. Do not use tools or write memory.', address=False)
            roots[name] = root
            state['context_roots'][name] = {'channel': channel, 'root_id': root, 'reference': code}
            bootstrap.save(report, state)
            exact(root, code)
        for channel, name in cases:
            follow = post(channel, 'What context reference was assigned in THIS thread? Reply with the code only. '
                          'Do not use tools, write memory, or explain.', roots[name], address=False)
            exact(roots[name], codes[name], state['post_times'][follow])
        state['checks'].append('three-threads-across-two-channels-recall-only-own-reference')
        unrelated = post(first, 'What context reference was assigned in THIS thread? Reply with that code only, or NO_REFERENCE if none. '
                         'Do not use tools, inspect other threads, write memory, or explain.', address=False)
        state['context_roots']['unrelated'] = {'channel': first, 'root_id': unrelated}
        exact(unrelated, 'NO_REFERENCE')
        for name, root in roots.items():
            coordinator_only(root)
            if any(code in row.get('message', '') for row in bot_replies(root)
                   for code in codes.values() if code != codes[name]):
                raise ValueError('A delayed cross-thread reference leaked.')
        state['checks'].append('unrelated-new-root-knows-no-other-thread-reference')
        state['checks'].append('all-context-replies-are-code-only-from-coordinator')
        state['evidence_scope'] = 'Observed output and thread routing only; internal session isolation requires native inspection.'
    elif scenario == 'channel-coordinator':

        route = 'home'
        channels = topic_channels(channels, route, api, credentials['operator_id'])
        expected = 'COORDINATOR_' + marker
        root = post(route, 'Setup check. Reply with exactly ' + expected + '. Do not use tools or write memory.', address=False)
        wait(root, expected)
        state['checks'].append('unmentioned-owner-receives-coordinator-thread-reply')
        follow = post(route, 'Setup check follow-up. Reply with exactly FOLLOW_' + marker + '. Do not use tools or write memory.', root, address=False)
        wait(root, 'FOLLOW_' + marker, after=state['post_times'][follow])
        state['checks'].append('unmentioned-owner-followup-stays-in-original-thread')
        # All non-guest bots already belong to Town Square. Use synthetic text
        # there to prove channel policy, without changing any memberships.
        channel, _ = api.request('GET', '/channels/' + channels[route])
        town, _ = api.request('GET', '/teams/' + channel['team_id'] + '/channels/name/town-square')
        channels['town-square'] = bootstrap.identity(town, 'Town Square')
        mentioned = post('town-square', mention(accounts, 'personal') + ' Setup check for the coordinator. Reply with exactly MENTION_' + marker + '. Do not use tools or write memory.', address=False)
        wait(mentioned, 'MENTION_' + marker)
        state['checks'].append('owner-specialist-mention-gets-only-coordinator')
        bot = post('town-square', mention(accounts, 'default') + ' Setup safety check. Reply with exactly BOT_' + marker + '. Do not use tools.', address=False, sender='personal')
        silent(bot, state['post_times'][bot])
        coordinator_only(root); coordinator_only(mentioned)
        state['checks'].append('bot-authored-message-cannot-trigger-agents')
    elif direct:
        if resume_dm:
            previous = json.loads(probe_path('dm-' + role, previous_label).read_text())
            if previous.get('account_id') != account_id:
                raise ValueError('Resume must use the original agent account.')
            if previous.get('complete') is not True or not previous.get('replies'):
                raise ValueError('Initial DM check must pass before resuming it.')
            expected = previous['replies'][0]['text'].strip()
            prompt = ('After reconnecting, what setup-check reference did you return in our previous exchange? '
                      'Reply with only that reference. Do not use tools or write memory.')
        else:
            expected = 'DM_' + account_id.upper() + '_' + marker
            prompt = f'Setup check. Reply with exactly {expected}. Do not use tools or write memory.'
        root = post('direct', prompt)
        wait(root, expected)
    elif scenario in ROLE_CHANNELS:
        route = ROLE_CHANNELS[scenario]
        channels = topic_channels(channels, route, api, credentials['operator_id'])
        expected = scenario.upper() + '_' + marker
        prompt = f'Setup check. Reply with exactly {expected}. Do not use tools or write memory.'
        root = post(route, prompt)
        wait(root, expected)
    elif scenario in TOOL_PROBES:
        leaf, route, instruction = TOOL_PROBES[scenario]
        channels = topic_channels(channels, route, api, credentials['operator_id'])
        expected = scenario.upper().replace('-', '_') + '_' + marker
        root = post(route, f'Setup check. Delegate this read-only check using sessions_spawn with agentId {leaf} and mode run: '
                    f'{instruction} After the specialist confirms success, reply with exactly {expected} in this same thread. '
                    'Acknowledge accepted background work without quoting that reference, then use sessions_yield to await it. '
                    'If the tool fails, report the failure instead. Do not submit jobs, change anything, or write memory.')
        wait(root, expected, timeout=300)
    elif scenario == 'thread-isolation':
        first, second = 'ALPHA_' + marker, 'BETA_' + marker
        root_a = post('personal', f'Setup check. The reference for this thread is {first}. Repeat it exactly. Do not write memory.')
        wait(root_a, first)
        root_b = post('personal', f'Setup check. The reference for this thread is {second}. Repeat it exactly. Do not write memory.')
        wait(root_b, second)
        since = int(time.time() * 1000)
        post('personal', 'What is the reference for this thread? Reply with only that reference.', root_a)
        answer = wait(root_a, first, after=since)
        if second in answer['message']:
            raise ValueError('Thread contexts were mixed.')
        state['resume_root'] = root_a
        state['resume_expected'] = first
    elif scenario in {'delegation', 'delegation-concurrency'}:
        expected = 'DELEGATED_' + marker + '_42'
        root = post('inbox', 'Setup check. Delegate this to the personal specialist using sessions_spawn '
                    'with agentId personal and mode run: calculate 6*7 and return exactly ' + expected + '. '
                    'Acknowledge the accepted background task without quoting the expected result, '
                    'and use sessions_yield to await its result. '
                    'When the specialist returns, include its exact result in this same thread. '
                    'Do not call any other action tools or write memory.')
        wait(root, expected, timeout=300)
    elif scenario == 'restart-resume':
        previous = json.loads(probe_path('thread-isolation', previous_label).read_text())
        if previous.get('complete') is not True or previous.get('account_id') != 'default':
            raise ValueError('A fresh coordinator thread-isolation probe must pass before restart/resume; legacy specialist receipts remain historical.')
        root, expected = previous['resume_root'], previous['resume_expected']
        since = int(time.time() * 1000)
        post('personal', 'After reconnecting, what is the reference for this thread? Reply with only the reference.', root)
        wait(root, expected, after=since)
    else:
        raise ValueError('Unknown probe scenario.')
    state['complete'] = True
    state['completed_at'] = time.time()
    bootstrap.save(report, state)
    return {'scenario': scenario, 'passed': True, 'posts': len(state['posts']),
            'verified_thread_replies': len(state['replies']), 'checks': state['checks'],
            'account_id': account_id, 'private_report': str(report)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', choices=SCENARIOS)
    parser.add_argument('--label')
    parser.add_argument('--previous-label', help='Read a prior labeled probe without changing its receipt.')
    parser.add_argument('--project-channel', default='agent-playground', help='Owner-created private channel slug.')
    parser.add_argument('--context-channels', default='agent-playground,inbox', help='Two existing owner-created channel slugs, comma-separated.')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root on home-core.')
    os.umask(0o077)
    try:
        print(json.dumps(run(args.scenario, args.label, previous_label=args.previous_label, project_channel=args.project_channel, context_channels=args.context_channels)))
    except (OSError, KeyError, ValueError, bootstrap.BootstrapError) as error:
        raise SystemExit(str(error) if isinstance(error, (ValueError, bootstrap.BootstrapError))
                         else 'Acceptance probe failed; inspect private retained state.') from None


if __name__ == '__main__':
    main()
