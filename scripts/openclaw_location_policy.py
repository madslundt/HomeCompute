"""Operator-owned people, zones and destinations for finite reminder capabilities."""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from openclaw_services import private

CONFIG = Path('/var/lib/homecompute-openclaw/config/location-reminders.json')
CONFIG_PATH = CONFIG
TOKEN_PATH = Path('/var/lib/homecompute-openclaw/config/household-ha-token')
PEOPLE = {'mads': ('person.madslundt', 'mobile_app_mads_iphone'),
          'kristina': ('person.kristinasorensen', 'mobile_app_kristina_iphone')}
ALIAS = re.compile(r'[a-z][a-z0-9_]{0,63}')


def label(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 80 and value == value.strip()
            and not any(unicodedata.category(char).startswith('C') for char in value)
            and not any(char in value for char in '@<>[]') and '://' not in value)


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Explicit reminder timestamp required')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class Policy:
    def __init__(self, path: Path = CONFIG):
        try:
            self._value = self._validate(json.loads(private(path, 16384)))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('Private location reminder policy refused') from None

    @classmethod
    def from_value(cls, value: Any) -> Policy:
        policy = cls.__new__(cls)
        policy._value = cls._validate(value)
        return policy

    @staticmethod
    def _validate(value: Any) -> dict[str, Any]:
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'ha_token_file', 'owner_person', 'persons', 'zones'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['ha_token_file'] != str(TOKEN_PATH) or value['owner_person'] != 'mads'
                or not isinstance(value['persons'], dict) or not 1 <= len(value['persons']) <= 2
                or not {'mads'} <= set(value['persons']) <= set(PEOPLE)
                or not isinstance(value['zones'], dict) or not 1 <= len(value['zones']) <= 32):
            raise ValueError('Finite location reminder policy required')
        for alias, person in value['persons'].items():
            if (not isinstance(person, dict) or set(person) != {'name', 'entity_id', 'notify_service'}
                    or not label(person['name'])
                    or (person['entity_id'], person['notify_service']) != PEOPLE[alias]):
                raise ValueError('Fixed reminder person and phone required')
        for alias, zone in value['zones'].items():
            if (not isinstance(alias, str) or not ALIAS.fullmatch(alias) or not isinstance(zone, dict)
                    or set(zone) != {'name', 'entity_id'} or not label(zone['name'])
                    or zone['entity_id'] != 'zone.' + alias):
                raise ValueError('Finite reminder zone required')
        return copy.deepcopy(value)

    @property
    def value(self) -> dict[str, Any]:
        return copy.deepcopy(self._value)

    @property
    def config(self) -> dict[str, Any]:
        return self.value

    @property
    def persons(self) -> dict[str, Any]:
        return copy.deepcopy(self._value['persons'])

    @property
    def zones(self) -> dict[str, Any]:
        return copy.deepcopy(self._value['zones'])

    def options(self, scope: str) -> dict[str, Any]:
        people = self.allowed_people(scope)
        return {'persons': [{'alias': alias, 'name': self._value['persons'][alias]['name']} for alias in people],
                'zones': [{'alias': alias, 'name': zone['name']} for alias, zone in self._value['zones'].items()],
                'triggers': ['zone_enter', 'zone_leave'], 'trigger_repeat': ['once', 'every_time'],
                'delivery_targets': ['person', 'family'] if scope == 'family' else ['person']}

    def allowed_people(self, scope: str) -> list[str]:
        if not isinstance(scope, str) or scope not in {'owner', 'family'}:
            raise ValueError('Explicit reminder scope required')
        return list(self._value['persons']) if scope == 'family' else [self._value['owner_person']]

    def validate_task(self, task: dict[str, Any], scope: str) -> None:
        allowed = self.allowed_people(scope)
        if not isinstance(task, dict):
            raise ValueError('Finite reminder task required')
        for field in ('reminder_trigger', 'trigger_person', 'trigger_zone', 'delivery_target', 'trigger_repeat', 'trigger_expires_at'):
            if field in task and not isinstance(task[field], str):
                raise ValueError('Finite reminder fields required')
        trigger = task.get('reminder_trigger', 'time' if task.get('remind_at') else 'none')
        target = task.get('delivery_target', 'mattermost')
        person = task.get('trigger_person', '')
        if trigger not in {'none', 'time', 'zone_enter', 'zone_leave'} or target not in {'mattermost', 'person', 'family'}:
            raise ValueError('Finite reminder trigger and destination required')
        if target == 'family' and scope != 'family':
            raise ValueError('Private reminders cannot target Family')
        if task.get('trigger_repeat', 'once') not in {'once', 'every_time'}:
            raise ValueError('Finite reminder repetition required')
        if task.get('trigger_expires_at'):
            timestamp(task['trigger_expires_at'])
        if trigger.startswith('zone_'):
            if (person not in allowed or task.get('trigger_zone') not in self._value['zones']
                    or target not in ({'person', 'family'} if scope == 'family' else {'person'})
                    or task.get('remind_at')):
                raise ValueError('Approved person, zone and location reminder destination required')
        elif target == 'person' and person not in allowed:
            raise ValueError('Approved reminder phone recipient required')

    def rules(self) -> list[tuple[str, str, str]]:
        return [(person, zone, event) for person in self._value['persons']
                for zone in self._value['zones'] for event in ('enter', 'leave')]

    def person(self, alias: str) -> dict[str, str]:
        try:
            return copy.deepcopy(self._value['persons'][alias])
        except (KeyError, TypeError):
            raise ValueError('Approved reminder person required') from None

    def zone(self, alias: str) -> dict[str, str]:
        try:
            return copy.deepcopy(self._value['zones'][alias])
        except (KeyError, TypeError):
            raise ValueError('Approved reminder zone required') from None
