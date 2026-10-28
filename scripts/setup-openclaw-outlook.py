#!/usr/bin/env python3
"""Authorize the owner's direct Mail.Read connector without n8n or client secrets."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

from openclaw_outlook import (NoRedirect, Outlook, SCOPES, atomic_json,
                              private_json, private_path, validate_token_response)

CONFIG = Path('/var/lib/homecompute-openclaw/config')
DIRECTORY = CONFIG / 'outlook-owner'
CLIENT = DIRECTORY / 'client.json'
TOKEN = DIRECTORY / 'token.json'
PENDING = DIRECTORY / 'device.json'
MAIL = CONFIG / 'mail.json'
VERIFY = 'https://microsoft.com/devicelogin'


def post(endpoint: str, parameters: dict[str, str]) -> dict[str, Any]:
    if endpoint not in {'https://login.microsoftonline.com/consumers/oauth2/v2.0/' + part
                        for part in ('devicecode', 'token')}:
        raise ValueError('Fixed Microsoft authorization endpoint required')
    request = urllib.request.Request(endpoint, method='POST',
        data=urllib.parse.urlencode(parameters).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        if error.code != 400:
            raise ValueError('Microsoft authorization unavailable') from None
        response = error
    with response:
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError('Microsoft authorization response refused')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Microsoft authorization response refused')
        if response.status != 200 and (response.status != 400 or not isinstance(result.get('error'), str)):
            raise ValueError('Microsoft authorization response refused')
        return result


def configuration(client_id: str) -> dict[str, Any]:
    if not isinstance(client_id, str) or str(uuid.UUID(client_id)) != client_id:
        raise ValueError('Microsoft application client ID required')
    return {'schema_version': 1, 'client_id': client_id, 'authority': 'consumers', 'token_file': str(TOKEN)}


def mail_configuration(connected: bool) -> dict[str, Any]:
    return {'schema_version': 2, 'provider': 'outlook-direct', 'outlook_config_file': str(CLIENT),
            'principal_scope': 'owner', 'data_domain': 'personal',
            'accounts': ['outlook_mads'] if connected else []}


def check_client() -> dict[str, Any]:
    client = private_json(CLIENT)
    if client != configuration(client.get('client_id', '')):
        raise ValueError('Fixed owner Outlook client required')
    return client


def prepare_directory() -> None:
    private_path(DIRECTORY)
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    private_path(CLIENT)


def check_mail() -> None:
    if not MAIL.exists() and not MAIL.is_symlink():
        return
    prior = private_json(MAIL)
    legacy = {'schema_version': 1, 'bridge_token_file': str(CONFIG / 'mail-owner-route-token'),
              'principal_scope': 'owner', 'data_domain': 'personal', 'accounts': []}
    if prior not in (mail_configuration(False), mail_configuration(True), legacy):
        raise ValueError('Existing mailbox requires explicit migration')


def stage() -> dict[str, Any]:
    """Select the disconnected direct backend without needing a Microsoft app yet."""
    prepare_directory()
    check_mail()
    prior = private_json(MAIL) if MAIL.exists() else None
    if TOKEN.exists() or prior and prior.get('accounts'):
        raise ValueError('Connected mailbox cannot be replaced by disconnected staging')
    backup = DIRECTORY / 'previous-mail.json'
    if backup.exists() or backup.is_symlink():
        private_json(backup)
    elif prior is not None:
        atomic_json(backup, prior)
    atomic_json(MAIL, mail_configuration(False))
    return {'status': 'application-registration-required', 'backend': 'outlook-direct',
            'account': 'outlook_mads', 'n8n_used': False, 'mailbox_content_read': False}


def connected() -> dict[str, Any]:
    """Finish a retained successful grant without redeeming its device code again."""
    Outlook(CLIENT)
    check_mail()
    if not MAIL.exists() or private_json(MAIL) != mail_configuration(True):
        atomic_json(MAIL, mail_configuration(True))
    result = {'status': 'connected', 'backend': 'outlook-direct', 'account': 'outlook_mads',
              'permissions': 'Mail.Read', 'n8n_used': False, 'mailbox_content_read': False}
    try:
        PENDING.unlink(missing_ok=True)
    except OSError:
        result['pending_cleanup_required'] = True
    return result


def check_pending(client: dict[str, Any]) -> dict[str, Any]:
    pending = private_json(PENDING)
    keys = {'client_id', 'scope', 'device_code', 'user_code', 'verification_uri',
            'expires_at', 'interval', 'next_poll_at'}
    if (set(pending) != keys or pending['client_id'] != client['client_id'] or pending['scope'] != SCOPES
            or pending['verification_uri'] not in {VERIFY, 'https://www.microsoft.com/link'}
            or not isinstance(pending['device_code'], str) or not 1 <= len(pending['device_code']) <= 8192
            or any(character.isspace() or ord(character) < 32 for character in pending['device_code'])
            or not isinstance(pending['user_code'], str) or not re.fullmatch(r'[A-Z0-9-]{1,32}', pending['user_code'])
            or type(pending['interval']) is not int or not 1 <= pending['interval'] <= 60
            or any(type(pending[key]) not in {int, float} or not math.isfinite(pending[key])
                   or pending[key] < 0 for key in ('expires_at', 'next_poll_at'))
            or pending['expires_at'] > time.time() + 1800
            or pending['next_poll_at'] > pending['expires_at'] + 60):
        raise ValueError('Retained authorization differs')
    return pending


def configure(client_id: str) -> dict[str, Any]:
    expected = configuration(client_id)
    prepare_directory()
    if CLIENT.exists() and private_json(CLIENT) != expected:
        raise ValueError('Existing Outlook application differs; explicit reconciliation required')
    if TOKEN.exists() and not CLIENT.exists():
        raise ValueError('Retained Outlook grant requires its original client configuration')
    check_mail()
    atomic_json(CLIENT, expected)
    if TOKEN.exists():
        return connected()
    atomic_json(MAIL, mail_configuration(False))
    return {'status': 'authorization-required', 'backend': 'outlook-direct',
            'account': 'outlook_mads', 'mailbox_content_read': False, 'n8n_used': False}


def begin() -> dict[str, Any]:
    client = check_client()
    if TOKEN.exists():
        return connected()
    now = time.time()
    pending = check_pending(client) if PENDING.exists() else None
    if pending is None or pending['expires_at'] <= now:
        endpoint = 'https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode'
        response = post(endpoint, {'client_id': client['client_id'], 'scope': SCOPES})
        if (response.get('error') or response.get('verification_uri') not in {VERIFY, 'https://www.microsoft.com/link'}
                or not isinstance(response.get('device_code'), str) or not 1 <= len(response['device_code']) <= 8192
                or any(character.isspace() or ord(character) < 32 for character in response['device_code'])
                or not isinstance(response.get('user_code'), str) or not re.fullmatch(r'[A-Z0-9-]{1,32}', response['user_code'])
                or type(response.get('expires_in')) is not int or not 1 <= response['expires_in'] <= 1800
                or type(response.get('interval', 5)) is not int or not 1 <= response.get('interval', 5) <= 60):
            raise ValueError('Microsoft device authorization refused')
        pending = {'client_id': client['client_id'], 'scope': SCOPES,
                   'device_code': response['device_code'], 'user_code': response['user_code'],
                   'verification_uri': response['verification_uri'], 'expires_at': now + response['expires_in'],
                   'interval': response.get('interval', 5), 'next_poll_at': now + response.get('interval', 5)}
        atomic_json(PENDING, pending)
    return {'status': 'authorization-required', 'user_code': pending['user_code'],
            'verification_uri': pending['verification_uri'], 'expires_at': pending['expires_at'],
            'permissions': 'Mail.Read', 'n8n_used': False}


def poll() -> dict[str, Any]:
    client = check_client()
    if TOKEN.exists():
        return connected()
    pending = check_pending(client)
    now = time.time()
    if pending['expires_at'] <= now:
        return {'status': 'expired'}
    if pending['next_poll_at'] > now:
        return {'status': 'authorization-pending', 'retry_after': max(1, int(pending['next_poll_at'] - now) + 1)}
    pending['next_poll_at'] = now + pending['interval']
    atomic_json(PENDING, pending)
    response = post('https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
                    {'client_id': client['client_id'], 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                     'device_code': pending['device_code']})
    error = response.get('error')
    if error in {'authorization_pending', 'slow_down'}:
        pending['interval'] = min(60, pending['interval'] + (5 if error == 'slow_down' else 0))
        pending['next_poll_at'] = now + pending['interval']
        atomic_json(PENDING, pending)
        return {'status': 'authorization-pending', 'retry_after': pending['interval']}
    if error:
        pending['expires_at'] = min(pending['expires_at'], now)
        pending['next_poll_at'] = min(pending['next_poll_at'], now)
        atomic_json(PENDING, pending)
        raise ValueError('Microsoft authorization refused or expired; start sign-in again')
    token = validate_token_response(response)
    try:
        atomic_json(TOKEN, token)
    except OSError:
        if not TOKEN.exists():
            pending['expires_at'] = min(pending['expires_at'], now)
            pending['next_poll_at'] = min(pending['next_poll_at'], now)
            atomic_json(PENDING, pending)
        raise
    return connected()


def status() -> dict[str, Any]:
    if not CLIENT.exists():
        return {'status': 'application-registration-required', 'backend': 'outlook-direct', 'n8n_used': False}
    check_client()
    enabled = TOKEN.exists() and MAIL.exists() and private_json(MAIL) == mail_configuration(True)
    if enabled:
        Outlook(CLIENT)
    return {'status': 'connected' if enabled else 'authorization-required', 'backend': 'outlook-direct',
            'account': 'outlook_mads', 'n8n_used': False, 'mailbox_content_read': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['stage', 'configure', 'begin', 'poll', 'status'])
    parser.add_argument('--client-id')
    parser.add_argument('--enable-mail', action='store_true', help='explicitly re-enable optional mail connectivity')
    args = parser.parse_args()
    if args.operation != 'status' and not args.enable_mail:
        parser.error('Mail integration is disabled; explicit --enable-mail is required for setup')
    os.umask(0o077)
    try:
        if args.operation == 'status' and not DIRECTORY.exists():
            result = status()
        else:
            if args.operation == 'configure' and not args.client_id:
                parser.error('--client-id required for configure')
            prepare_directory()
            lock = DIRECTORY / 'setup.lock'
            descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('Private Outlook setup lock required')
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = configure(args.client_id) if args.operation == 'configure' else globals()[args.operation]()
            finally:
                os.close(descriptor)
        print(json.dumps(result))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit('Direct Outlook setup refused; no credentials were printed. Check app registration and protected state.') from None


if __name__ == '__main__':
    main()
