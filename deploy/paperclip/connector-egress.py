"""Expose the connector proxy and fixed GitHub TLS routes on private links."""
import asyncio
from contextlib import suppress
import threading

from openclaw_site_egress import Proxy, public_addresses

LISTEN = '172.30.198.2'
GITHUB_METADATA_LISTEN = '172.30.198.10'
PROXY_HOSTS = frozenset({'my.paperclip.app', 'api.github.com', 'github.com'})


async def dial_tls(destination):
    addresses = await asyncio.wait_for(asyncio.to_thread(public_addresses, destination), 5)
    for family, _, _, address in addresses:
        try:
            return await asyncio.wait_for(asyncio.open_connection(address[0], 443, family=family), 3)
        except (OSError, TimeoutError):
            continue
    raise OSError('Connector TLS destination unavailable')


async def forward(source, destination):
    while data := await asyncio.wait_for(source.read(65536), 90):
        destination.write(data)
        await asyncio.wait_for(destination.drain(), 10)
    destination.write_eof()


async def serve_tls(destination, host, port=443):
    slots = asyncio.Semaphore(16)

    async def relay(client_reader, client_writer):
        upstream_writer = None
        try:
            if slots.locked():
                return
            async with slots, asyncio.timeout(300):
                prefix = await asyncio.wait_for(client_reader.readexactly(5), 5)
                if prefix[:2] != b'\x16\x03':
                    return
                upstream_reader, upstream_writer = await dial_tls(destination)
                upstream_writer.write(prefix)
                async with asyncio.TaskGroup() as streams:
                    streams.create_task(forward(client_reader, upstream_writer))
                    streams.create_task(forward(upstream_reader, client_writer))
        except (OSError, EOFError, ValueError, TimeoutError, ExceptionGroup):
            # Never log TLS bytes or credentials.
            pass
        finally:
            for writer in (client_writer, upstream_writer):
                if writer is not None:
                    writer.close()
                    with suppress(OSError, TimeoutError):
                        await asyncio.wait_for(writer.wait_closed(), 2)

    return await asyncio.start_server(relay, host, port, backlog=32)


async def main():
    async with (await serve_tls('api.githubcopilot.com', LISTEN) as mcp,
                await serve_tls('github.com', GITHUB_METADATA_LISTEN) as metadata):
        with Proxy((LISTEN, 3128), PROXY_HOSTS, tunnel_seconds=300) as proxy:
            worker = threading.Thread(target=proxy.serve_forever, daemon=True)
            worker.start()
            try:
                async with asyncio.TaskGroup() as servers:
                    servers.create_task(mcp.serve_forever())
                    servers.create_task(metadata.serve_forever())
            finally:
                proxy.shutdown()
                worker.join()


if __name__ == '__main__':
    asyncio.run(main())
