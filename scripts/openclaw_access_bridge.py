"""Finite account-access requests. Vault and operator credentials stay elsewhere.

The authenticated bridge principal is OpenClaw, shared by native specialists.
Native policy grants these tools to travel and shopper; it is not a per-specialist
cryptographic identity. Site execution requires a separately configured trusted backend.
"""
from __future__ import annotations

import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from openclaw_services import private
from openclaw_site_registry import HOST_PATTERN, SCOPES, account_name, parameter_envelope

ENDPOINT = 'http://127.0.0.1:18812'
TOKEN_PATH = Path('/var/lib/homecompute-openclaw/config/access-request-token')
PRINCIPAL = 'openclaw'
NAMES = frozenset({'homecompute_access_request', 'homecompute_access_status'})
SITE_ENDPOINT = 'http://127.0.0.1:18816'
SITE_TOKEN_PATH = Path('/var/lib/homecompute-openclaw/config/site-operation-token')
SITE_NAMES = frozenset({'homecompute_site_run', 'homecompute_site_status'})
SITE_STATES = frozenset({'idle', 'running', 'completed', 'human_sign_in_required', 'expired', 'revoked', 'unavailable'})


def tool(name: str, description: str, properties: dict, required: list[str], readonly: bool, *, idempotent: bool = True) -> dict:
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'additionalProperties': False,
                            'properties': properties, 'required': required},
            'annotations': {'readOnlyHint': readonly, 'destructiveHint': False,
                            'idempotentHint': idempotent, 'openWorldHint': False}}


TOOLS = [
    tool('homecompute_access_request',
         'Request owner approval to use an operator-registered website for a bounded task and duration. '
         'Reuse request_key after an uncertain response. Read notification_delivery before claiming a notification was sent. '
         'Check account_execution_available; this describes the reviewed adapter, not a signed-in account. '
         'A private owner sign-in step may be required. An access approval never authorizes bookings or payments.',
         {'account': {'type': 'string', 'minLength': 3, 'maxLength': 253, 'pattern': '^' + HOST_PATTERN + '$'},
          'scope': {'type': 'string', 'enum': sorted(SCOPES)},
          'minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440},
          'request_key': {'type': 'string', 'minLength': 16, 'maxLength': 128,
                          'pattern': '^[A-Za-z0-9_-]{16,128}$'}},
         ['account', 'scope', 'minutes', 'request_key'], False),
    tool('homecompute_access_status',
         'Read a known OpenClaw account-access request. Approval, revocation, vault values and browser sessions '
         'are unavailable to this tool. An active grant alone does not mean the owner has signed in.',
         {'request_id': {'type': 'string', 'minLength': 36, 'maxLength': 36,
                         'pattern': '^[0-9a-f-]{36}$'}}, ['request_id'], True),
]


def reference(value: Any) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise ValueError('Invalid account-access reference') from None
    return value


