"""Direct, bounded, read-only Outlook access with private Microsoft OAuth tokens."""
from __future__ import annotations

import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

SCOPES = 'openid offline_access https://graph.microsoft.com/Mail.Read'
GRAPH = 'https://graph.microsoft.com/v1.0/me/messages'
ACCOUNT = 'outlook_mads'
IDENTITY = re.compile(r'[A-Za-z0-9_+=/-]{1,1024}')
MAX_RESPONSE = 262144


def private_path(path: Path) -> None:
    """Require an absolute private directory without writable/symlink ancestors."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Private Outlook path required')
    for directory in reversed(path.parents):
        info = directory.lstat()
        sticky_system = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) & 0o022 and not sticky_system):
            raise ValueError('Private Outlook path required')
    parent = path.parent.lstat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError('Private Outlook directory required')


def private_json(path: Path) -> dict[str, Any]:
    private_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_RESPONSE):
            raise ValueError('Private Outlook state required')
        raw = os.read(descriptor, MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('Private Outlook state exceeds limit')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Private Outlook state required')
        return result
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Write owner-only state atomically, without exposing its content in errors."""
    private_path(path)
    if path.exists() or path.is_symlink():
        private_json(path)
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(12))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _secret(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 65536
            and not any(character.isspace() or ord(character) < 32 for character in value))


