#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import socketserver
import subprocess
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROXY = ROOT / "scripts" / "tcp-edge-proxy.py"


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(65536):
            self.request.sendall(data)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TcpEdgeProxyTest(unittest.TestCase):
    def test_relays_bytes_without_protocol_parsing(self) -> None:
        upstream_port = free_port()
        listen_port = free_port()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", upstream_port), EchoHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = os.environ.copy()
        environment.update(
            EDGE_LISTEN_PORT=str(listen_port),
            EDGE_UPSTREAM_HOST="localhost",
            EDGE_UPSTREAM_PORT=str(upstream_port),
        )
        process = subprocess.Popen(
            [str(PROXY)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            for _ in range(50):
                try:
                    client = socket.create_connection(("127.0.0.1", listen_port), timeout=1)
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail(f"proxy exited with status {process.returncode}")
                    time.sleep(0.02)
            else:
                self.fail("proxy did not start")
            with client:
                client.sendall(b"opaque-request-payload")
                self.assertEqual(client.recv(65536), b"opaque-request-payload")
        finally:
            process.terminate()
            process.wait(timeout=5)
            server.shutdown()
            server.server_close()

    def test_accepts_canonical_ipv4_and_probes_upstream(self) -> None:
        upstream_port = free_port()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", upstream_port), EchoHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = os.environ.copy()
        environment.update(
            EDGE_LISTEN_PORT=str(free_port()),
            EDGE_UPSTREAM_HOST="127.0.0.1",
            EDGE_UPSTREAM_PORT=str(upstream_port),
        )
        try:
            result = subprocess.run(
                [str(PROXY), "--probe-upstream"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            server.shutdown()
            server.server_close()

    def test_rejects_noncanonical_ipv4(self) -> None:
        environment = os.environ.copy()
        environment.update(
            EDGE_LISTEN_PORT=str(free_port()),
            EDGE_UPSTREAM_HOST="127.000.000.001",
            EDGE_UPSTREAM_PORT=str(free_port()),
        )
        result = subprocess.run(
            [str(PROXY), "--probe-upstream"], env=environment, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical IP address", result.stderr)


if __name__ == "__main__":
    unittest.main()
