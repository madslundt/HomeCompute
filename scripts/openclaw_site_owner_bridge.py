"""Owner-authenticated finite browser controls and private WebSocket transport.

Only the owner HTTP service imports this module. No browser payload reaches an
assistant tool, a log, a saved file or a model-visible API.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import secrets
import select
import socket
import threading
import time
from uuid import UUID

from openclaw_bitwarden import read_private
from openclaw_site_owner_page import BROWSER_CSP, CLIENT, private_browser
from openclaw_site_registry import account_name

# A protected filename, not a credential literal.
TOKEN_FILE = '/var/lib/homecompute-access/config/browser-owner-token'  # nosec B105
ACTIONS = frozenset({'start', 'fill', 'ready', 'close'})
STATES = frozenset({'idle', 'starting', 'awaiting_owner', 'ready', 'closed', 'expired', 'revoked', 'unavailable'})
ROUTE = re.compile(r'/browser/([a-f0-9-]{36})/(.*)')


class BrowserOwnerRefused(ValueError):
    """Fixed diagnostics only; no upstream response or private session text."""


class BrowserOwnerBridge:
    def __init__(self, config: Path, authorize, *, clock=time.monotonic, wall=time.time):
        cfg = json.loads(read_private(config.parent, config.name, 4096))
        if (not isinstance(cfg, dict) or set(cfg) != {'schema_version', 'endpoint', 'token_file'}
                or type(cfg['schema_version']) is not int or cfg['schema_version'] != 1
                or cfg['endpoint'] != 'http://127.0.0.1:18816' or cfg['token_file'] != TOKEN_FILE):
            raise BrowserOwnerRefused('Private browser configuration refused')
        value = read_private(Path(TOKEN_FILE).parent, Path(TOKEN_FILE).name, 1024).decode().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', value):
            raise BrowserOwnerRefused('Private browser authentication refused')
        self.token, self.authorize, self.clock, self.wall = value, authorize, clock, wall
        self.nonces, self.lock = {}, threading.Lock()
        self._fill_notices = {}

    def active(self, reference: str) -> dict:
        if str(UUID(reference)) != reference:
            raise BrowserOwnerRefused('Private browser reference refused')
        item = self.authorize(reference)
        if (item.get('request_id') != reference or item.get('actor') != 'openclaw'
                or account_name(item.get('account')) != item.get('account') or item.get('status') != 'active'
                or type(item.get('expires_at')) is not int or type(item.get('approved_at')) is not int
                or not item['approved_at'] <= self.wall() < item['expires_at']
                or not re.fullmatch(r'[a-f0-9]{64}', item.get('request_digest', ''))):
            raise BrowserOwnerRefused('Private browser permission unavailable')
        return item

    def route(self, path: str) -> tuple[str, str]:
        match = ROUTE.fullmatch(path)
        if not match or len(path) > 256:
            raise BrowserOwnerRefused('Private browser path refused')
        reference, suffix = match.groups()
        if str(UUID(reference)) != reference:
            raise BrowserOwnerRefused('Private browser path refused')
        if suffix not in {'', 'client.js', 'state', 'socket', *ACTIONS}:
            if (not suffix.startswith('vendor/') or not re.fullmatch(r'vendor/[A-Za-z0-9_./-]+\.js', suffix)
                    or any(part in {'', '.', '..'} for part in suffix.split('/'))):
                raise BrowserOwnerRefused('Private browser asset refused')
        return reference, suffix

    def challenge(self, item: dict, action: str) -> str:
        nonce, now = secrets.token_urlsafe(32), self.clock()
        key = hashlib.sha256(nonce.encode()).digest()
        with self.lock:
            self.nonces = {key: value for key, value in self.nonces.items() if value[3] > now}
            if len(self.nonces) >= 1000:
                raise BrowserOwnerRefused('Private browser challenge capacity reached')
            self.nonces[key] = (item['request_id'], item['request_digest'], action, now + 300, now)
        return nonce

    def consume(self, item: dict, action: str, values: dict):
        if (set(values) != {'digest', 'csrf'} or not isinstance(values['csrf'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', values['csrf'])
                or values['digest'] != item['request_digest']):
            raise BrowserOwnerRefused('Private browser decision refused')
        with self.lock:
            entry = self.nonces.pop(hashlib.sha256(values['csrf'].encode()).digest(), None)
        now = self.clock()
        if (entry is None or entry[:3] != (item['request_id'], item['request_digest'], action)
                or not entry[4] <= now < entry[3]):
            raise BrowserOwnerRefused('Private browser challenge refused')

    def request(self, reference: str, suffix: str, *, method='GET', limit=1024 * 1024):
        connection = http.client.HTTPConnection('127.0.0.1', 18816, timeout=35 if method == 'POST' else 3)
        try:
            headers = {'Authorization': 'Bearer ' + self.token}
            body = None
            if method == 'POST':
                headers['Content-Type'], body = 'application/json', b'{}'
            connection.request(method, '/owner/browser/' + reference + '/' + suffix, body, headers)
            response = connection.getresponse()
            raw = response.read(limit + 1)
            if response.status != 200 or len(raw) > limit:
                raise BrowserOwnerRefused('Private browser upstream refused')
            return raw
        except Exception:
            raise BrowserOwnerRefused('Private browser unavailable') from None
        finally:
            connection.close()

    def state(self, reference: str) -> dict:
        item = self.active(reference)
        value = json.loads(self.request(reference, 'state', limit=4096))
        if (not isinstance(value, dict) or set(value) != {'status', 'request_id', 'account'}
                or value.get('status') not in STATES or value.get('request_id') != reference
                or value.get('account') != item['account']):
            raise BrowserOwnerRefused('Private browser state refused')
        return {'status': value['status']}

    def _fill_result(self, item: dict, raw: bytes) -> str:
        def unique(pairs):
            value = {}
            for key, entry in pairs:
                if key in value:
                    raise BrowserOwnerRefused('Private browser fill result refused')
                value[key] = entry
            return value
        try:
            value = json.loads(raw, object_pairs_hook=unique)
            if (not isinstance(value, dict) or set(value) != {'status', 'request_id', 'account', 'filled_fields'}
                    or value['status'] != 'awaiting_owner' or value['request_id'] != item['request_id']
                    or value['account'] != item['account'] or type(value['filled_fields']) is not int
                    or not 0 <= value['filled_fields'] <= 2):
                raise BrowserOwnerRefused('Private browser fill result refused')
            return {0: 'no_fields', 1: 'filled_one', 2: 'filled_two'}[value['filled_fields']]
        except Exception:
            raise BrowserOwnerRefused('Private browser fill result refused') from None

    def _save_fill_notice(self, item: dict, code: str) -> None:
        now = self.clock()
        with self.lock:
            self._fill_notices = {reference: entry for reference, entry in self._fill_notices.items()
                                  if entry[3] <= now < entry[4]}
            self._fill_notices.pop(item['request_id'], None)
            if len(self._fill_notices) >= 1000:
                oldest = min(self._fill_notices, key=lambda reference: self._fill_notices[reference][4])
                del self._fill_notices[oldest]
            self._fill_notices[item['request_id']] = (item['request_digest'], item['account'], code, now, now + 300)

    def _take_fill_notice(self, item: dict) -> str | None:
        with self.lock:
            entry = self._fill_notices.pop(item['request_id'], None)
        now = self.clock()
        if (entry is not None and entry[:2] == (item['request_digest'], item['account'])
                and entry[3] <= now < entry[4]):
            return entry[2]
        return None

    def get(self, handler):
        reference, suffix = self.route(handler.path)
        item = self.active(reference)
        if suffix == 'socket':
            return self.websocket(handler, reference, item)
        if suffix == 'client.js':
            return handler.send(200, CLIENT, content_type='text/javascript; charset=utf-8', csp=BROWSER_CSP)
        if suffix == 'state':
            state = self.state(reference)
            self.active(reference)
            return handler.send(200, state)
        if suffix.startswith('vendor/'):
            raw = self.request(reference, suffix)
            self.active(reference)
            return handler.send(200, raw, content_type='text/javascript; charset=utf-8', csp=BROWSER_CSP)
        if suffix:
            raise BrowserOwnerRefused('Private browser resource refused')
        state = self.state(reference)
        self.active(reference)
        challenges = {action: self.challenge(item, action) for action in ACTIONS}
        return handler.send(200, private_browser(item, state, challenges, fill_result=self._take_fill_notice(item)), csp=BROWSER_CSP)

    def post(self, handler, values: dict):
        reference, action = self.route(handler.path)
        if action not in ACTIONS:
            raise BrowserOwnerRefused('Private browser action refused')
        item = self.active(reference)
        self.consume(item, action, values)
        self.active(reference)
        if action == 'fill':
            with self.lock:
                self._fill_notices.pop(reference, None)
        # Fixed action and empty body only. The backend independently checks the
        # grant, registered destination and current page before filling fields.
        raw = self.request(reference, action, method='POST', limit=4096)
        if action == 'fill':
            code = self._fill_result(item, raw)
            current = self.active(reference)
            if current['request_digest'] != item['request_digest'] or current['account'] != item['account']:
                raise BrowserOwnerRefused('Private browser permission changed')
            self._save_fill_notice(current, code)
        return handler.send(303, {}, location='/browser/' + reference + '/')

    def websocket(self, handler, reference: str, item: dict):
        headers = handler.headers
        if (headers.get_all('Origin', []) != [handler.server.operator_origin]
                or len(headers.get_all('Sec-Fetch-Site', [])) > 1
                or headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin'
                or headers.get_all('Upgrade', []) != ['websocket']
                or len(headers.get_all('Connection', [])) != 1
                or 'upgrade' not in {part.strip().lower() for part in headers['Connection'].split(',')}
                or headers.get_all('Sec-WebSocket-Version', []) != ['13']
                or len(headers.get_all('Sec-WebSocket-Key', [])) != 1
                or headers.get('Content-Length') is not None or headers.get('Transfer-Encoding') is not None
                or len(headers.get_all('Sec-WebSocket-Protocol', [])) > 1
                or headers.get('Sec-WebSocket-Protocol') not in {None, 'binary'}):
            raise BrowserOwnerRefused('Private browser socket refused')
        key = headers['Sec-WebSocket-Key']
        try:
            if len(base64.b64decode(key, validate=True)) != 16:
                raise ValueError
        except Exception:
            raise BrowserOwnerRefused('Private browser socket refused') from None
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(), usedforsecurity=False).digest()).decode()
        upstream = None
        handler.close_connection = True
        upgraded = False
        try:
            upstream = socket.create_connection(('127.0.0.1', 18816), timeout=3)
            request = ('GET /owner/browser/' + reference + '/socket HTTP/1.1\r\nHost: 127.0.0.1:18816\r\n'
                       'Authorization: Bearer ' + self.token + '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                       'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' + key + '\r\n')
            if headers.get('Sec-WebSocket-Protocol'):
                request += 'Sec-WebSocket-Protocol: binary\r\n'
            upstream.sendall((request + '\r\n').encode('ascii'))
            raw = bytearray()
            while b'\r\n\r\n' not in raw:
                chunk = upstream.recv(4096)
                if not chunk:
                    raise BrowserOwnerRefused('Private browser socket unavailable')
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise BrowserOwnerRefused('Private browser socket unavailable')
            head, pending = bytes(raw).split(b'\r\n\r\n', 1)
            lines = head.decode('ascii').split('\r\n')
            if not re.fullmatch(r'HTTP/1\.[01] 101(?: Switching Protocols)?', lines[0]):
                raise BrowserOwnerRefused('Private browser socket unavailable')
            response = {}
            for line in lines[1:]:
                name, value = line.split(':', 1)
                name = name.lower()
                if name in response:
                    raise BrowserOwnerRefused('Private browser socket unavailable')
                response[name] = value.strip()
            if (not hmac.compare_digest(response.get('sec-websocket-accept', ''), expected)
                    or response.get('upgrade', '').lower() != 'websocket'
                    or response.get('connection', '').lower() != 'upgrade'
                    or response.get('sec-websocket-protocol') not in {None, headers.get('Sec-WebSocket-Protocol')}):
                raise BrowserOwnerRefused('Private browser socket unavailable')
            self.active(reference)
            handler.send_response(101)
            handler.send_header('Upgrade', 'websocket')
            handler.send_header('Connection', 'Upgrade')
            handler.send_header('Sec-WebSocket-Accept', expected)
            if response.get('sec-websocket-protocol'):
                handler.send_header('Sec-WebSocket-Protocol', response['sec-websocket-protocol'])
            handler.end_headers()
            upgraded = True
            handler.wfile.flush()
            if pending:
                handler.connection.sendall(pending)
            deadline = self.clock() + min(300, max(0, item['expires_at'] - self.wall()))
            next_check, transferred = 0, len(pending)
            upstream.settimeout(1)
            handler.connection.settimeout(1)
            while self.clock() < deadline:
                if self.clock() >= next_check:
                    current = self.active(reference)
                    if any(current[key] != item[key] for key in ('request_digest', 'approved_at', 'expires_at')):
                        break
                    next_check = self.clock() + 0.5
                ready, _, _ = select.select([upstream, handler.connection], [], [], 0.1)
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    transferred += len(chunk)
                    if transferred > 128 * 1024 * 1024:
                        return
                    (handler.connection if source is upstream else upstream).sendall(chunk)
        except Exception:
            if not upgraded:
                raise BrowserOwnerRefused('Private browser socket unavailable') from None
        finally:
            if upstream is not None:
                upstream.close()
