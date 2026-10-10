"""TLS facade for the dedicated guest: worker credential and finite routes only."""
from __future__ import annotations

import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import ssl
from typing import Any

TASK = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
MAX_BODY = 1500000
MAX_RESPONSE = 2000000


def permitted(method: str, path: str) -> bool:
    if method == "POST":
        return path == "/worker/claim" or bool(re.fullmatch(r"/tasks/" + TASK + r"/(heartbeat|result)", path))
    return method == "GET" and (path == "/healthz" or bool(re.fullmatch(r"/tasks/" + TASK, path)))


def serve(token: str, address: tuple[str, int], upstream: tuple[str, int] = ("127.0.0.1", 18792),
          guest: str = "10.77.21.2") -> ThreadingHTTPServer:
    if len(token) < 32 or "\n" in token or "\r" in token:
        raise ValueError("dedicated worker credential required")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def request(self) -> None:
            if self.client_address[0] != guest or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token):
                return self.reply(403, b'{"error":"worker access required"}')
            if not permitted(self.command, self.path):
                return self.reply(403, b'{"error":"worker route required"}')
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
                return self.reply(400, b'{"error":"bounded body required"}')
            try:
                size = int(lengths[0]) if lengths else 0
                if not 0 <= size <= MAX_BODY or self.command == "GET" and size:
                    raise ValueError("invalid size")
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("incomplete request")
            except (ValueError, TimeoutError):
                return self.reply(400, b'{"error":"bounded body required"}')
            connection = http.client.HTTPConnection(*upstream, timeout=15)
            try:
                connection.request(self.command, self.path, body=body,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                response = connection.getresponse()
                result = response.read(MAX_RESPONSE + 1)
                if len(result) > MAX_RESPONSE:
                    raise ValueError("response budget")
                self.reply(response.status, result)
            except Exception:
                self.reply(503, b'{"error":"broker unavailable; reconcile before retry"}')
            finally:
                connection.close()

        def reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = request
        do_POST = request
        do_DELETE = request

    return ThreadingHTTPServer(address, Handler)


def main() -> None:
    secrets = Path(os.environ["CODEX_BROKER_SECRETS"])
    server = serve((secrets / "worker_token").read_text().strip(), ("10.77.21.1", 19444))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(str(secrets / "worker-api.crt"), str(secrets / "worker-api.key"))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
