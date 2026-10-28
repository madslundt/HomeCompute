#!/usr/bin/env python3
"""Authenticated internal n8n retrieval; no actions, model tools or request logging."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any

from openclaw_services import private
from workflow_retrieval import WorkflowRetrieval, PROFILES


def serve(config: Path, service: Any, address: tuple[str, int] = ('172.28.200.1', 18795)) -> ThreadingHTTPServer:
    value = json.loads(private(config, 16384))
    if (set(value) != {'schema_version', 'tokens'} or value['schema_version'] != 1
            or set(value['tokens']) != PROFILES or len(set(value['tokens'].values())) != len(PROFILES)
            or any(not isinstance(token, str) or len(token) < 40 for token in value['tokens'].values())):
        raise ValueError('Independent workflow credentials required')
    budget = threading.BoundedSemaphore(3)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def handle_one_request(self) -> None:
            self.connection.settimeout(15)
            super().handle_one_request()

        def respond(self, code: int, data: Any) -> None:
            raw = json.dumps(data, allow_nan=False).encode()
            if len(raw) > 196608:
                code, raw = 503, b'{"error":"retrieval response exceeds budget"}'
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == '/healthz' and self.client_address[0] in {'127.0.0.1', '172.28.200.1'}:
                self.respond(200, {'ready': True})
            else:
                self.respond(404, {'error': 'not found'})

        def do_POST(self) -> None:
            if self.path != '/internal/workflow-retrieval':
                self.respond(404, {'error': 'not found'}); return
            if self.client_address[0] not in {'127.0.0.1', '172.28.200.1', '172.28.200.2'}:
                self.respond(403, {'error': 'private proxy required'}); return
            profile = next((name for name, token in value['tokens'].items()
                            if hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)), None)
            if profile is None:
                self.respond(401, {'error': 'unauthorized'}); return
            if not budget.acquire(blocking=False):
                self.respond(429, {'error': 'retrieval busy'}); return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 262144 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Bounded JSON required')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or body.get('profile') != profile:
                    self.respond(403, {'error': 'workflow profile mismatch'}); return
                self.respond(200, service.request(body, profile))
            except (ValueError, TypeError, KeyError):
                self.respond(400, {'error': 'invalid bounded retrieval request'})
            except Exception:
                self.respond(503, {'error': 'local retrieval unavailable'})
            finally:
                budget.release()
    return ThreadingHTTPServer(address, Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    serve(args.config, WorkflowRetrieval(args.state)).serve_forever()
