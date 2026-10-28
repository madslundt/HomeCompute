#!/usr/bin/env python3
"""Deliver durable owner/shared reminders through fixed Mattermost or HA phones."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from typing import Any

from openclaw_household_requests import private_file
from openclaw_household_tasks import FamilyTasks, HouseholdTasks, ZONE, TOKEN_PATH, FAMILY_TOKEN_PATH, configuration, instant
from openclaw_services import private

ORIGIN = 'http://127.0.0.1:18065/api/v4'
MARKER = 'homecompute_household_task_reminder'
FAMILY_MARKER = 'homecompute_family_task_reminder'


class Mattermost:
    def __init__(self, config: dict, *, scope: str = 'owner'):
        if scope not in {'owner', 'family'}:
            raise ValueError('Explicit household task scope required')
        self.config = config
        self.scope = scope
        self.marker = FAMILY_MARKER if scope == 'family' else MARKER
        token_path = FAMILY_TOKEN_PATH if scope == 'family' else TOKEN_PATH
        if config.get('token_file') != str(token_path):
            raise ValueError('Task delivery token differs from fixed scope')
        token = private(token_path, 512).decode().strip()
        if not re.fullmatch(r'[a-z0-9]{26}', token):
            raise ValueError('Private Mattermost bot token required')
        self.token = token
        self.deadline = time.monotonic() + 45
        from openclaw_services import NoRedirect
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path: str, body: dict | None = None) -> Any:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise ValueError('Household task delivery time budget exhausted')
        request = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None,
                                        headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=min(8, remaining)) as response:
            raw = response.read(1048577)
            if len(raw) > 1048576 or response.status not in {200, 201}:
                raise ValueError('Bounded Mattermost receipt required')
            return json.loads(raw)

    def verify(self) -> None:
        account = self.request('/users/me')
        channel = self.request('/channels/' + self.config['channel_id'])
        if (not isinstance(account, dict) or account.get('id') != self.config['bot_id'] or account.get('is_bot') is not True
                or not isinstance(channel, dict) or channel.get('id') != self.config['channel_id'] or channel.get('type') != 'P'
                or channel.get('delete_at') != 0):
            raise ValueError('Private task room and exact delivery bot required')

    def receipt(self, post: Any, key: str) -> str:
        if (not isinstance(post, dict) or post.get('channel_id') != self.config['channel_id']
                or post.get('user_id') != self.config['bot_id'] or not isinstance(post.get('props'), dict)
                or post['props'].get(getattr(self, 'marker', MARKER)) != key
                or not re.fullmatch(r'[a-z0-9]{26}', str(post.get('id', '')))):
            raise ValueError('Exact household task delivery receipt required')
        return post['id']

    def reconcile(self, row: dict) -> str | None:
        # Scan at most 500 recent messages. Absence never authorizes another send.
        for page in range(5):
            result = self.request(f"/channels/{self.config['channel_id']}/posts?page={page}&per_page=100")
            if not isinstance(result, dict) or not isinstance(result.get('order'), list) or not isinstance(result.get('posts'), dict):
                raise ValueError('Bounded task-room history required')
            if len(result['order']) > 100: raise ValueError('Task-room history exceeds budget')
            for post_id in result['order']:
                post = result['posts'].get(post_id)
                if (isinstance(post, dict) and isinstance(post.get('props'), dict)
                        and post['props'].get(getattr(self, 'marker', MARKER)) == row['key']):
                    return self.receipt(post, row['key'])
            if len(result['order']) < 100: break
        return None

    def send(self, row: dict) -> str:
        task = row['task']
        title = task['title'].replace('@', '@\u200b')
        due = instant(task['due_at']).astimezone(ZONE).strftime('%Y-%m-%d %H:%M %Z') if task['due_at'] else 'No due time'
        label = 'Family task reminder' if getattr(self, 'scope', 'owner') == 'family' else 'Household task reminder'
        text = f"{label}: {title}\nDue: {due}"
        if task['assignee']: text += '\nAssigned to: ' + task['assignee'].replace('@', '@\u200b')
        text += '\nTask ID: ' + task['task_id']
        post = self.request('/posts', {'channel_id': self.config['channel_id'], 'message': text,
                                      'props': {getattr(self, 'marker', MARKER): row['key']}})
        return self.receipt(post, row['key'])


class DeliveryRouter:
    """Dispatch through fixed destinations; phone admission is not device delivery."""
    def __init__(self, config: dict, *, scope: str, policy: Any = None):
        self.config, self.scope, self.policy = config, scope, policy
        self._mattermost: Mattermost | None = None

    def mattermost(self) -> Mattermost:
        if self._mattermost is None:
            self._mattermost = Mattermost(self.config, scope=self.scope)
            self._mattermost.verify()
        return self._mattermost

    def send(self, row: dict) -> str:
        if row['task'].get('delivery_target', 'mattermost') == 'person':
            if self.policy is None:
                raise ValueError('Configured personal reminder delivery required')
            self.policy.validate_task(row['task'], self.scope)
            from openclaw_location_transport import LocationClient
            return LocationClient(self.policy).send_person({**row, 'scope': self.scope})
        return self.mattermost().send(row)

    def reconcile(self, row: dict) -> str | None:
        if row['task'].get('delivery_target', 'mattermost') == 'person':
            return None
        return self.mattermost().reconcile(row)

    def terminal_state(self, row: dict) -> str:
        return 'accepted' if row['task'].get('delivery_target', 'mattermost') == 'person' else 'delivered'


def tick(tasks: HouseholdTasks, client: Any, now: datetime) -> dict:
    result = {'delivered': 0, 'accepted': 0, 'reconciled': 0, 'uncertain': 0, 'retryable': 0, 'quiet': False}
    # A prior process may have died after POST. Reconcile against stored post markers.
    for row in tasks.reminder_rows('sending'):
        tasks.reminder_transition(row['key'], 'sending', 'uncertain', now)
    for row in tasks.reminder_rows('uncertain')[:3]:
        post_id = client.reconcile(row)
        if post_id:
            tasks.reminder_transition(row['key'], 'uncertain', 'delivered', now, post_id)
            result['reconciled'] += 1
        else:
            tasks.reminder_transition(row['key'], 'uncertain', 'uncertain', now)
            result['uncertain'] += 1
    tasks.reminders(now)
    if now.astimezone(ZONE).hour >= 22 or now.astimezone(ZONE).hour < 7:
        result['quiet'] = True
        return result
    for row in tasks.reminder_rows('pending')[:8]:
        if not tasks.reminder_transition(row['key'], 'pending', 'sending', now,
                                         expected_revision=row['revision']): continue
        try:
            post_id = client.send(row)
        except urllib.error.HTTPError as error:
            # Explicit rate limiting rejects admission. Other failures may have committed.
            state = 'pending' if error.code == 429 else 'uncertain'
            error.close()
            tasks.reminder_transition(row['key'], 'sending', state, now)
            result['retryable' if state == 'pending' else 'uncertain'] += 1
            break
        except (OSError, ValueError, TypeError, KeyError):
            tasks.reminder_transition(row['key'], 'sending', 'uncertain', now)
            result['uncertain'] += 1
            break
        terminal = client.terminal_state(row) if isinstance(client, DeliveryRouter) else 'delivered'
        tasks.reminder_transition(row['key'], 'sending', terminal, now, post_id)
        result[terminal] += 1
    return result


def run_scope(scope: str, state: Path, config: Path, *, check: bool = False,
              project_home_assistant: bool = False) -> dict:
    if scope not in {'owner', 'family'} or project_home_assistant and scope != 'family':
        raise ValueError('Explicit task delivery scope required')
    from openclaw_location_policy import CONFIG_PATH, Policy
    policy = Policy(CONFIG_PATH) if CONFIG_PATH.exists() else None
    tasks = (FamilyTasks if scope == 'family' else HouseholdTasks)(state, location_policy=policy)
    lock_path = state / ('family-task-reminders.lock' if scope == 'family' else 'household-task-reminders.lock')
    private_file(lock_path)
    descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'busy': True}
        selected = configuration(config, scope=scope)
        now = datetime.now(timezone.utc)
        if project_home_assistant and not check:
            from openclaw_family_task_projection import publish
            publish(tasks, now)
        if check:
            client = Mattermost(selected, scope=scope)
            client.verify()
            return {'ok': True, 'destination_verified': True, 'sent': 0}
        tasks.reminders(now)
        router = DeliveryRouter(selected, scope=scope, policy=policy)
        if not any(row['task'].get('delivery_target') == 'person' for row in tasks.reminder_rows('pending')):
            router.mattermost()
        return tick(tasks, router, now)
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--scope', choices=('owner', 'family'), default='owner')
    parser.add_argument('--project-home-assistant', action='store_true', help='publish shared Family tasks to the fixed HA briefing sensor')
    parser.add_argument('--check', action='store_true', help='verify fixed bot/channel configuration without sending reminders')
    args = parser.parse_args()
    if args.project_home_assistant and args.scope != 'family':
        parser.error('Home Assistant task projection requires the shared Family scope')
    os.umask(0o077)
    try:
        result = run_scope(args.scope, args.state, args.config, check=args.check,
                           project_home_assistant=args.project_home_assistant)
        print(json.dumps(result))
        if result.get('uncertain'):
            raise SystemExit(2)  # Surface held deliveries to existing service-health monitoring.
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'ok': False, 'reason': 'household_task_delivery_unavailable'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
