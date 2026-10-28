#!/usr/bin/env python3
"""Configured loopback text service and a transparent, non-replaying intake gate."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import ssl
from urllib.request import urlopen
import threading
from urllib.parse import urlsplit

CONFIG = Path('/etc/homecompute/image-mode.json')
CONFIG_KEYS = {'default_text', 'workflow_activity_url', 'workflow_activity_ca_file'}
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}


@dataclass(frozen=True)
class TextConfig:
    container: str
    base_url: str = 'http://127.0.0.1:18300'
    display_name: str = 'Text model'
    health_path: str = '/health'

    def __post_init__(self):
        if not isinstance(self.container, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', self.container):
            raise ValueError('Invalid configured text container')
        url = urlsplit(self.base_url)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or
                not 1 <= url.port <= 65535 or url.username or url.password or
                url.path or url.query or url.fragment or url.netloc != f'127.0.0.1:{url.port}'):
            raise ValueError('Text endpoint must be an explicit HTTP loopback port')
        if not isinstance(self.display_name, str) or not 1 <= len(self.display_name) <= 120:
            raise ValueError('Invalid configured text display name')
        if not isinstance(self.health_path, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]{1,127}', self.health_path):
            raise ValueError('Invalid configured text health path')

    @property
    def port(self) -> int:
        return urlsplit(self.base_url).port

    @classmethod
    def load(cls, path: Path = CONFIG) -> 'TextConfig':
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Image-mode configuration must be a root-owned regular file without write access for others')
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or 'default_text' not in value or set(value) - CONFIG_KEYS:
            raise ValueError('Invalid image-mode configuration')
        text = value['default_text']
        if not isinstance(text, dict) or set(text) - {'container', 'base_url', 'display_name', 'health_path'}:
            raise ValueError('Invalid configured default text service')
        return cls(**text)


@dataclass(frozen=True)
class WorkflowActivity:
    url: str
    ca_file: str | None = None

    def __post_init__(self):
        url = urlsplit(self.url)
        if (url.scheme != 'https' or url.hostname not in {'ai.home.arpa', 'home-core.tail479ad.ts.net'} or
                url.port not in {None, 443, 8443} or url.username or url.password or
                url.path != '/llm/activity' or url.query or url.fragment):
            raise ValueError('Activity URL must be the fixed HTTPS home-core activity endpoint')
        if self.ca_file is None and url.hostname == 'home-core.tail479ad.ts.net':
            return  # This fixed tailnet hostname uses a publicly trusted certificate.
        if not isinstance(self.ca_file, str) or not self.ca_file.startswith('/'):
            raise ValueError('Activity CA must be an absolute certificate path')

    @classmethod
    def load(cls, path: Path = CONFIG) -> 'WorkflowActivity':
        TextConfig.load(path)  # Applies the same owner/schema checks.
        value = json.loads(path.read_text())
        return cls(value.get('workflow_activity_url', ''), value.get('workflow_activity_ca_file'))

    def get(self) -> dict:
        context = ssl.create_default_context()
        if self.ca_file is not None:
            info = Path(self.ca_file).stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Workflow activity CA is not trusted')
            context.load_verify_locations(cafile=self.ca_file)
        with urlopen(self.url, context=context, timeout=5) as response:
            if response.geturl() != self.url:
                raise RuntimeError('Unexpected workflow activity redirect')
            body = response.read(65537)
        if len(body) > 65536:
            raise RuntimeError('Unexpected workflow activity response')
        return json.loads(body)


class IntakeGate:
    def __init__(self):
        self.condition = threading.Condition()
        self.available = False
        self.state = 'starting'
        self.active = 0

    def close(self, state: str) -> None:
        with self.condition:
            self.available, self.state = False, state
            self.condition.notify_all()

    def open(self) -> None:
        with self.condition:
            self.available, self.state = True, 'ready'
            self.condition.notify_all()

    def enter(self, track: bool) -> bool:
        with self.condition:
            if not self.available:
                return False
            if track:
                self.active += 1
            return True

    def leave(self) -> None:
        with self.condition:
            self.active -= 1
            self.condition.notify_all()

    def status(self) -> dict:
        with self.condition:
            return dict(available=self.available, state=self.state, active_requests=self.active)


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    connection_timeout = 15

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.connection_timeout)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def proxy_handler(config: TextConfig, gate: IntakeGate):
    class Proxy(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass  # Neither prompts nor auth headers belong in service logs.

        def reply(self, code: int, message: str, retry: bool = False):
            body = json.dumps({'error': {'message': message, 'type': 'service_unavailable'}}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            if retry:
                self.send_header('Retry-After', '30')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            track = self.command == 'POST'
            paths = {'/v1/chat/completions', '/v1/completions', '/v1/responses'} if track else {'/v1/models', '/health'}
            if self.path not in paths:
                self.reply(404, 'Unknown text API resource')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 <= length <= 64 * 1024 * 1024:
                    self.reply(413, 'Unsupported or oversized request body')
                    return
                # Closing admission and leasing an upstream request share the
                # condition lock. Once closed, no new inference can escape.
                if not gate.enter(track):
                    self.reply(503, 'Text model is temporarily unavailable', retry=True)
                    return
                upstream, headers_sent = None, False
                try:
                    blocked = HOP_HEADERS | {'host', 'content-length'}
                    blocked.update(part.strip().lower() for part in self.headers.get('Connection', '').split(','))
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in blocked}
                    upstream = http.client.HTTPConnection('127.0.0.1', config.port, timeout=3600)
                    upstream.putrequest(self.command, self.path, skip_accept_encoding=True)
                    for key, value in headers.items():
                        upstream.putheader(key, value)
                    if length or track:
                        upstream.putheader('Content-Length', str(length))
                    upstream.endheaders()
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise OSError('Incomplete request body')
                        upstream.send(chunk)
                        remaining -= len(chunk)
                    response = upstream.getresponse()
                    self.send_response(response.status, response.reason)
                    blocked = HOP_HEADERS | {'server', 'date'}
                    blocked.update(part.strip().lower() for part in response.getheader('Connection', '').split(','))
                    for key, value in response.getheaders():
                        if key.lower() not in blocked:
                            self.send_header(key, value)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    headers_sent = True
                    while True:
                        chunk = response.read1(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    if not headers_sent:
                        self.reply(502, 'Text upstream request failed')
                    # A partially transmitted stream is closed, never replayed.
                finally:
                    if upstream is not None:
                        upstream.close()
                    self.close_connection = True
                    if track:
                        gate.leave()
            except (ValueError, OSError):
                self.close_connection = True
    return Proxy


def make_proxy(config: TextConfig, gate: IntakeGate, port: int = 18400) -> BoundedHTTPServer:
    return BoundedHTTPServer(('127.0.0.1', port), proxy_handler(config, gate))
