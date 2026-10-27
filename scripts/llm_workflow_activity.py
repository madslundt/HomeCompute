#!/usr/bin/env python3
"""Read-only, payload-free activity snapshots used before releasing the GPU."""
from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time


class NativeActivity:
    """Keep authenticated native RPC latency outside HTTP request handling."""
    def __init__(self):
        self.lock = threading.Lock()
        self.value = None
        threading.Thread(target=self.poll, daemon=True).start()

    def poll(self) -> None:
        from native_llm_activity import collect
        while True:
            try:
                value = collect()
            except Exception:
                value = None
            with self.lock:
                self.value = value
            time.sleep(5)

    def get(self) -> dict:
        with self.lock:
            value = self.value
        if (not isinstance(value, dict) or type(value.get('active_turns')) is not int or
                value['active_turns'] < 0 or type(value.get('observed_at')) not in (int, float) or
                not math.isfinite(value['observed_at']) or not -5 <= time.time() - value['observed_at'] <= 15):
            raise ValueError('Native activity is unavailable or stale')
        return value


class DisabledActivity:
    def get(self) -> dict:
        raise ValueError('Activity collection is disabled for the admission-only service')


def read_count(path: Path, query: str, parameters: tuple = ()) -> int:
    # Never create a missing database or ignore its live WAL state.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute(query, parameters).fetchone()[0]


def snapshot(config: Path, workflows: Path, turns: Path) -> dict:
    policy = json.loads(config.read_text())
    identifiers = policy.get('workflow_ids')
    if (policy.get('schema_version') != 1 or not isinstance(identifiers, list) or
            not identifiers or len(identifiers) != len(set(identifiers)) or
            any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]{1,64}', value)
                for value in identifiers)):
        raise ValueError('Invalid protected workflow policy')
    placeholders = ','.join('?' for _ in identifiers)
    running = read_count(workflows,
        f'SELECT count(*) FROM execution_entity WHERE status=? AND "deletedAt" IS NULL '
        f'AND "workflowId" IN ({placeholders})', ('running', *identifiers))
    active_turns = read_count(turns, "SELECT count(*) FROM turns WHERE state='running'")
    return dict(active_workflows=running, active_turns=active_turns, observed_at=time.time())


def text_available() -> bool:
    """Read advertised readiness without acquiring an inference lease or following redirects."""
    connection = http.client.HTTPConnection('172.28.200.2', 8188, timeout=3)
    try:
        connection.request('GET', '/control/api/llm-status')
        response = connection.getresponse()
        if response.status != 200:
            return False
        body = response.read(4097)
        if len(body) > 4096:
            return False
        result = json.loads(body)
        return (isinstance(result, dict) and result.get('available') is True
                and result.get('state') == 'ready')
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def make_server(config: Path, workflows: Path, turns: Path,
                address: tuple[str, int] = ('172.28.200.1', 18190), native=None,
                admission_only: bool = False) -> ThreadingHTTPServer:
    native = DisabledActivity() if admission_only else native or NativeActivity()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == '/llm/admission':
                if text_available():
                    result, status = {'available': True}, 200
                else:
                    result, status = {'error': {
                        'message': 'Text model is temporarily unavailable',
                        'type': 'service_unavailable'}}, 503
                self.reply(result, status)
                return
            if self.path != '/llm/activity':
                self.send_error(404)
                return
            try:
                activity = native.get()
                result, status = snapshot(config, workflows, turns), 200
                result['active_turns'] += activity['active_turns']
                result['observed_at'] = min(result['observed_at'], activity['observed_at'])
            except (OSError, ValueError, sqlite3.Error, TypeError) as error:
                print(f'Activity verification failed: {type(error).__name__}', file=sys.stderr, flush=True)
                result, status = {'error': 'Activity cannot be verified'}, 503
            self.reply(result, status)

        def reply(self, result: dict, status: int):
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if self.path == '/llm/admission' and status == 503:
                self.send_header('Retry-After', '30')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--workflows', type=Path, default=Path('/srv/state/automation/n8n/database.sqlite'))
    parser.add_argument('--turns', type=Path, default=Path('/var/lib/homecompute-openclaw/communication/adapter/transport.sqlite3'))
    parser.add_argument('--admission-only', action='store_true',
                        help='Serve readiness separately on port 18192 without polling activity')
    args = parser.parse_args()
    address = ('172.28.200.1', 18192 if args.admission_only else 18190)
    make_server(args.config, args.workflows, args.turns, address=address,
                admission_only=args.admission_only).serve_forever()
