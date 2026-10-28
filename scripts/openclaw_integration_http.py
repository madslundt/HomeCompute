"""Isolate provider HTTP so DNS, TLS, headers and bodies share a hard deadline."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

MAX_BYTES = 524288


class Reply(io.BytesIO):
    def __init__(self, payload):
        super().__init__(base64.b64decode(payload['body'], validate=True))
        self.status, self.headers = payload['status'], payload['headers']


class Opener:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(2)

    def open(self, request, timeout):
        deadline = time.monotonic() + timeout
        payload = {'url': request.full_url, 'body': base64.b64encode(request.data).decode() if request.data is not None else None,
                   'headers': dict(request.header_items()), 'method': request.get_method(), 'timeout': timeout}
        # Secrets travel only through anonymous stdin/stdout pipes, never argv,
        # environment, files or diagnostics. A timeout kills and reaps the child.
        if not self.slots.acquire(timeout=max(0, deadline - time.monotonic())): raise TimeoutError
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError
            result = subprocess.run([sys.executable, str(Path(__file__).resolve())], input=json.dumps(payload).encode(),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=remaining)
        finally:
            self.slots.release()
        if result.returncode or len(result.stdout) > 750000:
            raise ValueError('Provider HTTP failed')
        return Reply(json.loads(result.stdout))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None


def main():
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768: raise ValueError
        request = json.loads(raw)
        body = base64.b64decode(request['body'], validate=True) if request['body'] is not None else None
        req = urllib.request.Request(request['url'], data=body, headers=request['headers'], method=request['method'])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=request['timeout']) as response:
            content = response.read(MAX_BYTES + 1)
            if response.status not in {200, 202, 204} or len(content) > MAX_BYTES: raise ValueError
            session = response.headers.get('Mcp-Session-Id')
            headers = {'Mcp-Session-Id': session} if session is not None else {}
            sys.stdout.write(json.dumps({'status': response.status, 'headers': headers,
                                         'body': base64.b64encode(content).decode()}))
        return 0
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError): error.close()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
