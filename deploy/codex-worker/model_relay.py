"""Trusted fixed model-only relay; closes shared-Caddy SNI/Host tunneling."""
from __future__ import annotations

import hmac
import json
import ssl
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def serve(token: str, certificate: str, address: tuple[str, int]) -> ThreadingHTTPServer:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certificate)))
    budget = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def respond(self, status: int, data: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self) -> None:
            if self.path == "/healthz" and self.command == "GET":
                return self.respond(200, b'{"ok":true}')
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.respond(401, b'{"error":"unauthorized"}')
            if (self.command, self.path) not in {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}:
                return self.respond(404, b'{"error":"unsupported route"}')
            if not budget.acquire(blocking=False):
                return self.respond(429, b'{"error":"model concurrency budget"}')
            try:
                data = None
                if self.command == "POST":
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4194304:
                        raise ValueError("input budget")
                    value = json.loads(self.rfile.read(size))
                    if value.get("model") != "automation-moe":
                        raise ValueError("model not approved")
                    output = value.get("max_tokens", value.get("max_completion_tokens", 4096))
                    if type(output) is not int or not 1 <= output <= 4096:
                        raise ValueError("output budget")
                    value["max_tokens"] = output
                    value.pop("max_completion_tokens", None)
                    data = json.dumps(value).encode()
                request = urllib.request.Request("https://ai.home.arpa" + self.path,
                    data=data, method=self.command, headers={
                        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
                with opener.open(request, timeout=600) as upstream:
                    result = upstream.read(4194305)
                    if len(result) > 4194304:
                        raise ValueError("response budget")
                    self.respond(upstream.status, result, upstream.headers.get("Content-Type", "application/json"))
            except (ValueError, AttributeError):
                self.respond(400, b'{"error":"invalid request or response budget"}')
            except urllib.error.HTTPError as error:
                self.respond(error.code, b'{"error":"upstream rejected request"}')
            except Exception:
                self.respond(502, b'{"error":"model unavailable"}')
            finally:
                budget.release()

        do_POST = handle_request
        do_GET = handle_request

    return ThreadingHTTPServer(address, Handler)


if __name__ == "__main__":
    serve(Path("/run/secrets/relay_model_key").read_text().strip(), "/etc/model-ca.crt", ("0.0.0.0", 8081)).serve_forever()