def _scope(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError('Read-only Outlook permission required')
    scopes = {part.removeprefix('https://graph.microsoft.com/') for part in value.split()}
    if 'Mail.Read' not in scopes or scopes - {'Mail.Read', 'openid', 'offline_access', 'email', 'profile'}:
        raise ValueError('Read-only Outlook permission required')
    return value


def validate_token_response(response: Any, previous_refresh: str | None = None) -> dict[str, Any]:
    """Accept only delegated Mail.Read grants from the trusted token endpoint."""
    if (not isinstance(response, dict) or not isinstance(response.get('token_type'), str)
            or response['token_type'].lower() != 'bearer'):
        raise ValueError('Outlook authorization refused')
    access = response.get('access_token')
    refresh = response.get('refresh_token', previous_refresh)
    lifetime = response.get('expires_in')
    if (not _secret(access) or not _secret(refresh) or type(lifetime) is not int
            or not 1 <= lifetime <= 86400):
        raise ValueError('Outlook authorization refused')
    return {'access_token': access, 'refresh_token': refresh,
            'expires_at': time.time() + lifetime, 'scope': _scope(response.get('scope'))}


def _token(value: dict[str, Any]) -> dict[str, Any]:
    if (set(value) != {'access_token', 'refresh_token', 'expires_at', 'scope'}
            or not _secret(value.get('access_token')) or not _secret(value.get('refresh_token'))
            or type(value.get('expires_at')) not in {int, float}
            or not math.isfinite(value['expires_at']) or value['expires_at'] < 0
            or value['expires_at'] > time.time() + 86400):
        raise ValueError('Private Outlook authorization refused')
    _scope(value['scope'])
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(request: urllib.request.Request) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE:
            raise ValueError('Outlook response refused')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Outlook response refused')
        return result


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ''
    return ''.join(character for character in value if ord(character) >= 32 or character in '\n\t')[:limit]


def _arguments(operation: str, arguments: Any) -> dict[str, str]:
    key = {'search': 'query', 'thread': 'thread_id'}.get(operation) if isinstance(operation, str) else None
    if (key is None or not isinstance(arguments, dict) or set(arguments) != {'account', key}
            or arguments['account'] != ACCOUNT):
        raise ValueError('Read-only owner Outlook request required')
    value = arguments[key]
    if (not isinstance(value, str) or not 1 <= len(value) <= (200 if operation == 'search' else 1024)
            or value != value.strip() or any(ord(character) < 32 for character in value)
            or operation == 'thread' and not IDENTITY.fullmatch(value)):
        raise ValueError('Read-only owner Outlook request required')
    return arguments.copy()


def _project(raw: dict[str, Any], operation: str, arguments: dict[str, str]) -> dict[str, Any]:
    rows = raw.get('value')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Outlook response refused')
    for row in rows:
        identifier = row.get('conversationId' if operation == 'search' else 'id')
        if not isinstance(identifier, str) or not IDENTITY.fullmatch(identifier):
            raise ValueError('Outlook response refused')
        if operation == 'thread' and row.get('conversationId') != arguments['thread_id']:
            raise ValueError('Outlook conversation evidence differs')
    result = {'ok': True, 'account': ACCOUNT, 'truncated': len(rows) > 10 or bool(raw.get('@odata.nextLink'))}
    field = 'threads' if operation == 'search' else 'messages'
    projected, seen = [], set()
    for row in rows[:10]:
        identifier = row.get('conversationId' if operation == 'search' else 'id')
        if operation == 'search' and identifier in seen:
            continue
        seen.add(identifier)
        sender = row.get('from') or {}
        sender = sender.get('emailAddress') if isinstance(sender, dict) else None
        item = {'thread_id' if operation == 'search' else 'id': identifier,
                'subject': _text(row.get('subject'), 200),
                'sender': _text(sender.get('address') if isinstance(sender, dict) else None, 300),
                'received_at': _text(row.get('receivedDateTime'), 80),
                'snippet': _text(row.get('bodyPreview'), 500)}
        if operation == 'thread':
            body = row.get('body') or {}
            content = body.get('content') if isinstance(body, dict) else None
            plain = isinstance(body, dict) and body.get('contentType', '').lower() == 'text'
            item['body'] = _text(content if plain else '', 1400)
            item['body_truncated'] = not plain or not isinstance(content, str) or len(content) > 1400
        projected.append(item)
    result[field] = projected
    if operation == 'thread':
        result['thread_id'] = arguments['thread_id']
    if len(json.dumps(result, allow_nan=False).encode()) > 32000:
        raise ValueError('Outlook result exceeds budget')
    return result


class Outlook:
    def __init__(self, config_path: Path):
        try:
            config = private_json(config_path)
            client = config.get('client_id')
            authority = config.get('authority')
            if (set(config) != {'schema_version', 'client_id', 'authority', 'token_file'}
                    or type(config['schema_version']) is not int or config['schema_version'] != 1
                    or not isinstance(client, str) or str(uuid.UUID(client)) != client
                    or not isinstance(authority, str)
                    or authority != 'consumers' and str(uuid.UUID(authority)) != authority
                    or not isinstance(config['token_file'], str)):
                raise ValueError('Private Outlook configuration refused')
            self.token_path = Path(config['token_file'])
            private_path(self.token_path)
            _token(private_json(self.token_path))
            self.client_id = client
            self.token_endpoint = 'https://login.microsoftonline.com/' + authority + '/oauth2/v2.0/token'
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Private Outlook configuration refused') from None

    def _access(self, rejected: str | None = None) -> str:
        lock_path = self.token_path.with_name(self.token_path.name + '.lock')
        private_path(lock_path)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('Private Outlook authorization refused')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            token = _token(private_json(self.token_path))
            if token['expires_at'] > time.time() + 60 and token['access_token'] != rejected:
                return token['access_token']
            data = urllib.parse.urlencode({'client_id': self.client_id, 'grant_type': 'refresh_token',
                'refresh_token': token['refresh_token'], 'scope': SCOPES}).encode()
            request = urllib.request.Request(self.token_endpoint, data=data, method='POST',
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
            token = validate_token_response(_fetch(request), token['refresh_token'])
            atomic_json(self.token_path, token)
            return token['access_token']
        finally:
            os.close(descriptor)

    def request(self, operation: str, arguments: Any) -> dict[str, Any]:
        arguments = _arguments(operation, arguments)
        fields = 'id,conversationId,subject,from,receivedDateTime,bodyPreview'
        query = {'$top': '10', '$select': fields + (',body' if operation == 'thread' else '')}
        if operation == 'search':
            query['$search'] = '"' + arguments['query'].replace('\\', '\\\\').replace('"', '\\"') + '"'
        else:
            query['$filter'] = "conversationId eq '" + arguments['thread_id'].replace("'", "''") + "'"
        url = GRAPH + '?' + urllib.parse.urlencode(query)
        try:
            access = self._access()
            for attempt in range(2):
                request = urllib.request.Request(url, method='GET', headers={
                    'Authorization': 'Bearer ' + access, 'Accept': 'application/json',
                    'Prefer': 'outlook.body-content-type="text"'})
                try:
                    raw = _fetch(request)
                    return _project(raw, operation, arguments)
                except urllib.error.HTTPError as error:
                    error.close()
                    if error.code != 401 or attempt:
                        raise ValueError('Outlook read unavailable') from None
                    access = self._access(rejected=access)
            raise ValueError('Outlook read unavailable')
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Outlook unavailable; no message was sent or marked read') from None
