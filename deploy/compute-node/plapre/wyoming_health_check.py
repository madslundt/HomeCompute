#!/usr/bin/env python3
from __future__ import annotations

import asyncio

from wyoming.client import AsyncClient
from wyoming.info import Describe, Info


async def probe() -> None:
    client = AsyncClient.from_uri("tcp://127.0.0.1:10201", connect_timeout=5, read_timeout=5)
    async with client:
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None or not Info.is_type(event.type):
            raise SystemExit(1)
        if not any(program.installed for program in Info.from_event(event).tts):
            raise SystemExit(1)


asyncio.run(probe())
