"""Exercise tunnel resets before and after real dashboard requests cross the relay."""
import asyncio
import importlib.util
import json
import tempfile
from unittest.mock import patch
from contextlib import ExitStack
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dashboard_relay', ROOT / 'deploy/hermes/dashboard-relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.servers = []
        self.connections = []
        self.tasks = set()

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
        for writer in self.connections:
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(writer.wait_closed() for writer in self.connections), return_exceptions=True)
        await asyncio.gather(*(server.wait_closed() for server in self.servers))

    async def serve(self, handler):
        async def wrapped(reader, writer):
            self.connections.append(writer)
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                await handler(reader, writer)
            finally:
                writer.close()
                self.tasks.discard(task)
        server = await asyncio.start_server(wrapped, '127.0.0.1', 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def start_relay(self, backend):
        port = await self.serve(lambda r, w: relay.handle(r, w, port=backend, timeout=0.15, delay=0))
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        self.connections.append(writer)
        return reader, writer

    async def ready(self, reader, writer):
        self.assertEqual(await reader.readuntil(b'\r\n\r\n'), relay.PROBE)
        writer.write(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 6\r\n\r\ndenied')
        await writer.drain()

    async def test_reset_before_ready_retries_without_sending_post(self):
        attempts, requests = [], []
        async def backend(reader, writer):
            attempts.append(1)
            if len(attempts) == 1:
                self.assertEqual(await reader.readuntil(b'\r\n\r\n'), relay.PROBE)
                writer.transport.abort()
                return
            await self.ready(reader, writer)
            requests.append(await reader.readuntil(b'\r\n\r\n') + await reader.readexactly(7))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain()
        reader, writer = await self.start_relay(await self.serve(backend))
        request = b'POST /auth/password-login HTTP/1.1\r\nHost: agents.home.arpa\r\nContent-Length: 7\r\n\r\nprivate'
        writer.write(request); await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        self.assertTrue(response.endswith(b'\r\n\r\nok'), response)
        self.assertEqual(requests, [request])
        self.assertEqual(len(attempts), 2)

    async def test_ambiguous_reset_after_chat_submission_never_replays(self):
        requests = []
        async def backend(reader, writer):
            await self.ready(reader, writer)
            requests.append(await reader.readuntil(b'\r\n\r\n') + await reader.readexactly(4))
            writer.transport.abort()
        reader, writer = await self.start_relay(await self.serve(backend))
        request = b'POST /api/chat HTTP/1.1\r\nContent-Length: 4\r\n\r\nchat'
        writer.write(request); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(requests, [request])

    async def test_readiness_timeout_is_bounded_and_does_not_forward_private_bytes(self):
        attempts = []
        async def backend(reader, writer):
            attempts.append(await reader.readuntil(b'\r\n\r\n'))
            await reader.read()
        reader, writer = await self.start_relay(await self.serve(backend))
        writer.write(b'POST /api/chat HTTP/1.1\r\n\r\nprivate'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        self.assertEqual(attempts, [relay.PROBE] * relay.ATTEMPTS)

    async def test_unprotected_or_closed_or_malformed_probe_is_refused(self):
        for response in (
            b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n',
            b'HTTP/1.1 401 Unauthorized\r\nConnection: close\r\nContent-Length: 0\r\n\r\n',
            b'HTTP/1.1 401 Unauthorized\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n',
            b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 999999\r\n\r\n',
            b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n',
        ):
            received = []
            async def backend(reader, writer):
                received.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(response); await writer.drain()
                self.assertEqual(await reader.read(), b'')
            reader, writer = await self.start_relay(await self.serve(backend))
            writer.write(b'GET / HTTP/1.1\r\n\r\n'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
            self.assertTrue(received)
            self.assertTrue(all(item == relay.PROBE for item in received))

    async def test_websocket_upgrade_and_bidirectional_stream_survive(self):
        async def backend(reader, writer):
            await self.ready(reader, writer)
            request = await reader.readuntil(b'\r\n\r\n')
            self.assertIn(b'Upgrade: websocket', request)
            writer.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
            await writer.drain()
            writer.write(await reader.readexactly(5)); await writer.drain()
        reader, writer = await self.start_relay(await self.serve(backend))
        writer.write(b'GET /ws HTTP/1.1\r\nUpgrade: websocket\r\n\r\n'); await writer.drain()
        self.assertIn(b'101 Switching Protocols', await reader.readuntil(b'\r\n\r\n'))
        writer.write(b'frame'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(5), 2), b'frame')

    async def test_disconnected_browser_does_not_leave_a_native_channel_open(self):
        closed = asyncio.Event()
        async def backend(reader, writer):
            await self.ready(reader, writer)
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
            await writer.drain()
            await reader.read()
            closed.set()
            # Reproduce OpenShell retaining its response side after client EOF.
            await asyncio.Event().wait()
        reader, writer = await self.start_relay(await self.serve(backend))
        writer.write(b'GET / HTTP/1.1\r\n\r\n'); await writer.drain()
        await reader.readuntil(b'\r\n\r\n'); await reader.readexactly(2)
        writer.close(); await writer.wait_closed()
        await asyncio.wait_for(closed.wait(), 2)
        await asyncio.sleep(1.1)
        self.assertFalse(any(task.get_coro().__name__ == 'handle' for task in asyncio.all_tasks()))

    def test_api_route_allows_only_default_profile_models_and_chat(self):
        self.assertTrue(relay.api_request_header(
            b'GET /p/default/v1/models HTTP/1.1\r\nAuthorization: Bearer synthetic\r\n'))
        self.assertTrue(relay.api_request_header(
            b'POST /p/default/v1/chat/completions HTTP/1.1\r\nAuthorization: Bearer synthetic\r\n'))
        self.assertTrue(relay.api_request_header(
            b'GET /p/hermes-conversation-probe-does-not-exist/v1/models HTTP/1.1\r\n'))
        for request in (
            b'GET /p/default/v1/runs HTTP/1.1\r\n',
            b'GET /p/other/v1/models HTTP/1.1\r\n',
            b'DELETE /p/default/v1/models HTTP/1.1\r\n',
            b'POST /p/default/v1/chat/completions/extra HTTP/1.1\r\n',
        ):
            self.assertFalse(relay.api_request_header(request))

    async def test_api_request_uses_only_its_health_gated_loopback_forward(self):
        requests = []
        async def api_backend(reader, writer):
            request = await reader.readuntil(b'\r\n\r\n')
            if b'GET /health ' in request:
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                await writer.drain()
                return
            requests.append(request)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain()
        api_port = await self.serve(api_backend)
        with patch.object(relay, 'API_PORT', api_port):
            relay_port = await self.serve(lambda r, w: relay.handle(r, w, port=api_port, timeout=0.15, delay=0))
            reader, writer = await asyncio.open_connection('127.0.0.1', relay_port)
            self.connections.append(writer)
            request = (b'POST /p/default/v1/chat/completions HTTP/1.1\r\n'
                       b'Authorization: Bearer synthetic\r\nContent-Length: 0\r\n\r\n')
            writer.write(request); await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
        self.assertTrue(response.endswith(b'\r\n\r\nok'), response)
        self.assertEqual(requests, [request])


class InstallerTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('relay_install', ROOT / 'scripts/install-hermes-dashboard-relay.py')
        self.install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.install)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in (('STATE', root / 'state'), ('LIB', root / 'lib'),
                            ('UNIT_PATH', root / 'unit.service')):
            self.stack.enter_context(patch.object(self.install, name, value))
        self.stack.enter_context(patch.object(self.install, 'protected', lambda p: p.read_bytes()))
        self.stack.enter_context(patch.object(self.install, 'directory', lambda p, mode=0o700: p.mkdir(mode=mode, parents=True, exist_ok=True)))
        self.auth = self.stack.enter_context(patch.object(self.install, 'check_auth'))
        self.activate = self.stack.enter_context(patch.object(self.install, 'activate'))
        self.stack.enter_context(patch.object(self.install.subprocess, 'run'))
        self.revision = 'a' * 40
        template = (ROOT / 'deploy/hermes/hermes-dashboard-relay.service').read_bytes()
        self.legacy = template.replace(b'ExecStart=/usr/bin/python3 @RELAY@ --target-port @PORT@', self.install.LEGACY_START.encode())
        self.legacy = self.legacy.replace(b'MemoryMax=128M\nTasksMax=32\n', b'')
        self.install.UNIT_PATH.write_bytes(self.legacy)

    def apply(self):
        report = self.install.plan(self.revision)[0]
        return self.install.apply(self.revision, self.install.digest(json.dumps(report, sort_keys=True).encode()))

    def test_upgrade_is_idempotent_and_rollback_retains_original_preimage(self):
        self.apply()
        receipt = (self.install.STATE / 'receipt.json').read_bytes()
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), self.install.render(self.revision, 18794))
        self.apply()
        self.assertEqual((self.install.STATE / 'receipt.json').read_bytes(), receipt)
        self.install.rollback()
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), self.legacy)

    def test_unknown_unit_and_changed_plan_are_preserved(self):
        with self.assertRaises(ValueError):
            self.install.apply(self.revision, 'incorrect')
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), self.legacy)
        unknown = self.legacy.replace(b'18794', b'19999')
        self.install.UNIT_PATH.write_bytes(unknown)
        with self.assertRaises(ValueError):
            self.install.plan(self.revision)
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), unknown)

    def test_relay_repair_preserves_surface_and_only_explicit_cutover_selects_webapp(self):
        self.assertEqual(self.install.plan(self.revision)[0]['target_port'], 18794)
        report = self.install.plan(self.revision, 'webapp')[0]
        self.install.apply(self.revision, self.install.digest(json.dumps(report, sort_keys=True).encode()), 'webapp')
        self.auth.assert_called_once_with(18796)
        self.assertEqual(self.install.unit_port(self.install.UNIT_PATH.read_bytes()), 18796)
        self.assertEqual(self.install.plan(self.revision)[0]['target_port'], 18796)
        self.install.rollback()
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), self.legacy)

    def test_activation_failure_restores_unit(self):
        self.activate.side_effect = [RuntimeError('synthetic activation failure'), None]
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), self.legacy)

    def test_rollback_preserves_later_unit_changes(self):
        self.apply()
        unknown = self.install.UNIT_PATH.read_bytes() + b'# later edit\n'
        self.install.UNIT_PATH.write_bytes(unknown)
        with self.assertRaises(ValueError):
            self.install.rollback()
        self.assertEqual(self.install.UNIT_PATH.read_bytes(), unknown)


if __name__ == '__main__':
    unittest.main()
