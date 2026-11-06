#!/usr/bin/env python3
"""Gate each dashboard tunnel before forwarding any client bytes.

OpenShell can accept TCP and then reset while opening its sandbox channel.
Retry only the credential-free probe, never a browser request. After readiness,
copy bytes unchanged so Hermes owns HTTP, authentication and WebSockets.
"""
import asyncio
import contextlib

HOST = '127.0.0.1'
PORT = 18794
ATTEMPTS = 3
PROBE = (b'GET /api/config HTTP/1.1\r\nHost: agents.home.arpa\r\n'
         b'Connection: keep-alive\r\n\r\n')


async def close(writer):
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


async def ready(port, timeout, delay):
    for attempt in range(ATTEMPTS):
        writer = None
        try:
            # Includes the native channel handshake, not just TCP connect.
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(HOST, port, limit=16384)
                writer.write(PROBE)
                await writer.drain()
                header = await reader.readuntil(b'\r\n\r\n')
                lines = header.split(b'\r\n')
                if lines[0].split()[:2] != [b'HTTP/1.1', b'401']:
                    raise ValueError('native authentication not ready')
                fields = {}
                for line in lines[1:-2]:
                    key, value = line.split(b':', 1)
                    key = key.strip().lower()
                    if key in fields:
                        raise ValueError('ambiguous probe framing')
                    fields[key] = value.strip().lower()
                length = fields.get(b'content-length', b'')
                if (not length.isdigit() or int(length) > 65536
                        or b'transfer-encoding' in fields
                        or b'close' in fields.get(b'connection', b'')):
                    raise ValueError('probe must leave a reusable connection')
                await reader.readexactly(int(length))
            return reader, writer
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            if writer is not None:
                await close(writer)
            if attempt + 1 < ATTEMPTS:
                await asyncio.sleep(delay)
        except BaseException:
            if writer is not None:
                await close(writer)
            raise
    raise ConnectionError('native dashboard tunnel not ready')


async def copy(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()
        await writer.drain()


async def handle(reader, writer, *, port=PORT, timeout=8, delay=0.1):
    upstream = None
    tasks = []
    try:
        target, upstream = await ready(port, timeout, delay)
        # From this point failures are never retried: a submitted request may
        # already have changed state even if its response has not arrived.
        tasks = [asyncio.create_task(copy(reader, upstream)),
                 asyncio.create_task(copy(target, writer))]
        # Match the old socat relay's bounded half-close behavior. OpenShell
        # may retain its read side after the browser has disconnected.
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[1] not in done:
            await asyncio.wait_for(tasks[1], timeout=1)
    except (OSError, ConnectionError, asyncio.IncompleteReadError):
        pass  # No URLs, cookies, payloads or credentials enter relay logs.
    finally:
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                if upstream is not None:
                    await close(upstream)
            finally:
                await close(writer)


async def main(port):
    active = set()
    async def accept(reader, writer):
        if len(active) >= 128:
            await close(writer)
            return
        task = asyncio.current_task()
        active.add(task)
        try:
            await handle(reader, writer, port=port)
        finally:
            active.discard(task)
    server = await asyncio.start_server(accept, '10.77.20.2', 18804, backlog=128)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-port', type=int, choices=(18794, 8646), required=True)
    asyncio.run(main(parser.parse_args().target_port))
