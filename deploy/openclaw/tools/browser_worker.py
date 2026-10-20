"""Dedicated Chromium process and authenticated, bounded CDP forwarding."""
from __future__ import annotations

import hmac
import http.client
import ipaddress
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

CDP = ("127.0.0.1", 18801)
PATH = re.compile(r"/(?:json(?:/(?:version|list))?|devtools/(?:browser|page)/[A-Za-z0-9-]+)")
CLIENTS = threading.BoundedSemaphore(16)
DNS_CLIENTS = threading.BoundedSemaphore(4)
DNS_POOL = ThreadPoolExecutor(max_workers=4)
PUBLIC_RESEARCH_HOSTS = frozenset([
    'example.com', 'docs.openclaw.ai', 'docs.nvidia.com', 'docs.litellm.ai',
    'docs.n8n.io', 'www.home-assistant.io', 'developers.home-assistant.io',
    'nixos.org', 'wiki.nixos.org', 'nix.dev', 'search.nixos.org', 'github.com',
    'raw.githubusercontent.com', 'duckduckgo.com',
])


def public_dns(hostname: str, resolver=socket.getaddrinfo) -> dict:
    if hostname not in PUBLIC_RESEARCH_HOSTS:
        raise ValueError('Public research hostname required')
    addresses = set()
    for family, _kind, _proto, _canon, sockaddr in resolver(hostname, None, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(sockaddr[0])
        if (family not in (socket.AF_INET, socket.AF_INET6) or not address.is_global
                or address.is_reserved or address.is_multicast or getattr(address, 'ipv4_mapped', None) is not None):
            raise ValueError('DNS answer contains a non-public address')
        addresses.add((str(address), address.version))
    if not addresses or len(addresses) > 16:
        raise ValueError('Public DNS answer budget')
    return {'hostname': hostname, 'addresses': [{'address': address, 'family': family}
        for address, family in sorted(addresses)], 'ttl': 60}


def authorized(path: str, token: str) -> bool:
    parsed = urlsplit(path)
    values = parse_qs(parsed.query, strict_parsing=True)
    if parsed.path == '/json/public-dns':
        return (set(values) == {'token', 'hostname'} and len(values['token']) == 1
                and len(values['hostname']) == 1 and values['hostname'][0] in PUBLIC_RESEARCH_HOSTS
                and hmac.compare_digest(values['token'][0], token))
    return (bool(PATH.fullmatch(parsed.path)) and set(values) == {"token"}
            and len(values["token"]) == 1 and hmac.compare_digest(values["token"][0], token))


def remote_public_dns(hostname: str, token: str) -> dict:
    """Only the fixed DNS sidecar can resolve the reviewed public sources."""
    if hostname not in PUBLIC_RESEARCH_HOSTS:
        raise ValueError('Public research hostname required')
    conn = http.client.HTTPConnection('browser-dns', 18802, timeout=3)
    try:
        conn.request('GET', '/json/public-dns?' + urlencode({'hostname': hostname, 'token': token}))
        response = conn.getresponse()
        raw = response.read(4097)
        if response.status != 200 or len(raw) > 4096:
            raise ValueError('Public DNS response budget')
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('hostname') != hostname or value.get('ttl') != 60
                or not isinstance(value.get('addresses'), list) or not 1 <= len(value['addresses']) <= 16):
            raise ValueError('Public DNS response required')
        rows = []
        for row in value['addresses']:
            if not isinstance(row, dict) or row.get('family') not in (4, 6) or not isinstance(row.get('address'), str):
                raise ValueError('Public DNS address required')
            if ipaddress.ip_address(row['address']).version != row['family']:
                raise ValueError('Public DNS family required')
            rows.append((socket.AF_INET if row['family'] == 4 else socket.AF_INET6,
                         socket.SOCK_STREAM, 0, '', (row['address'], 0)))
        return public_dns(hostname, lambda *_args, **_kwargs: rows)
    finally:
        conn.close()


def serve(token: str, origin: str, address: tuple[str, int] = ("0.0.0.0", 18800),
          *, dns_only: bool = False, dns_lookup=None) -> ThreadingHTTPServer:
    if len(token) < 32 or not re.fullmatch(r"http://[0-9.]+:18800", origin):
        raise ValueError("dedicated browser token and fixed bridge origin required")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: object) -> None:
            pass

        def do_GET(self) -> None:
            try:
                valid = authorized(self.path, token) and (not dns_only or urlsplit(self.path).path == '/json/public-dns')
            except ValueError:
                valid = False
            if not valid:
                return self.reply(403, b'{"error":"browser access required"}')
            if not CLIENTS.acquire(blocking=False):
                return self.reply(503, b'{"error":"browser connection budget"}')
            try:
                self.connection.settimeout(15)
                path = urlsplit(self.path).path
                if path == '/json/public-dns':
                    if not DNS_CLIENTS.acquire(blocking=False):
                        return self.reply(503, b'{"error":"public DNS query budget"}')
                    hostname = parse_qs(urlsplit(self.path).query)['hostname'][0]
                    future = DNS_POOL.submit(dns_lookup or public_dns, hostname)
                    future.add_done_callback(lambda _future: DNS_CLIENTS.release())
                    try:
                        return self.reply(200, json.dumps(future.result(timeout=3)).encode())
                    except (OSError, ValueError, FutureTimeout):
                        return self.reply(502, b'{"error":"public DNS unavailable"}')
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
    if sys.argv[1:] == ['--dns-only']:
        serve(token, 'http://127.0.0.1:18800', ('172.30.188.5', 18802), dns_only=True).serve_forever()
        return
    if len(sys.argv) != 1:
        raise ValueError('Supported worker mode required')
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
        serve(token, os.environ["BROWSER_PUBLIC_ORIGIN"],
              dns_lookup=lambda hostname: remote_public_dns(hostname, token)).serve_forever()
    finally:
        browser.terminate()
        browser.wait(timeout=10)


if __name__ == "__main__":
    main()
