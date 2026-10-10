"""Dedicated Chromium process and authenticated, bounded CDP forwarding."""
from __future__ import annotations

import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

CDP = ("127.0.0.1", 18801)
PATH = re.compile(r"/(?:json(?:/(?:version|list))?|devtools/(?:browser|page)/[A-Za-z0-9-]+)")
CLIENTS = threading.BoundedSemaphore(16)


def authorized(path: str, token: str) -> bool:
    parsed = urlsplit(path)
    values = parse_qs(parsed.query, strict_parsing=True)
    return (bool(PATH.fullmatch(parsed.path)) and set(values) == {"token"}
            and len(values["token"]) == 1 and hmac.compare_digest(values["token"][0], token))


def serve(token: str, origin: str, address: tuple[str, int] = ("0.0.0.0", 18800)) -> ThreadingHTTPServer:
    if len(token) < 32 or not re.fullmatch(r"http://[0-9.]+:18800", origin):
        raise ValueError("dedicated browser token and fixed bridge origin required")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: object) -> None:
            pass

        def do_GET(self) -> None:
            try:
                valid = authorized(self.path, token)
            except ValueError:
                valid = False
            if not valid:
                return self.reply(403, b'{"error":"browser access required"}')
            if not CLIENTS.acquire(blocking=False):
                return self.reply(503, b'{"error":"browser connection budget"}')
            try:
                self.connection.settimeout(15)
                path = urlsplit(self.path).path
                if path.startswith("/devtools/"):
                    return self.websocket(path)
                conn = http.client.HTTPConnection(*CDP, timeout=5)
                try:
                    conn.request("GET", path, headers={"Host": "127.0.0.1:18801"})
                    response = conn.getresponse()
                    raw = response.read(1048577)
                    if len(raw) > 1048576:
                        return self.reply(502, b'{"error":"browser response budget"}')
                    value = json.loads(raw)
                    for row in value if isinstance(value, list) else [value]:
                        if isinstance(row, dict) and "webSocketDebuggerUrl" in row:
                            suffix = urlsplit(row["webSocketDebuggerUrl"]).path
                            row["webSocketDebuggerUrl"] = origin.replace("http:", "ws:") + suffix + "?" + urlencode({"token": token})
                    self.reply(response.status, json.dumps(value).encode())
                finally:
                    conn.close()
            except (OSError, ValueError, http.client.HTTPException):
                self.close_connection = True
            finally:
                CLIENTS.release()

        def websocket(self, path: str) -> None:
            if self.headers.get("Upgrade", "").lower() != "websocket":
                return self.reply(400, b'{"error":"websocket required"}')
            with socket.create_connection(CDP, timeout=5) as upstream:
                fields = {"Host": "127.0.0.1:18801", "Connection": "Upgrade", "Upgrade": "websocket"}
                for key in ("Sec-WebSocket-Key", "Sec-WebSocket-Version", "Sec-WebSocket-Protocol"):
                    value = self.headers.get(key)
                    if value:
                        if "\r" in value or "\n" in value or len(value) > 1024:
                            return self.reply(400, b'{"error":"invalid handshake"}')
                        fields[key] = value
                request = "GET " + path + " HTTP/1.1\r\n" + "".join(k + ": " + v + "\r\n" for k, v in fields.items()) + "\r\n"
                upstream.sendall(request.encode())
                head = b""
                while b"\r\n\r\n" not in head and len(head) < 16384:
                    chunk = upstream.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                if not head.startswith(b"HTTP/1.1 101 "):
                    return self.reply(502, b'{"error":"browser handshake failed"}')
                self.connection.sendall(head)
                self.close_connection = True
                # CDP activity refreshes the connection; abandoned clients expire.
                while True:
                    ready, _, _ = select.select([upstream, self.connection], [], [], 120)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)

        def reply(self, status: int, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer(address, Handler)


def main() -> None:
    token = Path(os.environ["BROWSER_TOKEN_FILE"]).read_text().strip()
    browser = subprocess.Popen(["/usr/bin/chromium", "--headless=new", "--no-sandbox",
        "--disable-dev-shm-usage", "--disable-quic", "--disable-background-networking",
        "--disable-component-update", "--no-first-run", "--no-default-browser-check",
        "--remote-debugging-port=18801", "--user-data-dir=/home/browser/profile",
        "--proxy-server=http://browser-egress:3128", "--proxy-bypass-list=<-loopback>",
        "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if browser.poll() is not None:
            raise RuntimeError("Chromium exited before readiness")
        try:
            with socket.create_connection(CDP, timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        browser.terminate()
        raise RuntimeError("Chromium readiness deadline")
    try:
        serve(token, os.environ["BROWSER_PUBLIC_ORIGIN"]).serve_forever()
    finally:
        browser.terminate()
        browser.wait(timeout=10)


if __name__ == "__main__":
    main()
