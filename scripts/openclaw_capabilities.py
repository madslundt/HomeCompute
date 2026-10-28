"""Trusted MCP bridge shared by native agents; no model calls or approval authority."""
from __future__ import annotations

from typing import Any
import http.client
import json
import math
import re
import socket

from openclaw_tasks import BROKER, Tasks, request


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
                 services: Any = None, operations_search: Any = None):
        self.tasks, self.infrastructure = tasks, infrastructure
        self.actions = Actions(tasks, actions) if actions else None
        self.home_assistant = home_assistant
        self.maintenance = maintenance
        self.services = services
        self.operations_search = operations_search

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
