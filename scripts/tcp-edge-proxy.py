#!/usr/bin/env python3
"""Small logging-free TCP relay for publishing an internal inference service."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import sys


HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def required_setting(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"missing {name}")
    return value


def port_setting(name: str) -> int:
    value = required_setting(name)
    if not value.isdecimal() or value != str(int(value)):
        raise ValueError(f"{name} must be a canonical decimal port")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be 1 through 65535")
    return port


def upstream_setting() -> str:
    value = required_setting("EDGE_UPSTREAM_HOST")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if not HOST_RE.fullmatch(value):
            raise ValueError("EDGE_UPSTREAM_HOST must be one DNS label or a canonical IP address")
        return value
    if str(address) != value:
        raise ValueError("EDGE_UPSTREAM_HOST IP address must be canonical")
    return value


async def copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        try:
            writer.write_eof()
        except (AttributeError, OSError):
            pass


async def serve() -> None:
    upstream_host = upstream_setting()
    listen_port = port_setting("EDGE_LISTEN_PORT")
    upstream_port = port_setting("EDGE_UPSTREAM_PORT")
    limit = asyncio.Semaphore(64)

    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        async with limit:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(upstream_host, upstream_port), timeout=10
                )
            except (OSError, TimeoutError):
                client_writer.close()
                await client_writer.wait_closed()
                return
            tasks = {
                asyncio.create_task(copy_stream(client_reader, upstream_writer)),
                asyncio.create_task(copy_stream(upstream_reader, client_writer)),
            }
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            upstream_writer.close()
            client_writer.close()
            await asyncio.gather(
                upstream_writer.wait_closed(), client_writer.wait_closed(), return_exceptions=True
            )

    server = await asyncio.start_server(handle, "0.0.0.0", listen_port, backlog=128)
    async with server:
        await server.serve_forever()


async def probe_upstream() -> None:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(upstream_setting(), port_setting("EDGE_UPSTREAM_PORT")), timeout=5
    )
    del reader
    writer.close()
    await writer.wait_closed()


def main() -> None:
    try:
        if sys.argv[1:] == ["--probe-upstream"]:
            asyncio.run(probe_upstream())
        elif sys.argv[1:]:
            raise ValueError("usage: tcp-edge-proxy.py [--probe-upstream]")
        else:
            asyncio.run(serve())
    except (OSError, ValueError) as error:
        print(f"tcp-edge-proxy: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
