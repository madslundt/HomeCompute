"""Trusted MCP bridge shared by native agents; no model calls or approval authority."""
from __future__ import annotations

from typing import Any
import http.client
import json
import math
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

from openclaw_tasks import BROKER, Tasks, request


HERMES_SERVICE_TOOLS = frozenset('homecompute_' + name for name in (
    'web_search', 'research_search', 'shopping_search', 'shopping_stores', 'shopping_offers',
    'school_discover', 'school_calendar', 'school_weekly_letter', 'school_posts',
    'school_threads', 'school_thread', 'school_attachment_read', 'school_post_attachment_read', 'school_search',
    'shopping_list', 'shopping_add', 'shopping_update', 'shopping_complete', 'shopping_remove',
    'discounts_read', 'discount_watch', 'discount_unwatch',
    'storage_list', 'storage_add', 'storage_update', 'storage_remove', 'storage_adjust',
    'household_request_status',
    'household_task_list', 'household_task_create', 'household_task_update',
    'household_task_complete', 'household_task_cancel',
    'family_task_list', 'family_task_create', 'family_task_update',
    'family_task_complete', 'family_task_cancel',
    'calendar_list', 'calendar_events', 'calendar_availability', 'calendar_create',
    'calendar_update', 'calendar_request_status',
))


ANDROID_READ_TOOLS = [
    {"name": "homecompute_android_status", "description": "Check the configured Android Portal connection.", "endpoint": "/ping"},
    {"name": "homecompute_android_screen", "description": "Read the Android accessibility tree; prefer this over screenshots.", "endpoint": "/a11y_tree"},
    {"name": "homecompute_android_apps", "description": "List launchable Android application packages.", "endpoint": "/packages"},
    {"name": "homecompute_android_visible_text", "description": "Read text from the current Android screen state.", "endpoint": "/state"},
    {"name": "homecompute_android_screenshot", "description": "Capture the screen only when accessibility inspection is insufficient.", "endpoint": "/screenshot?hideOverlay=true"},
]


def android_action_tool(name: str, description: str, endpoint: str,
                        properties: dict, required: list[str]) -> dict:
    return {"name": "homecompute_android_" + name, "description": description,
            "endpoint": endpoint, "method": "POST",
            "inputSchema": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": False, "destructiveHint": True,
                            "idempotentHint": False, "openWorldHint": False}}


