import asyncio
from collections.abc import Callable

import httpx

from .models import NormalizedEvent


class HomeAssistantUnavailable(Exception):
    pass


async def forward_event(
    event: NormalizedEvent,
    url: str,
    timeout: float,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    last_error: Exception | None = None
    async with client_factory(timeout=timeout, follow_redirects=False) as client:
        for attempt, delay in enumerate((0.0, 1.0, 3.0), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await client.post(
                    url,
                    json=event.model_dump(mode="json"),
                    headers={"User-Agent": "homecompute-ttlock-webhook/1"},
                )
                if 200 <= response.status_code < 300:
                    return
                if response.status_code < 500 and response.status_code != 429:
                    raise HomeAssistantUnavailable(
                        f"Home Assistant rejected event with HTTP {response.status_code}"
                    )
                last_error = RuntimeError(f"HTTP {response.status_code}")
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
    raise HomeAssistantUnavailable(f"Home Assistant unavailable after 3 attempts: {last_error}")


async def forward_raw_callback(
    body: bytes,
    content_type: str,
    url: str,
    timeout: float,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    """Relay TTLock's validated form callback to the HA TTLock integration."""
    last_error: Exception | None = None
    async with client_factory(timeout=timeout, follow_redirects=False) as client:
        for delay in (0.0, 1.0, 3.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": content_type,
                        "User-Agent": "homecompute-ttlock-webhook/1",
                    },
                )
                if 200 <= response.status_code < 300:
                    return
                if response.status_code < 500 and response.status_code != 429:
                    raise HomeAssistantUnavailable(
                        f"Home Assistant rejected raw callback with HTTP {response.status_code}"
                    )
                last_error = RuntimeError(f"HTTP {response.status_code}")
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
    raise HomeAssistantUnavailable(
        f"Home Assistant raw callback unavailable after 3 attempts: {last_error}"
    )