def validate(operation: str, arguments: Any) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError('Invalid account-access arguments')
    if operation == 'homecompute_access_request':
        if (set(arguments) != {'account', 'scope', 'minutes', 'request_key'}
                or not isinstance(arguments['scope'], str)
                or arguments['scope'] not in SCOPES
                or type(arguments['minutes']) is not int or not 1 <= arguments['minutes'] <= 1440
                or not isinstance(arguments['request_key'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', arguments['request_key'])):
            raise ValueError('Invalid account-access request')
        account_name(arguments['account'])
    elif operation == 'homecompute_access_status':
        if set(arguments) != {'request_id'}:
            raise ValueError('Invalid account-access status arguments')
        reference(arguments['request_id'])
    else:
        raise ValueError('Account-access operation is unavailable')
    return copy.deepcopy(arguments)


def project(value: Any) -> dict:
    required = {'request_id', 'actor', 'account', 'scope', 'requested_minutes', 'requested_at', 'request_digest',
                'status', 'approved_at', 'expires_at', 'account_execution_available', 'transaction_execution_available'}
    if not isinstance(value, dict) or not required <= set(value):
        raise ValueError('Invalid account-access receipt')
    reference(value['request_id'])
    account_name(value['account'])
    if (value['actor'] != PRINCIPAL
            or not isinstance(value['scope'], str) or value['scope'] not in SCOPES
            or value['status'] not in {'pending', 'active', 'expired', 'denied', 'revoked'}
            or type(value['requested_minutes']) is not int or not 1 <= value['requested_minutes'] <= 1440
            or not isinstance(value['request_digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['request_digest'])
            or type(value['account_execution_available']) is not bool or value['transaction_execution_available'] is not False):
        raise ValueError('Invalid account-access receipt')
    for key in ('requested_at', 'approved_at', 'expires_at'):
        if value[key] is None and key != 'requested_at':
            continue
        if type(value[key]) is not int or not 0 <= value[key] <= 9999999999:
            raise ValueError('Invalid account-access timestamp')
    if value['status'] == 'active' and (value['approved_at'] is None or value['expires_at'] is None
                                      or value['expires_at'] <= value['approved_at']):
        raise ValueError('Invalid account-access expiry')
    result = {key: value[key] for key in required}
    if 'notification_delivery' in value:
        if value['notification_delivery'] not in {'not_configured', 'submitted', 'delivered', 'delivery_uncertain', 'not_replayed'}:
            raise ValueError('Invalid notification receipt')
        result['notification_delivery'] = value['notification_delivery']
    return result


class AccessBridge:
    def __init__(self, config: Path):
        try:
            cfg = json.loads(private(config, 4096))
            if (not isinstance(cfg, dict) or set(cfg) != {'schema_version', 'principal', 'endpoint', 'token_file'}
                    or type(cfg['schema_version']) is not int or cfg['schema_version'] != 1
                    or cfg['principal'] != PRINCIPAL or cfg['endpoint'] != ENDPOINT
                    or cfg['token_file'] != str(TOKEN_PATH)):
                raise ValueError('Invalid account-access configuration')
            token = private(TOKEN_PATH, 1024).decode().strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', token):
                raise ValueError('Invalid account-access authentication')
        except Exception:
            raise ValueError('Private account-access configuration refused') from None
        self._token = token
        self.tools = copy.deepcopy(TOOLS)

    validate = staticmethod(validate)

    def request(self, operation: str, arguments: Any) -> dict:
        args = validate(operation, arguments)
        path = '/access/request' if operation == 'homecompute_access_request' else '/access/' + args['request_id']
        connection = HTTPConnection('127.0.0.1', 18812, timeout=10)
        try:
            headers = {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'}
            connection.request('POST' if operation == 'homecompute_access_request' else 'GET', path,
                               json.dumps(args).encode() if operation == 'homecompute_access_request' else None, headers)
            response = connection.getresponse()
            raw = response.read(8193)
            if response.status != 200 or len(raw) > 8192:
                raise ValueError('Account-access receipt unavailable')
            result = project(json.loads(raw))
            if operation == 'homecompute_access_status' and result['request_id'] != args['request_id']:
                raise ValueError('Account-access reference differs')
            if operation == 'homecompute_access_request':
                if (any(result[key] != args[field] for key, field in
                        [('account', 'account'), ('scope', 'scope'), ('requested_minutes', 'minutes')])
                        or 'notification_delivery' not in result):
                    raise ValueError('Account-access request receipt differs')
            return result
        except Exception:
            raise ValueError('Account-access result unavailable; use the same request key to reconcile an uncertain request') from None
        finally:
            connection.close()


SITE_TOOLS = [
    tool('homecompute_site_run',
         'Run one registered search, read or preparation operation using an approved request reference. '
         'The trusted backend selects the site, URL, selectors and credentials. No booking or payment is executed. '
         'If human_sign_in_required, the owner must complete private sign-in outside this tool; do not request '
         'credentials or verification codes in chat. A ready private owner session can be used only within its grant. '
         'Results contain operation state and a bounded count only; no page text, URLs, images, credentials or cookies.',
         {'request_id': {'type': 'string', 'minLength': 36, 'maxLength': 36, 'pattern': '^[0-9a-f-]{36}$'},
          'operation': {'type': 'string', 'enum': ['search', 'prepare', 'read']},
          'parameters': {'type': 'object', 'maxProperties': 16,
                         'propertyNames': {'pattern': '^[a-z][a-z0-9_]{0,31}$'},
                         'additionalProperties': {'anyOf': [{'type': 'string', 'maxLength': 512},
                                                           {'type': 'integer', 'minimum': -100000, 'maximum': 100000},
                                                           {'type': 'boolean'}]}}},
         ['request_id', 'operation', 'parameters'], False, idempotent=False),
    tool('homecompute_site_status',
         'Read the latest controlled site operation for an approved request reference. '
         'human_sign_in_required means the owner must use the private sign-in page. '
         'Only state, account identifier and bounded count are returned; no sign-in URLs, images, credentials or browser state.',
         {'request_id': {'type': 'string', 'minLength': 36, 'maxLength': 36, 'pattern': '^[0-9a-f-]{36}$'}},
         ['request_id'], True),
]


def validate_site(operation: str, arguments: Any) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError('Invalid site-operation arguments')
    if operation == 'homecompute_site_run':
        if (set(arguments) != {'request_id', 'operation', 'parameters'}
                or not isinstance(arguments['operation'], str) or arguments['operation'] not in {'search', 'prepare', 'read'}):
            raise ValueError('Invalid site operation')
        parameter_envelope(arguments['parameters'])
    elif operation != 'homecompute_site_status' or set(arguments) != {'request_id'}:
        raise ValueError('Site operation is unavailable')
    reference(arguments['request_id'])
    return copy.deepcopy(arguments)


def project_site(value: Any) -> dict:
    fields = {'request_id', 'operation_id', 'account', 'operation', 'status', 'results_count',
              'account_execution_available', 'transaction_execution_available'}
    if not isinstance(value, dict) or not fields <= set(value):
        raise ValueError('Invalid site-operation receipt')
    reference(value['request_id'])
    account_name(value['account'])
    if value['operation_id'] is not None:
        reference(value['operation_id'])
    if (value['operation'] is not None and (not isinstance(value['operation'], str) or value['operation'] not in {'search', 'prepare', 'read'})
            or not isinstance(value['status'], str) or value['status'] not in SITE_STATES
            or value['results_count'] is not None and (type(value['results_count']) is not int or not 0 <= value['results_count'] <= 100)
            or type(value['account_execution_available']) is not bool or value['transaction_execution_available'] is not False
            or value['status'] in {'running', 'completed', 'human_sign_in_required'}
            and (value['operation_id'] is None or value['operation'] is None)):
        raise ValueError('Invalid site-operation receipt')
    return {name: value[name] for name in fields}


class SiteBridge:
    def __init__(self, config: Path):
        try:
            cfg = json.loads(private(config, 4096))
            if (not isinstance(cfg, dict) or set(cfg) != {'schema_version', 'principal', 'endpoint', 'token_file'}
                    or type(cfg['schema_version']) is not int or cfg['schema_version'] != 1
                    or cfg['principal'] != PRINCIPAL or cfg['endpoint'] != SITE_ENDPOINT
                    or cfg['token_file'] != str(SITE_TOKEN_PATH)):
                raise ValueError('Invalid site-operation configuration')
            token = private(SITE_TOKEN_PATH, 1024).decode().strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', token):
                raise ValueError('Invalid site-operation authentication')
        except Exception:
            raise ValueError('Private site-operation configuration refused') from None
        self._token, self.tools = token, copy.deepcopy(SITE_TOOLS)

    validate = staticmethod(validate_site)

    def request(self, operation: str, arguments: Any) -> dict:
        args = validate_site(operation, arguments)
        path = '/site/run' if operation == 'homecompute_site_run' else '/site/' + args['request_id']
        connection = HTTPConnection('127.0.0.1', 18816, timeout=10)
        try:
            connection.request('POST' if operation == 'homecompute_site_run' else 'GET', path,
                               json.dumps(args).encode() if operation == 'homecompute_site_run' else None,
                               {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(8193)
            if response.status != 200 or len(raw) > 8192:
                raise ValueError('Site-operation receipt unavailable')
            result = project_site(json.loads(raw))
            if (result['request_id'] != args['request_id'] or operation == 'homecompute_site_run'
                    and result['operation'] != args['operation']):
                raise ValueError('Site-operation reference differs')
            return result
        except Exception:
            raise ValueError('Site-operation result unavailable; inspect site status before requesting another operation') from None
        finally:
            connection.close()


class CompositeServices:
    """Compose the finite request tools without widening the existing facade."""
    def __init__(self, existing: Any, access: AccessBridge, site: SiteBridge | None = None):
        self.existing, self.access, self.site = existing, access, site
        self.tools = ((copy.deepcopy(existing.tools) if existing is not None else []) + copy.deepcopy(access.tools)
                      + (copy.deepcopy(site.tools) if site is not None else []))

    def validate(self, operation: str, arguments: Any) -> dict:
        if operation in SITE_NAMES:
            if self.site is None:
                raise ValueError('Site operations are not configured')
            return self.site.validate(operation, arguments)
        if operation in NAMES:
            return self.access.validate(operation, arguments)
        if self.existing is None:
            raise ValueError('Service operation unavailable')
        return self.existing.validate(operation, arguments)

    def request(self, operation: str, arguments: Any) -> dict:
        if operation in SITE_NAMES:
            if self.site is None:
                raise ValueError('Site operations are not configured')
            return self.site.request(operation, arguments)
        if operation in NAMES:
            return self.access.request(operation, arguments)
        if self.existing is None:
            raise ValueError('Service operation unavailable')
        return self.existing.request(operation, arguments)
