"""Calendar event semantics and single-write/readback, independent of transport."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Europe/Copenhagen')
TIMEZONE = 'Europe/Copenhagen'
FIELDS = ('summary', 'description', 'location', 'start', 'end')


def instant(value: str) -> datetime:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Explicit RFC3339 seconds and offset required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    local = parsed.astimezone(ZONE)
    if not value.endswith('Z') and (parsed.replace(tzinfo=None) != local.replace(tzinfo=None)
                                   or parsed.utcoffset() != local.utcoffset()):
        raise ValueError('Copenhagen local time and offset do not match')
    return local


def times(start: str, end: str, all_day: bool, maximum_days: int = 31) -> dict[str, Any]:
    if all_day:
        if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in (start, end)):
            raise ValueError('All-day ISO dates required; end is exclusive')
        left, right = date.fromisoformat(start), date.fromisoformat(end)
        result = {'start': {'date': start}, 'end': {'date': end}}
    else:
        left, right = instant(start), instant(end)
        result = {'start': {'dateTime': left.isoformat(), 'timeZone': TIMEZONE},
                  'end': {'dateTime': right.isoformat(), 'timeZone': TIMEZONE}}
        # Compare instants across DST, rather than wall-clock subtraction.
        left, right = left.timestamp(), right.timestamp()
        if not 0 < right - left <= maximum_days * 86400:
            raise ValueError('Bounded increasing calendar interval required')
        return result
    if not timedelta(0) < right - left <= timedelta(days=maximum_days):
        raise ValueError('Bounded increasing calendar interval required')
    return result


def busy_interval(period: Any) -> dict[str, str]:
    if not isinstance(period, dict) or set(period) != {'start', 'end'}:
        raise ValueError('Busy interval refused')
    stamps = []
    for key in ('start', 'end'):
        value = period[key]
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('Provider timestamp refused')
        stamps.append(datetime.fromisoformat(value.replace('Z', '+00:00')))
    if not timedelta(0) < stamps[1] - stamps[0] <= timedelta(days=366):
        raise ValueError('Provider busy interval refused')
    return {'start': period['start'], 'end': period['end']}


def writable(event: dict[str, Any]) -> bool:
    validate_event(event)
    private = event.get('extendedProperties', {}).get('private', {})
    return (event.get('status') == 'confirmed' and event.get('eventType', 'default') == 'default'
            and event.get('organizer', {}).get('self') is True and not event.get('attendees')
            and not event.get('attendeesOmitted') and not event.get('recurrence')
            and not event.get('recurringEventId')
            and not any(k in private for k in ('sourceType', 'sourceId', 'sourceKey', 'aulaCalendar')))


def validate_event(event: Any) -> None:
    if not isinstance(event, dict):
        raise ValueError('Calendar event object required')
    for key in ('start', 'end', 'organizer', 'extendedProperties'):
        if key in event and not isinstance(event[key], dict):
            raise ValueError('Calendar event structure refused')
    if not isinstance(event.get('extendedProperties', {}).get('private', {}), dict):
        raise ValueError('Calendar event metadata refused')
    for key in ('attendees', 'recurrence'):
        if key in event and not isinstance(event[key], list):
            raise ValueError('Calendar event collection refused')


def matches(event: dict[str, Any], expected: dict[str, Any]) -> bool:
    validate_event(event)
    for key, value in expected.items():
        actual = event.get(key, '')
        if key not in {'start', 'end'}:
            if actual != value:
                return False
        elif not isinstance(actual, dict):
            return False
        elif 'date' in value:
            if actual.get('date') != value['date'] or 'dateTime' in actual:
                return False
        else:
            # Google may normalize offsets or omit timeZone without changing the instant.
            stamp = actual.get('dateTime')
            try:
                parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                desired = datetime.fromisoformat(value['dateTime'])
                if parsed.tzinfo is None or parsed != desired:
                    return False
            except (ValueError, TypeError, AttributeError):
                return False
    return True


def safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if ord(c) >= 32 or c in '\n\t')[:limit]


def project(event: dict[str, Any], calendar: str) -> dict[str, Any]:
    validate_event(event)
    if not isinstance(event, dict) or not isinstance(event.get('id'), str):
        raise ValueError('Calendar event identity required')
    start, end = event.get('start', {}), event.get('end', {})
    return {'calendar': calendar, 'event_id': safe_text(event['id'], 1024),
            'etag': safe_text(event.get('etag'), 256), 'summary': safe_text(event.get('summary'), 300),
            'start': safe_text(start.get('date', start.get('dateTime')), 40),
            'end': safe_text(end.get('date', end.get('dateTime')), 40),
            'all_day': 'date' in start, 'timezone': TIMEZONE,
            'description': safe_text(event.get('description'), 1200),
            'location': safe_text(event.get('location'), 300), 'writable': writable(event),
            'source_managed': any(k in event.get('extendedProperties', {}).get('private', {})
                                  for k in ('sourceType', 'sourceId', 'sourceKey', 'aulaCalendar'))}


def body(arguments: dict[str, Any]) -> dict[str, Any]:
    result = {k: arguments[k] for k in ('summary', 'description', 'location') if k in arguments}
    if 'start' in arguments:
        result.update(times(arguments['start'], arguments['end'], arguments['all_day']))
    return result


class Events:
    def __init__(self, request: Callable[[str, dict[str, Any]], dict[str, Any]]):
        self.call = request

    def write(self, operation: str, args: dict[str, Any], before_write: Callable[[], None]) -> dict[str, Any]:
        calendar = args['calendar']
        expected = body(args)
        event_id = args.get('event_id', 'hc' + args['request_id'].replace('-', ''))
        prior = self.call('event', {'calendar': calendar, 'event_id': event_id})
        existing = prior.get('event')
        if existing is not None:
            validate_event(existing)
        if operation == 'create':
            if existing is not None:
                if (existing.get('extendedProperties', {}).get('private', {}).get('homecompute_request') != args['request_id']
                        or not matches(existing, expected)):
                    raise ValueError('Calendar event ID already belongs to another event')
                return {'outcome': 'confirmed', 'changed': False, 'event': project(existing, calendar)}
        elif existing is not None and any(k in existing.get('extendedProperties', {}).get('private', {})
                                         for k in ('sourceType', 'sourceId', 'sourceKey', 'aulaCalendar')):
            return {'outcome': 'confirmed', 'changed': False, 'blocked_reason': 'source_managed_event',
                    'guidance': 'This event is maintained by an existing synchronization workflow; no edit was sent.',
                    'event': project(existing, calendar)}
        elif (not isinstance(existing, dict) or not writable(existing)
              or existing.get('etag') != args['expected_etag']):
            raise ValueError('Fresh writable event and exact ETag required')
        before_write()
        receipt = self.call(operation, args)
        if receipt.get('status') not in ({200, 201} if operation == 'create' else {200}):
            raise ValueError('Calendar mutation not confirmed')
        current = self.call('event', {'calendar': calendar, 'event_id': event_id}).get('event')
        if not isinstance(current, dict) or not matches(current, expected):
            raise ValueError('Calendar change readback differs')
        if operation == 'create' and current.get('extendedProperties', {}).get('private', {}).get('homecompute_request') != args['request_id']:
            raise ValueError('Calendar creation marker differs')
        return {'outcome': 'confirmed', 'changed': True, 'event': project(current, calendar)}
