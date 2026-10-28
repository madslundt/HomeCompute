"""Closed location-event admission and durable reminder occurrence rules."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

LOCATION_FIELDS = {
    'reminder_trigger': {'type': 'string', 'minLength': 1, 'maxLength': 10,
                         'enum': ['none', 'time', 'zone_enter', 'zone_leave']},
    'trigger_person': {'type': 'string', 'minLength': 0, 'maxLength': 64, 'pattern': r'(?:[a-z][a-z0-9_-]{0,63})?'},
    'trigger_zone': {'type': 'string', 'minLength': 0, 'maxLength': 64, 'pattern': r'(?:[a-z][a-z0-9_-]{0,63})?'},
    'delivery_target': {'type': 'string', 'minLength': 1, 'maxLength': 10, 'enum': ['mattermost', 'person', 'family']},
    'trigger_repeat': {'type': 'string', 'minLength': 1, 'maxLength': 10, 'enum': ['once', 'every_time']},
    'trigger_expires_at': {'type': 'string', 'minLength': 0, 'maxLength': 35},
}
IDENTITY_FIELDS = tuple(LOCATION_FIELDS) + ('remind_at',)
LOCATION_TTL_SECONDS = 300


def stamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Explicit location reminder timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    if not 2000 <= result.year <= 2100:
        raise ValueError('Location reminder timestamp outside supported range')
    return result


def location(task: dict[str, Any]) -> bool:
    return task.get('reminder_trigger') in {'zone_enter', 'zone_leave'}


def defaults(task: dict[str, Any]) -> dict[str, Any]:
    return {**{'reminder_trigger': 'time' if task.get('remind_at') else 'none', 'trigger_person': '',
               'trigger_zone': '', 'delivery_target': 'mattermost', 'trigger_repeat': 'once',
               'trigger_expires_at': '', 'trigger_generation': 0, 'trigger_armed_at': task.get('created_at', '')}, **task}


def configure(task: dict[str, Any], args: dict[str, Any], previous: dict[str, Any], now: datetime,
              policy: Any, scope: str) -> None:
    previous = defaults(previous)
    for key, value in defaults(task).items():
        task.setdefault(key, value)
    if 'remind_at' in args and 'reminder_trigger' not in args:
        task['reminder_trigger'] = 'time' if task['remind_at'] else 'none'
    if task['reminder_trigger'] == 'none':
        if args.get('remind_at'):
            raise ValueError('Disabled reminders cannot also request a reminder time')
        task['remind_at'] = ''
    elif task['reminder_trigger'] == 'time' and not task['remind_at']:
        raise ValueError('Timed reminders require an explicit remind_at')
    if location(task):
        if not location(previous) and 'delivery_target' not in args:
            task['delivery_target'] = 'person'
        if task['remind_at'] or task['recurrence'] != 'none':
            raise ValueError('Zone reminders cannot combine timed reminders or calendar recurrence')
    else:
        if args.get('trigger_zone'):
            raise ValueError('A zone requires a zone reminder trigger')
        task['trigger_zone'] = ''
        if 'trigger_repeat' not in args:
            task['trigger_repeat'] = 'once'
        if task['trigger_repeat'] != 'once':
            raise ValueError('Repeated zone triggers require a zone reminder')
    if task['trigger_expires_at']:
        task['trigger_expires_at'] = stamp(task['trigger_expires_at']).isoformat()
    if task['reminder_trigger'] != 'none' and (location(task) or task['delivery_target'] != 'mattermost'):
        if policy is None:
            raise ValueError('Location and phone reminder delivery require configured trusted options')
        policy.validate_task(task, scope)
    changed = any(task.get(key) != previous.get(key) for key in IDENTITY_FIELDS)
    if changed:
        if task['trigger_expires_at'] and stamp(task['trigger_expires_at']) <= now:
            raise ValueError('New reminder expiry must be in the future')
        task['trigger_generation'] = previous['trigger_generation'] + 1
        task['trigger_armed_at'] = now.isoformat()


def initialize(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute('PRAGMA table_info(household_task_reminders)')}
    if 'location_context' not in columns:
        db.execute('ALTER TABLE household_task_reminders ADD COLUMN location_context TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS household_location_events '
               '(event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, occurred REAL NOT NULL)')


def once_key(task: dict[str, Any]) -> str:
    return hashlib.sha256(f"{task['task_id']}:zone:{task.get('trigger_generation', 0)}".encode()).hexdigest()


def eligible(row: dict[str, Any], task: dict[str, Any], now: datetime) -> bool:
    if task['status'] != 'open':
        return False
    expiry = task.get('trigger_expires_at')
    if expiry and stamp(expiry) <= now:
        return False
    context = row.get('location_context')
    if context:
        context = json.loads(context) if isinstance(context, str) else context
        return (location(task) and context.get('generation') == task.get('trigger_generation')
                and context.get('person') == task.get('trigger_person')
                and context.get('zone') == task.get('trigger_zone')
                and 'zone_' + str(context.get('event')) == task.get('reminder_trigger')
                and (now - stamp(context['occurred_at'])).total_seconds() <= LOCATION_TTL_SECONDS)
    if location(task) or not task['remind_at']:
        return False
    key = hashlib.sha256(f"{task['task_id']}:{task['remind_at']}".encode()).hexdigest()
    return row['key'] == key


def synchronize(db: sqlite3.Connection, task: dict[str, Any], now: datetime) -> None:
    for record in db.execute('SELECT * FROM household_task_reminders WHERE task_id=?', (task['task_id'],)).fetchall():
        row = dict(record)
        current = eligible(row, task, now)
        if row['state'] == 'pending' and not current:
            db.execute("UPDATE household_task_reminders SET state='skipped',updated=? WHERE key=? AND state='pending'",
                       (now.timestamp(), row['key']))
        context = json.loads(row['location_context']) if row.get('location_context') else None
        same = (context.get('generation') == task.get('trigger_generation') if context else
                row['key'] == hashlib.sha256(f"{task['task_id']}:{task['remind_at']}".encode()).hexdigest())
        if same:
            db.execute('UPDATE household_task_reminders SET revision=? WHERE key=?', (task['revision'], row['key']))


def accept(db: sqlite3.Connection, event: Any, now: datetime, policy: Any, scope: str) -> dict[str, Any]:
    if (not isinstance(event, dict) or set(event) != {'event_id', 'person', 'zone', 'event', 'occurred_at'}
            or not isinstance(event['event_id'], str) or not re.fullmatch('[a-f0-9]{64}', event['event_id'])
            or any(not isinstance(event[key], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', event[key])
                   for key in ('person', 'zone')) or not isinstance(event['event'], str)
            or event['event'] not in {'enter', 'leave'}):
        raise ValueError('Closed trusted location event required')
    occurred = stamp(event['occurred_at'])
    catalog = policy.options('family')
    if (event['person'] not in {row['alias'] for row in catalog['persons']}
            or event['zone'] not in {row['alias'] for row in catalog['zones']}):
        raise ValueError('Location event outside configured aliases')
    if not -5 <= (now - occurred).total_seconds() <= 120:
        raise ValueError('Location event is stale or future dated')
    fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    prior = db.execute('SELECT fingerprint FROM household_location_events WHERE event_id=?', (event['event_id'],)).fetchone()
    if prior:
        if prior['fingerprint'] != fingerprint:
            raise ValueError('Location event identity reused with different content')
        return {'matched': 0, 'duplicate': True}
    if db.execute('SELECT count(*) FROM household_location_events').fetchone()[0] >= 100000:
        raise ValueError('Location event capacity requires operator review')
    db.execute('INSERT INTO household_location_events VALUES(?,?,?)', (event['event_id'], fingerprint, occurred.timestamp()))
    matched = 0
    for record in db.execute('SELECT body FROM household_tasks').fetchall():
        task = json.loads(record['body'])
        if (task['status'] != 'open' or task.get('reminder_trigger') != 'zone_' + event['event']
                or task.get('trigger_person') != event['person'] or task.get('trigger_zone') != event['zone']
                or occurred <= stamp(task['trigger_armed_at'])
                or task.get('trigger_expires_at') and stamp(task['trigger_expires_at']) <= now):
            continue
        try:
            policy.validate_task(task, scope)
        except ValueError:
            continue
        synchronize(db, task, now)
        prior_rows = db.execute('SELECT * FROM household_task_reminders WHERE task_id=? AND '
                                "json_extract(location_context,'$.generation')=? ORDER BY created DESC",
                                (task['task_id'], task['trigger_generation'])).fetchall()
        if task.get('trigger_repeat', 'once') == 'once':
            if any(row['state'] not in {'skipped', 'expired'} for row in prior_rows):
                continue
            key = once_key(task)
        else:
            if prior_rows and occurred.timestamp() - prior_rows[0]['created'] < 300:
                continue
            key = hashlib.sha256((once_key(task) + ':' + event['event_id']).encode()).hexdigest()
        context = {**event, 'generation': task['trigger_generation']}
        db.execute('INSERT INTO household_task_reminders '
                   '(key,task_id,revision,state,created,updated,post_id,location_context) VALUES(?,?,?,?,?,?,NULL,?) '
                   'ON CONFLICT(key) DO UPDATE SET state=excluded.state,revision=excluded.revision,'
                   'created=excluded.created,updated=excluded.updated,post_id=NULL,location_context=excluded.location_context',
                   (key, task['task_id'], task['revision'], 'pending', occurred.timestamp(), now.timestamp(), json.dumps(context)))
        matched += 1
    return {'matched': matched, 'duplicate': False}