ANDROID_ACTION_TOOLS = [
    android_action_tool("launch_app", "Launch one installed Android app by package name.", "/app",
        {"package": {"type": "string", "minLength": 3, "maxLength": 128,
                      "pattern": r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"}}, ["package"]),
    android_action_tool("tap", "Tap one point on the tablet screen. Inspect the screen before choosing coordinates.", "/tap",
        {"x": {"type": "integer", "minimum": 0, "maximum": 4095},
         "y": {"type": "integer", "minimum": 0, "maximum": 4095}}, ["x", "y"]),
    android_action_tool("swipe", "Swipe between two screen points. Inspect the screen before choosing coordinates.", "/swipe",
        {"start_x": {"type": "integer", "minimum": 0, "maximum": 4095},
         "start_y": {"type": "integer", "minimum": 0, "maximum": 4095},
         "end_x": {"type": "integer", "minimum": 0, "maximum": 4095},
         "end_y": {"type": "integer", "minimum": 0, "maximum": 4095},
         "duration_ms": {"type": "integer", "minimum": 50, "maximum": 2000}},
        ["start_x", "start_y", "end_x", "end_y"]),
    android_action_tool("type_text", "Type plain text into the focused field. Never use this to send messages or submit forms.", "/keyboard/input",
        {"text": {"type": "string", "minLength": 1, "maxLength": 1000}}, ["text"]),
    android_action_tool("navigation_key", "Navigate using BACK, HOME, or a D-pad direction.", "/keyboard/key",
        {"key": {"type": "string", "enum": ["BACK", "HOME", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]}}, ["key"]),
]
ANDROID_TOOLS = ANDROID_READ_TOOLS + ANDROID_ACTION_TOOLS
ANDROID_ACTION_NAMES = frozenset(row["name"] for row in ANDROID_ACTION_TOOLS)


class AndroidPortal:
    """Fixed, authenticated Mobilerun Portal client with scoped UI controls."""
    def __init__(self):
        path = Path('/etc/homecompute/android-gateway.json')
        self.enabled = path.is_file()
        self.device_id = 'android-tablet'
        self.base_url = ''
        self.token_file = Path('/etc/homecompute/android-portal-token')
        if self.enabled:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError('Android Portal configuration refused')
            if value.get('schema_version') == 1:
                value.pop('schema_version')
            if (not isinstance(value, dict) or set(value) != {'device_id', 'base_url', 'token_file'}
                    or not isinstance(value['base_url'], str) or not value['base_url'].startswith('http://')
                    or any(char in value['base_url'] for char in '?#')
                    or not isinstance(value['device_id'], str) or not value['device_id']
                    or not isinstance(value['token_file'], str) or not value['token_file'].startswith('/')):
                raise ValueError('Android Portal configuration refused')
            self.base_url = value['base_url'].rstrip('/')
            self.device_id = value['device_id']
            self.token_file = Path(value['token_file'])
        self.tools = []
        if self.enabled:
            for row in ANDROID_READ_TOOLS:
                self.tools.append({"name": row["name"], "description": row["description"],
                                   "inputSchema": {"type": "object", "additionalProperties": False,
                                                   "properties": {}, "required": []},
                                   "annotations": {"readOnlyHint": True, "openWorldHint": False}})
            self.tools.extend({key: value for key, value in row.items()
                               if key not in {"endpoint", "method"}} for row in ANDROID_ACTION_TOOLS)

    def validate(self, name: str, arguments: Any) -> dict[str, Any]:
        if not self.enabled or name not in {row['name'] for row in ANDROID_TOOLS} or not isinstance(arguments, dict):
            raise ValueError('Android tool is disabled or arguments are invalid')
        if name not in ANDROID_ACTION_NAMES:
            if arguments:
                raise ValueError('Android read tool arguments are invalid')
            return {}
        schema = next(row['inputSchema'] for row in ANDROID_ACTION_TOOLS if row['name'] == name)
        if set(arguments) - set(schema['properties']) or not set(schema['required']) <= set(arguments):
            raise ValueError('Android interaction arguments are invalid')
        for key, rule in schema['properties'].items():
            if key not in arguments:
                continue
            value = arguments[key]
            if rule['type'] == 'integer':
                if type(value) is not int or not rule['minimum'] <= value <= rule['maximum']:
                    raise ValueError('Android interaction arguments are invalid')
            elif (not isinstance(value, str)
                  or 'minLength' in rule and len(value) < rule['minLength']
                  or 'maxLength' in rule and len(value) > rule['maxLength']
                  or 'enum' in rule and value not in rule['enum']
                  or 'pattern' in rule and not re.fullmatch(rule['pattern'], value)):
                raise ValueError('Android interaction arguments are invalid')
        if name == 'homecompute_android_type_text' and any(ord(char) < 32 or ord(char) == 127 for char in arguments['text']):
            raise ValueError('Android text input must be printable single-line text')
        return arguments

    def request(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.validate(name, arguments)
        row = next(row for row in ANDROID_TOOLS if row['name'] == name)
        endpoint = row['endpoint']
        try:
            token = self.token_file.read_text().strip()
            if not 16 <= len(token) <= 4096 or any(char.isspace() for char in token):
                raise ValueError('Portal credential invalid')
            method = row.get('method', 'GET')
            payload = dict(arguments)
            if name == 'homecompute_android_swipe':
                payload = {'startX': arguments['start_x'], 'startY': arguments['start_y'],
                           'endX': arguments['end_x'], 'endY': arguments['end_y']}
                if 'duration_ms' in arguments:
                    payload['duration'] = arguments['duration_ms']
            elif name == 'homecompute_android_type_text':
                import base64
                payload = {'base64_text': base64.b64encode(arguments['text'].encode()).decode(), 'clear': False}
            elif name == 'homecompute_android_navigation_key':
                payload = {'key_code': {'BACK': 4, 'HOME': 3, 'DPAD_UP': 19, 'DPAD_DOWN': 20,
                                        'DPAD_LEFT': 21, 'DPAD_RIGHT': 22}[arguments['key']]}
            body = json.dumps(payload).encode() if method == 'POST' else None
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(self.base_url + endpoint, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError('Portal response exceeds limit')
            return {'device_id': self.device_id, 'available': True, 'data': json.loads(raw)}
        except (OSError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return {'device_id': self.device_id, 'available': False,
                    'message': 'Android device is unavailable; check Portal networking and authentication.'}


class HermesServices:
    """Owner household grant; future services and account administration stay excluded."""

    def __init__(self, services: Any):
        self.services = services
        self.android = AndroidPortal()
        self.tools = ([tool for tool in services.tools if tool['name'] in HERMES_SERVICE_TOOLS]
                      + self.android.tools)
        self.names = frozenset(tool['name'] for tool in self.tools)

    def validate(self, name: str, arguments: Any) -> dict[str, Any]:
        if name not in self.names:
            raise ValueError('tool outside Hermes household grant')
        if name.startswith('homecompute_android_'):
            return self.android.validate(name, arguments)
        return self.services.validate(name, arguments)

    def request(self, name: str, arguments: Any) -> dict[str, Any]:
        self.validate(name, arguments)
        if name.startswith('homecompute_android_'):
            return self.android.request(name, arguments)
        return self.services.request(name, arguments)


class AndroidOnlyServices:
    """Separate OpenBot principal with only the fixed Android Portal tools."""

    def __init__(self):
        self.android = AndroidPortal()
        self.tools = list(self.android.tools)

    def validate(self, name: str, arguments: Any) -> dict[str, Any]:
        return self.android.validate(name, arguments)

    def request(self, name: str, arguments: Any) -> dict[str, Any]:
        return self.android.request(name, arguments)


class HomeAssistant:
    """Fixed local IPC, authenticated Unix peer and trusted adapter-assigned actor."""
    def __init__(self, actor: str):
        if actor not in {'openclaw', 'hermes'}:
            raise ValueError('unknown native actor')
        self.actor = actor

    @staticmethod
    def validate(value: Any) -> dict[str, Any]:
        from homeassistant_actions import action
        return action(value)

    def request(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in {'propose', 'status', 'cancel'}:
            raise ValueError('HA execution requires the separate operator path')
        if set(arguments) != ({'action'} if operation == 'propose' else {'proposal_id'}):
            raise ValueError('exact actor-free HA arguments required')
        body = {**arguments, 'actor': self.actor}
        connection = http.client.HTTPConnection('localhost', timeout=5)
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(5)
        try:
            stream.connect('/run/homecompute-ha-actions/agent.sock')
            connection.sock = stream
            connection.request('POST', '/' + operation, json.dumps(body), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(16385)
            if response.status != 200 or len(raw) > 16384:
                raise ValueError('HA proposal request refused')
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get('actor') != self.actor
                    or not isinstance(value.get('proposal_id'), str) or not re.fullmatch(r'[a-f0-9]{32}', value['proposal_id'])
                    or value.get('state') not in {'proposed', 'reviewed', 'approved', 'expired', 'executing', 'executed', 'uncertain', 'cancelled'}
                    or type(value.get('created_at')) not in {int, float} or not math.isfinite(value['created_at'])
                    or value.get('approval_expires_at') is not None and
                        (type(value['approval_expires_at']) not in {int, float} or not math.isfinite(value['approval_expires_at']))):
                raise ValueError('invalid HA proposal receipt')
            result = {key: value[key] for key in ('proposal_id', 'actor', 'state', 'created_at', 'approval_expires_at') if key in value}
            result['action'] = self.validate(value.get('action'))
            if value.get('result') is None:
                result['result'] = None
            elif isinstance(value['result'], dict):
                executed = value['result'].get('executed')
                if executed is None or type(executed) is bool:
                    result['result'] = {'executed': executed}
                    if value['result'].get('reason') == 'transport_uncertain_no_retry':
                        result['result']['reason'] = 'transport_uncertain_no_retry'
            return result
        finally:
            connection.close()
            stream.close()


class Actions:
    def __init__(self, tasks: Tasks, allowed: dict[str, str]):
        self.tasks, self.allowed = tasks, dict(allowed)

    def admitted(self, row: dict[str, Any]) -> bool:
        return (isinstance(row, dict) and row.get("action_id") in self.allowed
                and self.allowed[row["action_id"]] == row.get("target"))

    def get(self, task_id: str) -> dict[str, Any]:
        row = request(BROKER, "/actions/" + task_id, self.tasks.tokens["snapshot"])
        if not self.admitted(row):
            raise ValueError("action outside reviewed adapter policy")
        return row

    def list_actions(self) -> list[dict[str, Any]]:
        rows = request(BROKER, "/actions", self.tasks.tokens["snapshot"])
        if not isinstance(rows, list):
            raise ValueError("invalid broker action list")
        return [row for row in rows if self.admitted(row)][:100]

    def propose(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.admitted(body):
            raise ValueError("action outside reviewed adapter policy")
        row = request(BROKER, "/actions", self.tasks.tokens["assistant"], body)
        if not self.admitted(row):
            raise ValueError("action receipt outside reviewed adapter policy")
        return row

    def cancel(self, task_id: str) -> dict[str, Any]:
        self.get(task_id)
        row = request(BROKER, "/actions/" + task_id + "/cancel", self.tasks.tokens["assistant"], {})
        if not self.admitted(row):
            raise ValueError("action receipt outside reviewed adapter policy")
        return row


class Maintenance:
    """Opaque plan IDs only; root owns plans, classification and approvals."""

    def request(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in {'list', 'status', 'run', 'prepare', 'preparation'}:
            raise ValueError('maintenance operation unavailable to agent')
        expected = set() if operation in {'list', 'preparation'} else {'target', 'component'} if operation == 'prepare' else {'plan_id'}
        if set(arguments) != expected:
            raise ValueError('exact maintenance arguments required')
        if operation == 'prepare' and (arguments['target'] not in {'home-core', 'home-spark', 'home-assistant'}
                or not isinstance(arguments['component'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}', arguments['component'])):
            raise ValueError('finite maintenance selection required')
        if operation in {'status', 'run'} and (not isinstance(arguments['plan_id'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', arguments['plan_id'])):
            raise ValueError('invalid maintenance plan ID')
        connection = http.client.HTTPConnection('localhost', timeout=10)
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stream.settimeout(10)
        try:
            stream.connect('/run/homecompute-infrastructure-maintenance/agent.sock')
            connection.sock = stream
            connection.request('POST', '/' + operation, json.dumps(arguments), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ValueError('maintenance request refused; operator review may be required')
            value = json.loads(raw)
            if not isinstance(value, (dict, list)):
                raise ValueError('invalid maintenance receipt')
            return {'plans': value} if isinstance(value, list) else {'preparation' if operation in {'prepare', 'preparation'} else 'plan': value}
        finally:
            connection.close()
            stream.close()


class Capabilities:
    """Protocol-compatible finite task ledger backed by existing fixed-origin transport."""

    def __init__(self, tasks: Tasks, infrastructure: Any = None, actions: dict[str, str] | None = None,
                 home_assistant: HomeAssistant | None = None, maintenance: Maintenance | None = None,
                 services: Any = None, operations_search: Any = None, read_only_only: bool = False):
        self.tasks, self.infrastructure = tasks, infrastructure
        self.actions = Actions(tasks, actions) if actions else None
        self.home_assistant = home_assistant
        self.maintenance = maintenance
        self.services = services
        self.operations_search = operations_search
        self.read_only_only = read_only_only

    def admitted(self, row: dict[str, Any]) -> bool:
        return isinstance(row, dict) and row.get("project") in self.tasks.projects

    def get(self, task_id: str) -> dict[str, Any]:
        row = request(BROKER, "/tasks/" + task_id, self.tasks.tokens["snapshot"])
        if not self.admitted(row):
            raise ValueError("task outside reviewed project policy")
        return row

    def list_tasks(self) -> list[dict[str, Any]]:
        rows = request(BROKER, "/tasks", self.tasks.tokens["snapshot"])
        if not isinstance(rows, list):
            raise ValueError("invalid broker task list")
        return [row for row in rows if self.admitted(row)][:100]

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.admitted(body):
            raise ValueError("task outside reviewed project policy")
        row = request(BROKER, "/tasks", self.tasks.tokens["assistant"], body)
        if not self.admitted(row):
            raise ValueError("task receipt outside reviewed project policy")
        return row

    def action(self, task_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if action != "cancel" or body:
            raise ValueError("native agents cannot approve, publish or execute")
        self.get(task_id)
        row = request(BROKER, "/tasks/" + task_id + "/cancel", self.tasks.tokens["assistant"], {})
        if not self.admitted(row):
            raise ValueError("task receipt outside reviewed project policy")
        return row
