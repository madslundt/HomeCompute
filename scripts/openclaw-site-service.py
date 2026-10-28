#!/usr/bin/env python3
"""Trusted localhost website runner. Never exposes vault or browser primitives."""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
# Cleanup targets only our own Docker label; no shell or model arguments.
import subprocess  # nosec B404
import threading

from openclaw_bitwarden import BitwardenLogin, read_private
from openclaw_site_executor import ContainerRunner, DOCKER, GrantClient, SiteOperations
from openclaw_site_registry import SiteRegistry
from openclaw_site_sessions import SiteSessions
from openclaw_site_view_transport import asset, websocket


def tokens(path: Path) -> dict:
    value = json.loads(read_private(path.parent, path.name, 4096))
    if (not isinstance(value, dict) or set(value) != {'openclaw'}
            or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', t) for t in value.values())):
        raise ValueError('Website transport identities refused')
    return value


def cleanup():
    result = subprocess.run([DOCKER, 'ps', '-aq', '--filter', 'label=homecompute.site-operation=true'],  # nosec B603
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ValueError('Website cleanup unavailable')
    ids = result.stdout.splitlines()
    if len(ids) > 16 or any(not re.fullmatch(r'[a-f0-9]{12,64}', value) for value in ids):
        raise ValueError('Website cleanup identity refused')
    if ids:
        result = subprocess.run([DOCKER, 'rm', '--force', *ids],  # nosec B603
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode:
            raise ValueError('Website cleanup incomplete')


class Server(ThreadingHTTPServer):
    daemon_threads, request_queue_size = True, 8

    def __init__(self, operations, identities, *, sessions=None, owner_token=None):
        self.operations, self.identities = operations, identities
        self.sessions, self.owner_token = sessions, owner_token
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(('127.0.0.1', 18816), Handler)

    def handle_error(self, *_args):
        pass

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *_args):
        pass

    def send(self, status: int, value: dict):
        raw = json.dumps(value).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)

    def actor(self):
        if (len(self.path) > 128 or '?' in self.path or '#' in self.path
                or len(self.headers) > 24 or sum(len(k) + len(v) for k, v in self.headers.items()) > 4096
                or self.headers.get_all('Host', []) != ['127.0.0.1:18816']
                or self.headers.get('Origin') is not None
                or len(self.headers.get_all('Authorization', [])) != 1):
            return None
        value = self.headers['Authorization']
        if not value.isascii() or len(value) > 520:
            return None
        if self.server.owner_token and hmac.compare_digest(value, 'Bearer ' + self.server.owner_token):
            return 'operator'
        return next((actor for actor, token in self.server.identities.items()
                     if hmac.compare_digest(value, 'Bearer ' + token)), None)

    def do_GET(self):
        actor = self.actor()
        if actor is None:
            return self.send(403, {'error': 'website_access_refused'})
        try:
            owner = re.fullmatch(r'/owner/browser/([a-f0-9-]{36})/(state|diagnostics|socket|vendor/[A-Za-z0-9_./-]+\.js)', self.path)
            if owner:
                if actor != 'operator' or self.server.sessions is None:
                    return self.send(403, {'error': 'website_access_refused'})
                reference, suffix = owner.groups()
                if suffix == 'state':
                    return self.send(200, self.server.sessions.state(reference))
                if suffix == 'diagnostics':
                    return self.send(200, self.server.sessions.diagnostics(reference))
                if suffix == 'socket':
                    return websocket(self, self.server.sessions, reference)
                return asset(self, self.server.sessions, reference, suffix)
            if self.path == '/site/health':
                return self.send(200, {'ready': True, 'transaction_execution_available': False})
            if re.fullmatch(r'/site/[a-f0-9-]{36}', self.path):
                return self.send(200, self.server.operations.status(actor, self.path[6:]))
            self.send(404, {'error': 'website_operation_unavailable'})
        except Exception:
            self.send(400, {'error': 'website_operation_refused'})

    def do_POST(self):
        actor = self.actor()
        if actor is None:
            return self.send(403, {'error': 'website_access_refused'})
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,4}', lengths[0])
                    or not 1 <= int(lengths[0]) <= 8192 or self.headers.get('Transfer-Encoding')
                    or self.headers.get('Content-Type') != 'application/json'):
                raise ValueError('Website request refused')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise ValueError('Website request refused')
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate field refused')
                    result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique)
            owner = re.fullmatch(r'/owner/browser/([a-f0-9-]{36})/(start|fill|ready|close)', self.path)
            if owner:
                if actor != 'operator' or self.server.sessions is None or value != {}:
                    raise ValueError('Owner browser action refused')
                reference, action = owner.groups()
                return self.send(200, getattr(self.server.sessions, action)(reference))
            if self.path != '/site/run':
                raise ValueError('Website operation refused')
            self.send(200, self.server.operations.run(actor, value))
        except Exception:
            self.send(400, {'error': 'website_operation_refused'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'cleanup'])
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Trusted root runner required')
    os.umask(0o077)
    if args.action == 'cleanup':
        cleanup()
        return
    if args.config is None:
        raise ValueError('Website runner configuration required')
    cfg = json.loads(read_private(args.config.parent, args.config.name, 8192))
    required = {'schema_version', 'image', 'sites_file', 'state_directory', 'assistant_tokens_file',
                'request_tokens_file', 'bws_executable', 'custody_directory', 'owner_token_file', 'sessions_directory',
                'forbidden_key_digests'}
    if (not isinstance(cfg, dict) or set(cfg) != required or type(cfg['schema_version']) is not int or cfg['schema_version'] != 1
            or cfg['sites_file'] != '/var/lib/homecompute-access/config/sites.json'
            or cfg['state_directory'] != '/var/lib/homecompute-sites/operations'
            or cfg['assistant_tokens_file'] != '/var/lib/homecompute-sites/config/operation-tokens.json'
            or cfg['request_tokens_file'] != '/var/lib/homecompute-access/config/assistant-tokens.json'
            or cfg['custody_directory'] != '/var/lib/homecompute-credential-secrets'
            or cfg['owner_token_file'] != '/var/lib/homecompute-access/config/browser-owner-token'
            or cfg['sessions_directory'] != '/var/lib/homecompute-sites/sessions'):
        raise ValueError('Website runner configuration refused')
    identities = tokens(Path(cfg['assistant_tokens_file']))
    owner_path = Path(cfg['owner_token_file'])
    owner_token = read_private(owner_path.parent, owner_path.name, 1024).decode().strip()
    request_identities = tokens(Path(cfg['request_tokens_file']))
    hashes = cfg['forbidden_key_digests']
    if (not isinstance(hashes, list) or len(hashes) > 16
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in hashes)
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', owner_token) or owner_token in identities.values()
            or owner_token in request_identities.values() or hashlib.sha256(owner_token.encode()).hexdigest() in hashes):
        raise ValueError('Distinct private owner authentication required')
    client = GrantClient(request_identities)
    registry = SiteRegistry.from_file(Path(cfg['sites_file']))
    runner = ContainerRunner(cfg['image'])
    cleanup()
    def provider(account):
        return BitwardenLogin(Path(cfg['custody_directory']), Path(cfg['bws_executable']), account=account).retrieve()
    sessions = SiteSessions(Path(cfg['sessions_directory']), cfg['image'], registry, client.status, provider)
    operations = SiteOperations(Path(cfg['state_directory']), registry, client.status, provider, runner, sessions=sessions)
    try:
        Server(operations, identities, sessions=sessions, owner_token=owner_token).serve_forever()
    finally:
        cleanup()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt):
        raise SystemExit('Private website runner stopped; no credential values are displayed.')
