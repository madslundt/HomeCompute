"""HA-native zone subscriptions and fixed-recipient reminder phone delivery."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import re
import select
import time
from typing import Any
import unicodedata
import urllib.request

from openclaw_ha_websocket import HomeAssistantError, HomeAssistantSocket, HOST, PORT, MAX_MESSAGES, TIMEOUT, encode
from openclaw_location_policy import Policy, TOKEN_PATH, timestamp
from openclaw_services import private


class LocationClient(HomeAssistantSocket):
    def __init__(self, policy: Policy):
        if not isinstance(policy, Policy):
            raise ValueError('Validated location reminder policy required')
        self.policy = policy
        super().__init__(private(TOKEN_PATH, 4096).decode().strip())
        self._rules: dict[int, tuple[str, str, str]] = {}
        self._pending: deque[dict[str, str]] = deque()

    def __enter__(self) -> LocationClient:
        self._rules.clear()
        self._pending.clear()
        super().__enter__()
        return self

    def call(self, command: dict[str, Any]) -> Any:
        raise HomeAssistantError()  # Only the methods below admit generated finite commands.

    def _failure(self) -> None:
        self._close()
        self._buffer.clear()
        self._pending.clear()
        raise HomeAssistantError() from None

    def _project(self, message: dict[str, Any], now: datetime | None = None) -> dict[str, str]:
        identifier = message.get('id')
        if message.get('type') != 'event' or type(identifier) is not int or identifier not in self._rules:
            raise HomeAssistantError()
        person, zone, event = self._rules[identifier]
        raw = message.get('event')
        variables = raw.get('variables') if isinstance(raw, dict) else None
        trigger = variables.get('trigger') if isinstance(variables, dict) else None
        if not isinstance(trigger, dict):
            raise HomeAssistantError()
        before, after, raw_zone = trigger.get('from_state'), trigger.get('to_state'), trigger.get('zone')
        entity = self.policy.person(person)['entity_id']
        if (trigger.get('entity_id') != entity or trigger.get('event') != event
                or not isinstance(raw_zone, dict) or raw_zone.get('entity_id') != self.policy.zone(zone)['entity_id']
                or not isinstance(before, dict) or not isinstance(after, dict)
                or before.get('entity_id') != entity or after.get('entity_id') != entity
                or not isinstance(before.get('state'), str) or not isinstance(after.get('state'), str)
                or before['state'] in {'unknown', 'unavailable'} or after['state'] in {'unknown', 'unavailable'}):
            raise HomeAssistantError()
        stamp = timestamp(after.get('last_updated'))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not -5 <= (current - stamp).total_seconds() <= 120:
            raise HomeAssistantError()
        context = raw.get('context')
        context_id = context.get('id') if isinstance(context, dict) else None
        if context_id is None:
            context = after.get('context')
            context_id = context.get('id') if isinstance(context, dict) else None
        if context_id is not None and (not isinstance(context_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', context_id)):
            raise HomeAssistantError()
        occurred = stamp.astimezone(timezone.utc).isoformat()
        identity = json.dumps([person, zone, event, occurred, context_id], separators=(',', ':'))
        return {'event_id': hashlib.sha256(identity.encode()).hexdigest(), 'person': person,
                'zone': zone, 'event': event, 'occurred_at': occurred}

    def _queue(self, message: dict[str, Any]) -> bool:
        if message.get('type') != 'event':
            return False
        projected = self._project(message)
        if len(self._pending) >= 128:
            raise HomeAssistantError()
        self._pending.append(projected)
        return True

    def subscribe(self) -> int:
        if self.sock is None or self._rules:
            raise HomeAssistantError()
        try:
            for rule in self.policy.rules():
                if len(self._rules) >= 128:
                    raise HomeAssistantError()
                person, zone, event = rule
                self.deadline = time.monotonic() + TIMEOUT
                self._next_id += 1
                identifier = self._next_id
                self._rules[identifier] = rule
                self._send(encode({'id': identifier, 'type': 'subscribe_trigger', 'trigger': {
                    'trigger': 'zone', 'entity_id': self.policy.person(person)['entity_id'],
                    'zone': self.policy.zone(zone)['entity_id'], 'event': event}}))
                for _ in range(MAX_MESSAGES):
                    message = self._message()
                    if self._queue(message):
                        continue
                    if (message.get('type') != 'result' or type(message.get('id')) is not int or message.get('id') != identifier
                            or message.get('success') is not True):
                        raise HomeAssistantError()
                    break
                else:
                    raise HomeAssistantError()
            return len(self._rules)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._failure()

    def _ping(self) -> None:
        self.deadline = time.monotonic() + TIMEOUT
        self._next_id += 1
        identifier = self._next_id
        self._send(encode({'id': identifier, 'type': 'ping'}))
        for _ in range(MAX_MESSAGES):
            message = self._message()
            if self._queue(message):
                continue
            if message.get('type') != 'pong' or type(message.get('id')) is not int or message.get('id') != identifier:
                raise HomeAssistantError()
            return
        raise HomeAssistantError()

    def next_event(self, timeout_seconds: float = 30) -> dict[str, str] | None:
        if (self.sock is None or not self._rules or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float)) or not 0 <= timeout_seconds <= 60):
            raise HomeAssistantError()
        try:
            if self._pending:
                return self._pending.popleft()
            if not self._buffer and not select.select([self.sock], [], [], timeout_seconds)[0]:
                self._ping()
                return self._pending.popleft() if self._pending else None
            self.deadline = time.monotonic() + TIMEOUT
            message = self._message()
            if not self._queue(message):
                raise HomeAssistantError()
            return self._pending.popleft()
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._failure()

    def send_person(self, row: dict[str, Any]) -> str:
        sent = False
        try:
            task = row['task']
            self.policy.validate_task(task, row.get('scope', 'owner'))
            if task.get('delivery_target') != 'person' or task.get('status') != 'open':
                raise ValueError('Active phone reminder required')
            if not re.fullmatch(r'[a-f0-9]{64}', row['key']):
                raise ValueError('Exact reminder occurrence required')
            title = task['title']
            if not isinstance(title, str) or not 1 <= len(title) <= 160:
                raise ValueError('Bounded reminder title required')
            title = ''.join(char for char in title if not unicodedata.category(char).startswith('C')).replace('@', '@\u200b')
            if not title.strip():
                raise ValueError('Reminder text required')
            message = title
            if task.get('reminder_trigger', '').startswith('zone_'):
                context = row.get('location_context')
                if not isinstance(context, dict):
                    raise ValueError('Location reminder provenance required')
                stamp = timestamp(context.get('occurred_at'))
                if (context.get('person') != task['trigger_person'] or context.get('zone') != task['trigger_zone']
                        or 'zone_' + str(context.get('event')) != task['reminder_trigger']
                        or not -5 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 300):
                    raise ValueError('Current location reminder provenance required')
                verb = 'Ankom til' if context['event'] == 'enter' else 'Forlod'
                message += '\n' + verb + ' ' + self.policy.zone(task['trigger_zone'])['name'] + ' · ' + stamp.isoformat()
            if task.get('trigger_expires_at') and timestamp(task['trigger_expires_at']) <= datetime.now(timezone.utc):
                raise ValueError('Current reminder required')
            body = {'message': message, 'title': 'Påmindelse',
                    'data': {'tag': 'homecompute-reminder-' + row['key']}}
            person = self.policy.person(task['trigger_person'])
            request = urllib.request.Request(f'http://{HOST}:{PORT}/api/services/notify/' + person['notify_service'],
                data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode(), method='POST',
                headers={'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'})
            sent = True
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(65537)
                if response.status != 200 or len(raw) > 65536:
                    raise HomeAssistantError(uncertain=True)
                result = json.loads(raw)
                if not isinstance(result, list):
                    raise HomeAssistantError(uncertain=True)
            return 'ha-service-accepted'
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise HomeAssistantError(uncertain=sent) from None
