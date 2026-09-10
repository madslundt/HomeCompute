from __future__ import annotations

import asyncio

from wyoming.client import AsyncClient
from wyoming.info import Describe, Info


async def main() -> None:
    client = AsyncClient.from_uri("tcp://127.0.0.1:10200", connect_timeout=3, read_timeout=5)
    async with client:
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None or not Info.is_type(event.type):
            raise RuntimeError("MOSS Wyoming endpoint did not return info")


asyncio.run(main())
