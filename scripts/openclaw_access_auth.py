"""Owner-only approval authentication using an existing Mattermost web session.

The incoming HttpOnly cookie is consumed in this trusted host process only.
Neither the session token nor the Mattermost user record is returned to tools.
"""
from __future__ import annotations

import hmac
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from openclaw_bitwarden import read_private

LOGIN_URL = 'https://home-core.tail479ad.ts.net:9443/login'
ME_URL = 'http://127.0.0.1:18065/api/v4/users/me'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def cookie_token(headers) -> str | None:
    cookies = headers.get_all('Cookie', [])
    if (len(cookies) != 1 or len(cookies[0]) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in cookies[0])):
        return None
    values = []
    for part in cookies[0].split(';'):
        key, separator, value = part.strip().partition('=')
        if key == 'MMAUTHTOKEN':
            if not separator or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', value):
                return None
            values.append(value)
    return values[0] if len(values) == 1 else None


class MattermostOwnerAuth:
    def __init__(self, config_file: Path, *, opener=None):
        self.config = json.loads(read_private(config_file.parent, config_file.name, 1024))
        if (set(self.config) != {'schema_version', 'owner_id'} or type(self.config['schema_version']) is not int
                or self.config['schema_version'] != 1
                or not isinstance(self.config['owner_id'], str)
                or not re.fullmatch(r'[a-z0-9]{26}', self.config['owner_id'])):
            raise ValueError('Owner authentication configuration refused')
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def __call__(self, headers) -> bool:
        token = cookie_token(headers)
        if token is None:
            return False
        request = urllib.request.Request(ME_URL, headers={'Authorization': 'Bearer ' + token})
        try:
            with self.opener.open(request, timeout=3) as response:
                if response.status != 200:
                    return False
                raw = response.read(65537)
            if len(raw) > 65536:
                return False
            user = json.loads(raw)
            identifier = user.get('id') if isinstance(user, dict) else None
            return (isinstance(identifier, str) and identifier.isascii()
                    and hmac.compare_digest(identifier, self.config['owner_id'])
                    and user.get('is_bot', False) is False and user.get('delete_at') == 0)
        except Exception:
            # Authentication errors must not log request headers or user bodies.
            return False
