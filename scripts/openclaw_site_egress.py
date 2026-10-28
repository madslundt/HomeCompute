"""TLS CONNECT proxy with exact registered hosts and public-IP DNS pinning.

It cannot inspect TLS payloads, and records neither URLs nor network traffic.
The browser network must separately deny every route except this proxy.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import stat
import threading
import time

HOST = re.compile(r'(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')


def unique_policy(pairs):
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError('Duplicate proxy policy field refused')
        value[key] = entry
    return value


def permitted(host: str, *, hosts=frozenset(), policy: Path | None = None) -> bool:
    """Reload an operator-owned mounted directory entry, never a browser input."""
    if not HOST.fullmatch(host):
        return False
    if policy is None:
        return host in hosts
    descriptor = os.open(policy, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 16384:
            raise ValueError('Private proxy policy refused')
        raw = os.read(descriptor, 16385)
        value = json.loads(raw, object_pairs_hook=unique_policy)
        if (len(raw) > 16384 or not isinstance(value, dict) or set(value) != {'allow_public', 'hosts'}
                or type(value['allow_public']) is not bool or not isinstance(value['hosts'], list)
                or len(value['hosts']) > 64 or len(set(value['hosts'])) != len(value['hosts'])
                or any(not isinstance(h, str) or not HOST.fullmatch(h) for h in value['hosts'])):
            raise ValueError('Private proxy policy refused')
        return value['allow_public'] or host in value['hosts']
    finally:
        os.close(descriptor)


def public_addresses(host: str, resolver=socket.getaddrinfo) -> list[tuple]:
    answers = resolver(host, 443, type=socket.SOCK_STREAM)
    addresses = []
    for family, kind, protocol, _name, target in answers:
        address = ipaddress.ip_address(target[0])
        if (family not in {socket.AF_INET, socket.AF_INET6} or not address.is_global
                or address.is_reserved or address.is_multicast
                or getattr(address, 'ipv4_mapped', None) is not None
                or address in ipaddress.ip_network('64:ff9b::/96')):
            raise ValueError('Non-public destination refused')
        if target[1] != 443:
            raise ValueError('TLS destination refused')
        item = (family, kind, protocol, target)
        if item not in addresses:
            addresses.append(item)
    if not 1 <= len(addresses) <= 16:
        raise ValueError('DNS response refused')
    return addresses


class Proxy(socketserver.ThreadingTCPServer):
    daemon_threads, allow_reuse_address = True, True

    def __init__(self, address: tuple[str, int], hosts: frozenset[str], *, policy: Path | None = None):
        if (policy is None and not hosts or len(hosts) > 64
                or any(not HOST.fullmatch(h) for h in hosts)):
            raise ValueError('Registered hosts required')
        self.hosts, self.policy, self.slots = hosts, policy, threading.BoundedSemaphore(32)
        super().__init__(address, Handler)

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


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream = None
        try:
            self.connection.settimeout(3)
            line = self.rfile.readline(1025)
            match = re.fullmatch(rb'CONNECT ([a-z0-9.-]{3,253}):443 HTTP/1\.[01]\r\n', line)
            if not match or not permitted(match[1].decode(), hosts=self.server.hosts,
                                          policy=getattr(self.server, 'policy', None)):
                raise ValueError('Destination refused')
            budget = len(line)
            for _ in range(32):
                header = self.rfile.readline(1025)
                budget += len(header)
                if budget > 8192 or not header.endswith(b'\r\n'):
                    raise ValueError('Header budget refused')
                if header == b'\r\n':
                    break
                if b':' not in header or header[:1] in {b' ', b'\t'}:
                    raise ValueError('Header refused')
            else:
                raise ValueError('Header count refused')
            # Connect to this validated answer directly: no second DNS lookup.
            for family, kind, protocol, target in public_addresses(match[1].decode()):
                candidate = socket.socket(family, kind, protocol)
                candidate.settimeout(3)
                try:
                    candidate.connect(target)
                    upstream = candidate
                    break
                except OSError:
                    candidate.close()
            if upstream is None:
                raise ValueError('Upstream unavailable')
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            self.wfile.flush()
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                if not permitted(match[1].decode(), hosts=self.server.hosts,
                                 policy=getattr(self.server, 'policy', None)):
                    return
                readable, _, _ = select.select([upstream, self.connection], [], [], 1)
                for source in readable:
                    value = source.recv(65536)
                    if not value:
                        return
                    (upstream if source is self.connection else self.connection).sendall(value)
        except Exception:
            # No upstream text or exception values may reach a browser/agent.
            try:
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
        finally:
            if upstream is not None:
                upstream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--hosts', type=Path)
    source.add_argument('--policy', type=Path)
    parser.add_argument('--listen', choices=['172.30.192.2', '172.30.192.10', '172.30.192.11'], default='172.30.192.2')
    args = parser.parse_args()
    value = json.loads(args.hosts.read_bytes()) if args.hosts else []
    if not isinstance(value, list):
        raise SystemExit('Registered hosts required')
    Proxy((args.listen, 3128), frozenset(value), policy=args.policy).serve_forever()
