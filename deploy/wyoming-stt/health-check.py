#!/usr/bin/env python3
"""Verify that the local Wyoming server answers Describe with ASR Info."""

import asyncio
import sys

from wyoming.client import AsyncClient
from wyoming.info import Describe, Info


async def check() -> None:
    async with AsyncClient.from_uri("tcp://127.0.0.1:10300") as client:
        await client.write_event(Describe().event())
        while True:
            event = await client.read_event()
            if event is None:
                raise RuntimeError("connection closed without Wyoming Info")
            if Info.is_type(event.type):
                info = Info.from_event(event)
                if not info.asr:
                    raise RuntimeError("Wyoming Info contains no ASR program")
                return


async def main() -> None:
    try:
        await asyncio.wait_for(check(), timeout=15)
    except Exception as error:  # Docker must receive a non-zero health status.
        print(f"unhealthy: {error or type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    asyncio.run(main())
