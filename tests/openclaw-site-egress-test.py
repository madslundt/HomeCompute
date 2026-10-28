#!/usr/bin/env python3
"""CONNECT parsing and DNS pinning without external network connections."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_site_egress as egress

HOST = 'shop.example.com'
SECRET = b'synthetic-upstream-private-error-never-output'


def answer(value='8.8.8.8', port=443, family=socket.AF_INET):
    return (family, socket.SOCK_STREAM, 0, '', (value, port))


class Connection:
    def __init__(self, raw):
        self.raw, self.sent, self.timeouts = raw, bytearray(), []
    def makefile(self, *_args): return io.BytesIO(self.raw)
    def settimeout(self, value): self.timeouts.append(value)
    def sendall(self, value): self.sent.extend(value)
    def recv(self, _size): return b'synthetic-tls-record'


class Upstream:
    def __init__(self, failed=False):
        self.failed, self.targets, self.closed, self.sent = failed, [], False, bytearray()
    def settimeout(self, _value): pass
    def connect(self, target):
        self.targets.append(target)
        if self.failed: raise OSError(SECRET)
    def recv(self, _size): return b''
    def sendall(self, value): self.sent.extend(value)
    def close(self): self.closed = True


class EgressTests(unittest.TestCase):
    def test_all_dns_answers_are_checked_before_public_destination_is_returned(self):
        for address in ['127.0.0.1', '10.1.2.3', '169.254.169.254', '100.110.248.102', '::1',
                        '::ffff:8.8.8.8', '64:ff9b::808:808', '224.0.0.1', '192.0.2.1']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                egress.public_addresses(HOST, resolver=lambda *_a, **_k: [answer(), answer(address)])
        for records in [[], [answer(port=80)], [answer(f'8.8.8.{i}') for i in range(1, 18)]]:
            with self.assertRaises(ValueError):
                egress.public_addresses(HOST, resolver=lambda *_a, **_k: records)

    def test_public_answers_are_deduplicated_and_keep_exact_pinned_socket_target(self):
        calls = []
        def resolver(host, port, **kwargs):
            calls.append((host, port, kwargs))
            return [answer(), answer(), answer('1.1.1.1')]
        values = egress.public_addresses(HOST, resolver=resolver)
        self.assertEqual([item[3] for item in values], [('8.8.8.8', 443), ('1.1.1.1', 443)])
        self.assertEqual(calls, [(HOST, 443, {'type': socket.SOCK_STREAM})])

    def handle(self, raw, *, records=None, failed=False, tunnel=False):
        connection, upstream = Connection(raw), Upstream(failed)
        resolutions = []
        selections = [([connection], [], []), ([upstream], [], [])] if tunnel else [([upstream], [], [])]
        original = egress.public_addresses
        def checked(host):
            resolutions.append(host)
            return original(host, resolver=lambda *_a, **_k: records or [answer()])
        with patch.object(egress, 'public_addresses', checked), \
             patch.object(egress.socket, 'socket', return_value=upstream) as create_socket, \
             patch.object(egress.select, 'select', side_effect=selections):
            egress.Handler(connection, ('fixture', 1), SimpleNamespace(hosts=frozenset({HOST})))
        return bytes(connection.sent), upstream, resolutions, create_socket.call_count

    def test_connect_uses_validated_ip_once_and_closes_tunnel(self):
        response, upstream, calls, count = self.handle(
            b'CONNECT shop.example.com:443 HTTP/1.1\r\nHost: shop.example.com:443\r\n\r\n', tunnel=True)
        self.assertEqual(response, b'HTTP/1.1 200 Connection Established\r\n\r\n')
        self.assertEqual(calls, [HOST]); self.assertEqual(count, 1)
        self.assertEqual(upstream.targets, [('8.8.8.8', 443)])
        self.assertEqual(upstream.sent, b'synthetic-tls-record'); self.assertTrue(upstream.closed)

    def test_non_connect_wrong_host_port_userinfo_ip_and_header_smuggling_never_resolve(self):
        lines = [b'GET https://shop.example.com/ HTTP/1.1\r\n',
                 b'CONNECT attacker.example.com:443 HTTP/1.1\r\n',
                 b'CONNECT shop.example.com:80 HTTP/1.1\r\n',
                 b'CONNECT user@shop.example.com:443 HTTP/1.1\r\n',
                 b'CONNECT 127.0.0.1:443 HTTP/1.1\r\n',
                 b'CONNECT shop.example.com:443 HTTP/1.1\n',
                 b'CONNECT shop.example.com:443 HTTP/1.1\r\n folded: x\r\n',
                 b'CONNECT shop.example.com:443 HTTP/1.1\r\nNo-colon\r\n',
                 b'CONNECT shop.example.com:443 HTTP/1.1\r\n' + b'X: a\r\n' * 33,
                 b'CONNECT shop.example.com:443 HTTP/1.1\r\nX: ' + b'a' * 9000 + b'\r\n']
        for line in lines:
            with self.subTest(line=line[:60]):
                response, upstream, resolutions, count = self.handle(line + b'\r\n')
                self.assertTrue(response.startswith(b'HTTP/1.1 403'))
                self.assertEqual(resolutions, []); self.assertEqual(count, 0)
                self.assertEqual(upstream.targets, [])

    def test_mixed_private_dns_is_rejected_before_dial_and_error_is_fixed(self):
        response, upstream, _calls, count = self.handle(
            b'CONNECT shop.example.com:443 HTTP/1.1\r\n\r\n', records=[answer(), answer('10.1.2.3')])
        self.assertTrue(response.startswith(b'HTTP/1.1 403')); self.assertEqual(count, 0)
        self.assertEqual(upstream.targets, [])
        response, upstream, _calls, _count = self.handle(
            b'CONNECT shop.example.com:443 HTTP/1.1\r\n\r\n', failed=True)
        self.assertTrue(response.startswith(b'HTTP/1.1 403')); self.assertNotIn(SECRET, response)
        self.assertTrue(upstream.closed)

    def test_dynamic_policy_refreshes_atomic_replacement_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            def write(value):
                replacement = path.with_suffix('.new')
                replacement.write_text(json.dumps(value)); replacement.chmod(0o644)
                os.replace(replacement, path)
            write({'allow_public': True, 'hosts': []})
            self.assertTrue(egress.permitted('identity.example.com', policy=path))
            self.assertFalse(egress.permitted('127.0.0.1', policy=path))
            self.assertFalse(egress.permitted('user@shop.example.com', policy=path))
            write({'allow_public': False, 'hosts': [HOST]})
            self.assertTrue(egress.permitted(HOST, policy=path))
            self.assertFalse(egress.permitted('identity.example.com', policy=path))
            for value in [{'allow_public': True, 'hosts': [], 'url': SECRET.decode()},
                          {'allow_public': 1, 'hosts': []}, {'allow_public': False, 'hosts': [HOST, HOST]},
                          {'allow_public': True, 'hosts': ['https://shop.example.com']},
                          {'allow_public': True, 'hosts': [] , 'padding': 'x' * 16384}]:
                write(value)
                with self.assertRaises(Exception): egress.permitted(HOST, policy=path)
            write({'allow_public': True, 'hosts': []}); path.chmod(0o666)
            with self.assertRaises(Exception): egress.permitted(HOST, policy=path)
            path.chmod(0o644)
            path.write_text('{"allow_public":false,"allow_public":true,"hosts":[]}')
            with self.assertRaises(Exception): egress.permitted(HOST, policy=path)
            path.unlink(); path.symlink_to('missing')
            with self.assertRaises(Exception): egress.permitted(HOST, policy=path)

    def test_existing_connect_tunnel_is_closed_when_dynamic_policy_refuses_host(self):
        connection = Connection(b'CONNECT shop.example.com:443 HTTP/1.1\r\n\r\n')
        upstream = Upstream()
        with patch.object(egress, 'permitted', side_effect=[True, False]), \
             patch.object(egress, 'public_addresses', return_value=[answer()[:3] + (answer()[4],)]), \
             patch.object(egress.socket, 'socket', return_value=upstream), \
             patch.object(egress.select, 'select') as poll:
            egress.Handler(connection, ('fixture', 1), SimpleNamespace(hosts=frozenset(), policy=Path('/operator/policy.json')))
        self.assertTrue(bytes(connection.sent).startswith(b'HTTP/1.1 200'))
        self.assertTrue(upstream.closed); poll.assert_not_called()


if __name__ == '__main__':
    unittest.main()
