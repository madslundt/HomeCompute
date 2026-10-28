"""Publish only shared tasks to the fixed Home Assistant briefing sensor."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import urllib.request

from openclaw_household import TOKEN_PATH
from openclaw_household_tasks import FamilyTasks
from openclaw_services import NoRedirect, private

URL = 'http://192.168.30.30/api/states/sensor.homecompute_family_tasks'
FIELDS = ('task_id', 'revision', 'title', 'assignee', 'due_at', 'remind_at', 'recurrence', 'status')


def payload(tasks: FamilyTasks, now: datetime) -> dict[str, Any]:
    if not isinstance(tasks, FamilyTasks) or tasks.source != 'shared-family-tasks':
        raise ValueError('Only shared Family tasks may be projected')
    selected = []
    offset = 0
    while True:
        result = tasks.request('homecompute_family_task_list', {'status': 'open', 'offset': offset})
        for row in result['tasks']:
            candidate = {key: row[key] for key in FIELDS}
            if len(selected) >= 100 or len(json.dumps(selected + [candidate]).encode()) > 32000:
                break
            selected.append(candidate)
        following = result['next_offset']
        if following is None or len(selected) >= 100 or len(selected) < offset + len(result['tasks']):
            break
        offset = following
    return {'state': str(result['total']), 'attributes': {
        'friendly_name': 'Fælles familieopgaver', 'icon': 'mdi:clipboard-check-outline',
        'source': 'shared-family-tasks', 'updated_at': now.astimezone(timezone.utc).isoformat(),
        'tasks': selected, 'total': result['total'], 'truncated': len(selected) < result['total']}}


def publish(tasks: FamilyTasks, now: datetime) -> None:
    body = payload(tasks, now)
    token = private(TOKEN_PATH, 4096).decode().strip()
    if not 32 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('Private household authentication required')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(URL, data=json.dumps(body, allow_nan=False).encode(), method='POST',
                                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with opener.open(request, timeout=8) as response:
        raw = response.read(131073)
        if response.status not in (200, 201) or len(raw) > 131072:
            raise ValueError('Family task projection unconfirmed')
        saved = json.loads(raw)
    if (saved.get('entity_id') != 'sensor.homecompute_family_tasks'
            or saved.get('state') != body['state'] or saved.get('attributes') != body['attributes']):
        raise ValueError('Family task projection receipt differs')
