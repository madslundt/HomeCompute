"""Internal owner-only noVNC transport. No Cookie headers or screen logging."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import re
import select
import socket
import time

from openclaw_site_executor import SiteExecutionRefused


def asset(handler, sessions, reference: str, suffix: str):
    if (not re.fullmatch(r'vendor/[A-Za-z0-9_./-]+\.js', suffix)
            or any(part in {'', '.', '..'} for part in suffix.split('/'))):
        raise SiteExecutionRefused('Private viewer asset refused')
    target = sessions.target(reference)
    upstream = http.client.HTTPConnection(*target, timeout=3)
    try:
        upstream.request('GET', '/' + suffix[7:])
        response = upstream.getresponse()
        raw = response.read(1048577)
        if response.status != 200 or len(raw) > 1048576:
            raise SiteExecutionRefused('Private viewer asset unavailable')
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/javascript; charset=utf-8')
        handler.send_header('Content-Length', str(len(raw)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        handler.wfile.write(raw)
        handler.close_connection = True
    finally:
        upstream.close()


def websocket(handler, sessions, reference: str):
    key = handler.headers.get('Sec-WebSocket-Key', '')
    try:
        valid = len(base64.b64decode(key, validate=True)) == 16
    except Exception:
        valid = False
    if (not valid or handler.headers.get('Upgrade', '').lower() != 'websocket'
            or handler.headers.get('Sec-WebSocket-Version') != '13'
            or len(handler.headers.get_all('Sec-WebSocket-Key', [])) != 1):
        raise SiteExecutionRefused('Private viewer handshake refused')
    target = sessions.target(reference)
    with socket.create_connection(target, timeout=3) as upstream:
        fields = {'Host': target[0] + ':6080', 'Connection': 'Upgrade', 'Upgrade': 'websocket',
                  'Sec-WebSocket-Key': key, 'Sec-WebSocket-Version': '13'}
        protocol = handler.headers.get('Sec-WebSocket-Protocol')
        if protocol:
            if not re.fullmatch(r'[A-Za-z0-9_, .-]{1,128}', protocol):
                raise SiteExecutionRefused('Private viewer protocol refused')
            fields['Sec-WebSocket-Protocol'] = protocol
        # Neither user cookies nor the internal owner bearer reach the worker.
        request = 'GET /websockify HTTP/1.1\r\n' + ''.join(k + ': ' + v + '\r\n' for k, v in fields.items()) + '\r\n'
        upstream.sendall(request.encode())
        header = bytearray()
        while b'\r\n\r\n' not in header and len(header) <= 16384:
            value = upstream.recv(1)
            if not value:
                break
            header.extend(value)
        response = bytes(header).decode('ascii')
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode(), usedforsecurity=False).digest()).decode()
        headers = {}
        for line in response.split('\r\n')[1:]:
            name, separator, value = line.partition(':')
            if separator:
                if name.lower() in headers:
                    raise SiteExecutionRefused('Duplicate viewer handshake header refused')
                headers[name.lower()] = value.strip()
        if not response.startswith('HTTP/1.1 101 ') or not hmac.compare_digest(headers.get('sec-websocket-accept', ''), expected):
            raise SiteExecutionRefused('Private viewer handshake unavailable')
        handler.send_response(101)
        for name in ('upgrade', 'connection', 'sec-websocket-accept', 'sec-websocket-protocol'):
            if name in headers:
                handler.send_header(name, headers[name])
        handler.end_headers()
        handler.close_connection = True
        deadline = time.monotonic() + min(300, sessions.grant(reference)['expires_at'] - time.time())
        try:
            while time.monotonic() < deadline:
                sessions.target(reference)  # Rechecks the active grant and session, including close.
                readable, _, _ = select.select([handler.connection, upstream], [], [], 0.25)
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is handler.connection else handler.connection).sendall(chunk)
        except (OSError, ValueError):
            return
